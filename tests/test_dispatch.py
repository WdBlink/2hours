import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "dispatch.py"
SPEC = importlib.util.spec_from_file_location("dispatch", SCRIPT)
dispatch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(dispatch)


class DispatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.run = Path(self.temp.name)
        (self.run / "blurt").mkdir()
        self.manifest = {
            "run_id": "test-run",
            "sources": [{"id": "S1", "provider": "codex", "session_id": "thread-1"}],
            "panels": [{"id": "A1", "title": "成果", "source_ids": ["S1"]}],
            "targets": [{"key": "T1", "type": "thread", "thread_id": "thread-1", "host_id": "local",
                         "title": "原始会话标题", "source_ids": ["S1"]}],
        }
        self.items = {"reviewed": True, "items": [
            {"id": "I1", "status": "confirmed", "quote": "只改展示", "confidence": "high",
             "questions": [], "answer": "", "frames": [{"path": "frames/I1.png", "t": 1.5}]},
            {"id": "I2", "status": "draft", "quote": "补一个验证", "confidence": "high", "questions": []},
        ]}
        self.routes = {"tasks": [
            {"id": "D1", "item_ids": ["I1"], "panel_ids": ["A1"], "target_key": "T1",
             "objective": "改展示", "acceptance": "检查页面", "constraints": "不改数据"},
            {"id": "D2", "item_ids": ["I2"], "panel_ids": ["A1"], "target_key": "T1",
             "objective": "补验证", "acceptance": "验证通过", "constraints": ""},
        ]}
        self.save_inputs()

    def save(self, name, value):
        (self.run / name).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def save_inputs(self):
        self.save("manifest.json", self.manifest)
        self.save("blurt/items.json", self.items)
        self.save("routes.json", self.routes)
        (self.run / "report.html").write_text("<h2>A1 成果</h2>", encoding="utf-8")

    def approve(self, plan, batch_ids=None):
        self.save("approval.json", {"plan_id": plan["plan_id"],
                                   "batch_ids": batch_ids or [batch["id"] for batch in plan["batches"]],
                                   "approved_at": "2026-10-23T09:00:00+08:00"})

    def claimed(self):
        plan = dispatch.prepare(self.run)
        self.approve(plan)
        return dispatch.claim(self.run, "B1")

    def test_group_and_keep_evidence(self):
        plan = dispatch.prepare(self.run)
        self.assertEqual(len(plan["batches"]), 1)
        batch = plan["batches"][0]
        self.assertEqual(len(batch["tasks"]), 2)
        self.assertEqual(batch["tasks"][0]["source_items"][0]["frames"], self.items["items"][0]["frames"])
        self.assertIn("只改展示", batch["prompt"])
        self.assertEqual(dispatch.prepare(self.run), plan)

    def test_written_and_voice_items_share_batches_and_bind_submitted_snapshot(self):
        written = {"reviewed": True, "items": [{"id": "W-A1", "status": "confirmed", "quote": "补来源"}]}
        self.save("written-items.json", written)
        self.routes["tasks"][0]["item_ids"].append("W-A1")
        self.save("routes.json", self.routes)
        plan = dispatch.prepare(self.run)
        self.assertEqual(len(plan["batches"]), 1)
        self.assertIn("补来源", plan["batches"][0]["prompt"])
        self.assertIn("written-items.json", plan["snapshot"])
        self.approve(plan)
        written["items"][0]["quote"] = "已修改的意见"
        self.save("written-items.json", written)
        with self.assertRaisesRegex(dispatch.DispatchError, "snapshot changed"):
            dispatch.claim(self.run, "B1")

    def test_written_only_needs_no_recording_but_still_needs_dispatch_approval(self):
        (self.run / "blurt/items.json").unlink()
        self.save("written-items.json", {"reviewed": True, "items": self.items["items"]})
        plan = dispatch.prepare(self.run)
        self.assertNotIn("blurt/items.json", plan["snapshot"])
        with self.assertRaises(dispatch.DispatchError):
            dispatch.claim(self.run, "B1")
        self.approve(plan)
        self.assertIn("tool", dispatch.claim(self.run, "B1"))

    def test_requires_completed_review(self):
        self.items["reviewed"] = False
        self.save_inputs()
        with self.assertRaisesRegex(dispatch.DispatchError, "review"):
            dispatch.prepare(self.run)

    def test_deleted_item_never_dispatched(self):
        self.items["items"][0]["status"] = "deleted"
        self.save_inputs()
        with self.assertRaisesRegex(dispatch.DispatchError, "Deleted"):
            dispatch.prepare(self.run)
        self.routes["tasks"].pop(0)
        self.save_inputs()
        plan = dispatch.prepare(self.run)
        self.assertEqual([task["id"] for task in plan["batches"][0]["tasks"]], ["D2"])

    def test_unresolved_low_confidence_blocked_until_answered(self):
        self.items["items"][0].update(confidence="low", questions=["改哪一处？"])
        self.save_inputs()
        with self.assertRaisesRegex(dispatch.DispatchError, "Unresolved"):
            dispatch.prepare(self.run)
        self.items["items"][0]["answer"] = "只改报告第一张图"
        self.save_inputs()
        self.assertIn("只改报告第一张图", dispatch.prepare(self.run)["batches"][0]["prompt"])

    def test_unknown_item_panel_and_target_ids_rejected(self):
        for field, value in (("item_ids", ["missing"]), ("panel_ids", ["missing"]), ("target_key", "missing")):
            with self.subTest(field=field):
                original = self.routes["tasks"][0][field]
                self.routes["tasks"][0][field] = value
                self.save_inputs()
                with self.assertRaisesRegex(dispatch.DispatchError, "Unknown"):
                    dispatch.prepare(self.run)
                self.routes["tasks"][0][field] = original

    def test_duplicate_task_id_rejected(self):
        self.routes["tasks"][1]["id"] = "D1"
        self.save_inputs()
        with self.assertRaisesRegex(dispatch.DispatchError, "Duplicate"):
            dispatch.prepare(self.run)

    def test_duplicate_destination_alias_rejected(self):
        self.manifest["targets"].append(dict(self.manifest["targets"][0], key="T2"))
        self.save_inputs()
        with self.assertRaisesRegex(dispatch.DispatchError, "Duplicate destination"):
            dispatch.prepare(self.run)

    def test_claim_needs_approval(self):
        dispatch.prepare(self.run)
        with self.assertRaises(dispatch.DispatchError):
            dispatch.claim(self.run, "B1")
        self.assertFalse((self.run / "dispatch-state.json").exists())

    def test_source_change_invalidates_approval(self):
        plan = dispatch.prepare(self.run)
        self.approve(plan)
        self.items["items"][0]["quote"] = "别改展示"
        self.save_inputs()
        with self.assertRaisesRegex(dispatch.DispatchError, "snapshot changed"):
            dispatch.claim(self.run, "B1")
        new_plan = dispatch.prepare(self.run)
        self.assertNotEqual(new_plan["plan_id"], plan["plan_id"])
        with self.assertRaisesRegex(dispatch.DispatchError, "different plan"):
            dispatch.claim(self.run, "B1")

    def test_report_change_and_plan_tampering_rejected(self):
        plan = dispatch.prepare(self.run)
        self.approve(plan)
        plan["batches"][0]["prompt"] = "tampered"
        self.save("dispatch-plan.json", plan)
        with self.assertRaisesRegex(dispatch.DispatchError, "was modified"):
            dispatch.claim(self.run, "B1")
        dispatch.prepare(self.run)
        (self.run / "report.html").write_text("different report", encoding="utf-8")
        with self.assertRaisesRegex(dispatch.DispatchError, "snapshot changed"):
            dispatch.claim(self.run, "B1")

    def test_bad_approval_batch_and_timestamp_rejected(self):
        plan = dispatch.prepare(self.run)
        self.approve(plan, ["missing"])
        with self.assertRaisesRegex(dispatch.DispatchError, "Unknown"):
            dispatch.claim(self.run, "B1")
        self.save("approval.json", {"plan_id": plan["plan_id"], "batch_ids": ["B1"], "approved_at": "2026-10-23"})
        with self.assertRaisesRegex(dispatch.DispatchError, "timezone"):
            dispatch.claim(self.run, "B1")

    def test_claim_persists_before_return_and_cannot_repeat(self):
        call = self.claimed()
        self.assertEqual(call["tool"], "send_message_to_thread")
        self.assertEqual(call["args"]["threadId"], "thread-1")
        self.assertEqual(call["args"]["hostId"], "local")
        self.assertEqual(dispatch.status(self.run)["batches"][0]["status"], "sending")
        with self.assertRaisesRegex(dispatch.DispatchError, "already claimed"):
            dispatch.claim(self.run, "B1")
        self.routes["tasks"][0]["objective"] = "changed"
        self.save_inputs()
        with self.assertRaisesRegex(dispatch.DispatchError, "Previous plan"):
            dispatch.prepare(self.run)

    def test_project_target_creates_project_thread_only_after_approval(self):
        self.manifest["targets"][0] = {"key": "T1", "type": "project", "project_id": "project-1",
                                         "title": "研究项目", "source_ids": ["S1"]}
        self.save_inputs()
        call = self.claimed()
        self.assertEqual(call["tool"], "create_thread")
        self.assertEqual(call["args"]["target"], {"type": "project", "projectId": "project-1",
                                                   "environment": {"type": "local"}})

    def test_routes_add_project_without_changing_frozen_manifest_and_bind_approval(self):
        frozen_manifest = (self.run / "manifest.json").read_bytes()
        self.routes["targets"] = [*self.manifest["targets"], {
            "key": "T2", "type": "project", "project_id": "project-1",
            "title": "口述后核验的项目", "source_ids": ["S1"],
        }]
        self.routes["tasks"][1]["target_key"] = "T2"
        self.save("routes.json", self.routes)
        plan = dispatch.prepare(self.run)
        self.assertEqual([batch["target"]["key"] for batch in plan["batches"]], ["T1", "T2"])
        self.approve(plan)
        self.routes["targets"][1]["project_id"] = "project-2"
        self.save("routes.json", self.routes)
        with self.assertRaisesRegex(dispatch.DispatchError, "snapshot changed"):
            dispatch.claim(self.run, "B2")
        revised_plan = dispatch.prepare(self.run)
        self.assertNotEqual(revised_plan["plan_id"], plan["plan_id"])
        with self.assertRaisesRegex(dispatch.DispatchError, "different plan"):
            dispatch.claim(self.run, "B2")
        self.approve(revised_plan)
        call = dispatch.claim(self.run, "B2")
        self.assertEqual(call["tool"], "create_thread")
        self.assertEqual(call["args"]["target"]["projectId"], "project-2")
        self.assertEqual((self.run / "manifest.json").read_bytes(), frozen_manifest)

    def test_routes_targets_replace_table_and_require_existing_sources(self):
        self.routes["targets"] = []
        self.save("routes.json", self.routes)
        with self.assertRaisesRegex(dispatch.DispatchError, "Unknown target"):
            dispatch.prepare(self.run)
        self.routes["targets"] = [dict(self.manifest["targets"][0], source_ids=["missing"])]
        self.save("routes.json", self.routes)
        with self.assertRaisesRegex(dispatch.DispatchError, "Unknown target sources"):
            dispatch.prepare(self.run)

    def test_failed_receipt_not_sent_and_no_auto_retry(self):
        self.claimed()
        failure = {"isError": True, "content": [{"type": "text", "text": "Failure"}]}
        self.save("response.json", failure)
        with self.assertRaisesRegex(dispatch.DispatchError, "Error response"):
            dispatch.receipt(self.run, "B1", self.run / "response.json", "sent")
        self.assertEqual(dispatch.status(self.run)["batches"][0]["status"], "sending")
        result = dispatch.receipt(self.run, "B1", self.run / "response.json", "failed")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["response"], failure)
        with self.assertRaisesRegex(dispatch.DispatchError, "already claimed"):
            dispatch.claim(self.run, "B1")

    def test_nested_error_and_text_error_rejected(self):
        self.claimed()
        for response in (
            {"content": [{"type": "text", "text": '{"error": "No such thread"}'}]},
            {"content": [{"type": "text", "text": "Error: not available"}]},
            {"structuredContent": {"ok": False}},
        ):
            with self.subTest(response=response):
                self.save("response.json", response)
                with self.assertRaisesRegex(dispatch.DispatchError, "Error response"):
                    dispatch.receipt(self.run, "B1", self.run / "response.json", "sent")

    def test_success_keeps_exact_response_without_inventing_message_id(self):
        self.claimed()
        response = {"content": [{"type": "text", "text": "Message queued"}]}
        self.save("response.json", response)
        result = dispatch.receipt(self.run, "B1", self.run / "response.json", "sent")
        self.assertEqual(result["response"], response)
        self.assertNotIn("message_id", result)
        with self.assertRaisesRegex(dispatch.DispatchError, "unresolved batch"):
            dispatch.receipt(self.run, "B1", self.run / "response.json", "sent")

    def test_concurrent_claim_only_one_wins(self):
        plan = dispatch.prepare(self.run)
        self.approve(plan)
        command = [sys.executable, str(SCRIPT), "claim", "--run", str(self.run), "--batch", "B1"]
        children = [subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
        results = [child.communicate(timeout=10) for child in children]
        self.assertEqual(sorted(child.returncode for child in children), [0, 1], results)
        output = next(stdout for child, (stdout, _) in zip(children, results) if child.returncode == 0)
        self.assertEqual(json.loads(output)["tool"], "send_message_to_thread")

    def review_body(self, review, selected=("D1", "D2")):
        return {"review_id": review["review_id"], "tasks": [
            {**{key: task.get(key, "") for key in ("id", "objective", "constraints", "acceptance", "target_key")},
             "selected": task["id"] in selected, "answers": {}}
            for task in review["tasks"]]}

    def test_unreviewed_voice_prepares_without_approval_and_final_edit_preserves_evidence(self):
        self.items["reviewed"] = False
        self.items["items"][0].update(confidence="low", questions=["展示还是数据？"])
        self.manifest["targets"].append({"key": "T2", "type": "project", "project_id": "project-2",
                                         "title": "新项目", "source_ids": ["S1"]})
        self.save_inputs()
        original = (self.run / "blurt/items.json").read_bytes()
        review = dispatch.prepare_review(self.run)
        self.assertFalse((self.run / "dispatch-plan.json").exists())
        with self.assertRaises(dispatch.DispatchError):
            dispatch.claim(self.run, "B1")
        body = self.review_body(review, selected=("D1",))
        body["tasks"][0].update(objective="只改第一张卡片", target_key="T2", answers={"I1": "只改展示"})
        approval, fresh = dispatch.approve_review(self.run, body)
        self.assertTrue(fresh)
        self.assertEqual(dispatch.approve_review(self.run, body), (approval, False))
        plan = dispatch.current_plan(self.run)
        self.assertEqual(plan["batches"][0]["target"]["project_id"], "project-2")
        self.assertEqual(plan["batches"][0]["tasks"][0]["source_items"][0]["answer"], "只改展示")
        self.assertEqual((self.run / "blurt/items.json").read_bytes(), original)
        self.assertIn("review-confirmation.json", plan["snapshot"])
        self.assertEqual(dispatch.claim(self.run, "B1")["tool"], "create_thread")
        self.assertEqual(dispatch.approve_review(self.run, body), (approval, False))
        body["tasks"][0]["objective"] = "再改数据"
        with self.assertRaisesRegex(dispatch.DispatchError, "already confirmed"):
            dispatch.approve_review(self.run, body)

    def test_unified_validation_happens_before_any_confirmation(self):
        self.items["reviewed"] = False
        self.items["items"][0]["questions"] = ["需要明确边界"]
        self.save_inputs()
        review = dispatch.prepare_review(self.run)
        for mutate, message in [
            (lambda b: None, "Unresolved"),
            (lambda b: b["tasks"][0].update(target_key="missing"), "Unknown target"),
            (lambda b: b["tasks"][0].update(answers={"I9": "answer"}), "Unknown answer"),
            (lambda b: b["tasks"].pop(), "every task"),
        ]:
            body = self.review_body(review)
            mutate(body)
            with self.assertRaisesRegex(dispatch.DispatchError, message):
                dispatch.approve_review(self.run, body)
            self.assertFalse((self.run / "review-confirmation.json").exists())
            self.assertFalse((self.run / "approval.json").exists())

    def test_all_deferred_finishes_without_dispatch_or_resolving_questions(self):
        self.items["reviewed"] = False
        self.items["items"][0]["questions"] = ["下次再处理"]
        self.save_inputs()
        review = dispatch.prepare_review(self.run)
        body = self.review_body(review, selected=())
        body["tasks"][0]["objective"] = ""
        approval, _ = dispatch.approve_review(self.run, body)
        self.assertEqual(approval["batch_ids"], [])
        self.assertEqual(dispatch.current_plan(self.run)["batches"], [])
        self.assertEqual(dispatch.status(self.run)["batches"], [])
        with self.assertRaises(dispatch.DispatchError):
            dispatch.claim(self.run, "B1")

    def test_changed_source_or_frame_invalidates_draft_and_claim(self):
        frame = self.run / "blurt/frames/I1.png"
        frame.parent.mkdir()
        frame.write_bytes(b"\x89PNG\r\n\x1a\noriginal")
        review = dispatch.prepare_review(self.run)
        self.assertEqual(list(review["evidence_files"]), ["blurt/frames/I1.png"])
        body = self.review_body(review)
        dispatch.approve_review(self.run, body)
        frame.write_bytes(b"\x89PNG\r\n\x1a\nchanged")
        with self.assertRaisesRegex(dispatch.DispatchError, "snapshot changed"):
            dispatch.current_review(self.run)
        with self.assertRaisesRegex(dispatch.DispatchError, "snapshot changed"):
            dispatch.claim(self.run, "B1")
        with self.assertRaisesRegex(dispatch.DispatchError, "snapshot changed"):
            dispatch.approve_review(self.run, body)

    def test_conflicting_shared_answers_rejected_before_write(self):
        self.routes["tasks"][1]["item_ids"] = ["I1"]
        self.save_inputs()
        review = dispatch.prepare_review(self.run)
        body = self.review_body(review)
        body["tasks"][0]["answers"] = {"I1": "first"}
        body["tasks"][1]["answers"] = {"I1": "different"}
        with self.assertRaisesRegex(dispatch.DispatchError, "Conflicting"):
            dispatch.approve_review(self.run, body)
        self.assertFalse((self.run / "review-confirmation.json").exists())

    def test_interrupted_finalization_is_recoverable_with_identical_request(self):
        review = dispatch.prepare_review(self.run)
        body = self.review_body(review)
        original_write = dispatch.write_json
        def fail_plan(path, value):
            if path.name == "dispatch-plan.json":
                raise OSError("simulated interrupted write")
            original_write(path, value)
        with patch.object(dispatch, "write_json", side_effect=fail_plan):
            with self.assertRaises(OSError):
                dispatch.approve_review(self.run, body)
        self.assertTrue((self.run / "review-confirmation.json").exists())
        self.assertFalse((self.run / "approval.json").exists())
        with self.assertRaises(dispatch.DispatchError):
            dispatch.claim(self.run, "B1")
        approval, fresh = dispatch.approve_review(self.run, body)
        self.assertTrue(fresh)
        self.assertEqual(approval["batch_ids"], ["B1"])
        self.assertEqual(dispatch.claim(self.run, "B1")["status"], "sending")

    def test_prepare_review_cannot_repurpose_legacy_approved_run(self):
        self.claimed()
        with self.assertRaisesRegex(dispatch.DispatchError, "legacy approval"):
            dispatch.prepare_review(self.run)


class AutomaticDispatchTests(unittest.TestCase):
    def setUp(self):
        self.fixture = DispatchTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.run = self.fixture.run.resolve()
        self.fixture.run = self.run
        self.home = self.run / "settings-home"
        self.home.mkdir()
        patcher = patch.object(dispatch, "SETTINGS_HOME", self.home)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.fixture.items.update(reviewed=False, source_video="recording.mp4")
        for task in self.fixture.routes["tasks"]:
            task["target_basis"] = "explicit"
        self.fixture.save_inputs()
        (self.run / "blurt/recording.mp4").write_bytes(b"user voice")

    def enable(self, consent_id="consent-1", bind=True):
        self.consent = {"id": consent_id, "enabled_at": dispatch.now()}
        dispatch.write_json(self.home / "settings.json", {"dispatch_mode": "automatic", "dispatch_consent": self.consent})
        if bind:
            dispatch.bind_input(self.run, "blurt/recording.mp4", self.consent, self.fixture.manifest["targets"])

    def test_default_disabled_and_no_retroactive_authorization(self):
        with self.assertRaisesRegex(dispatch.DispatchError, "disabled"):
            dispatch.prepare_auto(self.run)
        self.assertFalse((self.run / "dispatch-plan.json").exists())
        self.enable(bind=False)
        plan = dispatch.prepare_auto(self.run)
        self.assertEqual(plan["batches"], [])
        self.assertEqual(len(plan["pending"]), 2)
        self.assertFalse((self.run / "approval.json").exists())
        self.assertFalse((self.run / "review-confirmation.json").exists())

    def test_automatic_claim_is_idempotent_and_receipt_recovers_after_disable(self):
        self.enable()
        original = (self.run / "blurt/items.json").read_bytes()
        plan = dispatch.prepare_auto(self.run)
        self.assertEqual(dispatch.prepare_auto(self.run), plan)
        self.assertEqual(plan["authorization_mode"], "automatic")
        self.assertIn("选择自动派发后主动提交", plan["batches"][0]["prompt"])
        self.assertNotIn("用户已审核", plan["batches"][0]["prompt"])
        claimed = dispatch.claim(self.run, "B1")
        self.assertEqual(claimed["tool"], "send_message_to_thread")
        with self.assertRaisesRegex(dispatch.DispatchError, "already claimed"):
            dispatch.claim(self.run, "B1")
        dispatch.write_json(self.home / "settings.json", {"dispatch_mode": "manual"})
        response = self.run / "response.json"
        dispatch.write_json(response, {"ok": True, "threadId": "thread-1"})
        self.assertEqual(dispatch.receipt(self.run, "B1", response, "sent")["status"], "sent")
        self.assertFalse(dispatch.status(self.run)["snapshot_current"])
        self.assertEqual(dispatch.status(self.run)["batches"][0]["status"], "sent")
        with self.assertRaisesRegex(dispatch.DispatchError, "dispatch attempts"):
            dispatch.prepare_review(self.run)
        self.assertEqual((self.run / "blurt/items.json").read_bytes(), original)
        self.assertFalse((self.run / "approval.json").exists())

    def test_disabled_or_expired_opt_in_blocks_unclaimed_batches(self):
        self.enable()
        dispatch.prepare_auto(self.run)
        dispatch.write_json(self.home / "settings.json", {"dispatch_mode": "manual"})
        with self.assertRaisesRegex(dispatch.DispatchError, "disabled"):
            dispatch.claim(self.run, "B1")
        self.enable("consent-2", bind=False)
        with self.assertRaisesRegex(dispatch.DispatchError, "snapshot changed"):
            dispatch.claim(self.run, "B1")
        self.assertFalse((self.run / "dispatch-state.json").exists())
        self.assertEqual(dispatch.prepare_auto(self.run)["batches"], [])

    def test_changed_voice_or_processed_items_invalidate_old_plan(self):
        self.enable()
        dispatch.prepare_auto(self.run)
        self.fixture.items["items"][0]["quote"] = "新的处理内容"
        self.fixture.save("blurt/items.json", self.fixture.items)
        with self.assertRaisesRegex(dispatch.DispatchError, "snapshot changed"):
            dispatch.claim(self.run, "B1")
        (self.run / "blurt/recording.mp4").write_bytes(b"different voice")
        self.assertEqual(dispatch.prepare_auto(self.run)["batches"], [])

    def test_unknown_target_and_ambiguous_item_are_individually_pending(self):
        self.enable()
        self.fixture.routes["tasks"][1]["target_key"] = "unknown"
        self.fixture.save("routes.json", self.fixture.routes)
        plan = dispatch.prepare_auto(self.run)
        self.assertEqual([task["id"] for task in plan["batches"][0]["tasks"]], ["D1"])
        self.assertEqual(plan["pending"][0]["task_id"], "D2")
        self.fixture.routes["tasks"][1]["target_key"] = "T1"
        self.fixture.items["items"][1]["questions"] = ["具体要改哪里"]
        self.fixture.save_inputs()
        self.assertEqual(dispatch.prepare_auto(self.run)["pending"][0]["task_id"], "D2")

    def new_project(self):
        self.enable()
        self.fixture.items["items"][1]["quote"] = "请在 Two Hours 项目新建会话做验证"
        target = {"key": "T2", "type": "project", "project_id": "observed-project-2", "title": "Two Hours", "source_ids": ["S1"],
                  "verification": {"tool": "list_projects", "id": "observed-project-2", "verified_at": dispatch.now()}}
        self.fixture.routes["targets"] = [*self.fixture.manifest["targets"], target]
        self.fixture.routes["tasks"][1].update(target_key="T2", target_quote="请在 Two Hours 项目新建会话做验证")
        self.fixture.save_inputs()

    def test_explicit_new_project_with_native_verification_is_eligible(self):
        self.new_project()
        plan = dispatch.prepare_auto(self.run)
        self.assertEqual(len(plan["batches"]), 2)
        self.assertEqual(plan["pending"], [])
        self.assertEqual(dispatch.claim(self.run, "B2")["tool"], "create_thread")

    def test_new_project_quote_must_be_original_user_words(self):
        self.new_project()
        self.fixture.routes["tasks"][1]["target_quote"] = "编造的新项目指令"
        self.fixture.save("routes.json", self.fixture.routes)
        plan = dispatch.prepare_auto(self.run)
        self.assertEqual(len(plan["batches"]), 1)
        self.assertIn("原话", " ".join(plan["pending"][0]["reasons"]))

    def test_new_project_verification_id_must_match_target(self):
        self.new_project()
        self.fixture.routes["targets"][1]["verification"]["id"] = "different-project"
        self.fixture.save("routes.json", self.fixture.routes)
        plan = dispatch.prepare_auto(self.run)
        self.assertEqual(len(plan["batches"]), 1)
        self.assertIn("核验", " ".join(plan["pending"][0]["reasons"]))

    def test_written_submission_hash_and_original_authorization_are_bound(self):
        self.enable(bind=False)
        (self.run / "blurt/items.json").unlink()
        dispatch.write_json(self.run / "written-items.json", {"reviewed": True, "items": self.fixture.items["items"]})
        dispatch.bind_input(self.run, "written-items.json", self.consent, self.fixture.manifest["targets"])
        plan = dispatch.prepare_auto(self.run)
        self.assertEqual(len(plan["batches"]), 1)
        self.fixture.items["items"][0]["quote"] = "改过的文字"
        dispatch.write_json(self.run / "written-items.json", {"reviewed": True, "items": self.fixture.items["items"]})
        self.assertEqual(dispatch.prepare_auto(self.run)["batches"], [])

    def test_automatic_plan_without_claim_can_reach_manual_review(self):
        self.enable()
        dispatch.prepare_auto(self.run)
        review = dispatch.prepare_review(self.run)
        self.assertEqual(dispatch.current_review(self.run), review)
        self.assertFalse((self.run / "dispatch-plan.json").exists())
        approval, _ = dispatch.approve_review(self.run, self.fixture.review_body(review))
        self.assertTrue(approval["batch_ids"])
        self.assertNotIn("authorization_mode", dispatch.current_plan(self.run))


if __name__ == "__main__":
    unittest.main()
