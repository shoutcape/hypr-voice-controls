# Getting Started

This guide sets up Hypr Voice Controls from scratch with the current architecture in this repo.

## What you get

- Hold-to-command: press key to record, release to transcribe and run a configured action.
- Hold-to-dictate: press key to record, release to transcribe and paste text.
- Hold duration is controlled by key release (no fixed max hold timeout).
- A user-level daemon managed by systemd for reliable startup.

## 1) Install dependencies

On Arch/Omarchy:

```bash
sudo pacman -S --needed ffmpeg pamixer libnotify wl-clipboard
sudo pacman -S --needed zenity
```

Python environment:

```bash
python -m venv ~/.venvs/voice
~/.venvs/voice/bin/pip install -U pip
~/.venvs/voice/bin/pip install faster-whisper
```

## 2) Verify repository path

This guide assumes the repo is here:

`/home/shoutcape/Github/hypr-voice-controls`

If your path differs, update the keybind and service paths below accordingly.

Template files are available under:

- `examples/hypr/voice-hotkey.bindings.conf`
- `examples/systemd/voice-hotkey.service`
- `examples/hypr/voice-hotkey.autostart.conf`
- `examples/hypr/voice-commands.json`

## 3) Configure Hyprland binds (press/release)

On Omarchy, add binds directly to your user overrides file (or copy from `examples/hypr/voice-hotkey.bindings.conf`):

```bash
$EDITOR ~/.config/hypr/bindings.conf
```

Add:

```conf
# Voice command: hold SUPER+V, release to execute
bind  = SUPER, V, exec, /home/shoutcape/Github/hypr-voice-controls/voice-hotkey.py --input command-start
bindr = SUPER, V, exec, /home/shoutcape/Github/hypr-voice-controls/voice-hotkey.py --input command-stop

# Dictation: hold SUPER+SHIFT+V, release to paste
bind  = SUPER SHIFT, V, exec, /home/shoutcape/Github/hypr-voice-controls/voice-hotkey.py --input dictate-start
bindr = SUPER SHIFT, V, exec, /home/shoutcape/Github/hypr-voice-controls/voice-hotkey.py --input dictate-stop

# Toggle wake-word listener state
bind = SUPER, B, exec, /home/shoutcape/Github/hypr-voice-controls/voice-hotkey.py --input wakeword-toggle
```

If you prefer a separate file under `~/.config/hypr/conf.d/`, make sure your `~/.config/hypr/hyprland.conf` explicitly sources it.

Reload Hyprland:

```bash
hyprctl reload
```

## 4) Create and enable the user service

Create service file:

```bash
mkdir -p ~/.config/systemd/user
$EDITOR ~/.config/systemd/user/voice-hotkey.service
```

Use (or start from `examples/systemd/voice-hotkey.service`):

```ini
[Unit]
Description=Voice hotkey daemon (Whisper + Hyprland)
After=graphical-session.target

[Service]
Type=simple
ExecStart=%h/.venvs/voice/bin/python %h/Github/hypr-voice-controls/voice-hotkey.py --daemon
Restart=on-failure
RestartSec=1
Environment=VOICE_AUDIO_BACKEND=pulse
Environment=VOICE_AUDIO_SOURCE=default
Environment=VOICE_COMMAND_MODEL_EN=small.en
Environment=VOICE_COMMAND_MODEL_FI=small
Environment=VOICE_DICTATE_MODEL_EN=medium.en
Environment=VOICE_DICTATE_MODEL_FI=medium

[Install]
WantedBy=default.target
```

Enable and start:

```bash
systemctl --user daemon-reload
systemctl --user enable --now voice-hotkey.service
systemctl --user status voice-hotkey.service --no-pager
```

Sync current Wayland session vars into user systemd (recommended):

```bash
systemctl --user import-environment WAYLAND_DISPLAY DISPLAY XDG_RUNTIME_DIR DBUS_SESSION_BUS_ADDRESS HYPRLAND_INSTANCE_SIGNATURE
systemctl --user restart voice-hotkey.service
```

