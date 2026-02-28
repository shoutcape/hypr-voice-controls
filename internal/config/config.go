// Package config handles loading and validating the TOML configuration file.
// It provides sane defaults so the daemon works out of the box with zero config.
//
// Priority (highest wins):
//  1. Environment variables (VOICE_MODEL, VOICE_SOCKET, VOICE_AUDIO)
//  2. TOML config file (~/.config/voice-controls/config.toml)
//  3. Built-in defaults
package config

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/BurntSushi/toml"
)

// Version is set at build time via -ldflags "-X config.Version=x.y.z".
var Version = "dev"

// Config holds all runtime configuration for the voice-controls daemon.
type Config struct {
	// General
	SocketPath string `toml:"socket_path"`
	LogLevel   string `toml:"log_level"`

	// STT / Model
	ModelPath string `toml:"model_path"`
	ModelName string `toml:"model_name"`
	Device    string `toml:"device"` // "cpu" or "cuda"

	// Audio
	AudioSource string `toml:"audio_source"`

	// Output
	PasteShortcut string `toml:"paste_shortcut"`

	// Session
	MaxRecordSecs int `toml:"max_record_secs"`

	// Wakeword
	WakewordEnabled         bool    `toml:"wakeword_enabled"`
	WakewordMelModel        string  `toml:"wakeword_mel_model"`
	WakewordEmbModel        string  `toml:"wakeword_emb_model"`
	WakewordModel           string  `toml:"wakeword_model"`
	WakewordThreshold       float64 `toml:"wakeword_threshold"`
	WakewordTriggerLevel    int     `toml:"wakeword_trigger_level"`
	WakewordRefractory      int     `toml:"wakeword_refractory"`
	WakewordSilenceStopSecs float64 `toml:"wakeword_silence_stop_secs"`
	WakewordMaxRecordSecs   int     `toml:"wakeword_max_record_secs"`

	// ONNX Runtime
	OnnxLibPath string `toml:"onnx_lib_path"`
}

// Defaults returns a Config populated with sane defaults.
func Defaults() *Config {
	runtimeDir := os.Getenv("XDG_RUNTIME_DIR")
	if runtimeDir == "" {
		runtimeDir = fmt.Sprintf("/run/user/%d", os.Getuid())
	}
	homeDir, _ := os.UserHomeDir()
	modelDir := filepath.Join(homeDir, ".local", "share", "voice-controls", "models")

	return &Config{
		SocketPath:    filepath.Join(runtimeDir, "voice-controls.sock"),
		LogLevel:      "info",
		ModelPath:     filepath.Join(modelDir, "ggml-distil-large-v3.bin"),
		ModelName:     "distil-large-v3",
		Device:        "cpu",
		AudioSource:   "default",
		PasteShortcut: "CTRL SHIFT,V,",
		MaxRecordSecs: 120,

		WakewordEnabled:         false,
		WakewordMelModel:        filepath.Join(modelDir, "melspectrogram.onnx"),
		WakewordEmbModel:        filepath.Join(modelDir, "embedding_model.onnx"),
		WakewordModel:           filepath.Join(modelDir, "hey_hyper.onnx"),
		WakewordThreshold:       0.5,
		WakewordTriggerLevel:    4,
		WakewordRefractory:      20,
		WakewordSilenceStopSecs: 1.5,
		WakewordMaxRecordSecs:   10,

		OnnxLibPath: resolveOnnxLibPath(),
	}
}

// Load builds a Config by layering:
//  1. Defaults
//  2. TOML file at configPath (or the default location if configPath is "")
//  3. Environment variable overrides
//
// If no config file exists the defaults are used silently — no error.
func Load(configPath string) (*Config, error) {
	cfg := Defaults()

	// ── Resolve config file path ──────────────────────────────────
	if configPath == "" {
		homeDir, err := os.UserHomeDir()
		if err == nil {
			configPath = filepath.Join(homeDir, ".config", "voice-controls", "config.toml")
		}
	}

	// ── Parse TOML if the file exists ────────────────────────────
	if configPath != "" {
		if _, err := os.Stat(configPath); err == nil {
			// Decode into a separate partial struct so we only overwrite
			// fields that are explicitly set in the file.
			var file fileConfig
			if _, err := toml.DecodeFile(configPath, &file); err != nil {
				return nil, fmt.Errorf("failed to parse config file %q: %w", configPath, err)
			}
			file.applyTo(cfg)
		}
	}

	// ── Expand tildes in path fields ─────────────────────────────
	cfg.ModelPath = expandTilde(cfg.ModelPath)
	cfg.SocketPath = expandTilde(cfg.SocketPath)
	cfg.WakewordMelModel = expandTilde(cfg.WakewordMelModel)
	cfg.WakewordEmbModel = expandTilde(cfg.WakewordEmbModel)
	cfg.WakewordModel = expandTilde(cfg.WakewordModel)
	cfg.OnnxLibPath = expandTilde(cfg.OnnxLibPath)

	// ── Environment variable overrides (highest priority) ────────
	if v := os.Getenv("VOICE_MODEL"); v != "" {
		cfg.ModelPath = v
	}
	if v := os.Getenv("VOICE_SOCKET"); v != "" {
		cfg.SocketPath = v
	}
	if v := os.Getenv("VOICE_AUDIO"); v != "" {
		cfg.AudioSource = v
	}

	return cfg, nil
}

