package bridge

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/shoutcape/hypr-voice-controls/internal/config"
)

func TestVoxtypeSessionEndedWaitsForBusyState(t *testing.T) {
	ended, observed := voxtypeSessionEnded(false, false)
	if ended || observed {
		t.Fatalf("idle before recording should not end the session: ended=%v observed=%v", ended, observed)
	}

	ended, observed = voxtypeSessionEnded(observed, true)
	if ended || !observed {
		t.Fatalf("recording state should be observed without ending: ended=%v observed=%v", ended, observed)
	}

	ended, observed = voxtypeSessionEnded(observed, false)
	if !ended || !observed {
		t.Fatalf("idle after recording should end the session: ended=%v observed=%v", ended, observed)
	}
}

func TestVoxtypeBusyReadsStateFile(t *testing.T) {
	statePath := filepath.Join(t.TempDir(), "state")
	b := &Bridge{cfg: &config.Config{VoxtypeStateFile: statePath}}

	for _, test := range []struct {
		name  string
		state string
		busy  bool
	}{
		{name: "idle", state: "idle\n", busy: false},
		{name: "recording", state: "recording\n", busy: true},
		{name: "transcribing", state: "transcribing\n", busy: true},
	} {
		t.Run(test.name, func(t *testing.T) {
			if err := os.WriteFile(statePath, []byte(test.state), 0o600); err != nil {
				t.Fatal(err)
			}
			if got := b.voxtypeBusy(); got != test.busy {
				t.Errorf("voxtypeBusy() = %v, want %v", got, test.busy)
			}
		})
	}

	if err := os.Remove(statePath); err != nil {
		t.Fatal(err)
	}
	if b.voxtypeBusy() {
		t.Error("missing state file should be treated as idle")
	}
}
