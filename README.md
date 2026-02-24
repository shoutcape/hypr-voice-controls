# hypr-voice-controls

A lightweight, fully offline, English-only voice dictation daemon for Hyprland / Wayland.

Hold a key → speak → release → transcribed text is pasted into the focused window.

No cloud. No internet. Everything runs locally.

---

## How it works

```
hold key
    └─▶ voice-controls --input dictate-start
            └─▶ daemon spawns ffmpeg (mic capture, 16 kHz mono WAV)

release key
    └─▶ voice-controls --input dictate-stop
            └─▶ daemon stops ffmpeg
            └─▶ whisper.cpp transcribes WAV → text
            └─▶ wl-copy writes text to clipboard
            └─▶ hyprctl dispatch sendshortcut pastes into focused window
            └─▶ desktop notification shows what was pasted
```

The daemon keeps the whisper model resident in memory so transcription is fast
on every activation — no cold-start penalty after the first use.

---

## Requirements

**Build dependencies**

| Tool | Purpose |
|------|---------|
| `go` ≥ 1.21 | Build the Go binary |
| `cmake` ≥ 3.14 | Build whisper.cpp |
| `gcc` / `g++` | C/C++ compiler for whisper.cpp |
| `git` | Clone whisper.cpp at build time |

**Runtime dependencies**

| Tool | Required | Purpose |
|------|----------|---------|
| `ffmpeg` | Yes | Mic capture (PipeWire / PulseAudio) |
| `wl-copy` | Yes | Write text to Wayland clipboard |
| `hyprctl` | Yes | Trigger paste shortcut |
| `notify-send` | No | Desktop notifications (fallback if no hyprctl) |

**Optional — CUDA GPU acceleration**

| Tool | Purpose |
|------|---------|
| CUDA toolkit ≥ 11 | GPU inference (much faster transcription) |
| NVIDIA driver | Required for CUDA |

---

## Quick start

### 1. Build

```bash
git clone https://github.com/shoutcape/hypr-voice-controls.git
cd hypr-voice-controls
make build          # clones whisper.cpp, builds libwhisper.a, compiles binary
```

For CUDA GPU acceleration:

```bash
make build-cuda
```

### 2. Download a model

```bash
make model          # downloads ggml-base.en.bin (~142 MB) into models/
```

Available English-only models (faster and more accurate than multilingual):

| Model | Size | Speed | Accuracy |
|-------|------|-------|----------|
| `tiny.en` | 75 MB | fastest | lowest |
| `base.en` | 142 MB | fast | good ← default |
| `small.en` | 466 MB | moderate | better |
| `medium.en` | 1.5 GB | slow | best |

To download a different model:

```bash
./scripts/download-model.sh models small.en
```

### 3. Test the pipeline

```bash
./scripts/test-dictation.sh        # records 4 seconds, transcribes, pastes
./scripts/test-dictation.sh 6      # record for 6 seconds instead
```

Focus the window you want text pasted into before running the script.

---

## Configuration

Copy the example config and edit it:

```bash
mkdir -p ~/.config/voice-controls
cp config.example.toml ~/.config/voice-controls/config.toml
```

Key settings (`~/.config/voice-controls/config.toml`):

```toml
# Path to the GGML model file
model_path = "~/.local/share/voice-controls/models/ggml-base.en.bin"

# Audio input device ("default" uses system default mic)
audio_source = "default"

# Paste shortcut sent via hyprctl
paste_shortcut = "CTRL SHIFT,V,"

# Max recording duration in seconds (safety cap)
max_record_secs = 120
```

**Environment variable overrides** (useful for testing, override config file):

| Variable | Purpose |
|----------|---------|
| `VOICE_MODEL` | Path to GGML model file |
| `VOICE_SOCKET` | Unix socket path |
| `VOICE_AUDIO` | PulseAudio source name |

---

## Hyprland integration

### Keybindings

Source the example bindings file from your `hyprland.conf`:

