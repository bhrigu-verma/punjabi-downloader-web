#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

export DOWNLOADER_CONFIG="${DOWNLOADER_CONFIG:-$PROJECT_DIR/config/default.env}"
HOST="${UI_HOST:-0.0.0.0}"
PORT="${PORT:-${UI_PORT:-8787}}"

echo "Starting Punjabi Downloader Web"
echo "Project: $PROJECT_DIR"
echo "Config: $DOWNLOADER_CONFIG"
echo "Host: $HOST"
echo "Port: $PORT"

exec python3 "$PROJECT_DIR/scripts/web_ui.py" --host "$HOST" --port "$PORT" --project "$PROJECT_DIR"
