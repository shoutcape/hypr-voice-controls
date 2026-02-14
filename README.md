# Hypr Voice Controls

Voice hotkey daemon for Hyprland with two paths:

- hold-to-command: transcribe speech and execute a configured desktop action
- hold-to-dictate: transcribe speech and paste text into the focused app
- press/hold capture runs until key release (no fixed max hold timeout)

This repo is the canonical source. Hyprland binds and the user service should point to this checkout.

## Config templates

Use repo examples instead of committing personal desktop config:

- `examples/hypr/voice-hotkey.bindings.conf`
- `examples/systemd/voice-hotkey.service`
- `examples/hypr/voice-hotkey.autostart.conf`
- `examples/hypr/voice-commands.json`

Replace `<REPO_DIR>` in templates with your local checkout path, then copy the lines into:

- `~/.config/hypr/bindings.conf`
- `~/.config/systemd/user/voice-hotkey.service`
- `~/.config/hypr/autostart.conf`

For private spoken-command definitions, copy `examples/hypr/voice-commands.json` to `~/.config/hypr/voice-commands.json`.

## Runtime architecture

- `voice-hotkey.py`: stable compatibility entrypoint
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
export VOICE_WAKE_DICTATE_SESSION_MAX_SECONDS=16
export VOICE_WAKE_START_SPEECH_TIMEOUT_MS=7000
export VOICE_WAKE_VAD_RMS_THRESHOLD=80
export VOICE_WAKE_VAD_MIN_SPEECH_MS=20
export VOICE_WAKE_VAD_END_SILENCE_MS=300
export VOICE_WAKE_INTENT_VAD_END_SILENCE_MS=700
export VOICE_WAKE_DICTATE_VAD_END_SILENCE_MS=1800
export VOICE_VAD_RMS_THRESHOLD=600
export VOICE_VAD_MIN_SPEECH_MS=120
export VOICE_VAD_END_SILENCE_MS=800
export VOICE_DICTATION_INJECTOR=wtype
export VOICE_OVERLAY_ENABLED=true
export VOICE_DAEMON_MAX_REQUEST_BYTES=8192
export VOICE_LOG_TRANSCRIPTS=false
export VOICE_LOG_COMMAND_OUTPUT_MAX=300
export VOICE_NOTIFY_TIMEOUT_MS=2200
export VOICE_TTS_ENABLED=false
export VOICE_TTS_COOLDOWN_MS=900
export VOICE_TTS_MAX_CHARS=90
export VOICE_STATE_MAX_AGE_SECONDS=900
export VOICE_WAKEWORD_ENABLED=false
export VOICE_WAKEWORD_MODEL_PATH="$HOME/.config/hypr-voice-controls/wakeword/"
export VOICE_WAKEWORD_MODEL_FILE=""
export VOICE_WAKEWORD_THRESHOLD=0.72
export VOICE_WAKEWORD_MIN_CONSECUTIVE=3
export VOICE_WAKEWORD_COOLDOWN_MS=1500
export VOICE_WAKEWORD_NO_SPEECH_REARM_MS=5000
export VOICE_WAKEWORD_FRAME_MS=40
export VOICE_WAKEWORD_PREROLL_MS=200
export VOICE_WAKE_GREETING_ENABLED=true
export VOICE_WAKE_GREETING_TEXT="hello"
```

Recommended quality-focused setup:

- use `large-v3-turbo` for both command and dictation models

## Wakeword daemon (optional)

Run always-on wake detection (custom model files in `~/.config/hypr-voice-controls/wakeword/`):

```bash
<REPO_DIR>/voice-hotkey.py --wakeword-daemon
```

By default, the wakeword service template sets `VOICE_WAKEWORD_ENABLED=false`.
Enable wake detection at runtime with `wakeword-enable`/`wakeword-toggle`, or set that env var to `true` in your service file.

Wakeword triggers are automatically suppressed while manual hold capture is active (`command-start`/`dictate-start`) to prevent overlap.

Systemd template:

- `examples/systemd/wakeword.service`

### Wake mode selection

When wakeword triggers `wake-start`, the daemon now runs a short intent capture first:

- say `command` to run command mode
- say `dictate`, `dictation`, or `write` for dictation mode
- if intent is unclear, it falls back to command matching (backward-compatible)

You can also do single-shot wake utterances to skip the follow-up capture:

- `command <payload>` (example: `command lock screen`)
- `dictate <payload>` (example: `dictate hello from wake mode`)

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
bind  = SUPER, V, exec, <REPO_DIR>/voice-hotkey.py --input command-start
bindr = SUPER, V, exec, <REPO_DIR>/voice-hotkey.py --input command-stop

# dictation mode
bind  = SUPER SHIFT, V, exec, <REPO_DIR>/voice-hotkey.py --input dictate-start
bindr = SUPER SHIFT, V, exec, <REPO_DIR>/voice-hotkey.py --input dictate-stop

# wake-word runtime toggle (reuses previous language-toggle key)
bind = SUPER, B, exec, <REPO_DIR>/voice-hotkey.py --input wakeword-toggle
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

```bash
<REPO_DIR>/voice-hotkey.py --input command-start
sleep 1
<REPO_DIR>/voice-hotkey.py --input command-stop
```

```bash
<REPO_DIR>/voice-hotkey.py --input dictate-start
sleep 1
<REPO_DIR>/voice-hotkey.py --input dictate-stop
```

```bash
<REPO_DIR>/voice-hotkey.py --input wakeword-status
<REPO_DIR>/voice-hotkey.py --input wakeword-toggle
<REPO_DIR>/voice-hotkey.py --input command-auto
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
python3 -m py_compile voice-hotkey.py voice_hotkey/*.py
```
