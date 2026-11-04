"""Work-state rules in scripts/dispatch.py, checked against Blurt v1.0.12.

tests/fixtures/blurt_lifecycle.json is an unchanged copy of the upstream
tests/fixtures/lifecycle.json at AGIHunt/blurt b9052e5f (MIT, Copyright (c)
2026 blurt contributors; see vendor/blurt/LICENSE and
references/blurt-task-ledger.SOURCE.json).
"""
import hashlib
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_dispatch as fixture  # noqa: E402
import test_work_receipts as receipts  # noqa: E402

dispatch = fixture.dispatch
LIFE = json.loads((Path(__file__).resolve().parent / "fixtures" / "blurt_lifecycle.json").read_text(encoding="utf-8"))
UPSTREAM_AGENT = {tuple(move) for move in LIFE["agent_moves"]}
UPSTREAM_APP_EXTRA = {tuple(move) for move in LIFE["app_extra_moves"]}

# Upstream agent moves that Two Hours deliberately does not accept, each with its reason.
NO_NEW_STATE = "Two Hours has no new state: a task enters the ledger at handed_off when its approved batch is claimed"
WITHDRAW_FIRST = ("a claim awaiting the user must be withdrawn with reopened before more work is reported, "
                  "so a later report cannot silently replace a pending claim")
AGENT_MOVES_NOT_PORTED = {
    ("new", "in_progress"): NO_NEW_STATE,
    ("new", "to_verify"): NO_NEW_STATE,
    ("to_verify", "in_progress"): WITHDRAW_FIRST,
    ("to_verify", "to_verify"): WITHDRAW_FIRST,
}
# Copied from upstream skills/blurt/scripts/_common.py:754 and :783-784 (not part of lifecycle.json).
UPSTREAM_WORK_KEYS = ("to", "agent", "job", "handoff", "claim", "note", "commit", "pr", "at", "by")
UPSTREAM_FIX_TO_WORK = {"fixing": ("in_progress", None), "fixed": ("to_verify", "fixed"),
                        "wontfix": ("to_verify", "wontfix"), "reopened": ("todo", None)}
STARTS = ("handed_off", "in_progress", "to_verify", "todo", "done", "wontfix")
# Fields of work after the user sends a task back (verify todo) that differ from upstream set_state
# (items.py:837-856: carry is empty for todo, and agent falls back to "human" for the user).
# field: (upstream value, Two Hours value, reason)
KEEP_HANDOFF = "the task stays bound to the session and attempt that will receive the follow-up"
VERIFY_TODO_DIFFERENCES = {
    "to": (None, "session", KEEP_HANDOFF),
    "agent": ("human", "previous agent", "the agent that made the rejected claim stays visible; by=user records the decision"),
    "job": (None, "attempt_id", KEEP_HANDOFF),
    "handoff": (None, "batch_id", KEEP_HANDOFF),
}


class LifecycleTableTests(unittest.TestCase):
    def test_work_states_are_upstream_states(self):
        self.assertTrue(set(dispatch.WORK_STATES) <= set(LIFE["states"]))
        self.assertEqual(set(STARTS), set(dispatch.WORK_STATES))

    def test_agent_moves_are_upstream_minus_documented_differences(self):
        self.assertTrue(set(AGENT_MOVES_NOT_PORTED) <= UPSTREAM_AGENT)
        self.assertTrue(all(reason for reason in AGENT_MOVES_NOT_PORTED.values()))
        self.assertEqual(set(dispatch.AGENT_MOVES), UPSTREAM_AGENT - set(AGENT_MOVES_NOT_PORTED))

    def test_claim_is_an_app_move_that_an_agent_may_not_make(self):
        self.assertIn(dispatch.CLAIM_MOVE, UPSTREAM_APP_EXTRA)
        self.assertNotIn(dispatch.CLAIM_MOVE, UPSTREAM_AGENT)

    def test_user_decisions_are_upstream_user_actions(self):
        actions = {(start, action["to"]) for action in LIFE["actions"] if action["to"] for start in action["from"]}
        self.assertEqual(set(dispatch.USER_DECISIONS), {("to_verify", "done"), ("to_verify", "wontfix"), ("to_verify", "todo")})
        self.assertTrue(set(dispatch.USER_DECISIONS) <= actions)

    def test_fixture_copy_is_recorded_in_source_file(self):
        root = Path(__file__).resolve().parents[1]
        source = json.loads((root / "references" / "blurt-task-ledger.SOURCE.json").read_text(encoding="utf-8"))
        copied = [entry for entry in source["sources"] if entry.get("copied_to") == "tests/fixtures/blurt_lifecycle.json"]
        # a Windows checkout may convert line endings; the recorded hash is of the LF upstream file
        data = (root / "tests" / "fixtures" / "blurt_lifecycle.json").read_bytes().replace(b"\r\n", b"\n")
        digest = hashlib.sha256(data).hexdigest()
        self.assertEqual([entry["sha256"] for entry in copied], [digest])
        self.assertEqual(source["upstream_commit"], "b9052e5f30e3a357fdc29f7d6ec56d44b428d60c")
        vendor = json.loads((root / "vendor" / "blurt" / "SOURCE.json").read_text(encoding="utf-8"))
        self.assertNotIn("upstream_task_receipts", vendor)

    def test_mark_mapping_and_work_keys_match_upstream(self):
        self.assertEqual(dispatch.MARK_STATES, UPSTREAM_FIX_TO_WORK)
        self.assertEqual(dispatch.WORK_KEYS, UPSTREAM_WORK_KEYS + ("source_thread", "source_host"))


