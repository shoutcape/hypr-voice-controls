#!/usr/bin/env bash
set -euo pipefail

LOG_FILE="${1:-$HOME/.local/state/voice-hotkey.log}"

mkdir -p "$(dirname "$LOG_FILE")"
touch "$LOG_FILE"

exec tail -n 200 -f "$LOG_FILE"
