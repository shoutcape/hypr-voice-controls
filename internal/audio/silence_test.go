package audio

import (
	"testing"
	"time"
)

func TestSilenceDetector_DetectsSilence(t *testing.T) {
	sd := NewSilenceDetector(0.02, 16000)

	// Feed silent audio (all zeros).
	silent := make([]float32, 512)
	sd.Feed(silent)

	if !sd.IsSilent() {
		t.Fatal("expected silence after feeding zeros")
	}
	if sd.SilentFor() == 0 {
		t.Fatal("expected non-zero silence duration")
	}
}

func TestSilenceDetector_DetectsSpeech(t *testing.T) {
	sd := NewSilenceDetector(0.02, 16000)

	// Feed loud audio (amplitude 0.5 → RMS = 0.5 >> threshold 0.02).
	loud := make([]float32, 512)
	for i := range loud {
		loud[i] = 0.5
	}
	sd.Feed(loud)

	if sd.IsSilent() {
		t.Fatal("expected speech (non-silence) after feeding loud audio")
	}
	if sd.SilentFor() != 0 {
		t.Fatal("SilentFor should be zero during speech")
	}
}

func TestSilenceDetector_TransitionSpeechToSilence(t *testing.T) {
	sd := NewSilenceDetector(0.02, 16000)

	loud := make([]float32, 512)
	for i := range loud {
		loud[i] = 0.5
	}
	silent := make([]float32, 512)

	sd.Feed(loud)   // speech
	sd.Feed(silent) // silence starts

	if !sd.IsSilent() {
		t.Fatal("expected silence after speech→silence transition")
	}
	if sd.SilentFor() == 0 {
		t.Fatal("expected non-zero SilentFor after transition")
	}
}

func TestSilenceDetector_SilentForGrowsOverTime(t *testing.T) {
	sd := NewSilenceDetector(0.02, 16000)
	silent := make([]float32, 512)

	sd.Feed(silent)
	before := sd.SilentFor()
	time.Sleep(50 * time.Millisecond)
	after := sd.SilentFor()

	if after <= before {
		t.Fatal("SilentFor should grow over time")
	}
}

func TestSilenceDetector_Reset(t *testing.T) {
	sd := NewSilenceDetector(0.02, 16000)
	silent := make([]float32, 512)
	sd.Feed(silent)
	sd.Reset()

	if sd.IsSilent() {
		t.Fatal("expected not silent after reset")
	}
	if sd.SilentFor() != 0 {
		t.Fatal("SilentFor should be zero after reset")
	}
}

func TestComputeRMS(t *testing.T) {
	// RMS of [1, 1, 1, 1] = 1.0
	ones := []float32{1, 1, 1, 1}
	if got := computeRMS(ones); got != 1.0 {
		t.Errorf("expected 1.0, got %f", got)
	}

	// RMS of empty slice = 0
	if got := computeRMS(nil); got != 0 {
		t.Errorf("expected 0 for nil, got %f", got)
	}

	// RMS of zeros = 0
	zeros := make([]float32, 512)
	if got := computeRMS(zeros); got != 0 {
		t.Errorf("expected 0 for zero slice, got %f", got)
	}
}
