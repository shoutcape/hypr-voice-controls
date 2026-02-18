# Getting Started (Hotkey Only)

## 1) Install dependencies

```bash
sudo pacman -S --needed ffmpeg pamixer libnotify wl-clipboard
python -m venv ~/.venvs/voice
~/.venvs/voice/bin/pip install -U pip
~/.venvs/voice/bin/pip install faster-whisper
```

## 2) Add Hyprland binds

```conf
bind  = SUPER, V, exec, <REPO_DIR>/hvc --input command-start
bindr = SUPER, V, exec, <REPO_DIR>/hvc --input command-stop

bind  = SUPER SHIFT, V, exec, <REPO_DIR>/hvc --input dictate-start
bindr = SUPER SHIFT, V, exec, <REPO_DIR>/hvc --input dictate-stop
```

Reload:

```bash
hyprctl reload
```

## 3) Enable daemon

```bash
mkdir -p ~/.config/systemd/user
cp <REPO_DIR>/examples/systemd/voice-hotkey.service ~/.config/systemd/user/voice-hotkey.service
# Edit <REPO_DIR> in the copied file
systemctl --user daemon-reload
systemctl --user enable --now voice-hotkey.service
```

## 4) Verify

```bash
<REPO_DIR>/hvc --input command-start
sleep 1
<REPO_DIR>/hvc --input command-stop

<REPO_DIR>/hvc --input dictate-start
sleep 1
<REPO_DIR>/hvc --input dictate-stop
```
