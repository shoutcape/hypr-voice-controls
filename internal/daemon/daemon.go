// Package daemon implements the long-running voice-controls daemon.
//
// Lifecycle:
//  1. Initialise PortAudio (audio.Init).
//  2. Open the shared audio stream (always-on mic).
//  3. Load whisper model into memory (kept warm between activations).
//  4. If wakeword is enabled: initialise ONNX Runtime, load wakeword detector,
//     start the wakeword listener goroutine.
//  5. Create Unix domain socket at cfg.SocketPath with mode 0600.
//  6. Print "READY\n" to stdout — the client waits for this before connecting.
//  7. Accept connections; handle each in its own goroutine.
//  8. On SIGTERM/SIGINT: stop any active session, close socket, free model,
//     close shared stream, teardown PortAudio (audio.Term).
//
// Session state machine (per daemon instance, not per connection):
//
//	idle ──dictate-start──▶ recording ──dictate-stop──▶ transcribing ──▶ idle
//	idle ──wakeword──▶ recording ──silence/timeout──▶ transcribing ──▶ idle
package daemon

import (
	"context"
	"fmt"
	"log"
	"net"
	"os"
	"os/signal"
	"sync"
	"syscall"
	"time"

	"github.com/shoutcape/hypr-voice-controls/internal/audio"
	"github.com/shoutcape/hypr-voice-controls/internal/config"
	"github.com/shoutcape/hypr-voice-controls/internal/ipc"
	"github.com/shoutcape/hypr-voice-controls/internal/notify"
	"github.com/shoutcape/hypr-voice-controls/internal/output"
	"github.com/shoutcape/hypr-voice-controls/internal/stt"
	"github.com/shoutcape/hypr-voice-controls/internal/wakeword"
)

// silenceThreshold is the RMS energy level below which audio is considered
// silent for the purposes of auto-stopping wakeword-triggered recordings.
const silenceThreshold = 0.02

// session holds the state for one active recording session.
type session struct {
	capture *audio.Capture
}

// daemon is the internal state holder. All mutable state is protected by mu.
type daemon struct {
	cfg     *config.Config
	engine  *stt.Engine
	stream  *audio.SharedStream
	wwWG    sync.WaitGroup
	mu      sync.Mutex
	current *session // nil when idle
}

// Run is the entry point called by main. It blocks until the daemon exits.
func Run(cfg *config.Config) error {
	// ── Initialise PortAudio ─────────────────────────────────────
	if err := audio.Init(); err != nil {
		return fmt.Errorf("failed to initialise audio: %w", err)
	}
	defer audio.Term()

	// ── Open shared audio stream (always-on mic) ─────────────────
	stream, err := audio.OpenShared(cfg)
	if err != nil {
		return fmt.Errorf("failed to open shared audio stream: %w", err)
	}
	defer stream.Close()

	// ── Load whisper model ───────────────────────────────────────
	log.Printf("loading model: %s", cfg.ModelPath)
	engine, err := stt.NewEngine(cfg)
	if err != nil {
		return fmt.Errorf("failed to load STT model: %w", err)
	}
	defer engine.Close()
	log.Printf("model loaded")

	d := &daemon{cfg: cfg, engine: engine, stream: stream}

	// ── Wakeword setup ───────────────────────────────────────────
	if cfg.WakewordEnabled {
		var wwCancel context.CancelFunc
		var wwStarted bool
		var detector *wakeword.Detector
		var ortReady bool

		if err := wakeword.InitORT(cfg.OnnxLibPath); err != nil {
			// Non-fatal: log and continue in PTT-only mode.
			log.Printf("wakeword: ONNX Runtime init failed: %v — disabling wakeword", err)
		} else {
			ortReady = true
			det, werr := wakeword.New(cfg)
			if werr != nil {
				log.Printf("wakeword: detector init failed: %v — disabling wakeword", werr)
			} else {
				detector = det
				wwCtx, cancel := context.WithCancel(context.Background())
				wwCancel = cancel
				d.wwWG.Add(1)
				go d.runWakewordLoop(wwCtx, detector)
				wwStarted = true
				log.Printf("wakeword: listening for wakeword")
			}
		}

		defer func() {
			if wwCancel != nil {
				wwCancel()
			}
			if wwStarted {
				d.wwWG.Wait()
			}
			if detector != nil {
				detector.Close()
			}
			if ortReady {
				wakeword.DestroyORT()
			}
		}()
	}

	// ── Socket setup ─────────────────────────────────────────────
	if err := removeStaleSocket(cfg.SocketPath); err != nil {
		return err
	}

	oldUmask := syscall.Umask(0o177)
	ln, err := net.Listen("unix", cfg.SocketPath)
	syscall.Umask(oldUmask)
	if err != nil {
		return fmt.Errorf("failed to listen on %s: %w", cfg.SocketPath, err)
	}
	defer func() {
		ln.Close()
		os.Remove(cfg.SocketPath)
	}()

	// ── Signal handling ──────────────────────────────────────────
	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, syscall.SIGTERM, syscall.SIGINT)

	go func() {
		<-sigCh
		log.Printf("shutting down")
		d.cancelSession()
		ln.Close()
	}()

	// ── Announce ready ───────────────────────────────────────────
	fmt.Println(ipc.ReadyMsg)

	// ── Accept loop ──────────────────────────────────────────────
	log.Printf("listening on %s", cfg.SocketPath)
	for {
		conn, err := ln.Accept()
		if err != nil {
			return nil
		}
		go d.handleConn(conn)
	}
}

