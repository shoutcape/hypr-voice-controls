#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPO_SCRIPT="$REPO_ROOT/voice-hotkey.py"
LIVE_SCRIPT="$HOME/.local/bin/voice-hotkey.py"
REPO_NOTE="$REPO_ROOT/getting-started.md"
LIVE_NOTE="$HOME/Documents/Obsidian Notes/Research/OpenCode/Voice Commands/getting-started.md"

status=0

check_legacy_symlink() {
  local path="$1"
  if [ -L "$path" ]; then
    printf 'legacy symlink still present: %s -> %s\n' "$path" "$(readlink -f "$path")"
    status=1
  fi
}

check_repo_file() {
  local path="$1"
  if [ ! -f "$path" ]; then
    printf 'missing repo file: %s\n' "$path"
    status=1
  fi
}

check_repo_file "$REPO_SCRIPT"
check_repo_file "$REPO_NOTE"
check_legacy_symlink "$LIVE_SCRIPT"
check_legacy_symlink "$LIVE_NOTE"

if [ "$status" -eq 0 ]; then
  printf 'repo-only check passed\n'
else
  printf 'repo-only check failed\n'
fi

exit "$status"
