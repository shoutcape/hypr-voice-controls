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
- `voice_hotkey/state_utils.py`: language/state-file helpers

## Features

- UNIX socket daemon for low-latency repeated hotkey calls
- press/release command and dictation flows (`command-start/stop`, `dictate-start/stop`)
- configurable command map loaded from `~/.config/hypr/voice-commands.json` (shell-free argv execution)
- separate command and dictation STT models with per-language overrides (`small.en`/`small` and `medium.en`/`medium` by default)
- cached model loading and background dictation warmup
- language toggle (`fi`/`en`) persisted under `~/.local/state/voice-hotkey-language`
- optional whisper.cpp server backend via `VOICE_ASR_BACKEND=whispercpp_server`
- wake-word runtime toggles (`wakeword-enable|disable|toggle|status`) with state under `~/.local/state/voice-hotkey-wakeword.json`

## Dependencies

- required: `ffmpeg`
- optional but recommended: `hyprctl`, `notify-send`, `zenity`, `pamixer`, `wtype`
- optional for clipboard fallback dictation: `wl-copy`
- optional for wake greeting voice: `spd-say` or `espeak`
- python: `faster-whisper`

Example setup:

```bash
python -m venv ~/.venvs/voice
~/.venvs/voice/bin/pip install -U pip
~/.venvs/voice/bin/pip install faster-whisper
```

## Environment overrides

```bash
export VOICE_COMMAND_MODEL=small
export VOICE_DICTATE_MODEL=medium
export VOICE_COMMAND_MODEL_EN=small.en
export VOICE_COMMAND_MODEL_FI=small
export VOICE_DICTATE_MODEL_EN=medium.en
export VOICE_DICTATE_MODEL_FI=medium
export VOICE_DEVICE="cuda,cpu"
export VOICE_COMPUTE_TYPE=float16
export VOICE_ASR_BACKEND=faster_whisper
export VOICE_WHISPER_SERVER_URL="http://127.0.0.1:8080/inference"
export VOICE_AUDIO_BACKEND=pulse
export VOICE_AUDIO_SOURCE=default
export VOICE_DICTATION_INJECTOR=wtype
export VOICE_OVERLAY_ENABLED=true
export VOICE_DAEMON_MAX_REQUEST_BYTES=8192
export VOICE_LOG_TRANSCRIPTS=false
export VOICE_LOG_COMMAND_OUTPUT_MAX=300
export VOICE_STATE_MAX_AGE_SECONDS=900
export VOICE_WAKEWORD_ENABLED=false
export VOICE_WAKEWORD_MODEL_PATH="$HOME/.config/hypr-voice-controls/wakeword/"
export VOICE_WAKE_GREETING_ENABLED=true
export VOICE_WAKE_GREETING_TEXT="hello"
```

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
bind  = SUPER, V, exec, /home/shoutcape/Github/hypr-voice-controls/voice-hotkey.py --input command-start
bindr = SUPER, V, exec, /home/shoutcape/Github/hypr-voice-controls/voice-hotkey.py --input command-stop

# dictation mode
bind  = SUPER SHIFT, V, exec, /home/shoutcape/Github/hypr-voice-controls/voice-hotkey.py --input dictate-start
bindr = SUPER SHIFT, V, exec, /home/shoutcape/Github/hypr-voice-controls/voice-hotkey.py --input dictate-stop

# language toggle
bind = SUPER, B, exec, /home/shoutcape/Github/hypr-voice-controls/voice-hotkey.py --input dictate-language

# wake-word runtime toggle
bind = SUPER, N, exec, /home/shoutcape/Github/hypr-voice-controls/voice-hotkey.py --input wakeword-toggle
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
/home/shoutcape/Github/hypr-voice-controls/voice-hotkey.py --input command-start
sleep 1
/home/shoutcape/Github/hypr-voice-controls/voice-hotkey.py --input command-stop
```

```bash
/home/shoutcape/Github/hypr-voice-controls/voice-hotkey.py --input dictate-start
sleep 1
/home/shoutcape/Github/hypr-voice-controls/voice-hotkey.py --input dictate-stop
```

```bash
/home/shoutcape/Github/hypr-voice-controls/voice-hotkey.py --input wakeword-status
/home/shoutcape/Github/hypr-voice-controls/voice-hotkey.py --input wakeword-toggle
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
