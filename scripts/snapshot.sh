#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPO_SCRIPT="$REPO_ROOT/live/.local/bin/voice-hotkey.py"
LIVE_SCRIPT="$HOME/.local/bin/voice-hotkey.py"
REPO_NOTE="$REPO_ROOT/live/Documents/Obsidian Notes/Research/OpenCode/Voice Commands/getting-started.md"
LIVE_NOTE="$HOME/Documents/Obsidian Notes/Research/OpenCode/Voice Commands/getting-started.md"

verify_symlink() {
  local live_path="$1"
  local repo_path="$2"

  if [ ! -e "$repo_path" ]; then
    printf 'warning: repo target missing: %s\n' "$repo_path"
    return
  fi

  if [ -L "$live_path" ]; then
    local link_target
    local repo_target
    link_target="$(readlink -f "$live_path")"
    repo_target="$(readlink -f "$repo_path")"
    if [ "$link_target" = "$repo_target" ]; then
      printf 'verified symlink: %s -> %s\n' "$live_path" "$repo_path"
    else
      printf 'warning: %s points to %s (expected %s)\n' "$live_path" "$link_target" "$repo_target"
    fi
  else
    printf 'warning: %s is not a symlink to repo source\n' "$live_path"
  fi
}

verify_symlink "$LIVE_SCRIPT" "$REPO_SCRIPT"
verify_symlink "$LIVE_NOTE" "$REPO_NOTE"

printf 'snapshot complete\n'
