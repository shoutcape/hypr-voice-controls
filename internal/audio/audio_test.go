package audio

import (
	"strings"
	"testing"
	"time"
)

func TestCaptureStopDoesNotHangWithoutChannelClose(t *testing.T) {
	ch := make(chan []float32)
	c := &Capture{
		sub:        ch,
		stopCh:     make(chan struct{}),
		done:       make(chan struct{}),
		samples:    make([]float32, 0, sampleRate),
		maxSamples: sampleRate,
	}

	go c.accumulate()

	done := make(chan error, 1)
	go func() {
		_, err := c.Stop()
		done <- err
	}()

	select {
	case err := <-done:
		if err == nil {
			t.Fatal("expected short-recording error, got nil")
		}
		if !strings.Contains(err.Error(), "recording too short") {
			t.Fatalf("unexpected error: %v", err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("Stop() hung waiting for capture goroutine")
	}
}
