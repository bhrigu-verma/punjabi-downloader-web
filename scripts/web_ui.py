#!/usr/bin/env python3
"""Local web UI server for standalone downloader."""

from __future__ import annotations

import argparse
import base64
import binascii
import glob
import hmac
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


def parse_bool(raw: str | None, default: bool) -> bool:
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def parse_int(raw: str | None, default: int, min_value: int = 0) -> int:
    if raw is None:
        return default
    try:
        return max(min_value, int(raw.strip()))
    except (TypeError, ValueError):
        return default


def command_exists(name: str) -> bool:
    if shutil.which(name):
        return True
    return any(Path(p).exists() for p in [f"/opt/homebrew/bin/{name}", f"/usr/local/bin/{name}"])


def run_quick(cmd: List[str], cwd: Path, env: Dict[str, str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(cmd, cwd=str(cwd), env=env, text=True, capture_output=True, check=False)
    except FileNotFoundError as exc:
        return subprocess.CompletedProcess(args=cmd, returncode=127, stdout="", stderr=str(exc))


def now_ts() -> int:
    return int(time.time())


class AppState:
    def __init__(self, project_dir: Path, config_path: Path):
        self.project_dir = project_dir
        self.config_path = config_path
        self.config = parse_env_file(config_path)
        self.runtime_dir = project_dir / self.config.get("RUNTIME_SUBDIR", "runtime")
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.urls_file = project_dir / self.config.get("URLS_FILENAME", "urls.txt")
        self.archive_file = self.runtime_dir / self.config.get("ARCHIVE_FILENAME", "downloaded.txt")
        self.log_dir = self._resolve_project_path(self.config.get("LOG_DIR", str(Path("runtime") / "logs")))
        self.output_dir = self._resolve_project_path(
            self.config.get("OUTPUT_DIR", str(Path("data") / "raw" / "youtube"))
        )
        self.num_workers = int(self.config.get("NUM_WORKERS", "10"))
        self.browser_sequential_mode = parse_bool(self.config.get("BROWSER_SEQUENTIAL_MODE"), False)
        self.auto_resume_enabled = parse_bool(self.config.get("AUTO_RESUME_ENABLED"), True)
        self.auto_resume_cooldown_seconds = parse_int(
            self.config.get("AUTO_RESUME_COOLDOWN_SECONDS"), 12, min_value=0
        )
        self.auto_resume_max_attempts = parse_int(self.config.get("AUTO_RESUME_MAX_ATTEMPTS"), 300, min_value=1)
        self.ui_state_path = self.runtime_dir / "ui_state.json"
        self.ui_state = self._load_ui_state()
        self.basic_auth_enabled = parse_bool(
            os.environ.get("BASIC_AUTH_ENABLED", self.config.get("BASIC_AUTH_ENABLED")),
            False,
        )
        self.basic_auth_user = os.environ.get("BASIC_AUTH_USER", self.config.get("BASIC_AUTH_USER", ""))
        self.basic_auth_pass = os.environ.get("BASIC_AUTH_PASS", self.config.get("BASIC_AUTH_PASS", ""))

        if self.basic_auth_enabled and (not self.basic_auth_user or not self.basic_auth_pass):
            raise SystemExit("BASIC_AUTH_ENABLED=1 requires BASIC_AUTH_USER and BASIC_AUTH_PASS")

    def _resolve_project_path(self, value: str) -> Path:
        p = Path(value)
        if p.is_absolute():
            return p
        return (self.project_dir / p).resolve()

    def _default_ui_state(self) -> Dict:
        return {
            "manual_start_seen": False,
            "last_start_ts": 0,
            "last_start_source": "",
            "last_start_ok": False,
            "last_start_message": "",
            "auto_resume_attempts": 0,
            "last_auto_resume_ts": 0,
        }

    def _load_ui_state(self) -> Dict:
        data = self._default_ui_state()
        if not self.ui_state_path.exists():
            return data
        try:
            raw = json.loads(self.ui_state_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data.update(raw)
        except Exception:
            return data
        return data

    def _save_ui_state(self) -> None:
        self.ui_state_path.parent.mkdir(parents=True, exist_ok=True)
        self.ui_state_path.write_text(json.dumps(self.ui_state, ensure_ascii=True, indent=2), encoding="utf-8")

    def _count_lines(self, path: Path) -> int:
        if not path.exists():
            return 0
        with path.open("r", encoding="utf-8", errors="ignore") as f:
            return sum(1 for _ in f)

    def pending_urls(self) -> int:
        return max(self._count_lines(self.urls_file) - self._count_lines(self.archive_file), 0)

    def next_auto_resume_at(self) -> int:
        last_auto = int(self.ui_state.get("last_auto_resume_ts", 0) or 0)
        if last_auto <= 0:
            return 0
        return last_auto + self.auto_resume_cooldown_seconds

    def can_auto_resume(self, now_ts: int | None = None) -> tuple[bool, int, str]:
        now_ts = int(now_ts if now_ts is not None else time.time())
        if not self.auto_resume_enabled:
            return False, 0, "auto resume disabled"
        if not bool(self.ui_state.get("manual_start_seen", False)):
            return False, 0, "manual start required before auto resume"
        if self.pending_urls() <= 0:
            return False, 0, "no pending urls"
        attempts = int(self.ui_state.get("auto_resume_attempts", 0) or 0)
        if attempts >= self.auto_resume_max_attempts:
            return False, 0, "auto resume max attempts reached"
        next_at = self.next_auto_resume_at()
        if next_at > now_ts:
            return False, next_at, "auto resume cooldown active"
        return True, 0, "ok"

    def mark_start_attempt(self, source: str, ok: bool, message: str, now_ts: int | None = None) -> None:
        now_ts = int(now_ts if now_ts is not None else time.time())
        source = (source or "manual").strip().lower()
        if source not in {"manual", "auto"}:
            source = "manual"

        self.ui_state["last_start_ts"] = now_ts
        self.ui_state["last_start_source"] = source
        self.ui_state["last_start_ok"] = bool(ok)
        self.ui_state["last_start_message"] = message

        if source == "manual":
            if ok:
                self.ui_state["manual_start_seen"] = True
                self.ui_state["auto_resume_attempts"] = 0
                self.ui_state["last_auto_resume_ts"] = 0
        else:
            self.ui_state["auto_resume_attempts"] = int(self.ui_state.get("auto_resume_attempts", 0) or 0) + 1
            self.ui_state["last_auto_resume_ts"] = now_ts

        self._save_ui_state()

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

    def _auth_required(self, path: str) -> bool:
        if not self.state.basic_auth_enabled:
            return False
        if path.startswith("/api/"):
            return True
        return path in {"/dashboard", "/dashboard/", "/index.html", "/app.js"}

    def _is_authorized(self) -> bool:
        if not self.state.basic_auth_enabled:
            return True

        header = self.headers.get("Authorization", "")
        if not header.startswith("Basic "):
            return False

        token = header.split(" ", 1)[1].strip()
        try:
            decoded = base64.b64decode(token).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError):
            return False

        if ":" not in decoded:
            return False
        user, passwd = decoded.split(":", 1)
        return hmac.compare_digest(user, self.state.basic_auth_user) and hmac.compare_digest(
            passwd,
            self.state.basic_auth_pass,
        )

    def _auth_challenge(self) -> None:
        body = b"Authentication required"
        self.send_response(HTTPStatus.UNAUTHORIZED)
        self.send_header("WWW-Authenticate", 'Basic realm="Punjabi Downloader Dashboard"')
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

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

    def _yt_dlp_usable(self) -> bool:
        env = self.state.env()
        cwd = self.state.project_dir
        if run_quick(["yt-dlp", "--version"], cwd=cwd, env=env).returncode == 0:
            return True
        return run_quick(["python3", "-m", "yt_dlp", "--version"], cwd=cwd, env=env).returncode == 0

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

    def _read_lines(self, path: Path) -> List[str]:
        if not path.exists():
            return []
        return [ln.strip() for ln in path.read_text(encoding="utf-8", errors="ignore").splitlines() if ln.strip()]

    def _failed_urls(self, worker_id: str | None = None) -> Dict:
        if worker_id:
            p = self.state.log_dir / f"worker_{worker_id}_failed.txt"
            items = self._read_lines(p)
            return {"worker": worker_id, "count": len(items), "items": items}

        merged: Dict[str, List[str]] = {}
        total = 0
        for wid in self.state.worker_ids():
            p = self.state.log_dir / f"worker_{wid}_failed.txt"
            items = self._read_lines(p)
            merged[wid] = items
            total += len(items)
        return {"worker": "all", "count": total, "workers": merged}

    def _recent_outputs(self, limit: int = 20) -> Dict:
        wavs = sorted(
            self.state.output_dir.glob("*.wav"),
            key=lambda p: p.stat().st_mtime if p.exists() else 0,
            reverse=True,
        )
        rows = []
        for p in wavs[:limit]:
            stat = p.stat()
            rows.append(
                {
                    "name": p.name,
                    "size": stat.st_size,
                    "modified_ts": int(stat.st_mtime),
                    "path": str(p),
                }
            )
        return {"count": len(wavs), "items": rows}

    def _parse_status_file(self, path: Path) -> Dict[str, str]:
        data: Dict[str, str] = {}
        if not path.exists():
            return data
        for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            if "=" not in raw_line:
                continue
            key, val = raw_line.split("=", 1)
            data[key.strip()] = val.strip()
        return data

    def _status_payload(self) -> Dict:
        tmux_running = self._tmux_running()
        timestamp = now_ts()
        total_urls = self._count_lines(self.state.urls_file)
        total_done = self._count_lines(self.state.archive_file)
        pending_urls = self.state.pending_urls()
        wav_count = len(glob.glob(str(self.state.output_dir / "*.wav")))

        workers = []
        totals = {"downloaded": 0, "failed": 0, "skipped": 0}
        for wid in self.state.worker_ids():
            log_file = self.state.log_dir / f"worker_{wid}.log"
            status_file = self.state.log_dir / f"worker_{wid}.status"
            chunk_file = self.state.runtime_dir / f"chunk_{wid}.txt"
            chunk_total = self._count_lines(chunk_file)
            dl = fl = sk = 0
            last = ""
            status_data = self._parse_status_file(status_file)
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
                    "status": status_data.get("STATUS", ""),
                    "video_id": status_data.get("VIDEO_ID", ""),
                    "extra": status_data.get("EXTRA", ""),
                    "status_ts": int(status_data.get("TIMESTAMP", "0") or 0),
                }
            )
            totals["downloaded"] += dl
            totals["failed"] += fl
            totals["skipped"] += sk

        can_auto_resume, next_at, auto_reason = self.state.can_auto_resume(now_ts=timestamp)
        if tmux_running:
            can_auto_resume = False
            auto_reason = "workers already running"

        success_total = totals["downloaded"] + totals["failed"]
        success_rate = (totals["downloaded"] / success_total * 100.0) if success_total > 0 else 100.0

        return {
            "tmux_running": tmux_running,
            "total_urls": total_urls,
            "total_done": total_done,
            "pending_urls": pending_urls,
            "wav_count": wav_count,
            "workers": workers,
            "totals": totals,
            "success_rate": round(success_rate, 1),
            "output_dir": str(self.state.output_dir),
            "log_dir": str(self.state.log_dir),
            "runtime_dir": str(self.state.runtime_dir),
            "browser_sequential_mode": self.state.browser_sequential_mode,
            "manual_start_seen": bool(self.state.ui_state.get("manual_start_seen", False)),
            "auto_resume_enabled": self.state.auto_resume_enabled,
            "auto_resume_attempts": int(self.state.ui_state.get("auto_resume_attempts", 0) or 0),
            "auto_resume_max_attempts": self.state.auto_resume_max_attempts,
            "auto_resume_cooldown_seconds": self.state.auto_resume_cooldown_seconds,
            "next_auto_resume_at": self.state.next_auto_resume_at(),
            "auto_resume_can_start": can_auto_resume,
            "auto_resume_reason": auto_reason,
            "auto_resume_next_at": next_at,
            "last_start": {
                "ts": int(self.state.ui_state.get("last_start_ts", 0) or 0),
                "source": str(self.state.ui_state.get("last_start_source", "") or ""),
                "ok": bool(self.state.ui_state.get("last_start_ok", False)),
                "message": str(self.state.ui_state.get("last_start_message", "") or ""),
            },
            "timestamp": timestamp,
        }

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path

        if self._auth_required(path) and not self._is_authorized():
            self._auth_challenge()
            return

        if path in ["/", "/landing.html"]:
            self._serve_static("landing.html")
            return
        if path in ["/dashboard", "/dashboard/", "/index.html"]:
            self._serve_static("index.html")
            return
        if path == "/styles.css":
            self._serve_static("styles.css")
            return
        if path == "/app.js":
            self._serve_static("app.js")
            return
        if path == "/healthz":
            self._json({"ok": True, "timestamp": now_ts()})
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
            sequential_ok = (not self.state.browser_sequential_mode) or self.state.num_workers == 1
            checks = {
                "yt-dlp": self._yt_dlp_usable(),
                "ffmpeg": command_exists("ffmpeg"),
                "tmux": command_exists("tmux"),
                "bc": command_exists("bc"),
                "urls_file": self.state.urls_file.exists(),
                "sequential_mode": sequential_ok,
            }
            self._json(
                {
                    "checks": checks,
                    "ok": all(checks.values()),
                    "browser_sequential_mode": self.state.browser_sequential_mode,
                    "num_workers": self.state.num_workers,
                }
            )
            return
        if path == "/api/config":
            self._json(
                {
                    "num_workers": self.state.num_workers,
                    "browser_sequential_mode": self.state.browser_sequential_mode,
                    "auto_resume_enabled": self.state.auto_resume_enabled,
                    "auto_resume_cooldown_seconds": self.state.auto_resume_cooldown_seconds,
                    "auto_resume_max_attempts": self.state.auto_resume_max_attempts,
                    "basic_auth_enabled": self.state.basic_auth_enabled,
                    "runtime_subdir": self.state.config.get("RUNTIME_SUBDIR", "runtime"),
                    "download_format": self.state.config.get("DOWNLOAD_FORMAT", "bestaudio/best"),
                    "use_cookies": parse_bool(self.state.config.get("USE_COOKIES"), True),
                    "strict_wav_validation": parse_bool(self.state.config.get("STRICT_WAV_VALIDATION"), False),
                    "audio_sample_rate": parse_int(self.state.config.get("AUDIO_SAMPLE_RATE"), 16000),
                    "audio_channels": parse_int(self.state.config.get("AUDIO_CHANNELS"), 1),
                }
            )
            return
        if path == "/api/failed":
            q = parse_qs(parsed.query)
            wid = q.get("worker", [""])[0]
            if wid and wid not in self.state.worker_ids():
                self._json({"error": "invalid worker"}, status=400)
                return
            self._json(self._failed_urls(wid if wid else None))
            return
        if path == "/api/recent-outputs":
            q = parse_qs(parsed.query)
            limit = max(1, min(100, parse_int(q.get("limit", ["20"])[0], 20, min_value=1)))
            self._json(self._recent_outputs(limit))
            return

        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if self._auth_required(path) and not self._is_authorized():
            self._auth_challenge()
            return

        if path == "/api/start":
            source = (query.get("source", ["manual"])[0] or "manual").strip().lower()
            if source not in {"manual", "auto"}:
                source = "manual"

            if self.state.browser_sequential_mode and self.state.num_workers != 1:
                self._json(
                    {
                        "ok": False,
                        "message": "browser sequential mode requires NUM_WORKERS=1",
                        "num_workers": self.state.num_workers,
                    },
                    status=400,
                )
                return

            pending = self.state.pending_urls()
            if pending <= 0:
                self._json({"ok": True, "message": "no pending urls"})
                return

            if source == "auto":
                can_resume, next_at, reason = self.state.can_auto_resume()
                if not can_resume:
                    self._json(
                        {
                            "ok": False,
                            "message": reason,
                            "next_auto_resume_at": next_at,
                        },
                        status=429,
                    )
                    return

            if self._tmux_running():
                self.state.mark_start_attempt(source, True, "already running")
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
                self.state.mark_start_attempt(source, True, "workers started")
                self._json({"ok": True, "message": "workers started"})
                return
            out, err = proc.communicate(timeout=15)
            self.state.mark_start_attempt(source, False, "failed to start")
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
    if state.basic_auth_enabled:
        print("Dashboard/API basic auth: enabled")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
