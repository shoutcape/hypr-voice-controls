#!/usr/bin/env bash
set -euo pipefail

STATE_DIR="$HOME/.local/state"

systemctl --user stop voice-hotkey.service || true

pkill -f "voice-hotkey.py --daemon" || true
pkill -f "hvc --daemon" || true

rm -f "$STATE_DIR/voice-hotkey.sock"
rm -f "$STATE_DIR/voice-hotkey.lock"
rm -f "$STATE_DIR/voice-hotkey-command.json"
rm -f "$STATE_DIR/voice-hotkey-dictate.json"

systemctl --user daemon-reload
systemctl --user start voice-hotkey.service

systemctl --user status voice-hotkey.service --no-pager --lines=8
