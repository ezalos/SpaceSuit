#!/usr/bin/env bash
# ABOUTME: Launch script for the whisperx transcription server.
# ABOUTME: Starts uvicorn on 0.0.0.0:8642 with the FastAPI app.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cd "$SCRIPT_DIR"

PORT="${WHISPERX_PORT:-8642}"
HOST="${WHISPERX_HOST:-0.0.0.0}"

echo "Starting WhisperX transcription server on ${HOST}:${PORT}..."
exec uv run uvicorn server:app \
    --host "$HOST" \
    --port "$PORT"
