// Package audio manages audio capture via PortAudio.
// It records from a SharedStream into an in-memory []float32 buffer
// (mono, 16 kHz) that whisper.cpp can consume directly.
//
// PortAudio requires a one-time global initialisation before any stream is
// opened and a matching teardown when the process exits:
//
//	if err := audio.Init(); err != nil { ... }
//	defer audio.Term()
//
// Per-capture lifecycle (using a shared always-on stream):
//
//	stream, err := audio.OpenShared(cfg)        // open mic once
//	defer stream.Close()
//
//	cap, err := audio.StartFromStream(stream, cfg)   // subscribe + buffer
//	samples, err := cap.Stop()                       // unsubscribe, return PCM
//	defer cap.Cleanup()                              // release buffer memory
package audio

import (
	"fmt"
	"log"
	"strings"
	"sync"
	"sync/atomic"

	"github.com/gordonklaus/portaudio"
	"github.com/shoutcape/hypr-voice-controls/internal/config"
)

const (
	// sampleRate must match whisper.SampleRate (16000 Hz).
	sampleRate = 16000
	// channels is always mono for whisper.cpp.
	channels = 1
	// framesPerBuffer is the number of frames delivered per callback
	// invocation. 512 frames at 16 kHz ≈ 32 ms.
	framesPerBuffer = 512
	// minSamples is the smallest buffer we accept as a valid recording.
	// 16000 samples = 1 second of audio at 16 kHz.
	minSamples = 16000
)

// Init initialises the PortAudio library. Must be called once at process
// startup, before any call to OpenShared or StartFromStream.
func Init() error {
	if err := portaudio.Initialize(); err != nil {
		return fmt.Errorf("portaudio init failed: %w", err)
	}
	return nil
}

// Term tears down the PortAudio library. Should be deferred immediately after
// a successful Init call.
func Term() {
	if err := portaudio.Terminate(); err != nil {
		log.Printf("audio: portaudio terminate: %v", err)
	}
}

// Capture represents an active dictation recording session. It subscribes to
// a SharedStream and accumulates samples until Stop is called.
// Obtain one via StartFromStream; release resources with Cleanup.
type Capture struct {
	stream     *SharedStream
	sub        <-chan []float32
	stopCh     chan struct{}
	done       chan struct{}
	mu         sync.Mutex
	samples    []float32
	stopped    atomic.Bool
	maxSamples int
}

// StartFromStream subscribes to stream and begins accumulating audio samples
// into an in-memory buffer. It spawns a goroutine that reads chunks from
// the shared stream until Stop is called.
//
// Returns an error if cfg.MaxRecordSecs is not positive.
func StartFromStream(stream *SharedStream, cfg *config.Config) (*Capture, error) {
	if cfg.MaxRecordSecs <= 0 {
		return nil, fmt.Errorf("MaxRecordSecs must be > 0, got %d", cfg.MaxRecordSecs)
	}

	maxSamples := cfg.MaxRecordSecs * sampleRate
	sub := stream.Subscribe()

	c := &Capture{
		stream:     stream,
		sub:        sub,
		stopCh:     make(chan struct{}),
		done:       make(chan struct{}),
		samples:    make([]float32, 0, maxSamples),
		maxSamples: maxSamples,
	}

	go c.accumulate()
	return c, nil
}

// accumulate reads chunks from the subscriber channel until Stop is called
// or the channel is closed. Runs in its own goroutine.
func (c *Capture) accumulate() {
	defer close(c.done)
	for {
		select {
		case <-c.stopCh:
			return
		case chunk, ok := <-c.sub:
			if !ok {
				return
			}
			if c.stopped.Load() {
				return
			}
			c.mu.Lock()
			remaining := c.maxSamples - len(c.samples)
			if remaining > 0 {
				n := len(chunk)
				if n > remaining {
					n = remaining
				}
				c.samples = append(c.samples, chunk[:n]...)
			}
			c.mu.Unlock()
		}
	}
}

// Stop halts accumulation, unsubscribes from the SharedStream, and returns a
// copy of the captured PCM samples (mono, 16 kHz, float32).
//
// Stop is idempotent — calling it more than once returns an error on the
// second call.
func (c *Capture) Stop() ([]float32, error) {
	if !c.stopped.CompareAndSwap(false, true) {
		return nil, fmt.Errorf("Stop called on already-stopped capture")
	}
	close(c.stopCh)

	// Unsubscribe so the accumulate goroutine sees no more chunks.
	if c.stream != nil {
		c.stream.Unsubscribe(c.sub)
	}

	// Wait for the accumulate goroutine to finish.
	<-c.done

	c.mu.Lock()
	n := len(c.samples)
	c.mu.Unlock()

	if n < minSamples {
		return nil, fmt.Errorf(
			"recording too short (%d samples, %.2fs) — did you hold the key long enough?",
			n, float64(n)/sampleRate,
		)
	}

	c.mu.Lock()
	out := make([]float32, n)
	copy(out, c.samples)
	c.mu.Unlock()
	return out, nil
}

// Cleanup releases the internal sample buffer. Safe to call multiple times
// and always safe to defer. The copy returned by Stop remains valid after
// Cleanup is called.
func (c *Capture) Cleanup() {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.samples = nil
}

// resolveDevice returns the PortAudio DeviceInfo for the requested source.
// "default" (or "") maps to portaudio.DefaultInputDevice().
// Any other string is matched by case-insensitive substring against device names.
func resolveDevice(source string) (*portaudio.DeviceInfo, error) {
	if source == "" || source == "default" {
		dev, err := portaudio.DefaultInputDevice()
		if err != nil {
			return nil, fmt.Errorf("no default input device: %w", err)
		}
		log.Printf("audio: using default input device %q", dev.Name)
		return dev, nil
	}

	devices, err := portaudio.Devices()
	if err != nil {
		return nil, fmt.Errorf("failed to enumerate audio devices: %w", err)
	}

	lowerSource := strings.ToLower(source)
	for _, dev := range devices {
		if dev.MaxInputChannels > 0 && strings.Contains(strings.ToLower(dev.Name), lowerSource) {
			log.Printf("audio: using input device %q (matched %q)", dev.Name, source)
			return dev, nil
		}
	}

	return nil, fmt.Errorf("audio input device %q not found; use \"default\" or check available devices", source)
}
