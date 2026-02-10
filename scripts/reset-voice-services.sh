#!/usr/bin/env bash
set -euo pipefail

STATE_DIR="$HOME/.local/state"

systemctl --user stop wakeword.service voice-hotkey.service || true

pkill -f "voice-hotkey.py --wakeword-daemon" || true
pkill -f "voice-hotkey.py --daemon" || true

rm -f "$STATE_DIR/voice-hotkey.sock"
rm -f "$STATE_DIR/voice-hotkey.lock"
rm -f "$STATE_DIR/voice-hotkey-command.json"
rm -f "$STATE_DIR/voice-hotkey-dictate.json"

systemctl --user daemon-reload
systemctl --user start voice-hotkey.service
systemctl --user start wakeword.service

systemctl --user status voice-hotkey.service --no-pager --lines=8
systemctl --user status wakeword.service --no-pager --lines=8
