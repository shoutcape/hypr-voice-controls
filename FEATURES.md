# Voice Controls — Feature List

Target: a lightweight, reliable, English-only, fully offline voice-control daemon for Hyprland / Wayland desktops.
Optimized for maximum performance using English-specific Whisper models (`.en` variants).
No internet connection required at runtime. All models and dependencies are local.

---

## 1. Core Speech-to-Text

| # | Feature | Description | Priority |
|---|---------|-------------|----------|
| 1.1 | Local/offline transcription | Run Whisper (via faster-whisper or whisper.cpp) entirely on-device. No cloud dependency. | Must |
| 1.2 | GPU acceleration | Auto-detect and use CUDA only CUDA. | Must |
| 1.3 | English-only models | Use `.en` model variants (tiny.en → medium.en) for faster inference and higher English accuracy. | Must |
| 1.4 | Single configurable model | Let the user pick tiny.en → medium.en to trade speed vs. accuracy. | Must |
| 1.4 | Hot model loading | Keep the model resident in memory between activations to eliminate cold-start latency. | Must |

## 2. Audio Capture

| # | Feature | Description | Priority |
|---|---------|-------------|----------|
| 2.1 | PipeWire / PulseAudio capture | Record from the default mic via PortAudio (supports PipeWire and PulseAudio backends). | Must |
| 2.2 | Configurable audio device | Let the user select a specific input device by name (substring-matched against PortAudio device list). | Should |
| 2.3 | Voice activity detection (VAD) | Use silero-vad or similar to detect speech boundaries and avoid transcribing silence. | Should |
| 2.4 | Noise filtering | Basic noise gate or pre-processing to improve recognition in noisy environments. | Could |

## 3. Text Input / Output

| # | Feature | Description | Priority |
|---|---------|-------------|----------|
| 3.1 | Wayland-native text injection | Use `wtype` for direct keystroke injection on Wayland — no X11 dependency. | Must |
| 3.2 | Clipboard fallback | Copy to clipboard via `wl-copy` + paste for apps that don't accept wtype input (e.g. Electron apps). | Must |
| 3.3 | Smart punctuation | Auto-insert periods, commas, question marks based on speech patterns and pauses. | Should |
| 3.4 | Capitalization rules | Auto-capitalize sentence starts; support "all caps" / "no caps" voice modifiers. | Should |
| 3.5 | Text formatters | Voice-triggered formatters: "snake case hello world" → `hello_world`, "camel case" → `helloWorld`, etc. | Could |
| 3.6 | Word overrides / corrections | User-defined word replacement map (e.g. "hyper land" → "Hyprland"). | Should |

## 4. Activation & Hotkey

| # | Feature | Description | Priority |
|---|---------|-------------|----------|
| 4.1 | Push-to-talk mode | Hold key to record, release to transcribe. | Must |
| 4.3 | Hyprland keybind integration | Work with Hyprland's `bind` / `bindr` for seamless compositor-level hotkey support. | Must |
| 4.4 | Configurable hotkey | User can set any key/combo as the activation trigger. | Must |
| 4.5 | Wake word activation | Optional always-listening wake word (e.g. "hey hypr") to start recording hands-free. | Could |

## 5. Daemon Architecture

| # | Feature | Description | Priority |
|---|---------|-------------|----------|
| 5.1 | Persistent daemon process | Run as a long-lived background process to keep model warm and respond instantly. | Must |
| 5.2 | Socket-based IPC | Control the daemon (start/stop/status) via a Unix socket for reliability and speed. | Must |
| 5.3 | Systemd user service | Ship a systemd user unit for auto-start, restart-on-crash, and clean lifecycle management. | Must |
| 5.4 | Graceful shutdown | Handle SIGTERM/SIGINT cleanly, release audio devices and in-memory buffers. | Must |
| 5.5 | Session recovery | Detect stale sockets/PID files and recover without requiring manual cleanup. | Should |
| 5.6 | Low idle resource usage | When not actively recording, the daemon should use near-zero CPU and minimal RAM beyond model weight. | Must |

## 6. User Feedback

