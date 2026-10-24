import base64
import hashlib
import html
import http.client
import importlib.util
import json
from pathlib import Path
import re
import tempfile
import threading
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location("review_bridge", Path(__file__).parents[1] / "scripts" / "review_bridge.py")
bridge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bridge)

REPORT = '''<!doctype html><html><head>
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'sha256-original'; style-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'">
</head><body><section id="panel-A1"><h2>A1 Test</h2></section></body></html>'''


class FakeProcess:
    def __init__(self, args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.done = threading.Event()
        self.rc = 0

    def wait(self):
        self.done.wait(3)
        return self.rc


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.run = self.root / "run"
        self.run.mkdir()
        (self.run / "report.html").write_text(REPORT)
        (self.run / "manifest.json").write_text(json.dumps({"run_id": "test-run",
            "sources": [{"id": "S1"}],
            "targets": [{"key": "T1", "type": "thread", "thread_id": "thread-1",
                         "title": "Test thread", "source_ids": ["S1"]}],
            "panels": [{"id": "A1", "title": "Test", "source_ids": ["S1"]}]}))
        self.skill = self.root / "skill"
        (self.skill / "scripts").mkdir(parents=True)
        (self.skill / "scripts" / "record.py").write_text("")
        self.events = []
        self.review = bridge.ReviewRun(self.run, self.skill, self.events.append)
        self.review.state_file = self.root / "recording.json"
        self.server = bridge.make_server(self.review)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
        self.thread.start()
        self.origin = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def request(self, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        request_headers = {"Content-Type": "application/json", "Origin": self.origin,
                           "X-2hours-Token": self.review.token}
        request_headers.update(headers or {})
        connection.request("GET" if body is None else "POST", path,
                           None if body is None else json.dumps(body), request_headers)
        response = connection.getresponse()
        content = response.read().decode()
        connection.close()
        return response.status, content

    def write_plan(self):
        plan = {"plan_id": "P1", "batches": [{"id": "B1", "target": {"type": "thread", "title": "Test"}, "tasks": []},
                                             {"id": "B2", "target": {"type": "project", "title": "Project"}, "tasks": []}]}
        (self.run / "dispatch-plan.json").write_text(json.dumps(plan))
        return plan

    def test_report_is_preview_only_and_preserves_csp(self):
        with patch.object(bridge.subprocess, "Popen") as popen:
            status, page = self.request("/")
            self.assertEqual(status, 200)
            self.assertIn("开始口述批注", page)
            self.assertIn("twohours-config", page)
            popen.assert_not_called()
        csp = html.unescape(re.search(r'Content-Security-Policy" content="([^"]+)', page)[1])
        self.assertIn("default-src 'none'", csp)
        self.assertIn("'sha256-original'", csp)
        script = (Path(__file__).parents[1] / "assets" / "bridge.js").read_text()
        digest = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
        self.assertIn(f"'sha256-{digest}'", csp)

    def test_requires_csp_and_matching_panels(self):
        (self.run / "report.html").write_text(REPORT.replace("panel-A1", "panel-A2"))
        with self.assertRaises(ValueError):
            bridge.ReviewRun(self.run, self.skill)
        with self.assertRaises(ValueError):
            bridge.inject_bridge("<body></body>", {}, "alert(1)")

    def test_rejects_foreign_origin_host_token_and_unknown_path(self):
        for headers in [{"Origin": "https://example.com"}, {"Origin": ""},
                        {"Host": "evil.test"}, {"X-2hours-Token": "wrong"}, {"X-2hours-Token": "é"}]:
            with self.subTest(headers=headers):
                self.assertEqual(self.request("/record", {}, headers)[0], 403)
        self.assertEqual(self.request("/manifest.json")[0], 404)
        self.assertEqual(self.request("/../manifest.json")[0], 404)
        self.assertEqual(self.request("/stop", {})[0], 404)
        self.assertEqual(self.request("/", headers={"Host": "evil.test"})[0], 403)

    def test_rejects_large_or_non_json_body(self):
        self.assertEqual(self.request("/record", {"text": "x" * bridge.MAX_BODY})[0], 413)
        self.assertEqual(self.request("/record", {}, {"Content-Type": "text/plain"})[0], 400)
        self.assertEqual(self.request("/record", ["not-an-object"])[0], 400)

    def test_missing_dependency_is_reported(self):
        with patch.object(bridge.shutil, "which", return_value=None):
            self.assertFalse(self.review.status()["can_record"])
            self.assertEqual(self.request("/record", {})[0], 503)

    def test_active_recording_is_never_stopped_or_replaced(self):
        self.review.state_file.write_text(json.dumps({"pid": 123, "session": "other"}))
        with patch.object(bridge.os, "kill", return_value=None) as kill, patch.object(bridge.subprocess, "Popen") as popen:
            self.assertEqual(self.request("/record", {})[0], 409)
            kill.assert_called_once_with(123, 0)
            popen.assert_not_called()
        self.assertTrue(self.review.state_file.exists())

    def test_malformed_recording_state_is_rejected(self):
        self.review.state_file.write_text("[]")
        with patch.object(bridge.subprocess, "Popen") as popen:
            self.assertEqual(self.request("/record", {})[0], 409)
            popen.assert_not_called()

    def test_recording_is_fixed_argv_and_duplicate_click_is_rejected(self):
        processes = []
        def spawn(*args, **kwargs):
            process = FakeProcess(*args, **kwargs)
            processes.append(process)
            return process
        with patch.object(bridge.shutil, "which", return_value="/usr/bin/uv"), patch.object(bridge.subprocess, "Popen", side_effect=spawn):
            self.assertEqual(self.request("/record", {"command": "ignored"})[0], 200)
            self.assertEqual(self.request("/record", {})[0], 409)
        process = processes[0]
        self.assertEqual(process.args, ["/usr/bin/uv", "run", str(self.skill / "scripts" / "record.py"),
                                       "start", "--session", str(self.run / "blurt")])
        self.assertTrue(process.kwargs["start_new_session"])
        self.assertNotIn("shell", process.kwargs)
        process.rc = 2
        process.done.set()
        for thread in threading.enumerate():
            if thread.name.endswith("(watch)"):
                thread.join(1)
        self.assertEqual(self.review.status()["state"], "cancelled")

    def test_recording_completion_requires_success_marker_and_video(self):
        video = self.run / "blurt" / "recording.mp4"
        video.parent.mkdir()
        video.write_bytes(b"video")
        self.review.log.write_text(f"DONE video={video} duration=00:03 markers=0\n")
        process = FakeProcess([])
        process.done.set()
        self.review.watch(process)
        self.assertEqual(self.events[-1]["event"], "recorded")
        self.assertEqual(self.events[-1]["video"], str(video))
        video.unlink()
        self.review.watch(process)
        self.assertEqual(self.events[-1]["event"], "failed")
        video.write_bytes(b"video")
        self.review.log.write_text("not done\n")
        self.review.watch(process)
        self.assertEqual(self.events[-1]["event"], "failed")

    def test_recording_cancellation_and_failure_are_distinct(self):
        self.review.log.write_text("CANCELLED\n")
        process = FakeProcess([])
        process.done.set()
        for rc, expected in [(2, "cancelled"), (1, "failed")]:
            process.rc = rc
            self.review.watch(process)
            self.assertEqual(self.events[-1]["event"], expected)

    def test_approval_binds_plan_and_selected_batches(self):
        self.write_plan()
        self.assertEqual(self.request("/plan")[0], 200)
        for body, expected in [({"plan_id": "old", "batch_ids": ["B1"]}, 409),
                               ({"plan_id": "P1", "batch_ids": []}, 400),
                               ({"plan_id": "P1", "batch_ids": ["unknown"]}, 400),
                               ({"plan_id": "P1", "batch_ids": ["B1", "B1"]}, 400)]:
            self.assertEqual(self.request("/approve", body)[0], expected)
        self.assertFalse((self.run / "approval.json").exists())
        approved = {"plan_id": "P1", "batch_ids": ["B2"]}
        self.assertEqual(self.request("/approve", approved)[0], 200)
        result = json.loads((self.run / "approval.json").read_text())
        self.assertEqual(result["batch_ids"], ["B2"])
        self.assertIn("approved_at", result)
        self.assertEqual(self.events[-1]["event"], "approved")
        self.assertEqual(self.request("/approve", approved)[0], 200)
        self.assertEqual(len(self.events), 1)

    def test_approval_can_monotonically_add_remaining_batches(self):
        self.write_plan()
        plan_bytes = (self.run / "dispatch-plan.json").read_bytes()
        self.assertEqual(self.request("/approve", {"plan_id": "P1", "batch_ids": ["B2"]})[0], 200)
        self.assertEqual(json.loads(self.request("/plan")[1])["approved_batch_ids"], ["B2"])
        self.assertEqual(self.request("/approve", {"plan_id": "P1", "batch_ids": ["B1"]})[0], 200)
        self.assertEqual(self.events[-1]["batch_ids"], ["B1", "B2"])
        self.assertEqual(len(self.events), 2)
        self.assertEqual(json.loads(self.request("/plan")[1])["approved_batch_ids"], ["B1", "B2"])
        self.assertEqual(self.request("/approve", {"plan_id": "P1", "batch_ids": ["B2"]})[0], 200)
        self.assertEqual(len(self.events), 2)
        self.assertEqual((self.run / "dispatch-plan.json").read_bytes(), plan_bytes)
        reloaded = bridge.ReviewRun(self.run, self.skill)
        self.assertEqual(reloaded.plan()["approved_batch_ids"], ["B1", "B2"])

    def test_popen_failure_allows_safe_retry_without_overwriting_evidence(self):
        with patch.object(bridge.shutil, "which", return_value="/usr/bin/uv"):
            with patch.object(bridge.subprocess, "Popen", side_effect=OSError("temporary spawn failure")):
                self.assertEqual(self.request("/record", {})[0], 503)
            self.assertTrue(self.review.status()["can_record"])
            self.assertFalse(self.review.log.exists())
            def fail_with_log(*args, **kwargs):
                kwargs["stdout"].write("keep startup error evidence\n")
                raise OSError("second temporary failure")
            with patch.object(bridge.subprocess, "Popen", side_effect=fail_with_log):
                self.assertEqual(self.request("/record", {})[0], 503)
            saved_logs = list(self.run.glob("recording-start-error-*.log"))
            self.assertEqual(len(saved_logs), 1)
            self.assertEqual(saved_logs[0].read_text(), "keep startup error evidence\n")
            self.assertTrue(self.review.status()["can_record"])
            process = FakeProcess([])
            process.rc = 2
            process.done.set()
            with patch.object(bridge.subprocess, "Popen", return_value=process):
                self.assertEqual(self.request("/record", {})[0], 200)
            for thread in threading.enumerate():
                if thread.name.endswith("(watch)"):
                    thread.join(1)
            self.assertEqual(self.review.status()["state"], "cancelled")
            self.assertEqual(self.request("/record", {})[0], 409)

    def test_changed_report_stays_frozen_but_cannot_be_approved(self):
        self.write_plan()
        (self.run / "report.html").write_text("changed")
        self.assertIn("panel-A1", self.request("/")[1])
        self.assertEqual(self.request("/approve", {"plan_id": "P1", "batch_ids": ["B1"]})[0], 409)
        self.assertEqual(self.request("/record", {})[0], 409)

    def test_focus_is_validated_and_never_claims_video_time(self):
        self.assertEqual(self.request("/focus", {"panel_id": "unknown", "kind": "click"})[0], 400)
        self.assertEqual(self.request("/focus", {"panel_id": "A1", "kind": "click", "video_time": 10})[0], 200)
        event = json.loads((self.run / "focus.jsonl").read_text())
        self.assertEqual(event["time_basis"], "wall_clock_only")
        self.assertNotIn("video_time", event)

    def test_review_state_persists_without_overwriting_a_newer_tab(self):
        self.assertEqual(self.request("/review-state")[0], 200)
        body = {"panel_id": "A1", "revision": 0, "comment": "只补证据", "read": True}
        self.assertEqual(self.request("/review-state", body)[0], 200)
        self.assertEqual(self.request("/review-state", body)[0], 409)
        reloaded = bridge.ReviewRun(self.run, self.skill)
        self.assertEqual(reloaded.review_state()["panels"]["A1"]["comment"], "只补证据")
        self.assertTrue(reloaded.review_state()["panels"]["A1"]["read"])
        self.assertFalse((self.run / "written-items.json").exists())
        self.assertEqual((self.run / "report.html").read_text(), REPORT)

    def test_written_submission_uses_saved_text_and_requires_current_preview(self):
        self.assertEqual(self.request("/submit-comments", {"revisions": {"A1": 0}})[0], 400)
        self.request("/review-state", {"panel_id": "A1", "revision": 0, "comment": "继续调研"})
        self.assertEqual(self.request("/submit-comments", {"revisions": {"A1": 0}})[0], 409)
        body = {"revisions": {"A1": 1}}
        self.assertEqual(self.request("/submit-comments", body)[0], 200)
        submitted = json.loads((self.run / "written-items.json").read_text())
        self.assertEqual(submitted["items"][0]["quote"], "继续调研")
        self.assertEqual(submitted["items"][0]["panel_ids"], ["A1"])
        self.assertTrue(submitted["reviewed"])
        self.request("/submit-comments", body)
        self.assertEqual(len(self.events), 1)
        self.assertFalse((self.run / "approval.json").exists())
        self.request("/review-state", {"panel_id": "A1", "revision": 1, "comment": "先暂停"})
        self.assertEqual(json.loads((self.run / "written-items.json").read_text()), submitted)
        (self.run / "dispatch-state.json").write_text(json.dumps({"batches": {"B1": {"status": "sent"}}}))
        self.assertEqual(self.request("/submit-comments", {"revisions": {"A1": 2}})[0], 409)

    def test_review_state_rejects_invalid_fields_and_foreign_origin(self):
        for body in [{"panel_id": "missing", "revision": 0, "read": True},
                     {"panel_id": "A1", "revision": 0, "read": "yes"},
                     {"panel_id": "A1", "revision": 0, "comment": "x" * 10001},
                     {"panel_id": "A1", "revision": 0, "comment": None}]:
            self.assertEqual(self.request("/review-state", body)[0], 400)
        self.assertEqual(self.request("/review-state", {"panel_id": "A1", "revision": 0, "read": True},
                                      {"Origin": "https://example.com"})[0], 403)

    def test_changed_submitted_comments_hide_and_reject_stale_plan(self):
        plan = self.write_plan()
        comments = self.run / "written-items.json"
        comments.write_text('{"reviewed":true,"items":[]}')
        plan["snapshot"] = {"written-items.json": hashlib.sha256(comments.read_bytes()).hexdigest()}
        (self.run / "dispatch-plan.json").write_text(json.dumps(plan))
        self.assertIsNotNone(self.review.plan())
        comments.write_text('{"reviewed":true,"items":[{"id":"W-A1"}]}')
        self.assertIsNone(self.review.plan())
        self.assertEqual(self.request("/approve", {"plan_id": "P1", "batch_ids": ["B1"]})[0], 409)

    def write_review(self, frames=None):
        (self.run / "blurt").mkdir(exist_ok=True)
        (self.run / "blurt/items.json").write_text(json.dumps({"reviewed": False, "items": [
            {"id": "I1", "quote": "只改展示", "confidence": "low", "questions": ["改哪一张？"],
             "frames": frames or [], "answer": ""}]}))
        (self.run / "routes.json").write_text(json.dumps({"tasks": [
            {"id": "D1", "item_ids": ["I1"], "panel_ids": ["A1"], "target_key": "T1",
             "objective": "改展示", "acceptance": "页面检查通过", "constraints": "不改数据"}]}))
        review = bridge.dispatch.prepare_review(self.run)
        body = {"review_id": review["review_id"], "tasks": [
            {"id": "D1", "selected": True, "target_key": "T1", "objective": "改第一张",
             "acceptance": "第一张页面通过", "constraints": "仅展示", "answers": {"I1": "第一张"}}]}
        return review, body

    def test_combined_review_confirms_once_and_reloads_final_edits(self):
        self.assertEqual(json.loads(self.request("/review-plan")[1]), None)
        review, body = self.write_review()
        draft = json.loads(self.request("/review-plan")[1])
        self.assertIsNone(draft["approval"])
        self.assertFalse(draft["tasks"][0]["selected"])
        self.assertEqual(draft["tasks"][0]["source_items"][0]["quote"], "只改展示")
        self.assertFalse((self.run / "approval.json").exists())
        self.assertEqual(self.request("/approve-review", body, {"Origin": "https://example.com"})[0], 403)
        self.assertEqual(self.request("/approve-review", body)[0], 200)
        self.assertEqual(self.request("/approve-review", body)[0], 200)
        self.assertEqual(len(self.events), 1)
        final = json.loads(self.request("/review-plan")[1])
        self.assertEqual(final["tasks"][0]["objective"], "改第一张")
        self.assertEqual(final["tasks"][0]["source_items"][0]["answer"], "第一张")
        self.assertTrue(final["tasks"][0]["selected"])
        self.assertEqual(final["approval"]["review_id"], review["review_id"])
        self.assertFalse(json.loads((self.run / "blurt/items.json").read_text())["reviewed"])
        self.assertEqual(self.request("/approve", {"plan_id": final["approval"]["plan_id"], "batch_ids": ["B1"]})[0], 409)
        self.assertEqual(bridge.dispatch.claim(self.run, "B1")["args"]["threadId"], "thread-1")

    def test_combined_review_rejects_stale_sources_and_new_comment_submission_after_confirmation(self):
        _, body = self.write_review()
        self.assertEqual(self.request("/approve-review", body)[0], 200)
        self.request("/review-state", {"panel_id": "A1", "revision": 0, "comment": "新的意见"})
        self.assertEqual(self.request("/submit-comments", {"revisions": {"A1": 1}})[0], 409)
        items = self.run / "blurt/items.json"
        data = json.loads(items.read_text())
        data["items"][0]["quote"] = "别改了"
        items.write_text(json.dumps(data))
        self.assertEqual(self.request("/review-plan")[0], 409)
        self.assertEqual(self.request("/approve-review", body)[0], 409)

    def test_evidence_only_serves_referenced_safe_frames_and_binds_their_contents(self):
        directory = self.run / "blurt/frames"
        directory.mkdir(parents=True)
        (directory / "safe.png").write_bytes(b"\x89PNG\r\n\x1a\nimage")
        (directory / "unreferenced.png").write_bytes(b"\x89PNG\r\n\x1a\nother")
        (directory / "linked.png").symlink_to(directory / "safe.png")
        (self.run / "blurt/alias").symlink_to(directory, target_is_directory=True)
        frames = [{"path": path, "caption": "原始画面"} for path in (
            "frames/safe.png", "frames/linked.png", "frames/../frames/safe.png",
            str(directory / "safe.png"), "alias/safe.png", "frames/../../manifest.json")]
        _, body = self.write_review(frames)
        draft = json.loads(self.request("/review-plan")[1])
        images = draft["tasks"][0]["source_items"][0]["evidence_images"]
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0]["caption"], "原始画面")
        content, mime = self.review.evidence(images[0]["url"])
        self.assertEqual(mime, "image/png")
        self.assertEqual(content, b"\x89PNG\r\n\x1a\nimage")
        unknown = hashlib.sha256(b"blurt/frames/unreferenced.png").hexdigest()
        self.assertEqual(self.request(f"/evidence/{draft['review_id']}/{unknown}")[0], 404)
        self.assertEqual(self.request("/evidence/../../manifest.json")[0], 404)
        (directory / "safe.png").write_bytes(b"\x89PNG\r\n\x1a\nchanged")
        self.assertEqual(self.request(images[0]["url"])[0], 409)
        self.assertEqual(self.request("/approve-review", body)[0], 409)


if __name__ == "__main__":
    unittest.main()
