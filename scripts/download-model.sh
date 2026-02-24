#!/usr/bin/env bash
# Download a whisper.cpp GGML model file.
# Usage: ./scripts/download-model.sh [models_dir] [model_name]
#
# Defaults:
#   models_dir = models/
#   model_name = base.en

set -euo pipefail

MODELS_DIR="${1:-models}"
MODEL_NAME="${2:-base.en}"
MODEL_FILE="ggml-${MODEL_NAME}.bin"
MODEL_URL="https://huggingface.co/ggerganov/whisper.cpp/resolve/main/${MODEL_FILE}"

mkdir -p "$MODELS_DIR"

DEST="${MODELS_DIR}/${MODEL_FILE}"

if [[ -f "$DEST" ]]; then
    echo "Model already exists: $DEST"
    exit 0
fi

echo "Downloading ${MODEL_FILE} to ${DEST}..."

if command -v curl &>/dev/null; then
    curl -L --progress-bar -o "$DEST" "$MODEL_URL"
elif command -v wget &>/dev/null; then
    wget -q --show-progress -O "$DEST" "$MODEL_URL"
else
    echo "Error: neither curl nor wget found" >&2
    exit 1
fi

echo "Done: $DEST ($(du -h "$DEST" | cut -f1))"
