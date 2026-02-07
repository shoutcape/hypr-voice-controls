#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

copy_to_live() {
  local rel="$1"
  local dst="$2"
  local src="$REPO_ROOT/live/$rel"

  mkdir -p "$(dirname "$dst")"
  cp "$src" "$dst"
  printf 'restored: %s\n' "$dst"
}

copy_to_live ".local/bin/voice-hotkey.py" "$HOME/.local/bin/voice-hotkey.py"
copy_to_live ".config/hypr/bindings.conf" "$HOME/.config/hypr/bindings.conf"
copy_to_live "Documents/Obsidian Notes/Research/OpenCode/Voice Commands/getting-started.md" "$HOME/Documents/Obsidian Notes/Research/OpenCode/Voice Commands/getting-started.md"

printf 'restore complete\n'
