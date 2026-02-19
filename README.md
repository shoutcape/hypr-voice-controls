# Hypr Voice Controls (Hotkey Only)

<!-- Responsibility: Primary setup and operations guide for the hotkey-only voice control stack. -->

This project now supports only press/release hotkeys:

- `command-start` / `command-stop`
- `dictate-start` / `dictate-stop`

Use `python -m voice_controls` as the launcher entrypoint.

## Dependencies

- required: `ffmpeg`
- recommended: `notify-send`, `wl-copy`, `hyprctl`
- python: `faster-whisper`

## Setup

```bash
python -m venv ~/.venvs/voice
~/.venvs/voice/bin/pip install -U pip
~/.venvs/voice/bin/pip install faster-whisper
PYTHON_BIN=~/.venvs/voice/bin/python
```

## Hyprland bindings

```conf
# command mode
bind  = SUPER, V, exec, env PYTHONPATH=<REPO_DIR> <PYTHON_BIN> -m voice_controls --input command-start
bindr = SUPER, V, exec, env PYTHONPATH=<REPO_DIR> <PYTHON_BIN> -m voice_controls --input command-stop

# dictation mode
bind  = SUPER SHIFT, V, exec, env PYTHONPATH=<REPO_DIR> <PYTHON_BIN> -m voice_controls --input dictate-start
bindr = SUPER SHIFT, V, exec, env PYTHONPATH=<REPO_DIR> <PYTHON_BIN> -m voice_controls --input dictate-stop
```

`examples/hypr/voice-commands.json` is the example spoken-command mapping file (`pattern` -> `argv`) loaded from `~/.config/hypr/voice-commands.json`.

## Systemd user service

Template: `examples/systemd/voice-hotkey.service`

```bash
systemctl --user daemon-reload
systemctl --user enable --now voice-hotkey.service
systemctl --user restart voice-hotkey.service
systemctl --user status voice-hotkey.service --no-pager
```

## Manual smoke test

```bash
PYTHONPATH=<REPO_DIR> <PYTHON_BIN> -m voice_controls --input command-start
sleep 1
PYTHONPATH=<REPO_DIR> <PYTHON_BIN> -m voice_controls --input command-stop

PYTHONPATH=<REPO_DIR> <PYTHON_BIN> -m voice_controls --input dictate-start
sleep 1
PYTHONPATH=<REPO_DIR> <PYTHON_BIN> -m voice_controls --input dictate-stop
```

## Live hotkey e2e test

This sends real key events via `ydotool` and verifies daemon request/response log activity.

```bash
./scripts/live_hotkey_e2e.sh         # defaults to keycodes 186, 187 (F16/F17)
./scripts/live_hotkey_e2e.sh 186 187 # explicit keycodes
```
