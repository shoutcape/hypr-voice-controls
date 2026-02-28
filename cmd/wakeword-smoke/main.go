// wakeword-smoke is a dev tool for validating the openWakeWord ONNX pipeline.
//
// It opens the microphone, streams audio through the three-stage pipeline
// (melspectrogram → embedding → wakeword classifier), prints the raw score
// each inference tick, and prints "DETECTED" when the activation counter fires.
//
// Use this to:
//   - Verify model files load correctly
//   - Calibrate the detection threshold for your environment
//   - Measure CPU usage
//
// Usage:
//
//	./build/wakeword-smoke \
//	    -mel  ~/.local/share/voice-controls/models/melspectrogram.onnx \
//	    -emb  ~/.local/share/voice-controls/models/embedding_model.onnx \
//	    -ww   ~/.local/share/voice-controls/models/hey_hyper.onnx \
//	    -lib  /usr/lib/libonnxruntime.so \
//	    -threshold 0.5
package main

import (
	"flag"
	"fmt"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/gordonklaus/portaudio"
	"github.com/shoutcape/hypr-voice-controls/internal/config"
	"github.com/shoutcape/hypr-voice-controls/internal/wakeword"
)

func main() {
	melModel := flag.String("mel", "", "Path to melspectrogram.onnx")
	embModel := flag.String("emb", "", "Path to embedding_model.onnx")
	wwModel := flag.String("ww", "", "Path to wakeword classifier .onnx")
	onnxLib := flag.String("lib", "/usr/lib/libonnxruntime.so", "Path to libonnxruntime.so")
	audioSrc := flag.String("source", "default", "PortAudio input device name")
	threshold := flag.Float64("threshold", 0.5, "Detection threshold (0–1)")
	triggerLevel := flag.Int("trigger", 4, "Consecutive frames required for detection")
	refractory := flag.Int("refractory", 20, "Post-detection suppression frames (~80ms each)")
	flag.Parse()

	// Validate required flags.
	missing := false
	for _, f := range []struct{ name, val string }{
		{"-mel", *melModel},
		{"-emb", *embModel},
		{"-ww", *wwModel},
	} {
		if f.val == "" {
			fmt.Fprintf(os.Stderr, "error: %s is required\n", f.name)
			missing = true
		}
	}
	if missing {
		flag.Usage()
		os.Exit(1)
	}

	// Build a config from flags.
	cfg := config.Defaults()
	cfg.WakewordEnabled = true
	cfg.WakewordMelModel = *melModel
	cfg.WakewordEmbModel = *embModel
	cfg.WakewordModel = *wwModel
	cfg.OnnxLibPath = *onnxLib
	cfg.WakewordThreshold = *threshold
	cfg.WakewordTriggerLevel = *triggerLevel
	cfg.WakewordRefractory = *refractory
	cfg.AudioSource = *audioSrc

	// ── Initialise ONNX Runtime ──────────────────────────────────
	fmt.Fprintf(os.Stderr, "Loading ONNX Runtime from %s\n", *onnxLib)
	if err := wakeword.InitORT(*onnxLib); err != nil {
		fmt.Fprintf(os.Stderr, "error: failed to initialise ONNX Runtime: %v\n", err)
		fmt.Fprintf(os.Stderr, "  Install with: sudo pacman -S onnxruntime-cpu\n")
		os.Exit(1)
	}
	defer wakeword.DestroyORT()

	// ── Load wakeword detector ────────────────────────────────────
	fmt.Fprintf(os.Stderr, "Loading models...\n")
	detector, err := wakeword.New(cfg)
	if err != nil {
		fmt.Fprintf(os.Stderr, "error: failed to load wakeword detector: %v\n", err)
		os.Exit(1)
	}
	defer detector.Close()

	fmt.Fprintf(os.Stderr, "Models loaded. Initialising audio...\n")

	// ── Initialise PortAudio ─────────────────────────────────────
	if err := portaudio.Initialize(); err != nil {
		fmt.Fprintf(os.Stderr, "error: portaudio init: %v\n", err)
		os.Exit(1)
	}
	defer portaudio.Terminate()

	// Select input device.
	var device *portaudio.DeviceInfo
	if cfg.AudioSource == "" || cfg.AudioSource == "default" {
		device, err = portaudio.DefaultInputDevice()
	} else {
		devices, lerr := portaudio.Devices()
		if lerr != nil {
			fmt.Fprintf(os.Stderr, "error: enumerate devices: %v\n", lerr)
			os.Exit(1)
		}
		for _, d := range devices {
			if d.MaxInputChannels > 0 {
				device = d
				break
			}
		}
	}
	if err != nil || device == nil {
		fmt.Fprintf(os.Stderr, "error: no input device: %v\n", err)
		os.Exit(1)
	}

	// ── Open audio stream ─────────────────────────────────────────
	const (
		sampleRate      = 16000
		framesPerBuffer = 1280 // one inference tick
	)
	audioCh := make(chan []float32, 32)
	buf := make([]float32, framesPerBuffer)

	params := portaudio.StreamParameters{
		Input: portaudio.StreamDeviceParameters{
			Device:   device,
			Channels: 1,
			Latency:  device.DefaultLowInputLatency,
		},
		SampleRate:      sampleRate,
		FramesPerBuffer: framesPerBuffer,
	}
	stream, err := portaudio.OpenStream(params, func(in []float32) {
		copy(buf, in)
		chunk := make([]float32, len(buf))
		copy(chunk, buf)
		select {
		case audioCh <- chunk:
		default: // drop if consumer is slow
		}
	})
	if err != nil {
		fmt.Fprintf(os.Stderr, "error: open audio stream: %v\n", err)
		os.Exit(1)
	}
	if err := stream.Start(); err != nil {
		fmt.Fprintf(os.Stderr, "error: start audio stream: %v\n", err)
		os.Exit(1)
	}
	defer stream.Stop()  //nolint:errcheck
	defer stream.Close() //nolint:errcheck

	// ── Signal handler ────────────────────────────────────────────
	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, syscall.SIGTERM, syscall.SIGINT)

	fmt.Fprintf(os.Stderr, "Listening... (say your wakeword, Ctrl+C to exit)\n")
	fmt.Fprintf(os.Stderr, "threshold=%.2f  trigger_level=%d  refractory=%d\n\n",
		*threshold, *triggerLevel, *refractory)

	tick := time.Now()
	for {
		select {
		case <-sigCh:
			fmt.Fprintf(os.Stderr, "\nExiting.\n")
			return

		case chunk := <-audioCh:
			_ = tick
			if detector.Feed(chunk) {
				fmt.Printf("[%s] DETECTED\n", time.Now().Format("15:04:05.000"))
			}
		}
	}
}
