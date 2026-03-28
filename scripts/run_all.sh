#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$PROJECT_DIR/../.." && pwd)"
WORKSPACE_ROOT="$(cd "$PROJECT_DIR/../../.." && pwd)"
CONFIG_FILE="${DOWNLOADER_CONFIG:-$PROJECT_DIR/config/default.env}"

if [[ ! -f "$CONFIG_FILE" ]]; then
  echo "ERROR: config file not found: $CONFIG_FILE"
  exit 1
fi

# shellcheck disable=SC1090
source "$CONFIG_FILE"

resolve_path() {
  local p="$1"
  if [[ "$p" = /* ]]; then
    echo "$p"
  else
    echo "$PROJECT_DIR/$p"
  fi
}

OUTPUT_DIR="$(resolve_path "${OUTPUT_DIR:-data/raw/youtube}")"
LOG_DIR="$(resolve_path "${LOG_DIR:-runtime/logs}")"

# Build PATH with deterministic priority so healthy envs win over stale shims.
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

RUNTIME_DIR="$PROJECT_DIR/${RUNTIME_SUBDIR:-runtime}"
URLS_FILE="$PROJECT_DIR/${URLS_FILENAME:-urls.txt}"
ARCHIVE_FILE="$RUNTIME_DIR/${ARCHIVE_FILENAME:-downloaded.txt}"
NO_MONITOR="${1:-}"

mkdir -p "$RUNTIME_DIR" "$OUTPUT_DIR" "$LOG_DIR"
touch "$ARCHIVE_FILE"

if [[ ! -f "$URLS_FILE" ]]; then
  echo "ERROR: URLs file not found: $URLS_FILE"
  echo "Place your URLs file at: $URLS_FILE"
  exit 1
fi

TOTAL_URLS=$(wc -l < "$URLS_FILE" | tr -cd '0-9')
TOTAL_URLS=${TOTAL_URLS:-0}

if [[ "$TOTAL_URLS" -eq 0 ]]; then
  echo "ERROR: URLs file is empty: $URLS_FILE"
  exit 1
fi

echo "== Standalone Punjabi YouTube Downloader =="
echo "Project:      $PROJECT_DIR"
echo "URLs:         $TOTAL_URLS"
echo "Workers:      ${NUM_WORKERS}"
echo "Output dir:   $OUTPUT_DIR"
echo "Runtime dir:  $RUNTIME_DIR"
echo "Logs dir:     $LOG_DIR"
echo

for bin in ffmpeg tmux bc; do
  if ! command -v "$bin" >/dev/null 2>&1; then
    echo "ERROR: missing dependency: $bin"
    echo "Install it first, then rerun."
    exit 1
  fi
done

if yt-dlp --version >/dev/null 2>&1; then
  :
elif python3 -m yt_dlp --version >/dev/null 2>&1; then
  echo "INFO: using python3 -m yt_dlp fallback (yt-dlp shim is unavailable)"
else
  echo "ERROR: yt-dlp is not usable (binary and python module both unavailable)"
  echo "Install with: python3 -m pip install -U yt-dlp"
  exit 1
fi

if ! command -v deno >/dev/null 2>&1; then
  echo "WARN: deno not found. Some YouTube signatures may fail without it."
fi

for cookie in $COOKIE_FILES; do
  if [[ ! -f "$PROJECT_DIR/$cookie" ]]; then
    echo "ERROR: cookie file missing: $PROJECT_DIR/$cookie"
    exit 1
  fi
done

if (( NUM_WORKERS < 1 )); then
  echo "ERROR: NUM_WORKERS must be >= 1"
  exit 1
fi

URLS_PER_WORKER=$(( (TOTAL_URLS + NUM_WORKERS - 1) / NUM_WORKERS ))
rm -f "$RUNTIME_DIR"/chunk_*.txt

awk -v n="$URLS_PER_WORKER" -v d="$RUNTIME_DIR" '{
  file = sprintf("%s/chunk_%02d.txt", d, int((NR-1)/n))
  print > file
}' "$URLS_FILE"

echo "Chunks created:"
for f in "$RUNTIME_DIR"/chunk_*.txt; do
  [[ -f "$f" ]] || continue
  echo "  $(basename "$f"): $(wc -l < "$f" | tr -cd '0-9') URLs"
done
echo

rm -f "$LOG_DIR"/worker_*.log "$LOG_DIR"/worker_*.status

tmux kill-session -t yt_workers 2>/dev/null || true

tmux new-session -d -s yt_workers -n worker_00
tmux send-keys -t yt_workers:worker_00 "cd '$PROJECT_DIR' && bash scripts/worker.sh 00" Enter

for ((i=1; i<NUM_WORKERS; i++)); do
  wid=$(printf '%02d' "$i")
  tmux new-window -t yt_workers -n "worker_${wid}"
  tmux send-keys -t "yt_workers:worker_${wid}" "cd '$PROJECT_DIR' && bash scripts/worker.sh ${wid}" Enter
  sleep 2
done

echo "Launched ${NUM_WORKERS} workers in tmux session: yt_workers"
echo "Attach: tmux attach -t yt_workers"

if [[ "$NO_MONITOR" == "--no-monitor" ]]; then
  exit 0
fi

exec bash "$SCRIPT_DIR/monitor.sh"
