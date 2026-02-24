// Package stt wraps the whisper.cpp Go bindings to provide speech-to-text.
// It loads a GGML model once and keeps it resident in memory for fast repeated
// transcription (no cold-start per activation).
package stt

import (
	"fmt"
	"io"
	"os"
	"runtime"
	"strings"

	whisper "github.com/ggerganov/whisper.cpp/bindings/go/pkg/whisper"
	wav "github.com/go-audio/wav"

	"github.com/shoutcape/hypr-voice-controls/internal/config"
)

// Engine holds a loaded whisper model and transcribes audio on demand.
type Engine struct {
	model whisper.Model
	cfg   *config.Config
}

// Result is the output of a transcription.
type Result struct {
	Text string
}

// NewEngine loads the whisper model at cfg.ModelPath and returns a ready Engine.
// The model stays in memory until Close is called — this is the "hot model"
// that eliminates cold-start latency between activations.
func NewEngine(cfg *config.Config) (*Engine, error) {
	if _, err := os.Stat(cfg.ModelPath); err != nil {
		return nil, fmt.Errorf("model file not found at %q: %w\n"+
			"  Run: make model   to download the default model", cfg.ModelPath, err)
	}

	model, err := whisper.New(cfg.ModelPath)
	if err != nil {
		return nil, fmt.Errorf("failed to load whisper model: %w", err)
	}
	if model.IsMultilingual() {
		_ = model.Close()
		return nil, fmt.Errorf("english-only mode requires a .en model (got multilingual model at %q)", cfg.ModelPath)
	}

	return &Engine{model: model, cfg: cfg}, nil
}

// TranscribeFile reads a mono 16 kHz 16-bit PCM WAV file and returns the
// transcribed text. The WAV must match whisper's expected sample rate
// (whisper.SampleRate = 16000 Hz) and be mono.
func (e *Engine) TranscribeFile(wavPath string) (*Result, error) {
	samples, err := loadWAV(wavPath)
	if err != nil {
		return nil, fmt.Errorf("failed to load WAV %q: %w", wavPath, err)
	}
	return e.Transcribe(samples)
}

// Transcribe processes mono 16 kHz float32 audio samples and returns the text.
func (e *Engine) Transcribe(samples []float32) (*Result, error) {
	ctx, err := e.model.NewContext()
	if err != nil {
		return nil, fmt.Errorf("failed to create whisper context: %w", err)
	}

	// Use all available CPUs for inference.
	ctx.SetThreads(uint(runtime.NumCPU()))

	ctx.ResetTimings()

	if err := ctx.Process(samples, nil, nil, nil); err != nil {
		return nil, fmt.Errorf("transcription failed: %w", err)
	}

	var sb strings.Builder
	for {
		seg, err := ctx.NextSegment()
		if err == io.EOF {
			break
		}
		if err != nil {
			return nil, fmt.Errorf("error reading segment: %w", err)
		}
		sb.WriteString(seg.Text)
	}

	text := strings.TrimSpace(sb.String())
	// whisper emits "[BLANK_AUDIO]" when it detects only silence.
	// Normalise to empty string — callers check for empty to detect no speech.
	if text == "[BLANK_AUDIO]" {
		text = ""
	}
	return &Result{Text: text}, nil
}

// Close releases the whisper model and frees its memory.
func (e *Engine) Close() error {
	if e.model != nil {
		return e.model.Close()
	}
	return nil
}

// loadWAV reads a WAV file and returns float32 audio samples.
// The file must be mono and sampled at whisper.SampleRate (16000 Hz).
func loadWAV(path string) ([]float32, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()

	dec := wav.NewDecoder(f)
	buf, err := dec.FullPCMBuffer()
	if err != nil {
		return nil, fmt.Errorf("failed to decode WAV: %w", err)
	}
	if dec.SampleRate != whisper.SampleRate {
		return nil, fmt.Errorf("unexpected sample rate %d Hz (want %d Hz)", dec.SampleRate, whisper.SampleRate)
	}
	if dec.NumChans != 1 {
		return nil, fmt.Errorf("unexpected channel count %d (want mono)", dec.NumChans)
	}

	return buf.AsFloat32Buffer().Data, nil
}
