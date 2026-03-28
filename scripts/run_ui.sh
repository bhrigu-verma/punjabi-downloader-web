#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$PROJECT_DIR/../.." && pwd)"
WORKSPACE_ROOT="$(cd "$PROJECT_DIR/../../.." && pwd)"
CONFIG_FILE="${DOWNLOADER_CONFIG:-$PROJECT_DIR/config/default.env}"
HOST="${UI_HOST:-127.0.0.1}"
PORT="${UI_PORT:-8787}"

if [[ ! -f "$CONFIG_FILE" ]]; then
  echo "ERROR: config file not found: $CONFIG_FILE"
  exit 1
fi

export DOWNLOADER_CONFIG="$CONFIG_FILE"

PATH_CANDIDATES=(
  "$WORKSPACE_ROOT/.venv-2/bin"
  "$REPO_ROOT/.venv-2/bin"
  "$WORKSPACE_ROOT/.venv/bin"
  "$REPO_ROOT/.venv/bin"
  "$WORKSPACE_ROOT/.venv-yt/bin"
  "$REPO_ROOT/.venv-yt/bin"
  "$WORKSPACE_ROOT/.venv-1/bin"
  "$REPO_ROOT/.venv-1/bin"
  "/opt/homebrew/bin"
  "/usr/local/bin"
)

PATH_PREFIX=""
for p in "${PATH_CANDIDATES[@]}"; do
  if [[ -d "$p" ]]; then
    PATH_PREFIX="${PATH_PREFIX:+$PATH_PREFIX:}$p"
  fi
done

if [[ -n "$PATH_PREFIX" ]]; then
  export PATH="$PATH_PREFIX:$PATH"
fi

# If the desired port is busy, choose the next free port.
CHOSEN_PORT=$(python3 - "$HOST" "$PORT" <<'PY'
import socket
import sys

host = sys.argv[1]
start = int(sys.argv[2])

for port in range(start, start + 20):
  s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
  try:
    s.bind((host, port))
    print(port)
    break
  except OSError:
    pass
  finally:
    s.close()
else:
  raise SystemExit("No free port found in range")
PY
)

if [[ "$CHOSEN_PORT" != "$PORT" ]]; then
  echo "INFO: port $PORT is busy, using $CHOSEN_PORT"
fi

exec python3 "$SCRIPT_DIR/web_ui.py" --host "$HOST" --port "$CHOSEN_PORT" --project "$PROJECT_DIR"
