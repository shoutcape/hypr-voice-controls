# Hypr Voice Controls

[![CI](https://github.com/shoutcape/hypr-voice-controls/actions/workflows/ci.yml/badge.svg)](https://github.com/shoutcape/hypr-voice-controls/actions/workflows/ci.yml)

Voice hotkey daemon for Hyprland with two paths:

- hold-to-command: transcribe speech and execute a configured desktop action
- hold-to-dictate: transcribe speech and paste text into the focused app
- press/hold capture runs until key release (no fixed max hold timeout)

This repo is the canonical source. Hyprland binds and the user service should point to this checkout.

Primary CLI command is `hvc`. The legacy `voice-hotkey.py` entrypoint remains for compatibility.

## CI status

- GitHub Actions workflow: `ci.yml`
- Run the same local gate before pushing:

```bash
<REPO_DIR>/scripts/pre_release_checks.sh
```

## Config templates

Use repo examples instead of committing personal desktop config:

- `examples/hypr/voice-hotkey.bindings.conf`
- `examples/systemd/voice-hotkey.service`
- `examples/systemd/voice-runtime-health.service` (optional)
- `examples/systemd/voice-runtime-health.timer` (optional)
- `examples/systemd/voice-runtime-health-notify@.service` (optional)
- `examples/hypr/voice-hotkey.autostart.conf`
- `examples/hypr/voice-commands.json`

Replace `<REPO_DIR>` in templates with your local checkout path, then copy the lines into:

- `~/.config/hypr/bindings.conf`
- `~/.config/systemd/user/voice-hotkey.service`
- `~/.config/hypr/autostart.conf`

For private spoken-command definitions, copy `examples/hypr/voice-commands.json` to `~/.config/hypr/voice-commands.json`.

## Runtime architecture

- `hvc`: primary CLI entrypoint
- `voice-hotkey.py`: compatibility wrapper entrypoint
- `voice_hotkey/app.py`: CLI modes, daemon client/server flow, orchestration
- `voice_hotkey/commands.py`: normalization, JSON command loading, optional local fallback examples
- `voice_hotkey/audio.py`: ffmpeg recording and stop-signal lifecycle
- `voice_hotkey/stt.py`: faster-whisper model loading, caching, transcription
- `voice_hotkey/integrations.py`: notifications, paste injection, safe command execution
- `voice_hotkey/config.py`: environment-driven config
- `voice_hotkey/state_utils.py`: state-file helpers

## Features

- UNIX socket daemon for low-latency repeated hotkey calls
- press/release command and dictation flows (`command-start/stop`, `dictate-start/stop`)
- configurable command map loaded from `~/.config/hypr/voice-commands.json` (shell-free argv execution)
- English-only recognition flow (no runtime language switching)
- separate command and dictation STT models (`large-v3-turbo` for both by default)
- cached model loading and background dictation warmup
- optional whisper.cpp server backend via `VOICE_ASR_BACKEND=whispercpp_server`
- wake-word runtime toggles (`wakeword-enable|disable|toggle|status`) with state under `~/.local/state/voice-hotkey-wakeword.json`
- endpointed wake sessions with intent routing (`wake-start`): say `command` or `dictate`, then speak payload
- optional always-on wakeword daemon via `--wakeword-daemon` (openWakeWord)
- Hyprland-native notifications with optional spoken feedback (TTS)

## Dependencies

- required: `ffmpeg`
- optional but recommended: `hyprctl`, `notify-send`, `pamixer`, `wtype`
- optional for clipboard fallback dictation: `wl-copy`
- optional for wake greeting voice: `spd-say` or `espeak`
- optional for spoken feedback (TTS): `spd-say` or `espeak`
- python: `faster-whisper`
- optional python for wakeword daemon: `openwakeword`, `numpy`

Example setup:

```bash
python -m venv ~/.venvs/voice
~/.venvs/voice/bin/pip install -U pip
~/.venvs/voice/bin/pip install faster-whisper
# optional wakeword daemon runtime
~/.venvs/voice/bin/pip install openwakeword numpy
```

## Environment overrides

Values below are code defaults unless explicitly noted; systemd templates may set different recommended overrides.

```bash
export VOICE_COMMAND_MODEL=large-v3-turbo
export VOICE_DICTATE_MODEL=large-v3-turbo
export VOICE_DEVICE="cuda,cpu"
export VOICE_COMPUTE_TYPE=float16
export VOICE_ASR_BACKEND=faster_whisper
export VOICE_WHISPER_SERVER_URL="http://127.0.0.1:8080/inference"
export VOICE_AUDIO_BACKEND=pulse
export VOICE_AUDIO_SOURCE=default
export VOICE_SAMPLE_RATE_HZ=16000
export VOICE_FRAME_MS=20
export VOICE_SESSION_MAX_SECONDS=12
export VOICE_WAKE_SESSION_MAX_SECONDS=8
export VOICE_WAKE_START_SPEECH_TIMEOUT_MS=7000
export VOICE_WAKE_VAD_RMS_THRESHOLD=80
export VOICE_WAKE_VAD_MIN_SPEECH_MS=20
export VOICE_WAKE_VAD_END_SILENCE_MS=300
export VOICE_WAKE_INTENT_VAD_END_SILENCE_MS=700
export VOICE_VAD_RMS_THRESHOLD=600
export VOICE_VAD_MIN_SPEECH_MS=120
export VOICE_VAD_END_SILENCE_MS=800
export VOICE_DICTATION_INJECTOR=wtype
export VOICE_OVERLAY_ENABLED=true
export VOICE_RUNTIME_V2=false
export VOICE_DAEMON_MAX_REQUEST_BYTES=8192
export VOICE_LOG_TRANSCRIPTS=false
export VOICE_LOG_COMMAND_OUTPUT_MAX=300
export VOICE_NOTIFY_TIMEOUT_MS=2200
export VOICE_TTS_ENABLED=false
export VOICE_TTS_COOLDOWN_MS=900
export VOICE_TTS_MAX_CHARS=90
export VOICE_STATE_MAX_AGE_SECONDS=900
export VOICE_WAKEWORD_ENABLED=true
export VOICE_WAKEWORD_MODEL_PATH="$HOME/.config/hypr-voice-controls/wakeword/"
export VOICE_WAKEWORD_MODEL_FILE=""
export VOICE_WAKEWORD_THRESHOLD=0.72
export VOICE_WAKEWORD_MIN_CONSECUTIVE=3
export VOICE_WAKEWORD_COOLDOWN_MS=1500
export VOICE_WAKEWORD_NO_SPEECH_REARM_MS=5000
export VOICE_WAKEWORD_FRAME_MS=40
export VOICE_WAKEWORD_PREROLL_MS=200
export VOICE_WAKE_DAEMON_RESPONSE_TIMEOUT_SECONDS=12
export VOICE_WAKE_GREETING_ENABLED=true
export VOICE_WAKE_GREETING_TEXT="hello"
```

Recommended quality-focused setup:

- use `large-v3-turbo` for both command and dictation models
- keep `VOICE_RUNTIME_V2=false` until the runtime-v2 refactor is fully rolled out

## Wakeword daemon (optional)

Run always-on wake detection (custom model files in `~/.config/hypr-voice-controls/wakeword/`):

```bash
<REPO_DIR>/hvc --wakeword-daemon
```

By default, the wakeword service template sets `VOICE_WAKEWORD_ENABLED=true`.
Disable wake detection at runtime with `wakeword-disable`/`wakeword-toggle`, or set that env var to `false` in your service file.

Wakeword triggers are automatically suppressed while manual hold capture is active (`command-start`/`dictate-start`) to prevent overlap.

Systemd template:

- `examples/systemd/wakeword.service`

### Wake mode selection

When wakeword triggers `wake-start`, explicit leading mode keywords are honored first:

- `command <payload>` (example: `command lock screen`)
- `dictate <payload>` (example: `dictate hello from wake mode`)

Keyword aliases:

- command: `command`, `commands`
- dictation: `dictate`, `dictation`, `write`

If you do not say an explicit intent keyword, wake mode uses a length heuristic:

- 1-3 words -> command path
- 4+ words -> dictation path

Wake prefix variants include `hey hyper`, `hey hypr`, `heyhyper`, and `heyhypr`.

## Private spoken commands (Hypr config)

Define personal phrase->command mappings in `~/.config/hypr/voice-commands.json`.

Format:

```json
[
  {
    "label": "Open Obsidian",
    "pattern": "^((open|launch|start) )?obsidian$",
    "argv": ["hyprctl", "dispatch", "exec", "uwsm-app -- obsidian"]
  }
]
```

Notes:

- commands in this file are matched before local fallback entries in `voice_hotkey/commands.py`
- set `"enabled": false` to disable an entry without deleting it
- the daemon auto-reloads this file when it changes (no restart required)
- default command set lives in `examples/hypr/voice-commands.json` (copy to `~/.config/hypr/voice-commands.json`)

## Hyprland bindings (hold to talk)

Use `bind` for key press and `bindr` for key release.

```conf
# command mode
bind  = SUPER, V, exec, <REPO_DIR>/hvc --input command-start
bindr = SUPER, V, exec, <REPO_DIR>/hvc --input command-stop

# dictation mode
bind  = SUPER SHIFT, V, exec, <REPO_DIR>/hvc --input dictate-start
bindr = SUPER SHIFT, V, exec, <REPO_DIR>/hvc --input dictate-stop

# wake-word runtime toggle (reuses previous language-toggle key)
bind = SUPER, B, exec, <REPO_DIR>/hvc --input wakeword-toggle
```

Reload Hyprland after edits:

```bash
hyprctl reload
```

## systemd user service

Recommended for startup reliability. Active service file:

- `~/.config/systemd/user/voice-hotkey.service`

Service lifecycle:

```bash
systemctl --user daemon-reload
systemctl --user enable --now voice-hotkey.service
systemctl --user restart voice-hotkey.service
systemctl --user status voice-hotkey.service --no-pager
```

Optional runtime health timer (checks queue/worker health every 2 minutes):

```bash
cp <REPO_DIR>/examples/systemd/voice-runtime-health.service ~/.config/systemd/user/
cp <REPO_DIR>/examples/systemd/voice-runtime-health.timer ~/.config/systemd/user/
cp <REPO_DIR>/examples/systemd/voice-runtime-health-notify@.service ~/.config/systemd/user/
# replace <REPO_DIR> inside the copied service file
systemctl --user daemon-reload
systemctl --user enable --now voice-runtime-health.timer
systemctl --user list-timers --all | rg voice-runtime-health
```

The health service triggers a desktop notification on failure via `OnFailure=voice-runtime-health-notify@%n.service`.

Run on demand:

```bash
systemctl --user start voice-runtime-health.service
journalctl --user -u voice-runtime-health.service -n 50 --no-pager
```

Safe failure test (verify health check + notification path):

```bash
# expected to fail because pending is always >= 0
<REPO_DIR>/scripts/runtime-health-check.sh --local --max-pending -1 || true

# manual notify template test
systemctl --user start "voice-runtime-health-notify@voice-runtime-health.service"
```

Wayland env sync (recommended for reboot/login reliability):

```bash
systemctl --user import-environment WAYLAND_DISPLAY DISPLAY XDG_RUNTIME_DIR DBUS_SESSION_BUS_ADDRESS HYPRLAND_INSTANCE_SIGNATURE
systemctl --user restart voice-hotkey.service
```

Persistent Omarchy/Hyprland startup hook:

```conf
# ~/.config/hypr/autostart.conf
exec-once = dbus-update-activation-environment --systemd --all
exec-once = systemctl --user import-environment WAYLAND_DISPLAY DISPLAY XDG_RUNTIME_DIR DBUS_SESSION_BUS_ADDRESS HYPRLAND_INSTANCE_SIGNATURE
exec-once = systemctl --user restart voice-hotkey.service
```

## Manual smoke tests

List all callable input actions (debug helper):

```bash
<REPO_DIR>/hvc --list-actions
<REPO_DIR>/hvc --describe-action wake-start
```

Run an action locally (without daemon RPC) for debugging:

```bash
<REPO_DIR>/hvc --input wakeword-status --local
<REPO_DIR>/hvc --input runtime-status-json --local
```

Run daemon-mode runtime-v2 acceptance checks (shared queue path):

```bash
<REPO_DIR>/scripts/runtime_v2_acceptance.py
```

Scan or rescan audio devices when headset/mic routing changes:

```bash
<REPO_DIR>/hvc --list-audio
<REPO_DIR>/hvc --rescan-audio
# shorthand alias
<REPO_DIR>/hvc --restart-audio
```

Reset voice services and clear stale runtime state:

```bash
<REPO_DIR>/hvc --reset
```

If your launcher lives outside this repo layout, set `VOICE_RESET_SCRIPT` to an explicit script path.

```bash
<REPO_DIR>/hvc --input command-start
sleep 1
<REPO_DIR>/hvc --input command-stop
```

```bash
<REPO_DIR>/hvc --input dictate-start
sleep 1
<REPO_DIR>/hvc --input dictate-stop
```

```bash
<REPO_DIR>/hvc --input wakeword-status
<REPO_DIR>/hvc --input wakeword-toggle
<REPO_DIR>/hvc --input command-auto
```

```bash
rg "Voice hotkey end status|Input source|Dictation hold|Paste attempt" ~/.local/state/voice-hotkey.log
```

## Troubleshooting

- `No speech detected`: verify mic battery/power, mute state, and selected source
- daemon not responding: `systemctl --user restart voice-hotkey.service`
- missing command actions: verify `~/.config/hypr/voice-commands.json` regex patterns and command argv values
- paste failures (`Clipboard write failed rc=1`): import Wayland vars into user systemd, then restart service:

```bash
systemctl --user import-environment WAYLAND_DISPLAY DISPLAY XDG_RUNTIME_DIR DBUS_SESSION_BUS_ADDRESS HYPRLAND_INSTANCE_SIGNATURE
systemctl --user restart voice-hotkey.service
```

## Development notes

- run syntax checks after edits:

```bash
python3 -m py_compile hvc voice-hotkey.py voice_hotkey/*.py
```

- run the full pre-release gate locally:

```bash
<REPO_DIR>/scripts/pre_release_checks.sh
# optional: skip daemon acceptance on constrained environments
<REPO_DIR>/scripts/pre_release_checks.sh --skip-acceptance
```

- CI runs syntax checks + unit tests via `.github/workflows/ci.yml`.
