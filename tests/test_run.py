import importlib.util
import contextlib
import io
import json
import os
import shutil
import subprocess
import unittest
import tempfile
from types import SimpleNamespace
from unittest.mock import patch
from datetime import datetime
from pathlib import Path

spec = importlib.util.spec_from_file_location("twohours_run", Path(__file__).parents[1] / "scripts/run.py")
run = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run)


class RunTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "renderer requires Node.js")
    def test_report_renders_chinese_labels_and_preserves_lists_and_code(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = ('---\ntitle: 工作线\ntemplate: doc\n---\n\n## A1 审计\n\n'
                      '**工作线结论：**多轮只读审计确认第1步存在缺口。 [S1]\n\n'
                      '**背景：**未读到可靠的先前判断。\n\n'
                      '- **证据：**保留来源 [S1]\n- 普通列表\n\n'
                      '`**代码：**保持原样`\n\n```text\n**代码：**保持原样\n```\n')
            (root / 'report.md').write_text(report)
            (root / 'manifest.json').write_text(json.dumps({'sources': [{'id': 'S1'}], 'panels': [{'id': 'A1', 'source_ids': ['S1']}]}))
            (root / 'board.json').write_text(json.dumps({'title': '工作线', 'period_label': '昨日', 'review_label': '今天', 'coverage_label': '完整', 'cards': [{'id': 'A1', 'title': '审计', 'lane': 'decide'}]}))
            with contextlib.redirect_stdout(io.StringIO()):
                run.render(SimpleNamespace(run=str(root)))
            output = (root / 'report.html').read_text()
            self.assertIn('<strong>工作线结论：</strong> 多轮只读审计', output)
            self.assertIn('<strong>背景：</strong> 未读到', output)
            self.assertIn('<li><strong>证据：</strong> 保留来源 [S1]</li>', output)
            self.assertIn('<li>普通列表</li>', output)
            self.assertIn('<code>**代码：**保持原样</code>', output)
            self.assertEqual((root / 'report.md').read_text(), report)

    def test_label_spacing_does_not_change_fenced_or_indented_code(self):
        code = '```text\n**背景：**正文\n```\n    **背景：**正文\n'
        self.assertEqual(run.normalize_label_spacing(code), code)
        for code in ('- ```text\n  **字面量：**精确保留\n  ```\n', '`起点\n**字面量：**精确保留\n终点`\n'):
            report = code + '\n**结论：**正文\n'
            self.assertEqual(run.normalize_label_spacing(report), code + '\n**结论：** 正文\n')

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
            for name in ("blurt", "cubox", "answer-me-with-html"):
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
            ready = subprocess.CompletedProcess([], 0, json.dumps({"version": "0.6.0", "commit": "pinned-commit", "built": True}), "")
            with patch.object(run, "ROOT", root), patch.object(run.shutil, "which", side_effect=lambda name: None if name == "cubox-cli" else "/bin/" + name) as which, patch.object(run.subprocess, "check_output", return_value="v22.16.0") as version_check, patch.object(run.subprocess, "run", return_value=ready) as workflow_check:
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    self.assertEqual(run.doctor(None), 0)
                report = json.loads(output.getvalue())
                self.assertEqual(report["commands"]["cubox-cli"], str(binary))
                self.assertNotIn("agent-note", report["commands"])
                self.assertNotIn("traceink", report["skills"])
                self.assertEqual(report["work_review"], ["/bin/node", str(root / "scripts/work_review.mjs")])
                self.assertEqual(report["agent_note_source"]["version"], "0.6.0")
                self.assertEqual(workflow_check.call_args.args[0][-1], "--check")
                self.assertEqual(report["recorder"]["name"], "Two Hours")
                self.assertEqual(report["recorder"]["bundle_id"], "dev.twohours.recorder")
                self.assertEqual(report["recording_permissions"], "not_tested")
                self.assertEqual(Path(report["asr"][-1]), root / "vendor/blurt/scripts/doctor.py")
                for response in (subprocess.CompletedProcess([], 1, "", "source drift"), subprocess.CompletedProcess([], 0, "bad json", ""), subprocess.CompletedProcess([], 0, '{"built": false}', "")):
                    with self.subTest(workflow_response=response):
                        workflow_check.return_value = response
                        output = io.StringIO()
                        with contextlib.redirect_stdout(output):
                            self.assertEqual(run.doctor(None), 1)
                        report = json.loads(output.getvalue())
                        self.assertIn("bundled agent-note workflow (run work_review_setup)", report["missing"])
                        self.assertTrue(report["work_review_error"])
                workflow_check.return_value = ready
                workflow_check.side_effect = subprocess.TimeoutExpired("workflow", 30)
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    self.assertEqual(run.doctor(None), 1)
                self.assertIn("bundled agent-note workflow (run work_review_setup)", json.loads(output.getvalue())["missing"])
                workflow_check.side_effect = None
                version_check.return_value = "v22.15.0"
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    self.assertEqual(run.doctor(None), 1)
                self.assertIn("Node.js >=22.16 (agent-note-cli)", json.loads(output.getvalue())["missing"])
                version_check.return_value = "v22.16.0"
                binary.unlink()
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    self.assertEqual(run.doctor(None), 1)
                self.assertIn("cubox-cli", json.loads(output.getvalue())["missing"])
                self.assertNotIn("agent-note", [call.args[0] for call in which.call_args_list])

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