// handleConn reads one request, dispatches it, writes one response, and closes.
func (d *daemon) handleConn(conn net.Conn) {
	defer conn.Close()

	req, err := ipc.ReadRequest(conn)
	if err != nil {
		log.Printf("read request error: %v", err)
		return
	}

	log.Printf("action: %s", req.Action)

	var resp *ipc.Response
	switch req.Action {
	case "dictate-start":
		resp = d.handleStart()
	case "dictate-stop":
		resp = d.handleStop()
	case "ping":
		resp = ipc.OK("pong")
	default:
		resp = ipc.Err(fmt.Sprintf("unknown action: %q", req.Action))
		resp.RC = 2
	}

	if err := ipc.WriteResponse(conn, resp); err != nil {
		log.Printf("write response error: %v", err)
	}
}

// handleStart begins a new push-to-talk recording session, preempting any
// existing session (including wakeword-triggered ones).
func (d *daemon) handleStart() *ipc.Response {
	d.mu.Lock()
	defer d.mu.Unlock()

	if d.current != nil {
		log.Printf("preempting existing session")
		_, _ = d.current.capture.Stop()
		d.current.capture.Cleanup()
		d.current = nil
	}

	cap, err := audio.StartFromStream(d.stream, d.cfg)
	if err != nil {
		return ipc.Err(fmt.Sprintf("failed to start recording: %v", err))
	}

	d.current = &session{capture: cap}
	log.Printf("recording started (PTT)")
	if err := notify.Send(notify.Info, "Recording..."); err != nil {
		log.Printf("notify error: %v", err)
	}
	return ipc.OK("recording started")
}

// handleStop stops the current session, transcribes the audio, and returns
// the transcribed text in the response Msg field.
func (d *daemon) handleStop() *ipc.Response {
	d.mu.Lock()
	sess := d.current
	d.current = nil
	d.mu.Unlock()

	if sess == nil {
		return ipc.Err("no active recording session")
	}

	return d.transcribeAndPaste(sess)
}

// transcribeAndPaste stops a session, transcribes its audio, pastes the
// result, and sends a notification. Returns an IPC response.
func (d *daemon) transcribeAndPaste(sess *session) *ipc.Response {
	defer sess.capture.Cleanup()

	samples, err := sess.capture.Stop()
	if err != nil {
		return ipc.Err(fmt.Sprintf("capture failed: %v", err))
	}

	log.Printf("transcribing %d samples (%.1fs)", len(samples), float64(len(samples))/16000)
	result, err := d.engine.Transcribe(samples)
	if err != nil {
		return ipc.Err(fmt.Sprintf("transcription failed: %v", err))
	}

	log.Printf("transcribed: %q", result.Text)
	cleanText := output.Sanitize(result.Text)

	if cleanText == "" {
		log.Printf("no speech detected")
		if err := notify.Send(notify.Info, "No speech detected"); err != nil {
			log.Printf("notify error: %v", err)
		}
		return ipc.OK("")
	}

	if err := output.Paste(d.cfg, cleanText); err != nil {
		log.Printf("paste error: %v", err)
		if nerr := notify.Send(notify.Error, fmt.Sprintf("Paste failed: %v", err)); nerr != nil {
			log.Printf("notify error: %v", nerr)
		}
		return ipc.Err(fmt.Sprintf("paste failed: %v", err))
	}
	log.Printf("paste completed")

	if err := notify.Send(notify.Success, cleanText); err != nil {
		log.Printf("notify error: %v", err)
	}
	return ipc.OK(cleanText)
}

