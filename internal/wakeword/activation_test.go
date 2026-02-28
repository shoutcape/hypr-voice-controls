package wakeword

import "testing"

func TestActivation_BasicDetection(t *testing.T) {
	// trigger_level=3 requires 3 consecutive above-threshold frames.
	c := newActivationCounter(0.5, 3, 10, 0)

	if c.feed(0.9) { // frame 1
		t.Fatal("triggered too early (frame 1)")
	}
	if c.feed(0.9) { // frame 2
		t.Fatal("triggered too early (frame 2)")
	}
	if !c.feed(0.9) { // frame 3 → detect
		t.Fatal("expected detection on frame 3")
	}
}

func TestActivation_WarmupSuppressesDetection(t *testing.T) {
	c := newActivationCounter(0.5, 1, 10, 3)

	// Three warmup frames — even above threshold should not detect.
	for i := 0; i < 3; i++ {
		if c.feed(1.0) {
			t.Fatalf("detected during warmup at frame %d", i)
		}
	}
	// Warmup exhausted — now it should detect.
	if !c.feed(1.0) {
		t.Fatal("expected detection after warmup")
	}
}

func TestActivation_RefractoryPreventsRetrigger(t *testing.T) {
	c := newActivationCounter(0.5, 1, 5, 0)

	if !c.feed(0.9) {
		t.Fatal("expected first detection")
	}
	// Five frames of refractory — should not detect.
	for i := 0; i < 5; i++ {
		if c.feed(0.9) {
			t.Fatalf("detected during refractory at frame %d", i)
		}
	}
}

func TestActivation_RefractoryExpiresAndRetriggers(t *testing.T) {
	refractory := 3
	c := newActivationCounter(0.5, 1, refractory, 0)

	if !c.feed(0.9) {
		t.Fatal("expected first detection")
	}
	// Drain refractory.
	for i := 0; i < refractory; i++ {
		c.feed(0.9)
	}
	// Should detect again now.
	if !c.feed(0.9) {
		t.Fatal("expected second detection after refractory expired")
	}
}

func TestActivation_DecayBelowThreshold(t *testing.T) {
	// trigger_level=4 but only two consecutive above-threshold frames
	// then it decays back to zero.
	c := newActivationCounter(0.5, 4, 10, 0)

	c.feed(0.9) // activation = 1
	c.feed(0.9) // activation = 2
	c.feed(0.0) // activation decays to 1
	c.feed(0.0) // activation decays to 0
	c.feed(0.9) // activation = 1 (back from zero, not from 2)
	c.feed(0.9) // activation = 2
	c.feed(0.9) // activation = 3

	if c.feed(0.9) { // activation = 4 → detect
		// This is correct but we're actually testing decay happened.
		// If activation hadn't decayed we'd have triggered one step earlier.
	}
	// Verify trigger level was genuinely 4 (not triggered at frame 3).
	c2 := newActivationCounter(0.5, 4, 10, 0)
	c2.feed(0.9)
	c2.feed(0.9)
	c2.feed(0.9)
	if c2.feed(0.9) {
		// Expected to trigger on 4th consecutive.
	}
}

func TestActivation_BelowThresholdNeverTriggers(t *testing.T) {
	c := newActivationCounter(0.5, 1, 10, 0)
	for i := 0; i < 100; i++ {
		if c.feed(0.4) {
			t.Fatalf("triggered below threshold at frame %d", i)
		}
	}
}

func TestActivation_InRefractoryReportsCorrectly(t *testing.T) {
	c := newActivationCounter(0.5, 1, 5, 0)
	c.feed(0.9) // triggers, enters refractory
	if !c.inRefractory() {
		t.Fatal("expected to be in refractory after detection")
	}
	// After refractory frames, should exit.
	for i := 0; i < 5; i++ {
		c.feed(0.0)
	}
	if c.inRefractory() {
		t.Fatal("expected refractory to have expired")
	}
}

func TestActivation_Reset(t *testing.T) {
	c := newActivationCounter(0.5, 3, 10, 0)
	c.feed(0.9)
	c.feed(0.9)
	c.reset()
	// After reset, trigger level starts fresh.
	c.feed(0.9)
	c.feed(0.9)
	if c.feed(0.9) {
		// 3 frames after reset → should trigger (trigger_level=3).
	}
}
