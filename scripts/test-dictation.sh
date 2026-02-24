#!/usr/bin/env bash
# Quick manual test for the full dictation pipeline:
# record → transcribe → paste into focused window → desktop notification.
#
# Spawns a temporary daemon on an isolated socket so it doesn't conflict
# with any already-running voice-controls service.
#
# Usage:
#   ./scripts/test-dictation.sh [duration_seconds]
#
# Default duration: 4 seconds.
# Focus the window you want text pasted into before running.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BINARY="$ROOT/build/voice-controls"
DURATION="${1:-4}"

# Use a per-run temp config pointing at the project model dir so the script
# works out of the box without a system install.
MODEL="${VOICE_MODEL:-$ROOT/models/ggml-base.en.bin}"
SOCK="/tmp/voice-controls-test-$$.sock"
CFG="/tmp/voice-controls-test-$$.toml"

# ── Sanity checks ─────────────────────────────────────────────────
if [[ ! -f "$BINARY" ]]; then
  echo "Binary not found. Run: make build" >&2
  exit 1
fi
if [[ ! -f "$MODEL" ]]; then
  echo "Model not found at: $MODEL" >&2
  echo "Run: make model" >&2
  exit 1
fi

# ── Write temp config ─────────────────────────────────────────────
cat > "$CFG" << EOF
model_path = "$MODEL"
socket_path = "$SOCK"
audio_source = "${VOICE_AUDIO:-default}"
log_level = "info"
EOF

# ── Start daemon ──────────────────────────────────────────────────
echo "Starting daemon ($(basename "$MODEL"))..."
"$BINARY" --config "$CFG" --daemon \
  >/tmp/vc-test-ready-$$.out \
  2>/tmp/vc-test-log-$$.out &
DAEMON_PID=$!

cleanup() {
  kill "$DAEMON_PID" 2>/dev/null || true
  wait "$DAEMON_PID" 2>/dev/null || true
  rm -f "/tmp/vc-test-ready-$$.out" "/tmp/vc-test-log-$$.out" "$CFG"
}
trap cleanup EXIT

# Wait for READY (up to 30s)
for i in $(seq 1 60); do
  sleep 0.5
  grep -q "^READY$" "/tmp/vc-test-ready-$$.out" 2>/dev/null && break
  kill -0 "$DAEMON_PID" 2>/dev/null || {
    echo "Daemon failed to start:" >&2
    cat "/tmp/vc-test-log-$$.out" >&2
    exit 1
  }
done
if ! grep -q "^READY$" "/tmp/vc-test-ready-$$.out" 2>/dev/null; then
  echo "Daemon did not become ready in time." >&2
  exit 1
fi

echo "Daemon ready."
echo ""
echo "Focus the window you want text pasted into, then speak."
echo ""

# ── Record ────────────────────────────────────────────────────────
"$BINARY" --config "$CFG" --input dictate-start
echo "Recording for ${DURATION}s — speak now..."

for i in $(seq "$DURATION" -1 1); do
  printf "\r  %ds remaining..." "$i"
  sleep 1
done
printf "\r  Stopping...          \n"

# ── Transcribe + paste ────────────────────────────────────────────
echo ""
echo "Transcribing and pasting..."
RESULT=$("$BINARY" --config "$CFG" --input dictate-stop 2>&1 || true)

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
if [[ -z "$RESULT" ]]; then
  echo "  (no speech detected)"
else
  echo "  $RESULT"
fi
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "Text pasted into focused window (if speech was detected)."
