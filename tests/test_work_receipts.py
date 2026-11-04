import json
from pathlib import Path
import subprocess
import sys
import unittest

import test_dispatch as fixture

dispatch = fixture.dispatch
SCRIPT = fixture.SCRIPT


class WorkReceiptTests(unittest.TestCase):
    save = fixture.DispatchTests.save
    save_inputs = fixture.DispatchTests.save_inputs
    approve = fixture.DispatchTests.approve
    claimed = fixture.DispatchTests.claimed

    def setUp(self):
        fixture.DispatchTests.setUp(self)
        root = self.run
        self.run = root / "one-run"
        self.run.mkdir()
        for path in list(root.iterdir()):
            if path != self.run:
                path.rename(self.run / path.name)

    def report(self, claim, task_id="D1", state="fixed", report_id="r1", **fields):
        current = dispatch.status(self.run)["batches"][0]["tasks"][task_id]
        return {"plan_id": claim["plan_id"], "batch_id": "B1", "attempt_id": claim["attempt_id"],
                "task_id": task_id, "uid": current["uid"], "report_id": report_id,
                "state": state, "note": "实现并验证", "expected_revision": current["revision"], **fields}

    def test_reports_are_per_task_and_need_user_verification(self):
        claim = self.claimed()
        self.assertIn(claim["attempt_id"], claim["args"]["prompt"])
        report = self.report(claim, commit="abc", pr="https://example.org/pr/1")
        item = dispatch.work_report(self.run, report, "thread-1")
        self.assertEqual(item["state"], "to_verify")
        self.assertEqual(item["work"]["claim"], "fixed")
        self.assertEqual(dispatch.work_report(self.run, report, "thread-1"), item)
        self.assertEqual(dispatch.status(self.run)["batches"][0]["tasks"]["D2"]["state"], "handed_off")
        with self.assertRaisesRegex(dispatch.DispatchError, "different content"):
            dispatch.work_report(self.run, dict(report, note="different"), "thread-1")
        verified = dispatch.verify_work(self.run, "B1", "D1", claim["attempt_id"], 1, "done", "检查通过")
        self.assertEqual(verified["work_history"][-1]["claim"], "fixed")
        self.assertEqual(verified["state"], "done")
        with self.assertRaisesRegex(dispatch.DispatchError, "final"):
            dispatch.work_report(self.run, self.report(claim, report_id="r2"), "thread-1")

    def test_mismatched_identity_and_invalid_transitions_do_not_change_state(self):
        claim = self.claimed()
        report = self.report(claim)
        for key in ("plan_id", "batch_id", "attempt_id", "task_id", "uid"):
            with self.subTest(key=key), self.assertRaises(dispatch.DispatchError):
                dispatch.work_report(self.run, dict(report, **{key: "wrong"}), "thread-1")
        with self.assertRaisesRegex(dispatch.DispatchError, "source thread"):
            dispatch.work_report(self.run, report, "wrong-thread")
        with self.assertRaisesRegex(dispatch.DispatchError, "source thread"):
            dispatch.work_report(self.run, report, "thread-1", "other-host")
        with self.assertRaises(dispatch.DispatchError):
            dispatch.verify_work(self.run, "B1", "D1", claim["attempt_id"], 0, "done", "no claim")
        dispatch.work_report(self.run, report, "thread-1")
        with self.assertRaisesRegex(dispatch.DispatchError, "Withdraw"):
            dispatch.work_report(self.run, self.report(claim, "D1", "fixing", "r2"), "thread-1")
        reopened = dispatch.work_report(self.run, self.report(claim, "D1", "reopened", "r3"), "thread-1")
        self.assertEqual(reopened["state"], "todo")
        self.assertEqual(reopened["work_history"][-1]["claim"], "fixed")

    def test_new_project_requires_exact_thread_from_delivery_response(self):
        self.manifest["targets"][0] = {"key": "T1", "type": "project", "project_id": "p1",
                                       "title": "project", "source_ids": ["S1"]}
        self.save_inputs()
        claim = self.claimed()
        report = self.report(claim)
        with self.assertRaisesRegex(dispatch.DispatchError, "source thread"):
            dispatch.work_report(self.run, report, "created-thread")
        self.save("response.json", {"threadId": "created-thread"})
        dispatch.receipt(self.run, "B1", self.run / "response.json", "sent")
        with self.assertRaisesRegex(dispatch.DispatchError, "source thread"):
            dispatch.work_report(self.run, report, "other")
        item = dispatch.work_report(self.run, report, "created-thread")
        self.assertEqual(item["work"]["source_thread"], "created-thread")

    def test_stale_report_and_stale_verification_are_rejected(self):
        claim = self.claimed()
        stale = self.report(claim, report_id="late", state="fixing")
        dispatch.work_report(self.run, self.report(claim, state="fixed"), "thread-1")
        with self.assertRaisesRegex(dispatch.DispatchError, "revision is stale"):
            dispatch.work_report(self.run, stale, "thread-1")
        with self.assertRaisesRegex(dispatch.DispatchError, "revision is stale"):
            dispatch.verify_work(self.run, "B1", "D1", claim["attempt_id"], 0, "done", "old view")
        dispatch.verify_work(self.run, "B1", "D1", claim["attempt_id"], 1, "todo", "请继续")
        with self.assertRaisesRegex(dispatch.DispatchError, "revision is stale"):
            dispatch.work_report(self.run, stale, "thread-1")

    def test_delivery_and_work_are_independent_and_persisted(self):
        claim = self.claimed()
        report = self.report(claim, state="fixing")
        path = self.run / "work.json"
        self.save("work.json", report)
        result = subprocess.run([sys.executable, str(SCRIPT), "work-report", "--run", str(self.run),
                                 "--report", str(path), "--source-thread", "thread-1"], capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout)["state"], "in_progress")
        self.save("response.json", {"ok": True})
        dispatch.receipt(self.run, "B1", self.run / "response.json", "sent")
        batch = dispatch.status(self.run)["batches"][0]
        self.assertEqual(batch["status"], "sent")
        self.assertEqual(batch["tasks"]["D1"]["state"], "in_progress")

    def test_ledger_lists_open_tasks_and_legacy_attempts_without_inventing_progress(self):
        claim = self.claimed()
        dispatch.work_report(self.run, self.report(claim), "thread-1")
        dispatch.verify_work(self.run, "B1", "D1", claim["attempt_id"], 1, "done", "核验")
        result = dispatch.ledger(self.run.parent)
        entries = [e for e in result["items"] if e["run"] == str(self.run)]
        self.assertEqual([e["task_id"] for e in entries], ["D2"])
        state = dispatch.read_json(self.run / "dispatch-state.json")
        state["batches"]["B1"].pop("tasks")
        state["batches"]["B1"].pop("attempt_id")
        dispatch.write_json(self.run / "dispatch-state.json", state)
        entries = [e for e in dispatch.ledger(self.run.parent)["items"] if e["run"] == str(self.run)]
        self.assertEqual(entries[0]["tracking"], "legacy_unavailable")
        self.assertNotIn("state", entries[0])

    def test_concurrent_reports_and_delivery_receipt_preserve_all_updates(self):
        claim = self.claimed()
        self.save("d1.json", self.report(claim, "D1", "fixing", "r1"))
        self.save("d2.json", self.report(claim, "D2", "fixed", "r2"))
        self.save("response.json", {"ok": True})
        commands = [
            [sys.executable, str(SCRIPT), "work-report", "--run", str(self.run), "--report", str(self.run / "d1.json"), "--source-thread", "thread-1"],
            [sys.executable, str(SCRIPT), "work-report", "--run", str(self.run), "--report", str(self.run / "d2.json"), "--source-thread", "thread-1"],
            [sys.executable, str(SCRIPT), "receipt", "--run", str(self.run), "--batch", "B1",
             "--outcome", "sent", "--response", str(self.run / "response.json")],
        ]
        processes = [subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                     for command in commands]
        for process in processes:
            out, err = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 0, (out, err))
        batch = dispatch.status(self.run)["batches"][0]
        self.assertEqual(batch["status"], "sent")
        self.assertEqual(batch["tasks"]["D1"]["state"], "in_progress")
        self.assertEqual(batch["tasks"]["D2"]["state"], "to_verify")

    def test_corrupt_ledger_is_reported(self):
        self.claimed()
        (self.run / "dispatch-state.json").write_text("bad", encoding="utf-8")
        result = dispatch.ledger(self.run.parent)
        self.assertTrue(any(e["run"] == str(self.run) for e in result["errors"]))
