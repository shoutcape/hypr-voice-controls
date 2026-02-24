// Package audio manages audio capture via an ffmpeg subprocess.
// It records from the configured PipeWire/PulseAudio source into a temporary
// WAV file (mono, 16 kHz, s16le) that whisper.cpp can consume directly.
//
// Lifecycle:
//
//	cap, err := audio.Start(cfg)   // spawns ffmpeg, returns immediately
//	wavPath, err := cap.Stop()     // sends SIGINT, waits for clean exit
//	defer cap.Cleanup()            // removes temp dir
package audio

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"syscall"
	"time"

	"github.com/shoutcape/hypr-voice-controls/internal/config"
)

const (
	// stopSIGINTTimeout is how long we wait after SIGINT before escalating.
	stopSIGINTTimeout = 3 * time.Second
	// stopSIGTERMTimeout is how long we wait after SIGTERM before SIGKILL.
	stopSIGTERMTimeout = 2 * time.Second
	// minWAVBytes is the smallest file we accept as a valid recording.
	// A WAV header alone is 44 bytes; anything below this has no audio data.
	minWAVBytes = 1024
)

// Capture represents an active audio recording session.
// Obtain one via Start; release resources with Cleanup.
type Capture struct {
	cmd     *exec.Cmd
	tmpDir  string
	WAVPath string
}

// Start spawns an ffmpeg process that records from cfg.AudioSource into a
// temporary WAV file. It returns as soon as ffmpeg has started; recording
// continues in the background until Stop is called.
func Start(cfg *config.Config) (*Capture, error) {
	tmpDir, err := os.MkdirTemp("", "voice-controls-*")
	if err != nil {
		return nil, fmt.Errorf("failed to create temp dir: %w", err)
	}

	wavPath := filepath.Join(tmpDir, "capture.wav")

	cmd := buildFFmpegCmd(cfg.AudioSource, wavPath, cfg.MaxRecordSecs)
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}

	if err := cmd.Start(); err != nil {
		os.RemoveAll(tmpDir)
		return nil, fmt.Errorf("failed to start ffmpeg: %w", err)
	}

	return &Capture{
		cmd:     cmd,
		tmpDir:  tmpDir,
		WAVPath: wavPath,
	}, nil
}

// Stop terminates the ffmpeg subprocess gracefully and returns the path to the
// completed WAV file. It sends SIGINT first (which causes ffmpeg to flush and
// write the WAV header), then escalates to SIGTERM and SIGKILL if needed.
func (c *Capture) Stop() (string, error) {
	if c.cmd == nil || c.cmd.Process == nil {
		return "", fmt.Errorf("capture not started")
	}

	// SIGINT → ffmpeg flushes buffers and writes a valid WAV header.
	if err := c.cmd.Process.Signal(syscall.SIGINT); err != nil {
		// Process may have already exited (e.g. max duration reached).
		// That's fine — fall through to Wait.
		if !isProcessDone(err) {
			return "", fmt.Errorf("failed to signal ffmpeg: %w", err)
		}
	}

	done := make(chan error, 1)
	go func() { done <- c.cmd.Wait() }()

	select {
	case <-done:
		// Clean exit after SIGINT.
	case <-time.After(stopSIGINTTimeout):
		// Escalate to SIGTERM.
		_ = c.cmd.Process.Signal(syscall.SIGTERM)
		select {
		case <-done:
		case <-time.After(stopSIGTERMTimeout):
			// Last resort: SIGKILL.
			_ = c.cmd.Process.Kill()
			<-done
		}
	}

	// Validate the WAV file has usable audio data.
	info, err := os.Stat(c.WAVPath)
	if err != nil {
		return "", fmt.Errorf("WAV file missing after capture: %w", err)
	}
	if info.Size() < minWAVBytes {
		return "", fmt.Errorf("recording too short (%.0f bytes) — did you hold the key long enough?", float64(info.Size()))
	}

	return c.WAVPath, nil
}

// Cleanup removes the temporary directory and all files created during capture.
// Safe to call more than once and always safe to defer.
func (c *Capture) Cleanup() {
	if c.tmpDir != "" {
		os.RemoveAll(c.tmpDir)
	}
}

// buildFFmpegCmd constructs the ffmpeg command for capturing mono 16 kHz audio.
//
// The output format is:
//   - Container: WAV
//   - Codec:     pcm_s16le (16-bit signed little-endian PCM)
//   - Channels:  1 (mono)
//   - Rate:      16000 Hz  (whisper.SampleRate)
//
// maxSecs is a hard safety cap; pass 0 to disable.
func buildFFmpegCmd(source, wavPath string, maxSecs int) *exec.Cmd {
	args := []string{
		"-hide_banner",
		"-loglevel", "error",
		"-f", "pulse",
		"-i", source,
		"-ac", "1",
		"-ar", "16000",
		"-c:a", "pcm_s16le",
	}
	if maxSecs > 0 {
		args = append(args, "-t", fmt.Sprintf("%d", maxSecs))
	}
	args = append(args, wavPath)

	return exec.Command("ffmpeg", args...)
}

// isProcessDone reports whether an error from Signal/Kill indicates the
// process has already exited (not a real error we need to surface).
func isProcessDone(err error) bool {
	if err == nil {
		return false
	}
	return err == os.ErrProcessDone ||
		err.Error() == "os: process already finished"
}
