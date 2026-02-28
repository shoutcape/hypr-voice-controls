package audio

import (
	"math"
	"time"
)

// SilenceDetector tracks the energy level of incoming audio and reports how
// long the audio has been below the silence threshold.
//
// Energy is computed as the RMS (root mean square) amplitude of each chunk.
// A chunk is considered silent when its RMS falls below the configured
// threshold. The detector tracks how long contiguous silence has lasted.
//
// Usage:
//
//	sd := audio.NewSilenceDetector(0.02, 16000)
//	sd.Feed(chunk)
//	if sd.SilentFor() >= 1500*time.Millisecond {
//	    // auto-stop recording
//	}
type SilenceDetector struct {
	threshold  float64   // RMS below this value is silence
	sampleRate int       // samples per second (for timing)
	silentFrom time.Time // when the current silence period started
	silent     bool      // whether the previous chunk was silent
}

// NewSilenceDetector creates a SilenceDetector with the given RMS threshold
// and sample rate. A threshold around 0.01–0.03 works well for typical mics
// in quiet environments.
func NewSilenceDetector(threshold float64, sampleRate int) *SilenceDetector {
	return &SilenceDetector{
		threshold:  threshold,
		sampleRate: sampleRate,
	}
}

// Feed processes one chunk of audio samples and updates the silence state.
// Call Feed for every chunk received from the audio stream.
func (s *SilenceDetector) Feed(samples []float32) {
	rms := computeRMS(samples)
	isSilent := rms < s.threshold

	if !isSilent {
		// Speech detected — reset silence timer.
		s.silent = false
		s.silentFrom = time.Time{}
		return
	}

	if !s.silent {
		// Transition into silence — record the start time.
		s.silent = true
		s.silentFrom = time.Now()
	}
}

// SilentFor returns how long the audio has been continuously silent.
// Returns zero if the audio is not currently silent.
func (s *SilenceDetector) SilentFor() time.Duration {
	if !s.silent || s.silentFrom.IsZero() {
		return 0
	}
	return time.Since(s.silentFrom)
}

// IsSilent returns true if the most recently fed chunk was below the threshold.
func (s *SilenceDetector) IsSilent() bool {
	return s.silent
}

// Reset clears the silence state, as if no audio had been fed.
func (s *SilenceDetector) Reset() {
	s.silent = false
	s.silentFrom = time.Time{}
}

// computeRMS returns the root mean square amplitude of samples.
func computeRMS(samples []float32) float64 {
	if len(samples) == 0 {
		return 0
	}
	var sum float64
	for _, s := range samples {
		sum += float64(s) * float64(s)
	}
	return math.Sqrt(sum / float64(len(samples)))
}
