// Package client implements the CLI-side IPC client.
// It connects to the daemon's Unix socket, sends a JSON-line request,
// and reads the response. If the daemon is not running it auto-starts it.
package client

import (
	"bufio"
	"fmt"
	"net"
	"os"
	"os/exec"
	"time"

	"github.com/shoutcape/hypr-voice-controls/internal/config"
	"github.com/shoutcape/hypr-voice-controls/internal/ipc"
)

// Send connects to the daemon, sends action, and prints the response message.
// If the daemon is not running it starts it automatically, waits for READY,
// then sends the request.
func Send(cfg *config.Config, action string) error {
	conn, err := dial(cfg.SocketPath)
	if err != nil {
		// Daemon not running — start it.
		if err := autoStart(cfg); err != nil {
			return fmt.Errorf("failed to start daemon: %w", err)
		}
		conn, err = dial(cfg.SocketPath)
		if err != nil {
			return fmt.Errorf("daemon started but socket unavailable: %w", err)
		}
	}
	defer conn.Close()

	// Send request.
	if err := ipc.Send(conn, &ipc.Request{Action: action}); err != nil {
		return fmt.Errorf("send request: %w", err)
	}

	// Read response (with generous timeout covering transcription time).
	conn.SetDeadline(time.Now().Add(ipc.ResponseTimeout)) //nolint:errcheck
	var resp ipc.Response
	if err := ipc.Recv(conn, &resp); err != nil {
		return fmt.Errorf("receive response: %w", err)
	}

	if resp.RC != 0 {
		return fmt.Errorf("%s", resp.Msg)
	}

	if resp.Msg != "" && resp.Msg != "recording started" && resp.Msg != "pong" {
		// Non-trivial message (e.g. transcribed text) — print it.
		fmt.Println(resp.Msg)
	}

	return nil
}

// dial attempts a single connection to the Unix socket with a short timeout.
func dial(socketPath string) (net.Conn, error) {
	return net.DialTimeout("unix", socketPath, ipc.DialTimeout)
}

// autoStart launches the daemon in the background and waits for its READY line.
func autoStart(cfg *config.Config) error {
	self, err := os.Executable()
	if err != nil {
		return fmt.Errorf("cannot determine executable path: %w", err)
	}

	args := []string{"--daemon"}
	if cfg.ModelPath != "" {
		// Daemon will pick up config via its own Load call; nothing extra needed.
	}

	cmd := exec.Command(self, args...)
	cmd.Stderr = os.Stderr // propagate daemon errors to the user's terminal

	// Capture stdout to watch for the READY signal.
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		return fmt.Errorf("stdout pipe: %w", err)
	}

	if err := cmd.Start(); err != nil {
		return fmt.Errorf("start daemon process: %w", err)
	}

	// Wait for "READY\n" with a timeout.
	readyCh := make(chan error, 1)
	go func() {
		scanner := bufio.NewScanner(stdout)
		for scanner.Scan() {
			if scanner.Text() == ipc.ReadyMsg {
				readyCh <- nil
				return
			}
		}
		if err := scanner.Err(); err != nil {
			readyCh <- fmt.Errorf("reading daemon stdout: %w", err)
			return
		}
		readyCh <- fmt.Errorf("daemon exited before printing READY")
	}()

	select {
	case err := <-readyCh:
		return err
	case <-time.After(ipc.ReadyTimeout):
		cmd.Process.Kill() //nolint:errcheck
		return fmt.Errorf("daemon did not become ready within %s", ipc.ReadyTimeout)
	}
}
