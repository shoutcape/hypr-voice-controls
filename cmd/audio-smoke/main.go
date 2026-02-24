// audio-smoke is a one-shot dev tool that records a short audio clip,
// transcribes it via whisper.cpp, and prints the result.
// This validates the full audio → STT pipeline end-to-end.
//
// Usage:
//
//	./build/audio-smoke [-dur 3] [-source default] [-model models/ggml-base.en.bin]
package main

import (
	"flag"
	"fmt"
	"os"
	"time"

	"github.com/shoutcape/hypr-voice-controls/internal/audio"
	"github.com/shoutcape/hypr-voice-controls/internal/config"
	"github.com/shoutcape/hypr-voice-controls/internal/stt"
)

func main() {
	dur := flag.Int("dur", 3, "Recording duration in seconds")
	source := flag.String("source", "default", "PulseAudio source name")
	modelPath := flag.String("model", "models/ggml-base.en.bin", "Path to GGML model")
	flag.Parse()

	cfg := config.Defaults()
	cfg.AudioSource = *source
	cfg.ModelPath = *modelPath
	cfg.MaxRecordSecs = *dur + 5

	// Load model first so it's warm before we record.
	fmt.Printf("Loading model: %s\n", cfg.ModelPath)
	engine, err := stt.NewEngine(cfg)
	if err != nil {
		fmt.Fprintf(os.Stderr, "error loading model: %v\n", err)
		os.Exit(1)
	}
	defer engine.Close()

	fmt.Printf("Model ready. Recording %ds from %q — speak now...\n", *dur, cfg.AudioSource)

	cap, err := audio.Start(cfg)
	if err != nil {
		fmt.Fprintf(os.Stderr, "error starting capture: %v\n", err)
		os.Exit(1)
	}
	defer cap.Cleanup()

	time.Sleep(time.Duration(*dur) * time.Second)

	wavPath, err := cap.Stop()
	if err != nil {
		fmt.Fprintf(os.Stderr, "error stopping capture: %v\n", err)
		os.Exit(1)
	}

	fmt.Printf("Captured: %s — transcribing...\n", wavPath)
	t := time.Now()

	result, err := engine.TranscribeFile(wavPath)
	if err != nil {
		fmt.Fprintf(os.Stderr, "error transcribing: %v\n", err)
		os.Exit(1)
	}

	fmt.Printf("Transcribed in %s\n\n", time.Since(t).Round(time.Millisecond))
	if result.Text == "" {
		fmt.Println("(no speech detected)")
	} else {
		fmt.Printf("Result: %q\n", result.Text)
	}
}
