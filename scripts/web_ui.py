#!/usr/bin/env python3
"""Local web UI server for standalone downloader."""

from __future__ import annotations

import argparse
import glob
import json
import os
import shlex
import shutil
import subprocess
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List
from urllib.parse import parse_qs, urlparse


def parse_env_file(path: Path) -> Dict[str, str]:
    data: Dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip()
        if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
            val = val[1:-1]
        data[key] = val
    return data


def command_exists(name: str) -> bool:
    if shutil.which(name):
        return True
    return any(Path(p).exists() for p in [f"/opt/homebrew/bin/{name}", f"/usr/local/bin/{name}"])


def run_quick(cmd: List[str], cwd: Path, env: Dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=str(cwd), env=env, text=True, capture_output=True, check=False)


class AppState:
    def __init__(self, project_dir: Path, config_path: Path):
        self.project_dir = project_dir
        self.config_path = config_path
        self.config = parse_env_file(config_path)
        self.runtime_dir = project_dir / self.config.get("RUNTIME_SUBDIR", "runtime")
        self.urls_file = project_dir / self.config.get("URLS_FILENAME", "urls.txt")
        self.archive_file = self.runtime_dir / self.config.get("ARCHIVE_FILENAME", "downloaded.txt")
        self.log_dir = self._resolve_project_path(self.config.get("LOG_DIR", str(Path("runtime") / "logs")))
        self.output_dir = self._resolve_project_path(
            self.config.get("OUTPUT_DIR", str(Path("data") / "raw" / "youtube"))
        )
        self.num_workers = int(self.config.get("NUM_WORKERS", "10"))

    def _resolve_project_path(self, value: str) -> Path:
        p = Path(value)
        if p.is_absolute():
            return p
        return (self.project_dir / p).resolve()

    def env(self) -> Dict[str, str]:
        env = os.environ.copy()
        env["DOWNLOADER_CONFIG"] = str(self.config_path)
        repo_root = self.project_dir.parent.parent
        workspace_root = self.project_dir.parent.parent.parent
        path_parts = [
            str(workspace_root / ".venv-2" / "bin"),
            str(repo_root / ".venv-2" / "bin"),
            str(workspace_root / ".venv" / "bin"),
            str(repo_root / ".venv" / "bin"),
            str(workspace_root / ".venv-yt" / "bin"),
            str(repo_root / ".venv-yt" / "bin"),
            str(workspace_root / ".venv-1" / "bin"),
            str(repo_root / ".venv-1" / "bin"),
            "/opt/homebrew/bin",
            "/usr/local/bin",
            env.get("PATH", ""),
        ]
        env["PATH"] = ":".join([p for p in path_parts if p])
        return env

    def worker_ids(self) -> List[str]:
        return [f"{i:02d}" for i in range(self.num_workers)]