```ini
source = ~/.config/hypr/voice-controls.bindings.conf
```

Or add manually — `bind` fires on key press, `bindr` fires on release:

```ini
bind  = , F17, exec, voice-controls --input dictate-start
bindr = , F17, exec, voice-controls --input dictate-stop
```

### Auto-start with Hyprland

Copy and enable the systemd user service:

```bash
cp examples/systemd/voice-controls.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now voice-controls.service
```

Source the autostart config in your `hyprland.conf`:

```ini
source = ~/.config/hypr/voice-controls.autostart.conf
```

Check service status:

```bash
systemctl --user status voice-controls
journalctl --user -u voice-controls -f
```

### Install the binary

```bash
cp build/voice-controls ~/.local/bin/
```

---

## Architecture

```
hypr-voice-controls/
├── cmd/
│   └── voice-controls/     # CLI entry point (--daemon / --input)
├── internal/
│   ├── config/             # TOML config loading, env var overrides, defaults
│   ├── daemon/             # Socket server, model lifecycle, session state
│   ├── client/             # Socket client, daemon auto-start
│   ├── ipc/                # JSON-line protocol (Request / Response types)
│   ├── stt/                # whisper.cpp wrapper (model load, transcribe)
│   ├── audio/              # ffmpeg subprocess (capture, stop, cleanup)
│   ├── output/             # wl-copy + hyprctl paste, text sanitisation
│   └── notify/             # hyprctl notify + notify-send fallback
├── examples/
│   ├── hypr/               # Hyprland keybinding and autostart configs
│   └── systemd/            # Systemd user service unit
├── scripts/
│   ├── download-model.sh   # Fetch GGML model from HuggingFace
│   └── test-dictation.sh   # End-to-end manual test script
├── Makefile                # Build orchestration
└── config.example.toml     # Annotated example configuration
```

### IPC protocol

Client and daemon communicate over a Unix domain socket (`$XDG_RUNTIME_DIR/voice-controls.sock`) using newline-delimited JSON:

```
→ {"action":"dictate-start"}
← {"rc":0,"msg":"recording started"}

→ {"action":"dictate-stop"}
← {"rc":0,"msg":"Hello world, this is a test."}

→ {"action":"ping"}
← {"rc":0,"msg":"pong"}
```

`rc=0` is success. `rc=1` is a runtime error. `rc=2` is an unknown action.

---

## Make targets

```
make build        Build the voice-controls binary (CPU)
make build-cuda   Build with CUDA GPU acceleration
make model        Download the default base.en model
make smoke        Run STT smoke test against JFK sample WAV
make test         Run Go tests
make lint         Run go vet
make fmt          Format Go source
make clean        Remove build artifacts
make clean-all    Remove build artifacts and whisper.cpp clone
```

---

## Development tools

Two dev-only binaries in `cmd/` are not installed but useful during development:

```bash
# Test STT with a WAV file directly
./build/stt-smoke -model models/ggml-base.en.bin -wav path/to/file.wav

# Test full audio capture + transcription pipeline
./build/audio-smoke -dur 3 -model models/ggml-base.en.bin
```

---

## Current status

| Phase | Feature | Status |
|-------|---------|--------|
| 1 | Build system (Makefile, Go module, project structure) | Done |
| 2 | STT integration (whisper.cpp via CGO) | Done |
| 3 | Audio capture (ffmpeg subprocess, PipeWire/PulseAudio) | Done |
| 4 | Daemon + IPC (Unix socket, JSON-line protocol) | Done |
| 5 | Text output (clipboard paste + desktop notifications) | Done |
| 6 | Config file (TOML), systemd service, Hyprland examples | Pending |

---

## Known limitations

- TOML config file parsing is not yet implemented — use env vars or defaults for now.
- CUDA build requires the CUDA toolkit at compile time; CPU-only is the default.
- The whisper.cpp C library prints verbose init logs to stderr on startup; these are suppressed in normal use but visible in daemon logs.
- `.en` models only — multilingual models are not supported by design.
