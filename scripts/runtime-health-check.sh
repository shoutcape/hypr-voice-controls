#!/usr/bin/env bash
set -euo pipefail

LOCAL_FLAG=""
MAX_PENDING=8
MAX_RUNNING_AGE_MS=30000
MAX_WORKER_RESTARTS=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --local)
      LOCAL_FLAG="--local"
      shift
      ;;
    --max-pending)
      MAX_PENDING="$2"
      shift 2
      ;;
    --max-running-age-ms)
      MAX_RUNNING_AGE_MS="$2"
      shift 2
      ;;
    --max-worker-restarts)
      MAX_WORKER_RESTARTS="$2"
      shift 2
      ;;
    *)
      printf 'Unknown argument: %s\n' "$1" >&2
      exit 2
      ;;
  esac
done

SCRIPT_DIR="$(dirname "$(readlink -f "$0")")"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
HVC="$REPO_DIR/hvc"

if [[ ! -x "$HVC" ]]; then
  printf 'Missing executable: %s\n' "$HVC" >&2
  exit 2
fi

RAW_JSON="$($HVC --input runtime-status-json $LOCAL_FLAG 2>/dev/null)"

python3 - "$RAW_JSON" "$MAX_PENDING" "$MAX_RUNNING_AGE_MS" "$MAX_WORKER_RESTARTS" <<'PY'
import json
import sys

raw_json, max_pending_s, max_running_age_ms_s, max_worker_restarts_s = sys.argv[1:5]

try:
    payload = json.loads(raw_json)
except json.JSONDecodeError as exc:
    print(f"FAIL: invalid JSON from runtime-status-json: {exc}")
    raise SystemExit(1)

def as_int(name: str, value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None

max_pending = int(max_pending_s)
max_running_age_ms = int(max_running_age_ms_s)
max_worker_restarts = int(max_worker_restarts_s)

pending = as_int("pending", payload.get("pending"))
running_age_ms = as_int("running_age_ms", payload.get("running_age_ms"))
worker_restarts = as_int("worker_restarts", payload.get("worker_restarts"))
worker_alive = payload.get("worker_alive")
state = payload.get("state")
running_job_name = payload.get("running_job_name")

errors: list[str] = []

if pending is None:
    errors.append("pending is missing or non-numeric")
elif pending > max_pending:
    errors.append(f"pending={pending} exceeds max_pending={max_pending}")

if worker_alive is not True:
    errors.append(f"worker_alive is not true (value={worker_alive!r})")

if worker_restarts is None:
    errors.append("worker_restarts is missing or non-numeric")
elif worker_restarts > max_worker_restarts:
    errors.append(
        f"worker_restarts={worker_restarts} exceeds max_worker_restarts={max_worker_restarts}"
    )

if running_age_ms is not None and running_age_ms > max_running_age_ms:
    errors.append(
        f"running_age_ms={running_age_ms} exceeds max_running_age_ms={max_running_age_ms}"
    )

summary = (
    f"state={state} pending={pending} running={running_job_name} "
    f"running_age_ms={running_age_ms} worker_alive={worker_alive} worker_restarts={worker_restarts}"
)

if errors:
    print("FAIL:", summary)
    for err in errors:
        print(f" - {err}")
    raise SystemExit(1)

print("OK:", summary)
PY