Make it persistent across reboot/login by adding this to `~/.config/hypr/autostart.conf` (or copy from `examples/hypr/voice-hotkey.autostart.conf`):

```conf
exec-once = dbus-update-activation-environment --systemd --all
exec-once = systemctl --user import-environment WAYLAND_DISPLAY DISPLAY XDG_RUNTIME_DIR DBUS_SESSION_BUS_ADDRESS HYPRLAND_INSTANCE_SIGNATURE
exec-once = systemctl --user restart voice-hotkey.service
```

## 5) Quick verification

Manual smoke test (command path):

```bash
/home/shoutcape/Github/hypr-voice-controls/voice-hotkey.py --input command-start
sleep 1
/home/shoutcape/Github/hypr-voice-controls/voice-hotkey.py --input command-stop
```

Manual smoke test (dictation path):

```bash
/home/shoutcape/Github/hypr-voice-controls/voice-hotkey.py --input dictate-start
sleep 1
/home/shoutcape/Github/hypr-voice-controls/voice-hotkey.py --input dictate-stop
```

Check logs:

```bash
rg "Voice hotkey end status|Input source|Dictation hold|Paste attempt" ~/.local/state/voice-hotkey.log
```

## 6) Supported command actions

The command path uses your JSON command map (`~/.config/hypr/voice-commands.json`).

The default example file includes actions like:

- workspace 1/2 (`hyprctl dispatch workspace 1|2`)
- volume up/down (`pamixer -i 5` / `pamixer -d 5`)
- lock screen (`loginctl lock-session`)

If speech does not match any configured pattern, the command path intentionally does nothing.

Optional private overrides:

- Create `~/.config/hypr/voice-commands.json` from `examples/hypr/voice-commands.json`.
- Entries in this file are matched first.
- `voice_hotkey/commands.py` contains optional local fallback examples if you prefer code-defined commands.
- File changes are auto-reloaded by the daemon.

## 7) Useful environment overrides

You can tune behavior via env vars in the service file:

```ini
Environment=VOICE_COMMAND_MODEL=small
Environment=VOICE_DICTATE_MODEL=medium
Environment=VOICE_COMMAND_MODEL_EN=small.en
Environment=VOICE_COMMAND_MODEL_FI=small
Environment=VOICE_DICTATE_MODEL_EN=medium.en
Environment=VOICE_DICTATE_MODEL_FI=medium
Environment=VOICE_DEVICE=cuda,cpu
Environment=VOICE_COMPUTE_TYPE=float16
Environment=VOICE_DAEMON_START_DELAY=0.05
Environment=VOICE_DAEMON_MAX_REQUEST_BYTES=8192
Environment=VOICE_LOG_TRANSCRIPTS=false
Environment=VOICE_LOG_COMMAND_OUTPUT_MAX=300
Environment=VOICE_STATE_MAX_AGE_SECONDS=900
```

After edits:

```bash
systemctl --user daemon-reload
systemctl --user restart voice-hotkey.service
```

## Troubleshooting

- `No speech detected`: check microphone battery, mute state, and active source.
- `Voice daemon unavailable`: restart service and verify socket at `~/.local/state/voice-hotkey.sock`.
- Paste failures (`Clipboard write failed rc=1`): import Wayland vars into user systemd and restart service:

```bash
systemctl --user import-environment WAYLAND_DISPLAY DISPLAY XDG_RUNTIME_DIR DBUS_SESSION_BUS_ADDRESS HYPRLAND_INSTANCE_SIGNATURE
systemctl --user restart voice-hotkey.service
```
- Model load errors on GPU: verify your CUDA/cuDNN runtime setup for `faster-whisper`.

## Upgrade workflow

After pulling repo changes:

```bash
cd /home/shoutcape/Github/hypr-voice-controls
systemctl --user restart voice-hotkey.service
systemctl --user status voice-hotkey.service --no-pager
```
