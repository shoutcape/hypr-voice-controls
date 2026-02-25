// Package daemon implements the long-running voice-controls daemon.
//
// Lifecycle:
//  1. Initialise PortAudio (audio.Init).
//  2. Load whisper model into memory (kept warm between activations).
//  3. Create Unix domain socket at cfg.SocketPath with mode 0600.
//  4. Print "READY\n" to stdout — the client waits for this before connecting.
//  5. Accept connections; handle each in its own goroutine.
//  6. On SIGTERM/SIGINT: stop any active session, close socket, free model,
//     teardown PortAudio (audio.Term).
//
// Session state machine (per daemon instance, not per connection):
//
//	idle ──dictate-start──▶ recording ──dictate-stop──▶ transcribing ──▶ idle
//	        (concurrent start preempts previous session)
package daemon

import (
	"fmt"
	"log"
	"net"
	"os"
	"os/signal"
	"sync"
	"syscall"

	"github.com/shoutcape/hypr-voice-controls/internal/audio"
	"github.com/shoutcape/hypr-voice-controls/internal/config"
	"github.com/shoutcape/hypr-voice-controls/internal/ipc"
	"github.com/shoutcape/hypr-voice-controls/internal/notify"
	"github.com/shoutcape/hypr-voice-controls/internal/output"
	"github.com/shoutcape/hypr-voice-controls/internal/stt"
)

// session holds the state for one active recording session.
type session struct {
	capture *audio.Capture
}

// daemon is the internal state holder. All mutable state is protected by mu.
type daemon struct {
	cfg     *config.Config
	engine  *stt.Engine
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

	// ── Load model ───────────────────────────────────────────────
	log.Printf("loading model: %s", cfg.ModelPath)
	engine, err := stt.NewEngine(cfg)
	if err != nil {
		return fmt.Errorf("failed to load STT model: %w", err)
	}
	defer engine.Close()
	log.Printf("model loaded")

	d := &daemon{cfg: cfg, engine: engine}

	// ── Socket setup ─────────────────────────────────────────────
	// Remove stale socket from a previous (crashed) run.
	if err := removeStaleSocket(cfg.SocketPath); err != nil {
		return err
	}

	// Create the socket with restrictive permissions (owner-only).
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
		ln.Close() // unblocks Accept
	}()

	// ── Announce ready ───────────────────────────────────────────
	// The client waits for "READY\n" on stdout before sending the first
	// request. Print it after the socket is open so there's no race.
	fmt.Println(ipc.ReadyMsg)

	// ── Accept loop ──────────────────────────────────────────────
	log.Printf("listening on %s", cfg.SocketPath)
	for {
		conn, err := ln.Accept()
		if err != nil {
			// A closed listener means we were asked to shut down.
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

// handleStart begins a new recording session, preempting any existing one.
func (d *daemon) handleStart() *ipc.Response {
	d.mu.Lock()
	defer d.mu.Unlock()

	// Preempt any existing session.
	// Stop() calls stream.Stop() which blocks for up to one callback period
	// (~32 ms) while holding d.mu. This is acceptable at the current buffer
	// size; if framesPerBuffer were ever increased significantly, consider
	// releasing d.mu before calling Stop().
	if d.current != nil {
		log.Printf("preempting existing session")
		_, _ = d.current.capture.Stop() // best-effort; discard samples
		d.current.capture.Cleanup()
		d.current = nil
	}

	cap, err := audio.Start(d.cfg)
	if err != nil {
		return ipc.Err(fmt.Sprintf("failed to start recording: %v", err))
	}

	d.current = &session{capture: cap}
	log.Printf("recording started")
	// Non-fatal: notify the user that recording is active.
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

	// Paste into the focused application.
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
	} else {
		log.Printf("success notification sent")
	}
	return ipc.OK(cleanText)
}

// cancelSession stops and cleans up any active session without transcribing.
// Safe to call from signal handlers or shutdown paths.
func (d *daemon) cancelSession() {
	d.mu.Lock()
	defer d.mu.Unlock()
	if d.current != nil {
		_, _ = d.current.capture.Stop() // best-effort; discard samples
		d.current.capture.Cleanup()
		d.current = nil
	}
}

// removeStaleSocket removes the socket file if it exists but nothing is
// listening on it (i.e. a previous daemon crashed and left it behind).
func removeStaleSocket(path string) error {
	if _, err := os.Stat(path); os.IsNotExist(err) {
		return nil // nothing to clean up
	}

	// Try connecting — if it succeeds, another daemon is already running.
	conn, err := net.Dial("unix", path)
	if err == nil {
		conn.Close()
		return fmt.Errorf("another daemon is already running (socket: %s)", path)
	}

	// Connection refused / no listener — stale socket, remove it.
	log.Printf("removing stale socket: %s", path)
	return os.Remove(path)
}
