# Punjabi Downloader Web

Standalone web + backend YouTube downloader pipeline extracted from the Punjabi Voice project.

This repo includes:

- Public landing page + protected dashboard flow
- Web UI launch dashboard (start/stop, live status, logs, failed queue, recent outputs)
- Backend API server
- Parallel tmux worker downloader
- WAV conversion pipeline for Punjabi audio dataset building

## Project Layout

```
config/
  default.env
  single_worker_android_nocookie.env
  single_worker_user_cookie.env
scripts/
  install_deps.sh
  run_ui.sh
  web_ui.py
  run_all.sh
  worker.sh
  monitor.sh
web/
  index.html
  app.js
  styles.css
```

## Prerequisites

- Python 3.9+
- yt-dlp
- ffmpeg
- tmux
- bc
- curl
- Optional: deno (improves signature handling for some videos)

The pipeline will not run if core dependencies are missing. Check quickly:

```bash
curl -s http://127.0.0.1:8787/api/preflight
```

For a compact pass/fail output:

```bash
curl -s http://127.0.0.1:8787/api/preflight | python3 -m json.tool
```

## Install Dependencies

### Linux (Ubuntu/Debian)

```bash
bash scripts/install_deps.sh
```

### macOS

```bash
brew install ffmpeg tmux bc
python3 -m pip install -U yt-dlp
```

## Quick Start (Web UI + Backend)

### 1) Clone and enter project

```bash
git clone https://github.com/<your-username>/punjabi-downloader-web.git
cd punjabi-downloader-web
```

### 2) Prepare input URLs

```bash
cp urls.txt.example urls.txt
```

Edit urls.txt and keep one YouTube URL per line.

### 3) Run with no-cookie single worker config

```bash
export DOWNLOADER_CONFIG=$PWD/config/single_worker_android_nocookie.env
bash scripts/run_ui.sh
```

### 4) Open dashboard

Open URLs:

- Landing page: http://127.0.0.1:8787/
- Dashboard: http://127.0.0.1:8787/dashboard

From the UI:

- Click Start once to launch downloader workers
- Watch mission metrics, worker radar, activity feed, and live logs
- Inspect failed URL queue and copy failed URLs for replay
- Track recent WAV outputs with file size and freshness
- Click Stop to stop workers

Browser sequential mode behavior:

- Uses one worker globally (one URL at a time)
- After your first manual Start, the browser auto-resumes downloading if workers stop and pending URLs remain
- Auto-resume uses cooldown and attempt limits from config

## Basic Auth (for user-facing access)

Enable auth for dashboard and all `/api/*` routes:

```bash
export BASIC_AUTH_ENABLED=1
export BASIC_AUTH_USER=admin
export BASIC_AUTH_PASS='change-this-password'
```

Then run UI normally:

```bash
bash scripts/run_ui.sh
```

Notes:

- Landing page `/` stays public
- Dashboard `/dashboard` and API require credentials
- If `BASIC_AUTH_ENABLED=1` and user/pass are missing, server exits with an error

Quick auth verification:

```bash
curl -i http://127.0.0.1:8787/dashboard
curl -i -u admin:'change-this-password' http://127.0.0.1:8787/dashboard
```

## Deploy (Docker)

Build image:

```bash
docker build -t punjabi-downloader-web .
```

Run container:

```bash
docker run --rm -p 8787:8787 \
  -e PORT=8787 \
  -e DOWNLOADER_CONFIG=/app/config/single_worker_android_nocookie.env \
  -e BASIC_AUTH_ENABLED=1 \
  -e BASIC_AUTH_USER=admin \
  -e BASIC_AUTH_PASS='change-this-password' \
  -v $(pwd)/urls.txt:/app/urls.txt \
  -v $(pwd)/runtime:/app/runtime \
  -v $(pwd)/data:/app/data \
  punjabi-downloader-web
```

Health check endpoint:

```bash
curl -s http://127.0.0.1:8787/healthz
```

## CLI Mode (without Web UI)

```bash
export DOWNLOADER_CONFIG=$PWD/config/single_worker_android_nocookie.env
bash scripts/run_all.sh
```

Run without monitor:

```bash
bash scripts/run_all.sh --no-monitor
```

Open monitor later:

```bash
bash scripts/monitor.sh
```

## Cookie Mode (optional)

If you want cookie-based downloads, create real cookie files:

```bash
cp cookies.txt.example cookies.txt
cp cookies1.txt.example cookies1.txt
cp cookies2.txt.example cookies2.txt
cp cookies3.txt.example cookies3.txt
cp cookies4.txt.example cookies4.txt
```

Then replace these files with your real Netscape cookie exports.

Run cookie config:

```bash
export DOWNLOADER_CONFIG=$PWD/config/default.env
bash scripts/run_ui.sh
```

## Useful Commands

Check API status:

```bash
curl -s http://127.0.0.1:8787/api/status
```

Check launch config snapshot:

```bash
curl -s http://127.0.0.1:8787/api/config
```

Inspect failed URL queue:

```bash
curl -s http://127.0.0.1:8787/api/failed
```

Inspect recent WAV outputs:

```bash
curl -s http://127.0.0.1:8787/api/recent-outputs
```

Start workers through API:

```bash
curl -s -X POST http://127.0.0.1:8787/api/start
```

Stop workers through API:

```bash
curl -s -X POST http://127.0.0.1:8787/api/stop
```

Attach to worker tmux session:

```bash
tmux attach -t yt_workers
```

## Output and Runtime Files

- WAV output: data/raw/youtube/
- Runtime state: runtime/ or runtime_single_test/
- Worker logs: runtime*/logs/
- Download archive: runtime*/downloaded.txt

## Configuration

Select config file at runtime:

```bash
export DOWNLOADER_CONFIG=$PWD/config/default.env
```

Main config knobs:

- NUM_WORKERS
- BROWSER_SEQUENTIAL_MODE
- AUTO_RESUME_ENABLED
- AUTO_RESUME_COOLDOWN_SECONDS
- AUTO_RESUME_MAX_ATTEMPTS
- BASIC_AUTH_ENABLED
- BASIC_AUTH_USER
- BASIC_AUTH_PASS
- USE_COOKIES
- DOWNLOAD_FORMAT
- STRICT_WAV_VALIDATION
- WAV_CODEC
- MAX_RETRIES
- PO_ARGS
- OUTPUT_DIR
- LOG_DIR

## Troubleshooting

yt-dlp unusable:

```bash
python3 -m pip install -U yt-dlp
```

Port 8787 busy:

- run_ui.sh automatically picks next free port.

Exit code 143 while restarting:

- Exit code `143` usually means the previous process received SIGTERM during restart.
- If the next `run_ui.sh` launch succeeds, this is expected and not a pipeline failure.

Workers not running:

```bash
tmux ls
```

Auto-resume not triggering:

- Ensure you clicked Start at least once manually in the UI
- Check status payload fields with:

```bash
curl -s http://127.0.0.1:8787/api/status
```

- Verify `manual_start_seen=true` and `pending_urls>0`

No output files:

- Check runtime*/logs/worker_00.log
- Verify URLs in urls.txt
- Try no-cookie config first to validate baseline pipeline

WAV strict validation failures:

- Check worker logs for `WAV validation failed`
- Confirm ffmpeg/ffprobe are installed
- Confirm config audio values match your target: `AUDIO_SAMPLE_RATE`, `AUDIO_CHANNELS`, `WAV_CODEC`
