# Hypr Voice Controls (Hotkey Only)

This project now supports only press/release hotkeys:

- `command-start` / `command-stop`
- `dictate-start` / `dictate-stop`

## Dependencies

- required: `ffmpeg`
- recommended: `notify-send`, `wl-copy`, `hyprctl`
- python: `faster-whisper`

## Setup

```bash
python -m venv ~/.venvs/voice
~/.venvs/voice/bin/pip install -U pip
~/.venvs/voice/bin/pip install faster-whisper
```

## Hyprland bindings

```conf
# command mode
bind  = SUPER, V, exec, <REPO_DIR>/hvc --input command-start
bindr = SUPER, V, exec, <REPO_DIR>/hvc --input command-stop

# dictation mode
bind  = SUPER SHIFT, V, exec, <REPO_DIR>/hvc --input dictate-start
bindr = SUPER SHIFT, V, exec, <REPO_DIR>/hvc --input dictate-stop
```

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
<REPO_DIR>/hvc --input command-start
sleep 1
<REPO_DIR>/hvc --input command-stop

<REPO_DIR>/hvc --input dictate-start
sleep 1
<REPO_DIR>/hvc --input dictate-stop
```

## Live hotkey e2e test

This sends real key events via `ydotool` and verifies daemon request/response log activity.

```bash
./scripts/live_hotkey_e2e.sh         # defaults to keycodes 186, 187 (F16/F17)
./scripts/live_hotkey_e2e.sh 186 187 # explicit keycodes
```
