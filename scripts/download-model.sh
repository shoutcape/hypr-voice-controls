#!/usr/bin/env bash
# Download the distil-large-v3 GGML model file for whisper.cpp.
# Usage: ./scripts/download-model.sh [models_dir]
#
# Default models_dir: models/
#
# The model file (ggml-distil-large-v3.bin, ~756 MB) is downloaded from:
#   https://huggingface.co/distil-whisper/distil-large-v3-ggml

set -euo pipefail

MODELS_DIR="${1:-models}"
MODEL_FILE="ggml-distil-large-v3.bin"
MODEL_URL="https://huggingface.co/distil-whisper/distil-large-v3-ggml/resolve/main/${MODEL_FILE}"

mkdir -p "$MODELS_DIR"

DEST="${MODELS_DIR}/${MODEL_FILE}"

if [[ -f "$DEST" ]]; then
    echo "Model already exists: $DEST"
    exit 0
fi

echo "Downloading ${MODEL_FILE} to ${DEST} (~756 MB)..."

if command -v curl &>/dev/null; then
    curl -L --progress-bar -o "$DEST" "$MODEL_URL"
elif command -v wget &>/dev/null; then
    wget -q --show-progress -O "$DEST" "$MODEL_URL"
else
    echo "Error: neither curl nor wget found" >&2
    exit 1
fi

echo "Done: $DEST ($(du -h "$DEST" | cut -f1))"
