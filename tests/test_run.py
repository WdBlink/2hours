import importlib.util
import contextlib
import io
import json
import os
import shutil
import unittest
import tempfile
from unittest.mock import patch
from datetime import datetime
from pathlib import Path

spec = importlib.util.spec_from_file_location("twohours_run", Path(__file__).parents[1] / "scripts/run.py")
run = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run)


class RunTests(unittest.TestCase):
    def test_bundled_skills_precede_global_install(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundled = root / "vendor/blurt"
            global_skill = root / "codex/skills/blurt"
            for path in (bundled, global_skill):
                path.mkdir(parents=True)
                (path / "SKILL.md").write_text("example")
            with patch.object(run, "ROOT", root), patch.object(run, "codex_home", return_value=root / "codex"):
                self.assertEqual(run.skill_dir("blurt"), bundled)
                (bundled / "SKILL.md").unlink()
                self.assertEqual(run.skill_dir("blurt"), global_skill)

    def test_doctor_uses_bundled_executable_and_reports_missing_binary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("traceink", "blurt", "cubox", "answer-me-with-html"):
                path = root / "vendor" / name
                path.mkdir(parents=True)
                (path / "SKILL.md").write_text("example")
            (root / "scripts").mkdir()
            (root / "scripts/review_bridge.py").write_text("")
            recorder = root / "vendor/blurt/scripts/_identity.py"
            recorder.parent.mkdir()
            shutil.copyfile(run.ROOT / "vendor/blurt/scripts/_identity.py", recorder)
            (root / "assets/brand").mkdir(parents=True)
            for name in ("icon.png", "MenuIconTemplate.png", "MenuIconTemplate@2x.png"):
                (root / "assets/brand" / name).write_bytes(b"fixture")
            binary = root / "vendor/cubox/cli/bin" / ("cubox-cli.exe" if os.name == "nt" else "cubox-cli")
            binary.parent.mkdir(parents=True)
            binary.write_text("fixture")
            binary.chmod(0o755)
            with patch.object(run, "ROOT", root), patch.object(run.shutil, "which", side_effect=lambda name: None if name == "cubox-cli" else "/bin/" + name), patch.object(run.subprocess, "check_output", return_value="v20.0.0"):
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    self.assertEqual(run.doctor(None), 0)
                report = json.loads(output.getvalue())
                self.assertEqual(report["commands"]["cubox-cli"], str(binary))
                self.assertEqual(report["recorder"]["name"], "Two Hours")
                self.assertEqual(report["recorder"]["bundle_id"], "dev.twohours.recorder")
                self.assertEqual(report["recording_permissions"], "not_tested")
                self.assertTrue(report["asr"][-1].endswith("vendor/blurt/scripts/doctor.py"))
                binary.unlink()
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    self.assertEqual(run.doctor(None), 1)
                self.assertIn("cubox-cli", json.loads(output.getvalue())["missing"])

    def test_week_is_calendar_week(self):
        scope = run.scope_for("last-week", "Asia/Shanghai", now=datetime.fromisoformat("2026-10-23T09:00:00+08:00"))
        self.assertEqual(scope["start"], "2026-10-12T00:00:00+08:00")
        self.assertEqual(scope["end_exclusive"], "2026-10-19T00:00:00+08:00")

    def test_recent_days_stops_at_now(self):
        scope = run.scope_for("last-7-days", "Asia/Shanghai", now=datetime.fromisoformat("2026-10-23T09:00:00+08:00"))
        self.assertEqual(scope["start"], "2026-10-17T00:00:00+08:00")
        self.assertEqual(scope["end_exclusive"], "2026-10-23T09:00:00+08:00")

    def test_bad_ranges_rejected(self):
        for start, end in [("2026-10-23", None), ("2026-10-24", "2026-10-22")]:
            with self.assertRaises(ValueError):
                run.scope_for("yesterday", "Asia/Shanghai", start, end)

    def test_manifest_and_unsafe_markup(self):
        m = {"sources": [{"id": "S1"}], "panels": [{"id": "A1", "source_ids": ["S1"]}]}
        run.validate_report(m, "## A1 实验\n已读取的事实。")
        for text in ["## B1 错误", "## A1 内容\n<script>alert(1)</script>", "## A1 内容\n```html\n危险\n```", "## A1 内容\n```py src=/secrets\n```"]:
            with self.assertRaises(ValueError):
                run.validate_report(m, text)

    def test_verified_empty_period_can_report_no_activity(self):
        m = {"sources": [], "panels": [{"id": "A1", "source_ids": []}], "coverage": {"status": "complete"}}
        run.validate_report(m, "## A1 本期没有新增工作或收藏")
        m["coverage"]["status"] = "not_collected"
        with self.assertRaises(ValueError):
            run.validate_report(m, "## A1 尚未收集")


if __name__ == "__main__":
    unittest.main()
