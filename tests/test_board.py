import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


SCRIPT = Path(__file__).parents[1] / "scripts/build_board.py"
SPEC = importlib.util.spec_from_file_location("build_board", SCRIPT)
board = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(board)


class BoardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.run = Path(self.temp.name)
        (self.run / "manifest.json").write_text(json.dumps({"panels": [{"id": "A1"}]}))
        (self.run / "report.am.html").write_text('<html lang="zh-CN"><style>body{margin:0}</style><section class="am-panel" id="panel-A1"><header class="am-panel-head"><h2>Test</h2></header><div class="am-panel-body">Full context</div></section></html>')
        self.data = {"title": "<script>literal title</script>", "subtitle": "Summary", "period_label": "Yesterday",
                     "review_label": "Today", "coverage_label": "One session", "cards": [
                         {"id": "A1", "title": "Read me", "lane": "decide"}]}
        self.save()

    def save(self):
        (self.run / "board.json").write_text(json.dumps(self.data))

    def test_preserves_panel_body_and_exposes_recording_and_comment_slots(self):
        result = board.build(self.run).read_text()
        self.assertIn("Full context", result)
        self.assertIn("&lt;script&gt;literal title&lt;/script&gt;", result)
        self.assertIn('id="review-controls"', result)
        self.assertIn('id="notes-window"', result)
        self.assertIn('aria-label="收起集中审核，保留草稿"', result)
        self.assertIn("原话、理解、去向 · 最后确认一次", result)
        self.assertIn(".approval-body", result)
        self.assertIn("connect-src 'self'", result)
        with self.assertRaisesRegex(ValueError, "already frozen"):
            board.build(self.run)

    def test_rejects_mismatched_ids_unknown_lanes_and_active_content(self):
        for update in ({"id": "A2"}, {"lane": "unmapped"}):
            previous = dict(self.data["cards"][0])
            self.data["cards"][0].update(update)
            self.save()
            with self.assertRaises(ValueError):
                board.build(self.run)
            self.data["cards"][0] = previous
        self.save()
        source = self.run / "report.am.html"
        source.write_text(source.read_text().replace("Full context", '<script>alert(1)</script>'))
        with self.assertRaises(AssertionError):
            board.build(self.run)
        self.assertFalse((self.run / "report.html").exists())


if __name__ == "__main__":
    unittest.main()
