#!/usr/bin/env bash
# Responsibility: Send real key events and assert daemon request/response log activity.
set -euo pipefail

LOG_PATH="${LOG_PATH:-$HOME/.local/state/voice-hotkey.log}"
# Mirror the Python config fallback: use XDG_RUNTIME_DIR when set (systemd
# user sessions), otherwise fall back to ~/.local/state/.
SOCKET_PATH="${SOCKET_PATH:-${XDG_RUNTIME_DIR:-$HOME/.local/state}/voice-hotkey.sock}"
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
  KEY_CODES=(186)
fi

PRESSED_KEY_CODE=""

send_daemon_input() {
  python3 - "$SOCKET_PATH" "$1" <<'PY'
import json
import socket
import sys

socket_path = sys.argv[1]
input_mode = sys.argv[2]
payload = json.dumps({"input": input_mode}).encode("utf-8") + b"\n"

with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
    client.settimeout(2)
    client.connect(socket_path)
    client.sendall(payload)
    data = client.recv(1024)

line = data.split(b"\n", 1)[0]
response = json.loads(line.decode("utf-8"))
rc = response.get("rc", 1)
raise SystemExit(int(rc) if isinstance(rc, (int, float, str)) else 1)
PY
}

cleanup_sessions() {
  send_daemon_input dictate-stop >/dev/null 2>&1 || true
}

release_pressed_key() {
  if [[ -n "$PRESSED_KEY_CODE" ]]; then
    ydotool key "${PRESSED_KEY_CODE}:0" >/dev/null 2>&1 || true
    PRESSED_KEY_CODE=""
  fi
}

on_exit() {
  release_pressed_key
  cleanup_sessions
}

trap on_exit EXIT INT TERM

cleanup_sessions

printf 'Live hotkey e2e test against %s\n' "$LOG_PATH"

for key_code in "${KEY_CODES[@]}"; do
  start_before="$(count_log_lines "Voice daemon request start")"
  end_before="$(count_log_lines "Voice daemon request end")"

  PRESSED_KEY_CODE="$key_code"
  ydotool key "${key_code}:1"
  ydotool key "${key_code}:0"
  PRESSED_KEY_CODE=""
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

cleanup_sessions

printf 'PASS: all keycodes produced daemon request/response activity\n'
