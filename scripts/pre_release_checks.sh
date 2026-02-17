#!/usr/bin/env bash
set -euo pipefail

SKIP_ACCEPTANCE=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-acceptance)
      SKIP_ACCEPTANCE=true
      shift
      ;;
    *)
      printf 'Unknown argument: %s\n' "$1" >&2
      exit 2
      ;;
  esac
done

SCRIPT_DIR="$(dirname "$(readlink -f "$0")")"
REPO_DIR="$(dirname "$SCRIPT_DIR")"

printf '[1/4] Syntax checks\n'
python3 -m py_compile \
  "$REPO_DIR"/voice-hotkey.py \
  "$REPO_DIR"/voice_hotkey/*.py \
  "$REPO_DIR"/voice_hotkey/runtime/*.py \
  "$REPO_DIR"/tests/test_phase0_guardrails.py \
  "$REPO_DIR"/tests/test_phase1_control_plane.py \
  "$REPO_DIR"/tests/test_phase2_state_machine.py \
  "$REPO_DIR"/tests/test_phase2_job_queue.py \
  "$REPO_DIR"/tests/test_phase2_orchestrator_cancel.py \
  "$REPO_DIR"/tests/test_phase3_wakeword_responsiveness.py \
  "$REPO_DIR"/scripts/phase0_baseline_latency.py \
  "$REPO_DIR"/scripts/runtime_v2_acceptance.py

printf '[2/4] Unit tests\n'
python3 -m unittest \
  tests/test_phase0_guardrails.py \
  tests/test_phase1_control_plane.py \
  tests/test_phase2_state_machine.py \
  tests/test_phase2_job_queue.py \
  tests/test_phase2_orchestrator_cancel.py \
  tests/test_phase3_wakeword_responsiveness.py

printf '[3/4] Local runtime health\n'
"$REPO_DIR"/scripts/runtime-health-check.sh --local

if [[ "$SKIP_ACCEPTANCE" == "true" ]]; then
  printf '[4/4] Daemon acceptance (skipped)\n'
else
  printf '[4/4] Daemon runtime-v2 acceptance\n'
  "$REPO_DIR"/scripts/runtime_v2_acceptance.py
fi

printf 'All pre-release checks passed.\n'
