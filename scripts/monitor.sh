#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CONFIG_FILE="${DOWNLOADER_CONFIG:-$PROJECT_DIR/config/default.env}"

if [[ ! -f "$CONFIG_FILE" ]]; then
  echo "ERROR: config file not found: $CONFIG_FILE"
  exit 1
fi

# shellcheck disable=SC1090
source "$CONFIG_FILE"

RUNTIME_DIR="$PROJECT_DIR/${RUNTIME_SUBDIR:-runtime}"
URLS_FILE="$PROJECT_DIR/${URLS_FILENAME:-urls.txt}"
ARCHIVE_FILE="$RUNTIME_DIR/${ARCHIVE_FILENAME:-downloaded.txt}"

WORKER_IDS=()
for i in $(seq -w 0 $((NUM_WORKERS - 1))); do
  WORKER_IDS+=("$i")
done

while true; do
  clear
  NOW=$(date '+%Y-%m-%d %H:%M:%S')
  TOTAL_URLS=$(wc -l < "$URLS_FILE" 2>/dev/null | tr -cd '0-9')
  TOTAL_URLS=${TOTAL_URLS:-0}
  TOTAL_DONE=$(wc -l < "$ARCHIVE_FILE" 2>/dev/null | tr -cd '0-9')
  TOTAL_DONE=${TOTAL_DONE:-0}
  WAV_COUNT=$(find "$OUTPUT_DIR" -name '*.wav' 2>/dev/null | wc -l | tr -cd '0-9')
  WAV_COUNT=${WAV_COUNT:-0}
  DISK_USED=$(du -sh "$OUTPUT_DIR" 2>/dev/null | cut -f1)
  DISK_USED=${DISK_USED:-0}

  echo "============================================================"
  echo "Punjabi YouTube Downloader Dashboard"
  echo "$NOW"
  echo "============================================================"
  echo "Total done: $TOTAL_DONE / $TOTAL_URLS"
  echo "WAV files:  $WAV_COUNT"
  echo "Disk used:  $DISK_USED"
  echo

  ALL_DL=0
  ALL_FAIL=0
  ALL_SKIP=0

  for wid in "${WORKER_IDS[@]}"; do
    LOG="$LOG_DIR/worker_${wid}.log"
    CHUNK="$RUNTIME_DIR/chunk_${wid}.txt"

    CT=$(wc -l < "$CHUNK" 2>/dev/null | tr -cd '0-9')
    CT=${CT:-0}

    DL=$(grep -c "DOWNLOADED:" "$LOG" 2>/dev/null | tr -cd '0-9')
    FL=$(grep -c "FAILED:" "$LOG" 2>/dev/null | tr -cd '0-9')
    SK=$(grep -c "SKIPPED" "$LOG" 2>/dev/null | tr -cd '0-9')
    DL=${DL:-0}
    FL=${FL:-0}
    SK=${SK:-0}

    ALL_DL=$((ALL_DL + DL))
    ALL_FAIL=$((ALL_FAIL + FL))
    ALL_SKIP=$((ALL_SKIP + SK))

    PROCESSED=$((DL + FL + SK))
    if (( CT > 0 )); then
      PCT=$((PROCESSED * 100 / CT))
    else
      PCT=0
    fi

    LAST=$(tail -1 "$LOG" 2>/dev/null | cut -c1-80)
    printf "worker %s: %d/%d (%d%%) | ok=%d fail=%d skip=%d\n" "$wid" "$PROCESSED" "$CT" "$PCT" "$DL" "$FL" "$SK"
    [[ -n "$LAST" ]] && echo "  $LAST"
  done

  echo
  echo "Totals: ok=$ALL_DL fail=$ALL_FAIL skip=$ALL_SKIP"
  if tmux has-session -t yt_workers 2>/dev/null; then
    echo "tmux: running (yt_workers)"
  else
    echo "tmux: stopped"
  fi

  echo
  echo "Ctrl+C to exit monitor. Workers keep running."
  sleep "$REFRESH_SECONDS"
done
