// stt-smoke is a one-shot CLI for verifying whisper.cpp model loading and
// transcription. Not part of the production binary.
//
// Usage:
//
//	./build/stt-smoke -model models/ggml-base.en.bin -wav <file.wav>
package main

import (
	"flag"
	"fmt"
	"os"
	"time"

	"github.com/shoutcape/hypr-voice-controls/internal/config"
	"github.com/shoutcape/hypr-voice-controls/internal/stt"
)

func main() {
	modelPath := flag.String("model", "models/ggml-base.en.bin", "Path to GGML model file")
	wavPath := flag.String("wav", "", "Path to WAV file (mono 16kHz)")
	flag.Parse()

	if *wavPath == "" {
		fmt.Fprintln(os.Stderr, "usage: stt-smoke -wav <file.wav> [-model <model.bin>]")
		os.Exit(1)
	}

	cfg := config.Defaults()
	cfg.ModelPath = *modelPath

	fmt.Printf("Loading model: %s\n", cfg.ModelPath)
	start := time.Now()

	engine, err := stt.NewEngine(cfg)
	if err != nil {
		fmt.Fprintf(os.Stderr, "error: %v\n", err)
		os.Exit(1)
	}
	defer engine.Close()

	fmt.Printf("Model loaded in %s\n", time.Since(start).Round(time.Millisecond))
	fmt.Printf("Transcribing: %s\n", *wavPath)

	tStart := time.Now()
	result, err := engine.TranscribeFile(*wavPath)
	if err != nil {
		fmt.Fprintf(os.Stderr, "error: %v\n", err)
		os.Exit(1)
	}

	fmt.Printf("Transcribed in %s\n\n", time.Since(tStart).Round(time.Millisecond))
	fmt.Printf("Result: %q\n", result.Text)
}
