// Package main is the entry point for the voice-controls binary.
// It parses CLI flags and dispatches to either daemon or client mode.
package main

import (
	"flag"
	"fmt"
	"os"
	"os/exec"

	"github.com/shoutcape/hypr-voice-controls/internal/client"
	"github.com/shoutcape/hypr-voice-controls/internal/config"
	"github.com/shoutcape/hypr-voice-controls/internal/daemon"
)

func main() {
	// Sub-command flags
	daemonMode := flag.Bool("daemon", false, "Run as background daemon (keeps model warm, listens on socket)")
	inputAction := flag.String("input", "", "Send an action to the daemon (e.g. dictate-start, dictate-stop)")
	startService := flag.Bool("start", false, "Start the voice-controls systemd user service")
	restartService := flag.Bool("restart", false, "Restart the voice-controls systemd user service")
	stopService := flag.Bool("stop", false, "Stop the voice-controls systemd user service")
	configPath := flag.String("config", "", "Path to TOML config file (default: ~/.config/voice-controls/config.toml)")
	showVersion := flag.Bool("version", false, "Print version and exit")

	flag.Usage = func() {
		fmt.Fprintf(os.Stderr, "Usage: voice-controls [options]\n\n")
		fmt.Fprintf(os.Stderr, "A lightweight, offline voice-control daemon for Hyprland.\n\n")
		fmt.Fprintf(os.Stderr, "Options:\n")
		flag.PrintDefaults()
		fmt.Fprintf(os.Stderr, "\nExamples:\n")
		fmt.Fprintf(os.Stderr, "  voice-controls --daemon          Start the daemon\n")
		fmt.Fprintf(os.Stderr, "  voice-controls --start           Start the systemd user service\n")
		fmt.Fprintf(os.Stderr, "  voice-controls --restart         Restart the systemd user service\n")
		fmt.Fprintf(os.Stderr, "  voice-controls --stop            Stop the systemd user service\n")
		fmt.Fprintf(os.Stderr, "  voice-controls --input dictate-start   Begin recording\n")
		fmt.Fprintf(os.Stderr, "  voice-controls --input dictate-stop    Stop and transcribe\n")
	}

	flag.Parse()

	if *showVersion {
		fmt.Println("voice-controls", config.Version)
		os.Exit(0)
	}

	switch {
	case *startService:
		if err := manageVoiceControlsService("start"); err != nil {
			fmt.Fprintf(os.Stderr, "error: failed to start service: %v\n", err)
			os.Exit(1)
		}
	case *restartService:
		if err := manageVoiceControlsService("restart"); err != nil {
			fmt.Fprintf(os.Stderr, "error: failed to restart service: %v\n", err)
			os.Exit(1)
		}
	case *stopService:
		if err := manageVoiceControlsService("stop"); err != nil {
			fmt.Fprintf(os.Stderr, "error: failed to stop service: %v\n", err)
			os.Exit(1)
		}
	case *daemonMode:
		cfg, err := config.Load(*configPath)
		if err != nil {
			fmt.Fprintf(os.Stderr, "error: failed to load config: %v\n", err)
			os.Exit(1)
		}
		if err := daemon.Run(cfg); err != nil {
			fmt.Fprintf(os.Stderr, "error: daemon failed: %v\n", err)
			os.Exit(1)
		}
	case *inputAction != "":
		cfg, err := config.Load(*configPath)
		if err != nil {
			fmt.Fprintf(os.Stderr, "error: failed to load config: %v\n", err)
			os.Exit(1)
		}
		if err := client.Send(cfg, *inputAction); err != nil {
			fmt.Fprintf(os.Stderr, "error: %v\n", err)
			os.Exit(1)
		}
	default:
		flag.Usage()
		os.Exit(1)
	}
}

func manageVoiceControlsService(action string) error {
	cmd := exec.Command("systemctl", "--user", action, "voice-controls.service")
	if out, err := cmd.CombinedOutput(); err != nil {
		if len(out) == 0 {
			return err
		}
		return fmt.Errorf("%w: %s", err, out)
	}
	return nil
}
