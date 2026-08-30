package bridge

import (
	"context"
	"fmt"
	"log"
	"os"
	"os/exec"
	"os/signal"
	"strings"
	"sync/atomic"
	"syscall"
	"time"

	"github.com/shoutcape/hypr-voice-controls/internal/audio"
	"github.com/shoutcape/hypr-voice-controls/internal/config"
	"github.com/shoutcape/hypr-voice-controls/internal/wakeword"
)

const silenceThreshold = 0.02

// Run starts the always-listening wakeword bridge for voxtype.
func Run(cfg *config.Config) error {
	if !cfg.WakewordEnabled {
		return fmt.Errorf("wakeword_enabled must be true to run the bridge")
	}

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	if err := audio.Init(); err != nil {
		return fmt.Errorf("failed to initialise audio: %w", err)
	}
	defer audio.Term()

	stream, err := audio.OpenShared(cfg)
	if err != nil {
		return fmt.Errorf("failed to open shared audio stream: %w", err)
	}
	defer stream.Close()

	if err := wakeword.InitORT(cfg.OnnxLibPath); err != nil {
		return fmt.Errorf("failed to initialise ONNX Runtime: %w", err)
	}
	defer wakeword.DestroyORT()

	detector, err := wakeword.New(cfg)
	if err != nil {
		return fmt.Errorf("failed to create wakeword detector: %w", err)
	}
	defer detector.Close()

	b := &Bridge{
		cfg:      cfg,
		stream:   stream,
		detector: detector,
	}

	log.Printf("wakeword bridge: listening for wakeword and triggering %q", cfg.VoxtypeBinary)
	return b.run(ctx)
}

// Bridge connects wakeword detection to voxtype start/stop commands.
type Bridge struct {
	cfg      *config.Config
	stream   *audio.SharedStream
	detector *wakeword.Detector
	active   atomic.Bool
}

func (b *Bridge) run(ctx context.Context) error {
	sub := b.stream.Subscribe()
	defer b.stream.Unsubscribe(sub)

	for {
		select {
		case <-ctx.Done():
			return nil
		case chunk, ok := <-sub:
			if !ok {
				return nil
			}

			if b.active.Load() || b.voxtypeBusy() {
				continue
			}

			if b.detector.Feed(chunk) {
				b.handleTrigger(ctx)
			}
		}
	}
}

func (b *Bridge) handleTrigger(ctx context.Context) {
	if !b.active.CompareAndSwap(false, true) {
		return
	}

	if b.voxtypeBusy() {
		b.active.Store(false)
		return
	}

	if err := b.runVoxtype(ctx, "record", "start", "--auto-submit"); err != nil {
		log.Printf("wakeword bridge: failed to start voxtype recording: %v", err)
		b.active.Store(false)
		return
	}

	log.Printf("wakeword bridge: wakeword detected, started voxtype recording")
	go b.monitorSession(ctx)
}

func (b *Bridge) monitorSession(ctx context.Context) {
	defer b.active.Store(false)

	silenceStop := time.Duration(b.cfg.WakewordSilenceStopSecs * float64(time.Second))
	hardCap := time.Duration(b.cfg.WakewordMaxRecordSecs) * time.Second
	silence := audio.NewSilenceDetector(silenceThreshold, 16000)

	sub := b.stream.Subscribe()
	defer b.stream.Unsubscribe(sub)

	timer := time.NewTimer(hardCap)
	defer timer.Stop()
	stateObserved := false

	for {
		select {
		case <-ctx.Done():
			b.cancelVoxtypeRecording()
			return
		case <-timer.C:
			if err := b.runVoxtype(ctx, "record", "stop"); err != nil {
				log.Printf("wakeword bridge: failed to stop voxtype after hard cap: %v", err)
			} else {
				log.Printf("wakeword bridge: stopped voxtype recording after hard cap")
			}
			return
		case chunk, ok := <-sub:
			if !ok {
				return
			}

			ended, observed := voxtypeSessionEnded(stateObserved, b.voxtypeBusy())
			if ended {
				return
			}
			stateObserved = observed

			silence.Feed(chunk)
			if silence.SilentFor() >= silenceStop {
				if err := b.runVoxtype(ctx, "record", "stop"); err != nil {
					log.Printf("wakeword bridge: failed to stop voxtype after silence: %v", err)
				} else {
					log.Printf("wakeword bridge: stopped voxtype recording after silence")
				}
				return
			}
		}
	}
}

// voxtypeSessionEnded waits for a busy state before treating idle as an
// externally stopped session. The state file may still say idle briefly after
// the start command returns.
func voxtypeSessionEnded(stateObserved, busy bool) (ended, observed bool) {
	if busy {
		return false, true
	}
	return stateObserved, stateObserved
}

func (b *Bridge) voxtypeBusy() bool {
	if b.cfg.VoxtypeStateFile == "" {
		return false
	}

	data, err := os.ReadFile(b.cfg.VoxtypeStateFile)
	if err != nil {
		return false
	}

	state := strings.TrimSpace(string(data))
	return state != "" && state != "idle"
}

func (b *Bridge) runVoxtype(ctx context.Context, args ...string) error {
	cmdArgs := make([]string, 0, len(args)+2)
	if b.cfg.VoxtypeConfig != "" {
		cmdArgs = append(cmdArgs, "-c", b.cfg.VoxtypeConfig)
	}
	cmdArgs = append(cmdArgs, args...)

	cmd := exec.CommandContext(ctx, b.cfg.VoxtypeBinary, cmdArgs...)
	output, err := cmd.CombinedOutput()
	if err != nil {
		if len(output) == 0 {
			return err
		}
		return fmt.Errorf("%w: %s", err, strings.TrimSpace(string(output)))
	}
	return nil
}

func (b *Bridge) cancelVoxtypeRecording() {
	shutdownCtx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()

	if err := b.runVoxtype(shutdownCtx, "record", "cancel"); err != nil {
		log.Printf("wakeword bridge: failed to cancel voxtype during shutdown: %v", err)
	}
}
