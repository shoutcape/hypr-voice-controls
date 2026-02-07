#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPO_SCRIPT="$REPO_ROOT/live/.local/bin/voice-hotkey.py"
LIVE_SCRIPT="$HOME/.local/bin/voice-hotkey.py"
REPO_NOTE="$REPO_ROOT/live/Documents/Obsidian Notes/Research/OpenCode/Voice Commands/getting-started.md"
LIVE_NOTE="$HOME/Documents/Obsidian Notes/Research/OpenCode/Voice Commands/getting-started.md"

link_to_live() {
  local src="$1"
  local dst="$2"
  mkdir -p "$(dirname "$dst")"
  ln -sfn "$src" "$dst"
  printf 'linked: %s -> %s\n' "$dst" "$src"
}

mkdir -p "$(dirname "$LIVE_SCRIPT")"
chmod +x "$REPO_SCRIPT"
link_to_live "$REPO_SCRIPT" "$LIVE_SCRIPT"
link_to_live "$REPO_NOTE" "$LIVE_NOTE"

printf 'restore complete\n'
