# Getting Started

This guide sets up Hypr Voice Controls from scratch with the current architecture in this repo.

## What you get

- Hold-to-command: press key to record, release to transcribe and run an allowlisted action.
- Hold-to-dictate: press key to record, release to transcribe and paste text.
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

## 3) Configure Hyprland binds (press/release)

Create a user override file:

```bash
mkdir -p ~/.config/hypr/conf.d
$EDITOR ~/.config/hypr/conf.d/voice-hotkey.conf
```

Add:

```conf
# Voice command: hold SUPER+V, release to execute
bind  = SUPER, V, exec, /home/shoutcape/Github/hypr-voice-controls/voice-hotkey.py --input command-start
bindr = SUPER, V, exec, /home/shoutcape/Github/hypr-voice-controls/voice-hotkey.py --input command-stop

# Dictation: hold SUPER+SHIFT+V, release to paste
bind  = SUPER SHIFT, V, exec, /home/shoutcape/Github/hypr-voice-controls/voice-hotkey.py --input dictate-start
bindr = SUPER SHIFT, V, exec, /home/shoutcape/Github/hypr-voice-controls/voice-hotkey.py --input dictate-stop

# Toggle dictation language (fi/en)
bind = SUPER, B, exec, /home/shoutcape/Github/hypr-voice-controls/voice-hotkey.py --input dictate-language
```

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

Use:

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

[Install]
WantedBy=default.target
```

Enable and start:

```bash
systemctl --user daemon-reload
systemctl --user enable --now voice-hotkey.service
systemctl --user status voice-hotkey.service --no-pager
```

## 5) Quick verification

Manual smoke test:

```bash
/home/shoutcape/Github/hypr-voice-controls/voice-hotkey.py --input command-start
sleep 1
/home/shoutcape/Github/hypr-voice-controls/voice-hotkey.py --input command-stop
```

Check logs:

```bash
rg "Voice hotkey end status|Input source|Dictation hold|Paste attempt" ~/.local/state/voice-hotkey.log
```

## 6) Supported command actions

Current allowlist includes:

- workspace 1/2 (`hyprctl dispatch workspace 1|2`)
- volume up/down (`pamixer -i 5` / `pamixer -d 5`)
- lock screen (`loginctl lock-session`)

If speech does not match this allowlist, the command path intentionally does nothing.

## 7) Useful environment overrides

You can tune behavior via env vars in the service file:

```ini
Environment=VOICE_COMMAND_MODEL=tiny
Environment=VOICE_DICTATE_MODEL=medium
Environment=VOICE_DEVICE=cuda,cpu
Environment=VOICE_COMPUTE_TYPE=float16
Environment=VOICE_MAX_HOLD_SECONDS=15
Environment=VOICE_DAEMON_START_DELAY=0.05
```

After edits:

```bash
systemctl --user daemon-reload
systemctl --user restart voice-hotkey.service
```

## Troubleshooting

- `No speech detected`: check microphone battery, mute state, and active source.
- `Voice daemon unavailable`: restart service and verify socket at `~/.local/state/voice-hotkey.sock`.
- Paste failures: verify `wl-copy` is installed and Hyprland is active in the current session.
- Model load errors on GPU: verify your CUDA/cuDNN runtime setup for `faster-whisper`.

## Upgrade workflow

After pulling repo changes:

```bash
cd /home/shoutcape/Github/hypr-voice-controls
systemctl --user restart voice-hotkey.service
systemctl --user status voice-hotkey.service --no-pager
```
