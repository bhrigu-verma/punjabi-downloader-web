#!/usr/bin/env bash
set -euo pipefail

echo "Installing downloader dependencies..."

if command -v apt-get >/dev/null 2>&1; then
  apt-get update -qq
  apt-get install -y -qq ffmpeg tmux bc curl python3 python3-pip
else
  echo "WARN: apt-get not found. Install ffmpeg tmux bc curl python3 pip manually."
fi

if command -v pip3 >/dev/null 2>&1; then
  pip3 install --upgrade yt-dlp
else
  echo "WARN: pip3 not found. Install yt-dlp manually."
fi

if ! command -v deno >/dev/null 2>&1; then
  curl -fsSL https://deno.land/install.sh | sh
  echo "Add deno to PATH if needed: export PATH=\"$HOME/.deno/bin:$PATH\""
fi

echo "Done. Verify with: yt-dlp --version && ffmpeg -version | head -1"
