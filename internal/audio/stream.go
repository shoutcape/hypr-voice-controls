package audio

// SharedStream is a single always-on PortAudio input stream that broadcasts
// each audio chunk to all registered subscribers. This allows both the
// wakeword detector and a dictation capture to share the same mic without
// opening two competing PortAudio streams.
//
// Lifecycle:
//
//	stream, err := audio.OpenShared(cfg)   // opens mic, starts broadcasting
//	sub := stream.Subscribe()              // receive chunks on this channel
//	stream.Unsubscribe(sub)                // stop receiving
//	stream.Close()                         // stop stream, release mic

import (
	"fmt"
	"log"
	"sync"
	"sync/atomic"

	"github.com/gordonklaus/portaudio"
	"github.com/shoutcape/hypr-voice-controls/internal/config"
)

// subChanSize is the number of chunks a subscriber channel can buffer before
// the broadcaster drops a chunk for that slow subscriber.
const subChanSize = 64

// SharedStream holds a running PortAudio input stream and a set of subscriber
// channels. Each audio chunk is broadcast to all active subscribers.
type SharedStream struct {
	stream  *portaudio.Stream
	stopped atomic.Bool

	mu   sync.Mutex
	subs []chan []float32
}

// OpenShared opens a PortAudio input stream configured by cfg and starts
// broadcasting audio chunks to any subscribers. It returns as soon as the
// stream is running.
//
// Call Close when done — this stops the stream and signals all subscribers
// by closing their channels.
func OpenShared(cfg *config.Config) (*SharedStream, error) {
	device, err := resolveDevice(cfg.AudioSource)
	if err != nil {
		return nil, err
	}

	s := &SharedStream{}

	params := portaudio.StreamParameters{
		Input: portaudio.StreamDeviceParameters{
			Device:   device,
			Channels: channels,
			Latency:  device.DefaultLowInputLatency,
		},
		SampleRate:      sampleRate,
		FramesPerBuffer: framesPerBuffer,
	}

	stream, err := portaudio.OpenStream(params, s.callback)
	if err != nil {
		return nil, fmt.Errorf("failed to open shared audio stream: %w", err)
	}
	s.stream = stream

	if err := stream.Start(); err != nil {
		stream.Close() //nolint:errcheck
		return nil, fmt.Errorf("failed to start shared audio stream: %w", err)
	}

	log.Printf("audio: shared stream started on %q", device.Name)
	return s, nil
}

// callback is the PortAudio real-time callback. It copies each incoming chunk
// into a new slice and sends it to every subscriber, dropping any subscriber
// whose channel is full (they are too slow to consume).
//
// This runs on the PortAudio RT thread — must not block or allocate heavily.
// The make([]float32, n) inside is unavoidable for safe multi-subscriber
// delivery; PortAudio reuses its input buffer between callbacks.
func (s *SharedStream) callback(in []float32) {
	if s.stopped.Load() {
		return
	}

	// Copy once — all subscribers share the same immutable snapshot.
	chunk := make([]float32, len(in))
	copy(chunk, in)

	s.mu.Lock()
	for _, ch := range s.subs {
		select {
		case ch <- chunk:
		default: // subscriber is slow; drop this chunk for them
		}
	}
	s.mu.Unlock()
}

// Subscribe returns a channel on which the caller will receive audio chunks.
// Each chunk is a newly allocated []float32 of length framesPerBuffer (512).
// The channel is closed when Close is called on the SharedStream.
func (s *SharedStream) Subscribe() <-chan []float32 {
	ch := make(chan []float32, subChanSize)
	s.mu.Lock()
	s.subs = append(s.subs, ch)
	s.mu.Unlock()
	return ch
}

// Unsubscribe removes the subscriber channel returned by a prior Subscribe
// call. After Unsubscribe returns, no further chunks will be sent to ch.
// The caller must drain ch if needed; it is not closed by Unsubscribe.
func (s *SharedStream) Unsubscribe(ch <-chan []float32) {
	s.mu.Lock()
	defer s.mu.Unlock()
	for i, c := range s.subs {
		if c == ch {
			// Swap-remove to avoid shifting.
			last := len(s.subs) - 1
			s.subs[i] = s.subs[last]
			s.subs[last] = nil
			s.subs = s.subs[:last]
			return
		}
	}
}

// Close stops the PortAudio stream and closes all subscriber channels.
// It is safe to call Close multiple times.
func (s *SharedStream) Close() {
	if !s.stopped.CompareAndSwap(false, true) {
		return
	}

	if err := s.stream.Stop(); err != nil {
		log.Printf("audio: shared stream stop: %v", err)
	}
	if err := s.stream.Close(); err != nil {
		log.Printf("audio: shared stream close: %v", err)
	}

	// Close all subscriber channels so goroutines blocked on range can exit.
	s.mu.Lock()
	for _, ch := range s.subs {
		close(ch)
	}
	s.subs = nil
	s.mu.Unlock()

	log.Printf("audio: shared stream closed")
}
