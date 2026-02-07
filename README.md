# Hypr Voice Controls

Voice hotkey daemon for Hyprland with two paths:

- hold-to-command: transcribe speech and execute an allowlisted desktop action
- hold-to-dictate: transcribe speech and paste text into the focused app

This repo is the canonical source. Hyprland binds and the user service should point to this checkout.

## Runtime architecture

- `voice-hotkey.py`: stable compatibility entrypoint
- `voice_hotkey/app.py`: CLI modes, daemon client/server flow, orchestration
- `voice_hotkey/commands.py`: normalization, regex allowlist, fuzzy fallbacks
- `voice_hotkey/audio.py`: ffmpeg recording and stop-signal lifecycle
- `voice_hotkey/stt.py`: faster-whisper model loading, caching, transcription
- `voice_hotkey/integrations.py`: notifications, paste injection, safe command execution
- `voice_hotkey/config.py`: environment-driven config
- `voice_hotkey/state_utils.py`: language/state-file helpers

## Features

- UNIX socket daemon for low-latency repeated hotkey calls
- press/release command and dictation flows (`command-start/stop`, `dictate-start/stop`)
- explicit command allowlist (workspace, volume, lock) with fixed argv execution
- separate command and dictation STT models (`tiny` and `medium` by default)
- cached model loading and background dictation warmup
- language toggle (`fi`/`en`) persisted under `~/.local/state/voice-hotkey-language`

## Dependencies

- required: `ffmpeg`
- optional but recommended: `hyprctl`, `wl-copy`, `notify-send`, `zenity`, `pamixer`
- python: `faster-whisper`

Example setup:

```bash
python -m venv ~/.venvs/voice
~/.venvs/voice/bin/pip install -U pip
~/.venvs/voice/bin/pip install faster-whisper
```

## Environment overrides

```bash
export VOICE_COMMAND_MODEL=tiny
export VOICE_DICTATE_MODEL=medium
export VOICE_DEVICE="cuda,cpu"
export VOICE_COMPUTE_TYPE=float16
export VOICE_AUDIO_BACKEND=pulse
export VOICE_AUDIO_SOURCE=default
export VOICE_MAX_HOLD_SECONDS=15
```

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
rg "Voice hotkey end status|Input source|Dictation hold|Paste attempt" ~/.local/state/voice-hotkey.log
```

## Troubleshooting

- `No speech detected`: verify mic battery/power, mute state, and selected source
- daemon not responding: `systemctl --user restart voice-hotkey.service`
- missing command actions: check allowlist matching in `voice_hotkey/commands.py`
- paste failures: ensure `wl-copy` and Hyprland `sendshortcut` are available

## Development notes

- run syntax checks after edits:

```bash
python3 -m py_compile voice-hotkey.py voice_hotkey/*.py
```
