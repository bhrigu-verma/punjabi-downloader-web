import importlib.util
import tempfile
import unittest
from pathlib import Path


def load_web_ui_module(project_root: Path):
    mod_path = project_root / "scripts" / "web_ui.py"
    spec = importlib.util.spec_from_file_location("web_ui", str(mod_path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestWebUiState(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name)
        (self.project / "scripts").mkdir(parents=True, exist_ok=True)
        (self.project / "config").mkdir(parents=True, exist_ok=True)
        (self.project / "runtime" / "logs").mkdir(parents=True, exist_ok=True)
        (self.project / "data" / "raw" / "youtube").mkdir(parents=True, exist_ok=True)

        src = Path("/Users/bhriguverma/punjabi_voice/punjabi-downloader-web/scripts/web_ui.py")
        (self.project / "scripts" / "web_ui.py").write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

        (self.project / "urls.txt").write_text(
            "https://www.youtube.com/watch?v=a\n"
            "https://www.youtube.com/watch?v=b\n"
            "https://www.youtube.com/watch?v=c\n",
            encoding="utf-8",
        )
        (self.project / "runtime" / "downloaded.txt").write_text("youtube a\n", encoding="utf-8")

        self.config = self.project / "config" / "test.env"
        self.config.write_text(
            "RUNTIME_SUBDIR=runtime\n"
            "URLS_FILENAME=urls.txt\n"
            "ARCHIVE_FILENAME=downloaded.txt\n"
            "LOG_DIR=runtime/logs\n"
            "OUTPUT_DIR=data/raw/youtube\n"
            "NUM_WORKERS=1\n"
            "BROWSER_SEQUENTIAL_MODE=1\n"
            "AUTO_RESUME_ENABLED=1\n"
            "AUTO_RESUME_COOLDOWN_SECONDS=10\n"
            "AUTO_RESUME_MAX_ATTEMPTS=2\n",
            encoding="utf-8",
        )

        self.web_ui = load_web_ui_module(self.project)

    def tearDown(self):
        self.tmp.cleanup()

    def test_pending_urls_is_total_minus_archive(self):
        state = self.web_ui.AppState(self.project, self.config)
        self.assertEqual(state.pending_urls(), 2)

    def test_auto_resume_requires_manual_start_first(self):
        state = self.web_ui.AppState(self.project, self.config)
        allowed, _, reason = state.can_auto_resume(now_ts=100)
        self.assertFalse(allowed)
        self.assertIn("manual", reason.lower())

    def test_manual_then_auto_has_cooldown(self):
        state = self.web_ui.AppState(self.project, self.config)
        state.mark_start_attempt("manual", True, "manual start", now_ts=90)

        allowed, _, _ = state.can_auto_resume(now_ts=100)
        self.assertTrue(allowed)

        state.mark_start_attempt("auto", False, "auto failed", now_ts=100)
        allowed, next_at, reason = state.can_auto_resume(now_ts=105)
        self.assertFalse(allowed)
        self.assertGreater(next_at, 105)
        self.assertIn("cooldown", reason.lower())

        allowed, _, _ = state.can_auto_resume(now_ts=111)
        self.assertTrue(allowed)

    def test_failed_manual_start_does_not_unlock_auto_resume(self):
        state = self.web_ui.AppState(self.project, self.config)
        state.mark_start_attempt("manual", False, "manual failed", now_ts=90)

        allowed, _, reason = state.can_auto_resume(now_ts=100)
        self.assertFalse(allowed)
        self.assertIn("manual", reason.lower())


if __name__ == "__main__":
    unittest.main()
