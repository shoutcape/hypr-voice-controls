// Package wakeword provides always-listening wakeword detection using the
// openWakeWord three-stage ONNX pipeline:
//
//	audio → melspectrogram → speech embeddings → wakeword classifier
//
// All inference runs locally via the ONNX Runtime shared library
// (libonnxruntime.so). No network access is required at runtime.
//
// Typical usage:
//
//	if err := wakeword.InitORT(cfg.OnnxLibPath); err != nil { ... }
//	defer wakeword.DestroyORT()
//
//	d, err := wakeword.New(cfg)
//	if err != nil { ... }
//	defer d.Close()
//
//	for chunk := range audioStream {
//	    if d.Feed(chunk) {
//	        // wakeword detected — start recording
//	    }
//	}
package wakeword

import (
	"fmt"
	"log"
	"sync"

	"github.com/shoutcape/hypr-voice-controls/internal/config"
)

// warmupFrames is the number of inference frames to suppress at startup.
// Prevents false triggers while the pipeline buffers fill.
const warmupFrames = 5

// Detector listens for the configured wakeword in a stream of audio chunks.
// It is safe to call Feed from a single goroutine; do not share across
// goroutines without external synchronisation.
type Detector struct {
	mu      sync.RWMutex
	closed  bool
	pipe    *pipeline
	counter *activationCounter
}

// New creates a Detector by loading the three openWakeWord ONNX models
// configured in cfg. InitORT must have been called successfully before New.
//
// Model paths:
//   - cfg.WakewordMelModel — melspectrogram.onnx
//   - cfg.WakewordEmbModel — embedding_model.onnx
//   - cfg.WakewordModel    — <wakeword>.onnx (your trained classifier)
func New(cfg *config.Config) (*Detector, error) {
	if !cfg.WakewordEnabled {
		return nil, fmt.Errorf("wakeword is not enabled in config")
	}

	pipe, err := newPipeline(cfg.WakewordMelModel, cfg.WakewordEmbModel, cfg.WakewordModel)
	if err != nil {
		return nil, fmt.Errorf("wakeword pipeline init: %w", err)
	}

	counter := newActivationCounter(
		cfg.WakewordThreshold,
		cfg.WakewordTriggerLevel,
		cfg.WakewordRefractory,
		warmupFrames,
	)

	log.Printf("wakeword: detector ready (threshold=%.2f trigger=%d refractory=%d)",
		cfg.WakewordThreshold, cfg.WakewordTriggerLevel, cfg.WakewordRefractory)

	return &Detector{pipe: pipe, counter: counter}, nil
}

// Feed processes a slice of mono float32 audio samples (16 kHz, any length)
// and returns true if the wakeword was detected.
//
// Audio must be raw float32 cast from int16 PCM — do NOT normalise to [-1,1].
// (This matches the format produced by PortAudio with float32 sample format.)
//
// Feed returns true at most once per refractory period even if the wakeword
// is heard continuously. A return value of true means: start recording now.
func (d *Detector) Feed(samples []float32) bool {
	d.mu.RLock()
	if d.closed || d.pipe == nil {
		d.mu.RUnlock()
		return false
	}
	pipe := d.pipe
	counter := d.counter
	d.mu.RUnlock()

	scores, err := pipe.feed(samples)
	if err != nil {
		log.Printf("wakeword: inference error: %v", err)
		return false
	}

	for _, score := range scores {
		if counter.feed(score) {
			log.Printf("wakeword: detected (score=%.3f)", score)
			return true
		}
	}
	return false
}

// Close releases all ONNX sessions and frees model memory.
// The Detector must not be used after Close returns.
func (d *Detector) Close() {
	d.mu.Lock()
	defer d.mu.Unlock()
	if d.closed {
		return
	}
	d.closed = true
	if d.pipe != nil {
		d.pipe.close()
		d.pipe = nil
	}
}