class Handler(BaseHTTPRequestHandler):
    state: AppState

    def _json(self, payload: Dict, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _text(self, payload: str, status: int = 200) -> None:
        body = payload.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_static(self, relative: str) -> None:
        static_root = self.state.project_dir / "web"
        file_path = (static_root / relative).resolve()
        if not str(file_path).startswith(str(static_root.resolve())) or not file_path.exists() or not file_path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        ctype = "text/plain"
        if file_path.suffix == ".html":
            ctype = "text/html; charset=utf-8"
        elif file_path.suffix == ".css":
            ctype = "text/css; charset=utf-8"
        elif file_path.suffix == ".js":
            ctype = "application/javascript; charset=utf-8"
        data = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _tmux_running(self) -> bool:
        proc = run_quick(["tmux", "has-session", "-t", "yt_workers"], cwd=self.state.project_dir, env=self.state.env())
        return proc.returncode == 0

    def _count_lines(self, path: Path) -> int:
        if not path.exists():
            return 0
        with path.open("r", encoding="utf-8", errors="ignore") as f:
            return sum(1 for _ in f)

    def _tail(self, path: Path, n: int) -> str:
        if not path.exists():
            return ""
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        return "\n".join(lines[-n:])

    def _status_payload(self) -> Dict:
        total_urls = self._count_lines(self.state.urls_file)
        total_done = self._count_lines(self.state.archive_file)
        wav_count = len(glob.glob(str(self.state.output_dir / "*.wav")))

        workers = []
        totals = {"downloaded": 0, "failed": 0, "skipped": 0}
        for wid in self.state.worker_ids():
            log_file = self.state.log_dir / f"worker_{wid}.log"
            chunk_file = self.state.runtime_dir / f"chunk_{wid}.txt"
            chunk_total = self._count_lines(chunk_file)
            dl = fl = sk = 0
            last = ""
            if log_file.exists():
                content = log_file.read_text(encoding="utf-8", errors="ignore")
                dl = content.count("DOWNLOADED:")
                fl = content.count("FAILED:")
                sk = content.count("SKIPPED")
                lines = [ln for ln in content.splitlines() if ln.strip()]
                if lines:
                    last = lines[-1]
            processed = dl + fl + sk
            pct = int((processed * 100 / chunk_total) if chunk_total else 0)
            workers.append(
                {
                    "id": wid,
                    "chunk_total": chunk_total,
                    "downloaded": dl,
                    "failed": fl,
                    "skipped": sk,
                    "processed": processed,
                    "pct": pct,
                    "last": last,
                }
            )
            totals["downloaded"] += dl
            totals["failed"] += fl
            totals["skipped"] += sk

        return {
            "tmux_running": self._tmux_running(),
            "total_urls": total_urls,
            "total_done": total_done,
            "wav_count": wav_count,
            "workers": workers,
            "totals": totals,
            "output_dir": str(self.state.output_dir),
            "log_dir": str(self.state.log_dir),
            "runtime_dir": str(self.state.runtime_dir),
            "timestamp": int(time.time()),
        }

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path

        if path in ["/", "/index.html"]:
            self._serve_static("index.html")
            return
        if path == "/styles.css":
            self._serve_static("styles.css")
            return
        if path == "/app.js":
            self._serve_static("app.js")
            return
        if path == "/api/status":
            self._json(self._status_payload())
            return
        if path == "/api/logs":
            q = parse_qs(parsed.query)
            wid = q.get("worker", ["00"])[0]
            if wid not in self.state.worker_ids():
                self._json({"error": "invalid worker"}, status=400)
                return
            n = int(q.get("lines", ["120"])[0])
            n = max(10, min(n, 2000))
            log_file = self.state.log_dir / f"worker_{wid}.log"
            self._json({"worker": wid, "content": self._tail(log_file, n)})
            return
        if path == "/api/preflight":
            checks = {
                "yt-dlp": command_exists("yt-dlp"),
                "ffmpeg": command_exists("ffmpeg"),
                "tmux": command_exists("tmux"),
                "bc": command_exists("bc"),
                "urls_file": self.state.urls_file.exists(),
            }
            self._json({"checks": checks, "ok": all(checks.values())})
            return

        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/start":
            if self._tmux_running():
                self._json({"ok": True, "message": "already running"})
                return
            cmd = ["bash", str(self.state.project_dir / "scripts" / "run_all.sh"), "--no-monitor"]
            proc = subprocess.Popen(
                cmd,
                cwd=str(self.state.project_dir),
                env=self.state.env(),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            time.sleep(1.5)
            running = self._tmux_running()
            if running:
                self._json({"ok": True, "message": "workers started"})
                return
            out, err = proc.communicate(timeout=15)
            self._json(
                {
                    "ok": False,
                    "message": "failed to start",
                    "stdout": out[-2000:],
                    "stderr": err[-2000:],
                    "cmd": " ".join(shlex.quote(c) for c in cmd),
                },
                status=500,
            )
            return

        if path == "/api/stop":
            proc = run_quick(["tmux", "kill-session", "-t", "yt_workers"], cwd=self.state.project_dir, env=self.state.env())
            if proc.returncode == 0:
                self._json({"ok": True, "message": "workers stopped"})
            else:
                self._json({"ok": False, "message": "session not running"}, status=400)
            return

        self.send_error(HTTPStatus.NOT_FOUND)


def main() -> None:
    parser = argparse.ArgumentParser(description="Local downloader web UI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--project", required=True)
    args = parser.parse_args()

    project_dir = Path(args.project).resolve()
    config_path = Path(os.environ.get("DOWNLOADER_CONFIG", str(project_dir / "config" / "default.env"))).resolve()
    state = AppState(project_dir, config_path)

    Handler.state = state
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"UI server running at http://{args.host}:{args.port}")
    print(f"Using config: {config_path}")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
