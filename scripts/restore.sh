#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPO_SCRIPT="$REPO_ROOT/voice-hotkey.py"
REPO_NOTE="$REPO_ROOT/getting-started.md"

if [ ! -f "$REPO_SCRIPT" ]; then
  printf 'missing repo script: %s\n' "$REPO_SCRIPT"
  exit 1
fi

if [ ! -f "$REPO_NOTE" ]; then
  printf 'missing repo note: %s\n' "$REPO_NOTE"
  exit 1
fi

chmod +x "$REPO_SCRIPT"
printf 'repo-only mode active\n'
printf 'script: %s\n' "$REPO_SCRIPT"
printf 'note:   %s\n' "$REPO_NOTE"
