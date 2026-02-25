// Package audio manages audio capture via PortAudio.
// It records from the configured input device into an in-memory []float32
// buffer (mono, 16 kHz) that whisper.cpp can consume directly.
//
// PortAudio requires a one-time global initialisation before any stream is
// opened and a matching teardown when the process exits:
//
//	if err := audio.Init(); err != nil { ... }
//	defer audio.Term()
//
// Per-capture lifecycle:
//
//	cap, err := audio.Start(cfg)       // opens stream, starts callback
//	samples, err := cap.Stop()         // stops stream, returns []float32 copy
//	defer cap.Cleanup()                // releases internal buffer memory
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
	// invocation. 512 frames at 16 kHz ≈ 32 ms — low enough for
	// responsive VAD when we add it later, not so small that overhead
	// dominates.
	framesPerBuffer = 512
	// minSamples is the smallest buffer we accept as a valid recording.
	// 16000 samples = 1 second of audio at 16 kHz.
	minSamples = 16000
)

// Init initialises the PortAudio library. Must be called once at process
// startup, before any call to Start.
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

// Capture represents an active audio recording session.
// Obtain one via Start; release resources with Cleanup.
type Capture struct {
	stream     *portaudio.Stream
	mu         sync.Mutex  // protects samples slice header for Cleanup
	samples    []float32   // written only by the callback goroutine
	stopped    atomic.Bool // guards against double-Stop and signals callback to exit
	maxSamples int         // hard cap to enforce MaxRecordSecs
}

// Start opens a PortAudio input stream and begins collecting audio samples
// via a real-time callback. It returns as soon as the stream is running;
// recording continues in the background until Stop is called.
//
// cfg.AudioSource selects the input device:
//   - "default" (or empty string) uses the system default input device.
//   - Any other value is matched by substring against device names returned
//     by portaudio.Devices(); the first match is used.
//
// Returns an error if cfg.MaxRecordSecs is not positive.
func Start(cfg *config.Config) (*Capture, error) {
	if cfg.MaxRecordSecs <= 0 {
		return nil, fmt.Errorf("MaxRecordSecs must be > 0, got %d", cfg.MaxRecordSecs)
	}

	device, err := resolveDevice(cfg.AudioSource)
	if err != nil {
		return nil, err
	}

	maxSamples := cfg.MaxRecordSecs * sampleRate

	cap := &Capture{
		samples:    make([]float32, 0, maxSamples),
		maxSamples: maxSamples,
	}

	params := portaudio.StreamParameters{
		Input: portaudio.StreamDeviceParameters{
			Device:   device,
			Channels: channels,
			Latency:  device.DefaultLowInputLatency,
		},
		SampleRate:      sampleRate,
		FramesPerBuffer: framesPerBuffer,
	}

	stream, err := portaudio.OpenStream(params, cap.processAudio)
	if err != nil {
		return nil, fmt.Errorf("failed to open audio stream: %w", err)
	}
	cap.stream = stream

	if err := stream.Start(); err != nil {
		stream.Close() //nolint:errcheck
		return nil, fmt.Errorf("failed to start audio stream: %w", err)
	}

	return cap, nil
}

// processAudio is the PortAudio callback. It runs on the PortAudio real-time
// thread and must not block.
//
// stopped is checked atomically so we never take a mutex on the RT thread.
// samples is only written here (single writer), so no lock is needed for the
// append itself. The mutex in Cleanup only protects the slice header nil-out,
// which cannot race with the callback because Stop() calls stream.Stop()
// first — which waits for any in-flight callback to return before proceeding.
func (c *Capture) processAudio(in []float32) {
	if c.stopped.Load() {
		return
	}
	remaining := c.maxSamples - len(c.samples)
	if remaining <= 0 {
		return
	}
	if len(in) <= remaining {
		c.samples = append(c.samples, in...)
	} else {
		c.samples = append(c.samples, in[:remaining]...)
	}
}

// Stop halts the audio stream and returns a copy of the captured PCM samples
// (mono, 16 kHz, float32). Returning a copy means the caller owns the slice
// independently of any subsequent Cleanup call.
//
// Stop is idempotent — calling it more than once returns an error on the
// second call without touching the stream.
func (c *Capture) Stop() ([]float32, error) {
	if !c.stopped.CompareAndSwap(false, true) {
		return nil, fmt.Errorf("Stop called on already-stopped capture")
	}

	// stream.Stop() waits for any in-flight callback invocation to return
	// before it returns, so it is safe to read c.samples immediately after.
	if err := c.stream.Stop(); err != nil {
		log.Printf("audio: stream.Stop: %v (continuing to close)", err)
	}
	if err := c.stream.Close(); err != nil {
		return nil, fmt.Errorf("failed to close audio stream: %w", err)
	}

	n := len(c.samples)
	if n < minSamples {
		return nil, fmt.Errorf(
			"recording too short (%d samples, %.2fs) — did you hold the key long enough?",
			n, float64(n)/sampleRate,
		)
	}

	// Return a copy so the caller's slice is independent of Cleanup.
	out := make([]float32, n)
	copy(out, c.samples)
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
