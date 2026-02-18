#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(dirname "$(readlink -f "$0")")"
REPO_DIR="$(dirname "$SCRIPT_DIR")"

printf '[1/3] Syntax checks\n'
python3 -m py_compile \
  "$REPO_DIR"/hvc \
  "$REPO_DIR"/voice_controls/*.py \
  "$REPO_DIR"/tests/test_*.py

printf '[2/3] Unit tests\n'
python3 -m unittest \
  discover -s tests -p 'test_*.py'

printf '[3/3] Live hotkey e2e\n'
set +e
"$REPO_DIR"/scripts/live_hotkey_e2e.sh
live_rc=$?
set -e

if [[ "$live_rc" -eq 0 ]]; then
  printf 'Live hotkey e2e passed.\n'
elif [[ "$live_rc" -eq 2 ]]; then
  if [[ "${VOICE_REQUIRE_LIVE_HOTKEY_TEST:-0}" == "1" ]]; then
    printf 'Live hotkey e2e prerequisites missing and strict mode is enabled.\n' >&2
    exit 1
  fi
  printf 'Live hotkey e2e skipped (missing local prerequisites).\n'
else
  printf 'Live hotkey e2e failed.\n' >&2
  exit "$live_rc"
fi

printf 'All pre-release checks passed.\n'
