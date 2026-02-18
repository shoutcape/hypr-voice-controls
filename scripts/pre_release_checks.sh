#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(dirname "$(readlink -f "$0")")"
REPO_DIR="$(dirname "$SCRIPT_DIR")"

printf '[1/2] Syntax checks\n'
python3 -m py_compile \
  "$REPO_DIR"/hvc \
  "$REPO_DIR"/voice_controls/*.py \
  "$REPO_DIR"/tests/test_phase0_guardrails.py

printf '[2/2] Unit tests\n'
python3 -m unittest \
  tests/test_phase0_guardrails.py

printf 'All pre-release checks passed.\n'
