# Punjabi Downloader Web

Standalone web + backend YouTube downloader pipeline extracted from the Punjabi Voice project.

This repo includes:

- Web UI dashboard (start/stop, live status, logs)
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

Open http://127.0.0.1:8787 in your browser.

From the UI:

- Click Start to launch downloader workers
- Watch live progress and logs
- Click Stop to stop workers

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
- USE_COOKIES
- DOWNLOAD_FORMAT
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

Workers not running:

```bash
tmux ls
```

No output files:

- Check runtime*/logs/worker_00.log
- Verify URLs in urls.txt
- Try no-cookie config first to validate baseline pipeline
