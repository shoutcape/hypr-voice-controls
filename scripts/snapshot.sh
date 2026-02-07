#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

copy_into_repo() {
  local src="$1"
  local rel="$2"
  local dst="$REPO_ROOT/live/$rel"

  mkdir -p "$(dirname "$dst")"
  cp "$src" "$dst"
  printf 'snapshotted: %s\n' "$src"
}

copy_into_repo "$HOME/.local/bin/voice-hotkey.py" ".local/bin/voice-hotkey.py"
copy_into_repo "$HOME/.config/hypr/bindings.conf" ".config/hypr/bindings.conf"
copy_into_repo "$HOME/Documents/Obsidian Notes/Research/OpenCode/Voice Commands/getting-started.md" "Documents/Obsidian Notes/Research/OpenCode/Voice Commands/getting-started.md"

printf 'snapshot complete\n'