| # | Feature | Description | Priority |
|---|---------|-------------|----------|
| 6.1 | Visual recording indicator | Show an on-screen indicator (OSD or Waybar module) when recording is active. | Must |
| 6.2 | Waybar integration | Provide a Waybar custom module showing status (idle / recording / transcribing). | Should |
| 6.3 | Audio feedback | Optional start/stop sounds to confirm activation without looking at the screen. | Could |
| 6.4 | Desktop notifications | Send a notification on errors or when transcription is pasted. | Could |
| 6.5 | Audio level visualization | Show real-time mic level during recording (in OSD or Waybar). | Could |

## 7. Voice Commands (beyond dictation)

| # | Feature | Description | Priority |
|---|---------|-------------|----------|
| 7.1 | Command mode vs. dictation mode | Separate modes: one inserts text, the other executes system actions. | Should |
| 7.2 | Built-in system commands | Voice commands for common actions: "open terminal", "switch workspace 3", "close window". | Should |
| 7.3 | Hyprland dispatcher integration | Map voice commands to `hyprctl dispatch` calls for window/workspace management. | Should |
| 7.4 | Custom command definitions | User-defined voice→action mappings in a config file. | Should |
| 7.5 | Application launching | "Open firefox", "launch code" — resolve app names to desktop entries or paths. | Could |
| 7.6 | Media control | "Pause music", "next track", "volume up" via playerctl / pactl. | Could |
| 7.7 | Command chaining | "Move window right and switch to workspace 2" — sequential compound commands. | Could |

## 8. Configuration

| # | Feature | Description | Priority |
|---|---------|-------------|----------|
| 8.1 | Single config file | One TOML or YAML file for all settings. | Must |
| 8.2 | Sane defaults | Work out of the box with zero configuration for the common case. | Must |
| 8.3 | Runtime config reload | Reload config without restarting the daemon (via signal or socket command). | Should |
| 8.4 | CLI for config management | `voice-controls config show`, `voice-controls config set key value`. | Could |
| 8.5 | Per-application rules | Different behavior per focused app (e.g. disable in games, command-only in terminal). | Could |

## 9. Reliability & Error Handling

| # | Feature | Description | Priority |
|---|---------|-------------|----------|
| 9.1 | Crash recovery | Daemon auto-restarts via systemd; cleans up stale state on startup. | Must |
| 9.2 | Audio device hot-plug | Handle mic disconnect/reconnect without crashing. | Should |
| 9.3 | Structured logging | Leveled logs (debug/info/warn/error) to journald with clear context. | Must |
| 9.4 | Health check endpoint | `voice-controls status` command returning daemon health, model loaded, mic status. | Should |
| 9.5 | Timeout protection | Recording sessions auto-stop after a configurable max duration to prevent runaway captures. | Should |

## 10. Developer Experience

| # | Feature | Description | Priority |
|---|---------|-------------|----------|
| 10.1 | Clean Go module | `go.mod` with proper dependencies and version pinning. | Must |
| 10.2 | Test suite | Unit tests for core logic; integration tests for IPC and audio pipeline. | Must |
| 10.3 | CI pipeline | GitHub Actions for lint, vet, and test on every push/PR. | Should |

## 11. Installation & Distribution

| # | Feature | Description | Priority |
|---|---------|-------------|----------|
| 11.1 | `make install` | Install binary, config, model, and systemd service in one step. | Must |
| 11.2 | AUR package | Arch User Repository package for easy Arch/Hyprland user installation. | Should |
| 11.3 | Dependency minimalism | Keep runtime deps small; avoid heavy frameworks. | Must |
| 11.4 | Post-install setup helper | Script or command that verifies mic access, downloads model, and creates systemd unit. | Should |

---

## Priority Key

| Label | Meaning |
|-------|---------|
| **Must** | Required for a usable v1.0 release |
| **Should** | Important for a good experience; target for v1.x |
| **Could** | Nice to have; consider for future versions |

## Lessons from Previous Implementation

- Daemon startup handshake was fragile — design the socket protocol carefully from the start.
- Command mode added complexity without clear value early on — ship dictation-first, add commands later.
- Model warmup in a background thread caused race conditions — use explicit ready-state signaling.
- Multiple backend options (whispercpp server, local lib) increased maintenance burden — pick one and do it well.
- Overlay/TTS features were removed as unused — validate features with actual usage before building.
