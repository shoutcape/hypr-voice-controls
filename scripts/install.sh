#!/usr/bin/env bash
# Install voice-controls for the current user.
#
# What this does:
#   1. Copies the binary to ~/.local/bin/voice-controls
#   2. Copies the example config (if none exists) to ~/.config/voice-controls/config.toml
#   3. Creates the model directory at ~/.local/share/voice-controls/models/
#   4. Installs the systemd user service and enables it
#   5. Downloads the default model if not already present
#
# Usage:
#   ./scripts/install.sh [--no-service] [--no-model]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

BINARY="$ROOT/build/voice-controls"
BIN_DIR="$HOME/.local/bin"
CONFIG_DIR="$HOME/.config/voice-controls"
MODEL_DIR="$HOME/.local/share/voice-controls/models"
SERVICE_SRC="$ROOT/examples/systemd/voice-controls.service"
SERVICE_DIR="$HOME/.config/systemd/user"

INSTALL_SERVICE=true
INSTALL_MODEL=true
INSTALL_WAKEWORD_MODELS=false

for arg in "$@"; do
  case "$arg" in
    --no-service)        INSTALL_SERVICE=false ;;
    --no-model)          INSTALL_MODEL=false ;;
    --wakeword-models)   INSTALL_WAKEWORD_MODELS=true ;;
    *) echo "Unknown argument: $arg" >&2; exit 1 ;;
  esac
done

# ── Pre-flight checks ─────────────────────────────────────────────
if [[ ! -f "$BINARY" ]]; then
  echo "Binary not found at $BINARY" >&2
  echo "Run 'make build' first." >&2
  exit 1
fi

# ── Binary ────────────────────────────────────────────────────────
echo "Installing binary → $BIN_DIR/voice-controls"
mkdir -p "$BIN_DIR"
cp "$BINARY" "$BIN_DIR/voice-controls"
chmod 755 "$BIN_DIR/voice-controls"

# ── Config ────────────────────────────────────────────────────────
mkdir -p "$CONFIG_DIR"
if [[ ! -f "$CONFIG_DIR/config.toml" ]]; then
  echo "Creating default config → $CONFIG_DIR/config.toml"
  cp "$ROOT/config.example.toml" "$CONFIG_DIR/config.toml"
else
  echo "Config already exists, skipping: $CONFIG_DIR/config.toml"
fi

# ── Model directory ───────────────────────────────────────────────
mkdir -p "$MODEL_DIR"

# ── Model download ────────────────────────────────────────────────
if $INSTALL_MODEL; then
  DEFAULT_MODEL="$MODEL_DIR/ggml-distil-large-v3.bin"
  if [[ ! -f "$DEFAULT_MODEL" ]]; then
    echo "Downloading default model (distil-large-v3, ~756 MB) → $MODEL_DIR"
    "$ROOT/scripts/download-model.sh" "$MODEL_DIR"
  else
    echo "Model already present: $DEFAULT_MODEL"
  fi
fi

# ── Systemd service ───────────────────────────────────────────────
if $INSTALL_SERVICE; then
  echo "Installing systemd service → $SERVICE_DIR/voice-controls.service"
  mkdir -p "$SERVICE_DIR"
  cp "$SERVICE_SRC" "$SERVICE_DIR/voice-controls.service"
  systemctl --user daemon-reload
  systemctl --user enable voice-controls.service
  echo "Service enabled. Start it with:"
  echo "  systemctl --user start voice-controls.service"
fi

# ── Wakeword models ──────────────────────────────────────────────
if $INSTALL_WAKEWORD_MODELS; then
  WW_SRC_DIR=""
  # Try to find shared openWakeWord models from a local checkout.
  for candidate in \
    "$HOME/Github/hey-hyper-training/openWakeWord/openwakeword/resources/models" \
    "$HOME/.config/hypr-voice-controls/wakeword"; do
    if [[ -d "$candidate" ]]; then
      WW_SRC_DIR="$candidate"
      break
    fi
  done

  if [[ -z "$WW_SRC_DIR" ]]; then
    echo "WARNING: --wakeword-models specified but could not locate openWakeWord model directory."
    echo "  Place melspectrogram.onnx and embedding_model.onnx manually in: $MODEL_DIR"
  else
    for f in melspectrogram.onnx embedding_model.onnx; do
      if [[ -f "$WW_SRC_DIR/$f" ]]; then
        echo "Installing wakeword shared model → $MODEL_DIR/$f"
        cp "$WW_SRC_DIR/$f" "$MODEL_DIR/$f"
      fi
    done
  fi
fi

echo ""
echo "Installation complete."
echo ""
echo "Next steps:"
if ! $INSTALL_SERVICE; then
  echo "  1. Copy examples/systemd/voice-controls.service to ~/.config/systemd/user/"
  echo "  2. Run: systemctl --user daemon-reload && systemctl --user enable --now voice-controls.service"
fi
echo "  • Add to hyprland.conf:"
echo "      source = ~/.config/hypr/voice-controls.bindings.conf"
echo "      source = ~/.config/hypr/voice-controls.autostart.conf"
echo "  • Copy the Hyprland examples:"
echo "      cp examples/hypr/* ~/.config/hypr/"
echo ""
echo "  Edit $CONFIG_DIR/config.toml to customise model, mic source, etc."
echo ""
echo "  For wakeword (hands-free) mode:"
echo "    1. sudo pacman -S onnxruntime-cpu   (or onnxruntime-cuda)"
echo "    2. Place melspectrogram.onnx, embedding_model.onnx, hey_hyper.onnx in $MODEL_DIR"
echo "    3. Set wakeword_enabled = true in $CONFIG_DIR/config.toml"
