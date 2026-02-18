#!/usr/bin/env bash
set -euo pipefail

LOG_PATH="${LOG_PATH:-$HOME/.local/state/voice-hotkey.log}"
SLEEP_SECONDS="${SLEEP_SECONDS:-1.0}"
MIN_DELTA="${MIN_DELTA:-1}"

if ! command -v ydotool >/dev/null 2>&1; then
  printf 'Missing required tool: ydotool\n' >&2
  exit 2
fi

if ! systemctl --user is-active --quiet voice-hotkey.service; then
  printf 'voice-hotkey.service is not active\n' >&2
  exit 2
fi

if [[ -z "${HYPRLAND_INSTANCE_SIGNATURE:-}" ]]; then
  printf 'HYPRLAND_INSTANCE_SIGNATURE is not set; run inside Hyprland session\n' >&2
  exit 2
fi

if [[ ! -f "$LOG_PATH" ]]; then
  printf 'Log file not found: %s\n' "$LOG_PATH" >&2
  exit 2
fi

count_log_lines() {
  python3 - "$LOG_PATH" "$1" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
needle = sys.argv[2]
text = path.read_text(encoding="utf-8", errors="ignore") if path.exists() else ""
count = sum(1 for line in text.splitlines() if needle in line)
print(count)
PY
}

KEY_CODES=("$@")
if [[ ${#KEY_CODES[@]} -eq 0 ]]; then
  KEY_CODES=(186 187)
fi

printf 'Live hotkey e2e test against %s\n' "$LOG_PATH"

for key_code in "${KEY_CODES[@]}"; do
  start_before="$(count_log_lines "Voice daemon request start")"
  end_before="$(count_log_lines "Voice daemon request end")"

  ydotool key "${key_code}:1" "${key_code}:0"
  sleep "$SLEEP_SECONDS"

  start_after="$(count_log_lines "Voice daemon request start")"
  end_after="$(count_log_lines "Voice daemon request end")"

  start_delta=$((start_after - start_before))
  end_delta=$((end_after - end_before))

  printf 'keycode=%s start_delta=%s end_delta=%s\n' "$key_code" "$start_delta" "$end_delta"

  if (( start_delta < MIN_DELTA || end_delta < MIN_DELTA )); then
    printf 'FAIL: keycode=%s did not produce expected daemon response\n' "$key_code" >&2
    exit 1
  fi
done

printf 'PASS: all keycodes produced daemon request/response activity\n'
