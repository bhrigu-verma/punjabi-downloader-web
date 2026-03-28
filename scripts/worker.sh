#!/usr/bin/env bash
set -euo pipefail

WORKER_ID="${1:-00}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
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

RUNTIME_DIR="$PROJECT_DIR/${RUNTIME_SUBDIR:-runtime}"
CHUNK_FILE="$RUNTIME_DIR/chunk_${WORKER_ID}.txt"
ARCHIVE_FILE="$RUNTIME_DIR/${ARCHIVE_FILENAME:-downloaded.txt}"
LOCK_FILE="$ARCHIVE_FILE.lock"
LOG_FILE="$LOG_DIR/worker_${WORKER_ID}.log"
STATUS_FILE="$LOG_DIR/worker_${WORKER_ID}.status"
METADATA_FILE="$RUNTIME_DIR/video_metadata.json"
WORK_DIR="${WORKER_TMP_PREFIX}${WORKER_ID}"

IFS=' ' read -r -a COOKIE_ARRAY <<< "$COOKIE_FILES"
if (( ${#COOKIE_ARRAY[@]} < 5 )); then
  echo "ERROR: COOKIE_FILES must contain 5 files"
  exit 1
fi

case "$WORKER_ID" in
  00|05) COOKIES_FILE="$PROJECT_DIR/${COOKIE_ARRAY[0]}" ;;
  01|06) COOKIES_FILE="$PROJECT_DIR/${COOKIE_ARRAY[1]}" ;;
  02|07) COOKIES_FILE="$PROJECT_DIR/${COOKIE_ARRAY[2]}" ;;
  03|08) COOKIES_FILE="$PROJECT_DIR/${COOKIE_ARRAY[3]}" ;;
  04|09) COOKIES_FILE="$PROJECT_DIR/${COOKIE_ARRAY[4]}" ;;
  *)
    idx=$((10#$WORKER_ID % 5))
    COOKIES_FILE="$PROJECT_DIR/${COOKIE_ARRAY[$idx]}"
    ;;
esac

mkdir -p "$LOG_DIR" "$RUNTIME_DIR" "$WORK_DIR" "$OUTPUT_DIR"
touch "$ARCHIVE_FILE"
: > "$LOG_FILE"
rm -f "$STATUS_FILE"

exec > >(tee -a "$LOG_FILE")
exec 2>&1

log_message() {
  echo "[$(date +'%Y-%m-%d %H:%M:%S')] [Worker $WORKER_ID] $1"
}

YT_DLP_CMD=()
if yt-dlp --version >/dev/null 2>&1; then
  YT_DLP_CMD=(yt-dlp)
elif python3 -m yt_dlp --version >/dev/null 2>&1; then
  YT_DLP_CMD=(python3 -m yt_dlp)
else
  log_message "ERROR: yt-dlp is unusable (binary and python module unavailable)"
  exit 1
fi

write_status() {
  local status_type="$1"
  local video_id="${2:-}"
  local extra="${3:-}"
  cat > "$STATUS_FILE" <<EOF
WORKER_ID=$WORKER_ID
STATUS=$status_type
TIMESTAMP=$(date +%s)
VIDEO_ID=$video_id
EXTRA=$extra
EOF
}

format_duration() {
  local secs="${1:-0}"
  if (( secs >= 3600 )); then
    printf "%dh%02dm%02ds" $((secs/3600)) $((secs%3600/60)) $((secs%60))
  elif (( secs >= 60 )); then
    printf "%dm%02ds" $((secs/60)) $((secs%60))
  else
    printf "%ds" "$secs"
  fi
}

if [[ ! -f "$CHUNK_FILE" ]]; then
  log_message "No chunk file found: $CHUNK_FILE"
  exit 0
fi

USE_COOKIES="${USE_COOKIES:-1}"

if [[ "$USE_COOKIES" == "1" ]]; then
  if [[ ! -f "$COOKIES_FILE" ]]; then
    log_message "ERROR: cookie file missing: $COOKIES_FILE"
    exit 1
  fi
fi

TOTAL=$(wc -l < "$CHUNK_FILE" | tr -cd '0-9')
TOTAL=${TOTAL:-0}
DOWNLOADED=0
FAILED=0
SKIPPED=0
CURRENT=0
START_TS=$(date +%s)

# Allow format override via config (defaults to existing behavior)
DOWNLOAD_FORMAT="${DOWNLOAD_FORMAT:-bestaudio/best}"

log_message "Starting worker with chunk $CHUNK_FILE"
if [[ "$USE_COOKIES" == "1" ]]; then
  log_message "Using cookies: $(basename "$COOKIES_FILE")"
else
  log_message "Using cookies: disabled"
fi

get_video_meta() {
  local vid="$1"
  local field="$2"
  if [[ -f "$METADATA_FILE" ]] && command -v python3 >/dev/null 2>&1; then
    python3 - <<PY 2>/dev/null
import json
vid = "$vid"
field = "$field"
try:
    with open("$METADATA_FILE") as f:
        data = json.load(f)
    for row in data:
        url = str(row.get("url", ""))
        if vid and vid in url:
            print(row.get(field, ""))
            break
except Exception:
    pass
PY
  fi
}

while IFS= read -r URL; do
  [[ -z "$URL" ]] && continue
  CURRENT=$((CURRENT + 1))

  VIDEO_ID=$(echo "$URL" | sed 's/.*v=//;s/&.*//')
  if [[ -z "$VIDEO_ID" ]]; then
    SKIPPED=$((SKIPPED + 1))
    log_message "SKIPPED invalid URL: $URL"
    write_status "skipped" "" "invalid-url"
    continue
  fi

  if grep -q "^youtube $VIDEO_ID$" "$ARCHIVE_FILE" 2>/dev/null; then
    SKIPPED=$((SKIPPED + 1))
    log_message "SKIPPED already done: $VIDEO_ID"
    write_status "skipped" "$VIDEO_ID" "already-downloaded"
    continue
  fi

  TITLE=$(get_video_meta "$VIDEO_ID" "title")
  [[ -z "$TITLE" ]] && TITLE="$VIDEO_ID"

  ELAPSED=$(( $(date +%s) - START_TS ))
  DONE=$((DOWNLOADED + FAILED))
  if (( DONE > 0 )); then
    AVG=$((ELAPSED / DONE))
    ETA=$(( (TOTAL - CURRENT) * AVG ))
    ETA_STR=$(format_duration "$ETA")
  else
    ETA_STR="calculating"
  fi

  log_message "[$CURRENT/$TOTAL] Downloading: $TITLE (eta $ETA_STR)"
  write_status "downloading" "$VIDEO_ID" "$TITLE"

  SUCCESS=0
  RETRY=0
  BACKOFF=20

  while (( RETRY < MAX_RETRIES )) && (( SUCCESS == 0 )); do
    RETRY=$((RETRY + 1))
    rm -f "$WORK_DIR"/*.wav "$WORK_DIR"/*.webm "$WORK_DIR"/*.m4a 2>/dev/null || true

    DOWNLOAD_OK=0
    if [[ "$USE_COOKIES" == "1" ]]; then
      if "${YT_DLP_CMD[@]}" \
        --cookies "$COOKIES_FILE" \
        --format "$DOWNLOAD_FORMAT" \
        --extract-audio \
        --audio-format wav \
        --audio-quality 0 \
        --postprocessor-args "ffmpeg:-ar $AUDIO_SAMPLE_RATE -ac $AUDIO_CHANNELS" \
        $PO_ARGS \
        --concurrent-fragments 4 \
        --no-check-certificates \
        --socket-timeout 30 \
        --retries 3 \
        --fragment-retries 5 \
        -o "$WORK_DIR/%(id)s.%(ext)s" \
        "$URL" >/tmp/yt_worker_${WORKER_ID}_last_output.txt 2>&1; then
        DOWNLOAD_OK=1
      fi
    else
      if "${YT_DLP_CMD[@]}" \
        --format "$DOWNLOAD_FORMAT" \
        --extract-audio \
        --audio-format wav \
        --audio-quality 0 \
        --postprocessor-args "ffmpeg:-ar $AUDIO_SAMPLE_RATE -ac $AUDIO_CHANNELS" \
        $PO_ARGS \
        --concurrent-fragments 4 \
        --no-check-certificates \
        --socket-timeout 30 \
        --retries 3 \
        --fragment-retries 5 \
        -o "$WORK_DIR/%(id)s.%(ext)s" \
        "$URL" >/tmp/yt_worker_${WORKER_ID}_last_output.txt 2>&1; then
        DOWNLOAD_OK=1
      fi
    fi

    if (( DOWNLOAD_OK == 1 )); then

      WAV_FILE=$(ls -1 "$WORK_DIR"/*.wav 2>/dev/null | head -1 || true)
      if [[ -f "$WAV_FILE" ]]; then
        FILE_SIZE=$(stat -c%s "$WAV_FILE" 2>/dev/null || stat -f%z "$WAV_FILE")
        if (( FILE_SIZE > MIN_FILE_SIZE )); then
          OUT_FILE="$OUTPUT_DIR/${VIDEO_ID}.wav"
          mv "$WAV_FILE" "$OUT_FILE"

          if command -v flock >/dev/null 2>&1; then
            {
              flock -x 200
              if ! grep -q "^youtube $VIDEO_ID$" "$ARCHIVE_FILE" 2>/dev/null; then
                echo "youtube $VIDEO_ID" >> "$ARCHIVE_FILE"
              fi
            } 200>"$LOCK_FILE"
          else
            if ! grep -q "^youtube $VIDEO_ID$" "$ARCHIVE_FILE" 2>/dev/null; then
              echo "youtube $VIDEO_ID" >> "$ARCHIVE_FILE"
            fi
          fi

          DOWNLOADED=$((DOWNLOADED + 1))
          SUCCESS=1
          log_message "DOWNLOADED: $VIDEO_ID"
          write_status "downloaded" "$VIDEO_ID" "ok"
        fi
      fi
    fi

    if (( SUCCESS == 0 )); then
      if (( RETRY < MAX_RETRIES )); then
        log_message "Retry $RETRY/$MAX_RETRIES for $VIDEO_ID after ${BACKOFF}s"
        sleep "$BACKOFF"
        BACKOFF=$((BACKOFF * 2))
      fi
    fi
  done

  if (( SUCCESS == 0 )); then
    FAILED=$((FAILED + 1))
    if [[ -f "/tmp/yt_worker_${WORKER_ID}_last_output.txt" ]]; then
      LAST_ERR=$(tail -n 2 "/tmp/yt_worker_${WORKER_ID}_last_output.txt" | tr '\n' ' ')
      log_message "FAILED reason: ${LAST_ERR}"
    fi
    log_message "FAILED: $VIDEO_ID"
    echo "$URL" >> "$LOG_DIR/worker_${WORKER_ID}_failed.txt"
    write_status "failed" "$VIDEO_ID" "max-retries"
  fi

  PAUSE=$((RANDOM % (MAX_SLEEP - MIN_SLEEP + 1) + MIN_SLEEP))
  sleep "$PAUSE"
done < "$CHUNK_FILE"

TOTAL_TIME=$(( $(date +%s) - START_TS ))
log_message "Completed. downloaded=$DOWNLOADED failed=$FAILED skipped=$SKIPPED runtime=$(format_duration "$TOTAL_TIME")"
write_status "done" "" "complete"

rm -rf "$WORK_DIR"
rm -f "/tmp/yt_worker_${WORKER_ID}_last_output.txt"