// runWakewordLoop runs in a goroutine and feeds audio from the shared stream
// to the wakeword detector. On detection, it starts a wakeword-triggered
// recording session with auto-stop.
func (d *daemon) runWakewordLoop(ctx context.Context, detector *wakeword.Detector) {
	defer d.wwWG.Done()
	sub := d.stream.Subscribe()
	defer d.stream.Unsubscribe(sub)

	for {
		select {
		case <-ctx.Done():
			return
		case chunk, ok := <-sub:
			if !ok {
				return
			}
			// Skip wakeword inference while a dictation session is active.
			d.mu.Lock()
			active := d.current != nil
			d.mu.Unlock()
			if active {
				continue
			}

			if detector.Feed(chunk) {
				log.Printf("wakeword: detected — starting auto-record session")
				d.handleWakewordTrigger()
			}
		}
	}
}

// handleWakewordTrigger starts a wakeword-triggered recording session and
// spawns a monitor goroutine that auto-stops it on silence or hard timeout.
func (d *daemon) handleWakewordTrigger() {
	d.mu.Lock()

	// If PTT started concurrently, let PTT win.
	if d.current != nil {
		log.Printf("wakeword: PTT session active — ignoring trigger")
		d.mu.Unlock()
		return
	}

	cap, err := audio.StartFromStream(d.stream, &config.Config{
		MaxRecordSecs: d.cfg.WakewordMaxRecordSecs,
	})
	if err != nil {
		d.mu.Unlock()
		log.Printf("wakeword: failed to start capture: %v", err)
		return
	}

	sess := &session{capture: cap}
	d.current = sess
	d.mu.Unlock()

	if err := notify.Send(notify.Info, "Listening..."); err != nil {
		log.Printf("notify error: %v", err)
	}

	// Monitor for silence or hard timeout in a separate goroutine so we don't
	// block the wakeword listener loop.
	go d.monitorWakewordSession(sess)
}

// monitorWakewordSession watches a wakeword-triggered session and stops it
// when silence is detected for the configured duration or when the hard cap
// is reached. After stopping, it hands off to transcribeAndPaste.
func (d *daemon) monitorWakewordSession(sess *session) {
	silenceStop := time.Duration(d.cfg.WakewordSilenceStopSecs * float64(time.Second))
	hardCap := time.Duration(d.cfg.WakewordMaxRecordSecs) * time.Second

	sd := audio.NewSilenceDetector(silenceThreshold, 16000)
	sub := d.stream.Subscribe()
	defer d.stream.Unsubscribe(sub)

	deadline := time.Now().Add(hardCap)

	for {
		select {
		case chunk, ok := <-sub:
			if !ok {
				// Stream closed (daemon shutting down).
				d.mu.Lock()
				if d.current == sess {
					d.current = nil
				}
				d.mu.Unlock()
				sess.capture.Cleanup()
				return
			}

			sd.Feed(chunk)

			// Check for silence auto-stop.
			if sd.SilentFor() >= silenceStop {
				log.Printf("wakeword: silence detected — stopping recording")
				d.finishWakewordSession(sess, false)
				return
			}

		case <-time.After(time.Until(deadline)):
			// Hard cap reached.
			log.Printf("wakeword: hard cap reached — stopping recording")
			if err := notify.Send(notify.Info, "Max recording time reached"); err != nil {
				log.Printf("notify error: %v", err)
			}
			d.finishWakewordSession(sess, true)
			return
		}

		// Check PTT preemption: if d.current changed to nil or another session,
		// the PTT handler already took over — just exit.
		d.mu.Lock()
		current := d.current
		d.mu.Unlock()
		if current != sess {
			return
		}
	}
}

// finishWakewordSession clears the current session and hands it to
// transcribeAndPaste. cappedByTimeout indicates whether the hard cap fired.
func (d *daemon) finishWakewordSession(sess *session, _ bool) {
	d.mu.Lock()
	if d.current == sess {
		d.current = nil
	}
	d.mu.Unlock()

	d.transcribeAndPaste(sess)
}

// cancelSession stops and cleans up any active session without transcribing.
// Safe to call from signal handlers or shutdown paths.
func (d *daemon) cancelSession() {
	d.mu.Lock()
	defer d.mu.Unlock()
	if d.current != nil {
		_, _ = d.current.capture.Stop()
		d.current.capture.Cleanup()
		d.current = nil
	}
}

// removeStaleSocket removes the socket file if it exists but nothing is
// listening on it (i.e. a previous daemon crashed and left it behind).
func removeStaleSocket(path string) error {
	if _, err := os.Stat(path); os.IsNotExist(err) {
		return nil
	}

	conn, err := net.Dial("unix", path)
	if err == nil {
		conn.Close()
		return fmt.Errorf("another daemon is already running (socket: %s)", path)
	}

	log.Printf("removing stale socket: %s", path)
	return os.Remove(path)
}
