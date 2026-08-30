package main

import (
	"flag"
	"fmt"
	"os"

	"github.com/shoutcape/hypr-voice-controls/internal/bridge"
	"github.com/shoutcape/hypr-voice-controls/internal/config"
)

func main() {
	configPath := flag.String("config", "", "Path to TOML config file (default: ~/.config/voice-controls/config.toml)")
	showVersion := flag.Bool("version", false, "Print version and exit")
	flag.Parse()

	if *showVersion {
		fmt.Println("voice-controls-wakeword-bridge", config.Version)
		os.Exit(0)
	}

	cfg, err := config.Load(*configPath)
	if err != nil {
		fmt.Fprintf(os.Stderr, "error: failed to load config: %v\n", err)
		os.Exit(1)
	}

	if err := bridge.Run(cfg); err != nil {
		fmt.Fprintf(os.Stderr, "error: wakeword bridge failed: %v\n", err)
		os.Exit(1)
	}
}
