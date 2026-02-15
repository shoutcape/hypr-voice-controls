#!/usr/bin/env bash
set -euo pipefail

STATE_DIR="$HOME/.local/state"

systemctl --user stop wakeword.service voice-hotkey.service || true

pkill -f "voice-hotkey.py --wakeword-daemon" || true
pkill -f "voice-hotkey.py --daemon" || true
pkill -f "hvc --wakeword-daemon" || true
pkill -f "hvc --daemon" || true

rm -f "$STATE_DIR/voice-hotkey.sock"
rm -f "$STATE_DIR/voice-hotkey.lock"
rm -f "$STATE_DIR/voice-hotkey-command.json"
rm -f "$STATE_DIR/voice-hotkey-dictate.json"

systemctl --user daemon-reload
systemctl --user start voice-hotkey.service
if systemctl --user cat wakeword.service >/dev/null 2>&1; then
  systemctl --user start wakeword.service
else
  echo "wakeword.service not installed; skipping wakeword restart"
fi

systemctl --user status voice-hotkey.service --no-pager --lines=8
if systemctl --user cat wakeword.service >/dev/null 2>&1; then
  systemctl --user status wakeword.service --no-pager --lines=8
fi
