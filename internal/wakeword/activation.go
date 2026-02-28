package wakeword

// activationCounter implements the rhasspy-style integer trigger counter used
// for robust wakeword activation with built-in debounce.
//
// State machine:
//
//	Idle (activation == 0)
//	  score > threshold  → activation++
//	    activation >= triggerLevel → DETECT, activation = -refractory
//	  score <= threshold → activation decays toward 0 (max 1 step per frame)
//
//	Refractory (activation < 0)
//	  activation increments toward 0 each frame regardless of score
//	  while activation < 0, detection is suppressed
//
// The initial warmup period suppresses detections for the first N frames to
// avoid false triggers during model/buffer initialisation.
type activationCounter struct {
	threshold    float64
	triggerLevel int
	refractory   int

	activation int
	warmup     int // remaining frames to suppress
}

func newActivationCounter(threshold float64, triggerLevel, refractory, warmupFrames int) *activationCounter {
	return &activationCounter{
		threshold:    threshold,
		triggerLevel: triggerLevel,
		refractory:   refractory,
		warmup:       warmupFrames,
	}
}

// feed processes one prediction score and returns true when the wakeword is
// detected (activation counter reaches triggerLevel for the first time after
// the refractory period expires).
func (a *activationCounter) feed(score float64) bool {
	// Burn down warmup period — suppress all detections.
	if a.warmup > 0 {
		a.warmup--
		return false
	}

	if score > a.threshold {
		a.activation++
		if a.activation >= a.triggerLevel {
			// Detection — enter refractory period.
			a.activation = -a.refractory
			return true
		}
	} else {
		// Decay toward zero from either direction.
		if a.activation > 0 {
			a.activation--
		} else if a.activation < 0 {
			a.activation++
		}
	}
	return false
}

// inRefractory returns true when the counter is in its post-detection
// suppression window (activation < 0).
func (a *activationCounter) inRefractory() bool {
	return a.activation < 0
}

// reset clears the counter state (useful on explicit stop/reset).
func (a *activationCounter) reset() {
	a.activation = 0
}