// fileConfig mirrors Config but uses pointers so we can distinguish
// "field not set in file" from "field explicitly set to zero value".
type fileConfig struct {
	SocketPath    *string `toml:"socket_path"`
	LogLevel      *string `toml:"log_level"`
	ModelPath     *string `toml:"model_path"`
	ModelName     *string `toml:"model_name"`
	Device        *string `toml:"device"`
	AudioSource   *string `toml:"audio_source"`
	PasteShortcut *string `toml:"paste_shortcut"`
	MaxRecordSecs *int    `toml:"max_record_secs"`

	WakewordEnabled         *bool    `toml:"wakeword_enabled"`
	WakewordMelModel        *string  `toml:"wakeword_mel_model"`
	WakewordEmbModel        *string  `toml:"wakeword_emb_model"`
	WakewordModel           *string  `toml:"wakeword_model"`
	WakewordThreshold       *float64 `toml:"wakeword_threshold"`
	WakewordTriggerLevel    *int     `toml:"wakeword_trigger_level"`
	WakewordRefractory      *int     `toml:"wakeword_refractory"`
	WakewordSilenceStopSecs *float64 `toml:"wakeword_silence_stop_secs"`
	WakewordMaxRecordSecs   *int     `toml:"wakeword_max_record_secs"`

	OnnxLibPath *string `toml:"onnx_lib_path"`
}

// applyTo merges non-nil fields from f into cfg, leaving defaults intact
// for fields not present in the file.
func (f *fileConfig) applyTo(cfg *Config) {
	if f.SocketPath != nil {
		cfg.SocketPath = *f.SocketPath
	}
	if f.LogLevel != nil {
		cfg.LogLevel = *f.LogLevel
	}
	if f.ModelPath != nil {
		cfg.ModelPath = *f.ModelPath
	}
	if f.ModelName != nil {
		cfg.ModelName = *f.ModelName
	}
	if f.Device != nil {
		cfg.Device = *f.Device
	}
	if f.AudioSource != nil {
		cfg.AudioSource = *f.AudioSource
	}
	if f.PasteShortcut != nil {
		cfg.PasteShortcut = *f.PasteShortcut
	}
	if f.MaxRecordSecs != nil {
		cfg.MaxRecordSecs = *f.MaxRecordSecs
	}
	if f.WakewordEnabled != nil {
		cfg.WakewordEnabled = *f.WakewordEnabled
	}
	if f.WakewordMelModel != nil {
		cfg.WakewordMelModel = *f.WakewordMelModel
	}
	if f.WakewordEmbModel != nil {
		cfg.WakewordEmbModel = *f.WakewordEmbModel
	}
	if f.WakewordModel != nil {
		cfg.WakewordModel = *f.WakewordModel
	}
	if f.WakewordThreshold != nil {
		cfg.WakewordThreshold = *f.WakewordThreshold
	}
	if f.WakewordTriggerLevel != nil {
		cfg.WakewordTriggerLevel = *f.WakewordTriggerLevel
	}
	if f.WakewordRefractory != nil {
		cfg.WakewordRefractory = *f.WakewordRefractory
	}
	if f.WakewordSilenceStopSecs != nil {
		cfg.WakewordSilenceStopSecs = *f.WakewordSilenceStopSecs
	}
	if f.WakewordMaxRecordSecs != nil {
		cfg.WakewordMaxRecordSecs = *f.WakewordMaxRecordSecs
	}
	if f.OnnxLibPath != nil {
		cfg.OnnxLibPath = *f.OnnxLibPath
	}
}

// resolveOnnxLibPath returns the first libonnxruntime.so path that exists,
// preferring the self-contained user install over the system package.
func resolveOnnxLibPath() string {
	homeDir, _ := os.UserHomeDir()
	candidates := []string{
		filepath.Join(homeDir, ".local", "lib", "libonnxruntime.so"),
		"/usr/lib/libonnxruntime.so",
		"/usr/local/lib/libonnxruntime.so",
	}
	for _, p := range candidates {
		if _, err := os.Stat(p); err == nil {
			return p
		}
	}
	return "/usr/lib/libonnxruntime.so" // fallback; will produce a clear error at runtime
}

// expandTilde replaces a leading "~" with the current user's home directory.
func expandTilde(path string) string {
	if !strings.HasPrefix(path, "~") {
		return path
	}
	homeDir, err := os.UserHomeDir()
	if err != nil {
		return path
	}
	return filepath.Join(homeDir, path[1:])
}