class WorkLifecycleTests(unittest.TestCase):
    setUp = receipts.WorkReceiptTests.setUp
    save = fixture.DispatchTests.save
    save_inputs = fixture.DispatchTests.save_inputs
    approve = fixture.DispatchTests.approve
    claimed = fixture.DispatchTests.claimed
    report = receipts.WorkReceiptTests.report

    def state_bytes(self):
        return (self.run / "dispatch-state.json").read_bytes()

    def force(self, task_id, work_state):
        state = dispatch.read_json(self.run / "dispatch-state.json")
        item = state["batches"]["B1"]["tasks"][task_id]
        item.update(state=work_state, reports={}, work_history=[])
        dispatch.write_json(self.run / "dispatch-state.json", state)

    def test_every_start_and_report_state_follows_the_table(self):
        claim = self.claimed()
        for number, (start, report_state) in enumerate((s, r) for s in STARTS for r in dispatch.MARK_STATES):
            with self.subTest(start=start, report=report_state):
                self.force("D1", start)
                before = self.state_bytes()
                report = self.report(claim, state=report_state, report_id=f"r{number}")
                allowed = (start, dispatch.MARK_STATES[report_state][0]) in UPSTREAM_AGENT - set(AGENT_MOVES_NOT_PORTED)
                if allowed:
                    self.assertEqual(dispatch.work_report(self.run, report, "thread-1")["state"],
                                     dispatch.MARK_STATES[report_state][0])
                else:
                    with self.assertRaises(dispatch.DispatchError):
                        dispatch.work_report(self.run, report, "thread-1")
                    self.assertEqual(self.state_bytes(), before)

    def test_reopened_only_withdraws_work_in_progress_or_awaiting_verification(self):
        claim = self.claimed()
        before = self.state_bytes()
        with self.assertRaisesRegex(dispatch.DispatchError, "handed_off -> todo"):
            dispatch.work_report(self.run, self.report(claim, state="reopened", report_id="r1"), "thread-1")
        self.assertEqual(self.state_bytes(), before)
        dispatch.work_report(self.run, self.report(claim, state="fixing", report_id="r2"), "thread-1")
        self.assertEqual(dispatch.work_report(self.run, self.report(claim, state="reopened", report_id="r3"), "thread-1")["state"], "todo")
        with self.assertRaisesRegex(dispatch.DispatchError, "todo -> todo"):
            dispatch.work_report(self.run, self.report(claim, state="reopened", report_id="r4"), "thread-1")
        dispatch.work_report(self.run, self.report(claim, state="fixed", report_id="r5"), "thread-1")
        self.assertEqual(dispatch.work_report(self.run, self.report(claim, state="reopened", report_id="r6"), "thread-1")["state"], "todo")

    def test_failed_delivery_rejects_reports(self):
        claim = self.claimed()
        self.save("response.json", {"error": "unavailable"})
        dispatch.receipt(self.run, "B1", self.run / "response.json", "failed")
        before = self.state_bytes()
        with self.assertRaisesRegex(dispatch.DispatchError, "delivery failed"):
            dispatch.work_report(self.run, self.report(claim, state="fixed"), "thread-1")
        self.assertEqual(self.state_bytes(), before)

    def test_unresolved_delivery_accepts_reports_without_changing_delivery(self):
        claim = self.claimed()
        item = dispatch.work_report(self.run, self.report(claim, state="fixed"), "thread-1")
        self.assertEqual(item["state"], "to_verify")
        self.assertEqual(dispatch.status(self.run)["batches"][0]["status"], "sending")

    def test_work_has_the_same_keys_after_claim_report_and_verification(self):
        claim = self.claimed()
        keys = set(dispatch.WORK_KEYS)
        item = dispatch.status(self.run)["batches"][0]["tasks"]["D1"]
        self.assertEqual(set(item["work"]), keys)
        self.assertEqual((item["work"]["by"], item["work"]["job"], item["work"]["handoff"]), ("app", claim["attempt_id"], "B1"))
        for number, report_state in enumerate(("fixing", "fixed")):
            item = dispatch.work_report(self.run, self.report(claim, state=report_state, report_id=f"r{number}",
                                                              commit="abc"), "thread-1")
            self.assertEqual(set(item["work"]), keys)
            self.assertEqual(item["work"]["by"], "agent")
        item = dispatch.verify_work(self.run, "B1", "D1", claim["attempt_id"], 2, "done", "核验通过")
        self.assertEqual(set(item["work"]), keys)
        self.assertEqual((item["work"]["by"], item["work"]["claim"], item["work"]["job"], item["work"]["commit"]),
                         ("user", None, claim["attempt_id"], "abc"))
        self.assertEqual([entry["state"] for entry in item["work_history"]], ["handed_off", "in_progress", "to_verify"])
        for entry in item["work_history"]:
            self.assertEqual(set(entry), keys | {"state"})

    def test_manual_claim_records_approval_as_authorization(self):
        self.claimed()
        attempt = dispatch.read_json(self.run / "dispatch-state.json")["batches"]["B1"]
        self.assertEqual(attempt["authorization"], {
            "mode": "manual", "approved_at": "2026-10-23T09:00:00+08:00",
            "approval_sha256": hashlib.sha256((self.run / "approval.json").read_bytes()).hexdigest()})

    def test_repeated_report_of_the_same_state_updates_without_history(self):
        claim = self.claimed()
        first = dispatch.work_report(self.run, self.report(claim, state="fixing", report_id="r1", commit="abc"), "thread-1")
        second = dispatch.work_report(self.run, dict(self.report(claim, state="fixing", report_id="r2"), note="继续"), "thread-1")
        self.assertEqual(second["state"], "in_progress")
        self.assertEqual(second["work_history"], first["work_history"])
        self.assertEqual((second["work"]["note"], second["work"]["commit"], second["revision"]), ("继续", "abc", 2))
        self.assertEqual(set(second["work"]), set(dispatch.WORK_KEYS))


    def test_resent_accepted_report_is_idempotent_after_delivery_failed(self):
        claim = self.claimed()
        report = self.report(claim, state="fixed", commit="abc")
        accepted = dispatch.work_report(self.run, report, "thread-1")
        self.save("response.json", {"error": "unavailable"})
        dispatch.receipt(self.run, "B1", self.run / "response.json", "failed")
        before = self.state_bytes()
        self.assertEqual(dispatch.work_report(self.run, dict(report), "thread-1"), accepted)
        self.assertEqual(self.state_bytes(), before)
        with self.assertRaisesRegex(dispatch.DispatchError, "different content"):
            dispatch.work_report(self.run, dict(report, note="改过"), "thread-1")
        with self.assertRaisesRegex(dispatch.DispatchError, "delivery failed"):
            dispatch.work_report(self.run, self.report(claim, state="reopened", report_id="r2"), "thread-1")
        self.assertEqual(self.state_bytes(), before)

    def test_claim_carries_commit_and_agent_from_earlier_work_like_upstream(self):
        claim = self.claimed()
        dispatch.work_report(self.run, self.report(claim, state="fixing", report_id="r1", commit="abc",
                                                   pr="https://example.org/pr/1", agent="codex"), "thread-1")
        item = dispatch.work_report(self.run, self.report(claim, state="fixed", report_id="r2"), "thread-1")
        self.assertEqual((item["work"]["commit"], item["work"]["pr"], item["work"]["agent"], item["work"]["claim"]),
                         ("abc", "https://example.org/pr/1", "codex", "fixed"))
        self.assertEqual(item["work"]["note"], "实现并验证")
        newer = dispatch.work_report(self.run, self.report(claim, state="reopened", report_id="r3"), "thread-1")
        self.assertEqual((newer["work"]["commit"], newer["work"]["pr"], newer["work"]["agent"]), (None, None, None))
        # a fresh start from todo does not inherit the withdrawn work (upstream carries only from handed_off,
        # in_progress and to_verify) and in_progress never carries commit or PR
        again = dispatch.work_report(self.run, self.report(claim, state="fixing", report_id="r4"), "thread-1")
        self.assertEqual((again["work"]["commit"], again["work"]["pr"], again["work"]["agent"]), (None, None, None))

    def test_new_report_values_win_over_carried_ones(self):
        claim = self.claimed()
        dispatch.work_report(self.run, self.report(claim, state="fixing", report_id="r1", commit="abc", agent="codex"), "thread-1")
        item = dispatch.work_report(self.run, self.report(claim, state="fixed", report_id="r2", commit="def",
                                                          agent="claude"), "thread-1")
        self.assertEqual((item["work"]["commit"], item["work"]["agent"]), ("def", "claude"))

    def test_verify_todo_clears_commit_and_pr(self):
        claim = self.claimed()
        dispatch.work_report(self.run, self.report(claim, state="fixed", commit="abc", pr="https://example.org/pr/1",
                                                   agent="codex"), "thread-1")
        item = dispatch.verify_work(self.run, "B1", "D1", claim["attempt_id"], 1, "todo", "还要改")
        work = item["work"]
        self.assertEqual(item["state"], "todo")
        self.assertIsNone(work["commit"])
        self.assertIsNone(work["pr"])
        self.assertIsNone(work["claim"])
        self.assertEqual((work["note"], work["by"]), ("还要改", "user"))
        self.assertEqual(item["work_history"][-1]["commit"], "abc")
        self.assertEqual(item["work_history"][-1]["pr"], "https://example.org/pr/1")
        ours = {"to": "session", "agent": "codex", "job": claim["attempt_id"], "handoff": "B1"}
        self.assertEqual(set(ours), set(VERIFY_TODO_DIFFERENCES))
        for field, (upstream, _, reason) in VERIFY_TODO_DIFFERENCES.items():
            self.assertTrue(reason)
            self.assertNotEqual(work[field], upstream)
            self.assertEqual(work[field], ours[field])
        # work restarted after the user sent it back starts clean, as upstream (todo is not a carry state)
        again = dispatch.work_report(self.run, self.report(claim, state="fixing", report_id="r2"), "thread-1")
        self.assertEqual((again["work"]["agent"], again["work"]["commit"], again["work"]["pr"]), (None, None, None))

    def test_verify_todo_differences_are_recorded_in_source_file(self):
        root = Path(__file__).resolve().parents[1]
        source = json.loads((root / "references" / "blurt-task-ledger.SOURCE.json").read_text(encoding="utf-8"))
        recorded = [entry["verify_todo_fields"] for entry in source["differences"] if "verify_todo_fields" in entry]
        self.assertEqual(len(recorded), 1)
        self.assertEqual({field: (value["upstream"], value["two_hours"]) for field, value in recorded[0].items()},
                         {field: (upstream, ours) for field, (upstream, ours, _) in VERIFY_TODO_DIFFERENCES.items()})


class AutomaticAuthorizationTests(unittest.TestCase):
    setUp = fixture.AutomaticDispatchTests.setUp
    enable = fixture.AutomaticDispatchTests.enable

    def test_automatic_claim_records_opt_in_and_app(self):
        self.enable()
        dispatch.prepare_auto(self.run)
        dispatch.claim(self.run, "B1")
        attempt = dispatch.read_json(self.run / "dispatch-state.json")["batches"]["B1"]
        self.assertEqual(attempt["authorization"], {"mode": "automatic", "opt_in_id": "consent-1"})
        for item in attempt["tasks"].values():
            self.assertEqual(item["work"]["by"], "app")


if __name__ == "__main__":
    unittest.main()
