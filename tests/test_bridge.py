import base64
import hashlib
import html
import http.client
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
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
        self.review.retention = bridge.settings.RetentionSettings(self.root / ".2hours")
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

    def test_restart_on_same_port_preserves_comments_and_rejects_old_token(self):
        self.request("/review-state", {"panel_id": "A1", "revision": 0, "comment": "保留这条意见"})
        port, old_token = self.server.server_port, self.review.token
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.review = bridge.ReviewRun(self.run, self.skill)
        self.server = bridge.make_server(self.review, port)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
        self.thread.start()
        self.assertEqual(self.server.server_port, port)
        code, body = self.request("/review-state")
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)["panels"]["A1"]["comment"], "保留这条意见")
        update = {"panel_id": "A1", "revision": 1, "comment": "恢复后保存"}
        self.assertEqual(self.request("/review-state", update, {"X-2hours-Token": old_token})[0], 403)
        self.assertEqual(self.request("/review-state", update)[0], 200)

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
            bridge.inject_bridge("<body></body>", {}, "alert(1)", "")

    def test_live_review_styles_do_not_rewrite_the_frozen_report(self):
        style = (Path(__file__).parents[1] / "assets" / "review.css").read_text()
        status, page = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn(f'<style id="twohours-review-style">{style}</style>', page)
        self.assertIn(".focus-body", style)
        self.assertNotIn(".approval-body", style)
        self.assertEqual((self.run / "report.html").read_text(), REPORT)
        self.review.unchanged()

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

    def test_recorder_state_is_isolated_from_independent_blurt(self):
        with patch.dict(bridge.os.environ, {"BLURT_HOME": str(self.root / "independent-blurt")}):
            review = bridge.ReviewRun(self.run, self.skill, self.events.append)
        self.assertEqual(review.state_file, Path.home() / ".2hours/recorder/recording.json")

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

    def test_recovered_recording_keeps_done_gate_and_reports_warning(self):
        video = self.run / "blurt/recording.mp4"
        video.parent.mkdir()
        video.write_bytes(b"recovered video")
        warning = "录制已恢复，但结尾可能丢失。麦克风口述是否被录到尚未核验。"
        (video.parent / "meta.json").write_text(json.dumps({"recovered": True, "incomplete": True, "warning": warning}))
        consent = self.review.retention.save({"dispatch_mode": "automatic", "revision": 0})["settings"]["dispatch_consent"]
        self.review.recording_consent = consent
        self.review.log.write_text(f"DONE video={video} duration=00:03\n")
        process = FakeProcess([])
        process.done.set()
        self.review.watch(process)
        self.assertEqual(self.events[-1]["event"], "recorded")
        self.assertTrue(self.events[-1]["incomplete"])
        self.assertEqual(self.events[-1]["message"], warning)
        grant = json.loads((self.run / "input-authorizations.json").read_text())["inputs"]["blurt/recording.mp4"]
        self.assertEqual(grant["opt_in_id"], consent["id"])
        (video.parent / "meta.json").write_text(json.dumps({"incomplete": True, "warning": warning}))
        self.review.watch(process)
        self.assertFalse(self.events[-1]["recovered"])
        self.assertTrue(self.events[-1]["incomplete"])
        self.assertEqual(self.events[-1]["message"], warning)
        self.review.log.write_text("recovery did not finish\n")
        self.review.watch(process)
        self.assertEqual(self.events[-1]["event"], "failed")

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

    def test_review_toggle_is_independent_of_empty_text_existing_text_and_voice(self):
        revision = 0
        for comment, voice in (("", False), ("已有文字", False), ("", True)):
            if voice:
                (self.run / "blurt").mkdir()
                (self.run / "blurt/items.json").write_text(json.dumps({"items": [], "reviewed": False}))
                self.review.state = {"state": "failed", "message": "录制错误不阻止审阅"}
            self.assertEqual(self.request("/review-state", {"panel_id": "A1", "revision": revision, "comment": comment})[0], 200)
            revision += 1
            for read in (True, False):
                code, body = self.request("/review-state", {"panel_id": "A1", "revision": revision, "read": read})
                self.assertEqual(code, 200)
                revision += 1
                self.assertEqual(json.loads(body)["panels"]["A1"]["comment"], comment)
                reloaded = bridge.ReviewRun(self.run, self.skill)
                self.assertEqual(reloaded.review_state()["panels"]["A1"]["read"], read)
        self.assertEqual(self.events, [])
        for name in ("approval.json", "review-confirmation.json", "written-items.json"):
            self.assertFalse((self.run / name).exists())

    def test_settings_api_persists_without_deleting_and_requires_same_origin_token(self):
        code, body = self.request("/settings")
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)["settings"]["retention"], "keep")
        change = {"retention": "1_week", "revision": 0}
        self.assertEqual(self.request("/settings", change, {"X-2hours-Token": "wrong"})[0], 403)
        code, body = self.request("/settings", change)
        self.assertEqual(code, 200)
        preview = json.loads(body)
        self.assertEqual(json.loads(self.request("/settings")[1])["settings"]["retention"], "1_week")
        self.assertEqual(self.request("/settings", change)[0], 409)
        self.assertEqual(self.request("/cleanup", {"preview_id": preview["preview_id"]}, {"Origin": "https://example.com"})[0], 403)
        self.assertEqual(self.request("/cleanup", {"path": str(self.run)})[0], 409)
        self.assertEqual((self.run / "report.html").read_text(), REPORT)

    def test_retention_recognizes_frozen_unified_confirmation_without_mutating_inputs(self):
        _, body = self.write_review()
        self.assertEqual(self.request("/approve-review", body)[0], 200)
        target = self.review.retention.runs / "test-run"
        shutil.copytree(self.run, target)
        (target / "blurt/recording.mp4").write_bytes(b"video")
        os.utime(target / "blurt/recording.mp4", (1000, 1000))
        source = (target / "blurt/items.json").read_bytes()
        preview = self.review.retention.save({"retention": "after_review", "revision": 0})
        self.assertTrue(preview["files"][0]["eligible"])
        self.assertEqual((target / "blurt/items.json").read_bytes(), source)
        (target / "routes.json").write_text("{}")
        self.assertFalse(self.review.retention.preview()["files"][0]["eligible"])

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

    def test_all_deferred_plan_and_status_remain_available_to_page(self):
        _, body = self.write_review()
        for task in body["tasks"]:
            task["selected"] = False
        self.assertEqual(self.request("/approve-review", body)[0], 200)
        for endpoint in ("/plan", "/review-plan", "/dispatch-status"):
            self.assertEqual(self.request(endpoint)[0], 200)
        self.assertEqual(json.loads(self.request("/plan")[1])["batches"], [])
        self.assertEqual(json.loads(self.request("/review-plan")[1])["approval"]["batch_ids"], [])
        self.assertEqual(json.loads(self.request("/dispatch-status")[1])["batches"], [])

    def test_dispatch_status_reports_sent_receipts(self):
        _, body = self.write_review()
        self.request("/approve-review", body)
        bridge.dispatch.claim(self.run, "B1")
        response = self.run / "response.json"
        response.write_text('{"ok":true}')
        bridge.dispatch.receipt(self.run, "B1", response, "sent")
        self.assertEqual(json.loads(self.request("/dispatch-status")[1])["batches"][0]["status"], "sent")

    def test_written_submission_requires_visible_current_opt_in_and_binds_input(self):
        self.request("/review-state", {"panel_id": "A1", "revision": 0, "comment": "做验证"})
        preferences = json.loads(self.request("/settings", {"dispatch_mode": "automatic", "revision": 0})[1])["settings"]
        self.assertEqual(self.request("/submit-comments", {"revisions": {"A1": 1}})[0], 409)
        self.assertFalse((self.run / "written-items.json").exists())
        body = {"revisions": {"A1": 1}, "dispatch_opt_in": preferences["dispatch_consent"]["id"]}
        self.assertEqual(self.request("/submit-comments", body)[0], 200)
        grant = json.loads((self.run / "input-authorizations.json").read_text())["inputs"]["written-items.json"]
        self.assertEqual(grant["sha256"], hashlib.sha256((self.run / "written-items.json").read_bytes()).hexdigest())
        self.assertEqual(grant["items"]["W-A1"]["opt_in_id"], preferences["dispatch_consent"]["id"])
        self.assertFalse((self.run / "approval.json").exists())

    def test_existing_manual_written_item_is_not_reauthorized_by_new_auto_item(self):
        self.request("/review-state", {"panel_id": "A1", "revision": 0, "comment": "旧意见"})
        self.assertEqual(self.request("/submit-comments", {"revisions": {"A1": 1}})[0], 200)
        self.review.panels.append({"id": "A2", "title": "第二项"})
        self.request("/review-state", {"panel_id": "A2", "revision": 0, "comment": "新增任务"})
        preferences = json.loads(self.request("/settings", {"dispatch_mode": "automatic", "revision": 0})[1])["settings"]
        body = {"revisions": {"A1": 1, "A2": 1}, "dispatch_opt_in": preferences["dispatch_consent"]["id"]}
        self.assertEqual(self.request("/submit-comments", body)[0], 200)
        items = json.loads((self.run / "input-authorizations.json").read_text())["inputs"]["written-items.json"]["items"]
        self.assertEqual(list(items), ["W-A2"])

    def test_voice_mode_is_captured_at_user_start_not_at_completion(self):
        preferences = self.review.retention.save({"dispatch_mode": "automatic", "revision": 0})["settings"]
        process = FakeProcess([])
        with patch.object(bridge.shutil, "which", return_value="/usr/bin/uv"), patch.object(bridge.subprocess, "Popen", return_value=process):
            self.assertEqual(self.request("/record", {})[0], 200)
        self.review.retention.save({"dispatch_mode": "manual", "revision": 1})
        video = self.run / "blurt/recording.mp4"
        video.write_bytes(b"fixture voice")
        self.review.log.write_text(f"DONE video={video} duration=00:03\n")
        process.done.set()
        for thread in threading.enumerate():
            if thread.name.endswith("(watch)"):
                thread.join(1)
        grant = json.loads((self.run / "input-authorizations.json").read_text())["inputs"]["blurt/recording.mp4"]
        self.assertEqual(grant["opt_in_id"], preferences["dispatch_consent"]["id"])
        self.assertEqual(self.review.retention.read()["dispatch_mode"], "manual")

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
