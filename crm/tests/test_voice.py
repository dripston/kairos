import json
import re
import unittest
from datetime import datetime

from kairos.db import connect
from kairos.seed import BORROWER_ID, LOAN_B
from kairos.views import borrower_view
from kairos.voice.executor import SCENARIOS, run_branch
from kairos.voice.flows import (IVR_MAX_ATTEMPTS, agent_specs, classify_utterance, extract_date, run_ivr,
                                run_voicebot)
from pathlib import Path

NOW = datetime.fromisoformat("2026-11-21T12:30:00+05:30")


def one(conn, sql, *args):
    return conn.execute(sql, args).fetchone()


class FlowTest(unittest.TestCase):
    def test_ivr_valid_digit_yields_intent(self):
        r = run_ivr(True, ["2"], LOAN_B)
        self.assertEqual((r.disposition, r.dtmf, r.intent["intent"]), ("COMPLETED", "2", "NEEDS_TIME"))

    def test_ivr_retries_are_bounded_and_invent_no_intent(self):
        r = run_ivr(True, ["7", "8", "0", "2"], LOAN_B)  # the 4th press is never asked for
        self.assertEqual((r.attempts, r.intent, r.exit_reason), (IVR_MAX_ATTEMPTS, None, "max_retries_invalid_digit"))
        r = run_ivr(True, [None, None, None], LOAN_B)
        self.assertEqual((r.intent, r.exit_reason), (None, "max_retries_no_input"))
        r = run_ivr(False, [], LOAN_B)
        self.assertEqual((r.disposition, r.intent, r.transcript), ("NO_ANSWER", None, []))

    def test_ivr_repeat_then_choice(self):
        r = run_ivr(True, ["9", "3"], LOAN_B)
        self.assertEqual((r.attempts, r.intent["intent"]), (2, "CALLBACK"))

    def test_voicebot_needs_time_asks_for_date_once(self):
        r = run_voicebot(True, ["haan", "thoda time chahiye", "parson tak"], LOAN_B, NOW)
        self.assertEqual(r.intent["intent"], "NEEDS_TIME")
        self.assertEqual(r.intent["agreed_date"][:10], "2026-11-23")
        r = run_voicebot(True, ["haan", "thoda time chahiye", "pata nahi"], LOAN_B, NOW)
        self.assertNotIn("agreed_date", r.intent)

    def test_voicebot_declined_and_unclear_have_no_intent(self):
        r = run_voicebot(True, ["Abhi nahi, busy hoon"], LOAN_B, NOW)
        self.assertEqual((r.disposition, r.intent), ("DECLINED", None))
        r = run_voicebot(True, ["haan", "hmm", "accha"], LOAN_B, NOW)
        self.assertEqual((r.disposition, r.exit_reason, r.intent), ("COMPLETED", "unclear", None))

    def test_bots_never_speak_amounts_or_loan_numbers(self):
        for r in (run_ivr(True, ["1"], LOAN_B),
                  run_voicebot(True, ["haan", "salary 25 ko aayegi"], LOAN_B, NOW),
                  run_voicebot(True, ["haan", "pay kar diya"], LOAN_B, NOW)):
            for turn in r.transcript:
                if turn["speaker"] == "bot":
                    self.assertNotRegex(turn["text"], r"₹|Rs|PL\d|\d{4,}")

    def test_hardship_reason_is_restricted(self):
        r = run_voicebot(True, ["ji", "mera accident ho gaya, hospital mein hoon"], LOAN_B, NOW)
        self.assertEqual(r.intent["intent"], "HARDSHIP")
        self.assertNotIn("hospital", r.intent["reason"].lower())

    def test_nlu_and_dates(self):
        self.assertEqual(classify_utterance("Maine kal hi pay kar diya"), "CLAIMS_PAID")
        self.assertEqual(classify_utterance("please call mat karo"), "STOP")
        self.assertEqual(extract_date("25 ko de dunga", NOW).day, 25)
        self.assertEqual(extract_date("5 tarikh tak", NOW).month, 12)  # 5th already passed this month

    def test_exported_specs_match_code(self):
        agents = Path(__file__).resolve().parent.parent / "kairos" / "voice" / "agents"
        for flow_id, spec in agent_specs().items():
            self.assertEqual(json.loads((agents / f"{flow_id}.json").read_text()), spec,
                             f"{flow_id}.json is stale: run python3 -m kairos.voice export")


class ExecutorTest(unittest.TestCase):
    def setUp(self):
        self.conn = connect(":memory:")

    def tearDown(self):
        self.conn.close()

    def test_every_scenario_runs_from_the_saved_state(self):
        for s in SCENARIOS:
            run = run_branch(self.conn, s.key)
            self.assertEqual(run.touch_id, "T-1042-V001", s.key)

    def test_ivr_needs_time_flows_to_supervisor_wait(self):
        run = run_branch(self.conn, "ivr_needs_time")
        types = [e["event_type"] for e in run.to_dict()["events"]]
        self.assertEqual(types, ["touch.planned", "touch.sent", "call.outcome", "borrower.intent",
                                 "agent.outputs", "supervisor.decision"])
        d = one(self.conn, "SELECT decision, trigger_event_id FROM supervisor_decisions ORDER BY occurred_at DESC LIMIT 1")
        intent = one(self.conn, "SELECT event_id, source_event_id, touch_id FROM borrower_intents ORDER BY occurred_at DESC LIMIT 1")
        call = one(self.conn, "SELECT event_id, touch_id FROM call_outcomes")
        self.assertEqual(d["decision"], "WAIT")
        self.assertEqual(d["trigger_event_id"], intent["event_id"])
        self.assertEqual((intent["source_event_id"], intent["touch_id"]), (call["event_id"], call["touch_id"]))

    def test_no_answer_and_invalid_digits_create_no_intent(self):
        for key in ("ivr_no_answer", "ivr_invalid_digits"):
            run_branch(self.conn, key)
            n = one(self.conn, "SELECT COUNT(*) n FROM borrower_intents WHERE source_event_id IN "
                               "(SELECT event_id FROM call_outcomes)")["n"]
            self.assertEqual(n, 0, key)

    def test_blocked_sends(self):
        run = run_branch(self.conn, "ivr_during_hold")
        self.assertIn("Supervisor WAIT", run.blocked_reason)
        run = run_branch(self.conn, "voicebot_no_consent")
        self.assertIn("No voice consent", run.blocked_reason)
        self.assertEqual(one(self.conn, "SELECT COUNT(*) n FROM call_outcomes")["n"], 0)
        self.assertEqual(one(self.conn, "SELECT status FROM touches WHERE touch_id = ?", run.touch_id)["status"],
                         "BLOCKED")
        view = borrower_view(self.conn, BORROWER_ID)
        self.assertTrue(any(e["title"].startswith("Blocked send") for e in view["timeline"]))

    def test_promise_date_and_hardship_drive_next_action(self):
        run_branch(self.conn, "voicebot_promise_date")
        na = borrower_view(self.conn, BORROWER_ID)["next_action"]
        self.assertEqual(na["action"], "WAIT")
        self.assertIn("25 Nov", na["headline"])
        run_branch(self.conn, "voicebot_hardship")
        self.assertEqual(borrower_view(self.conn, BORROWER_ID)["next_action"]["action"], "HANDOFF")

    def test_repeated_call_completion_webhook_is_ignored(self):
        from kairos.ingest import ingest_event
        from kairos.voice.executor import SARVAM, sarvam_webhook
        from kairos.voice.flows import run_ivr as ivr

        run = run_branch(self.conn, "ivr_no_answer")
        call_id = one(self.conn, "SELECT call_id FROM call_outcomes")["call_id"]
        before = one(self.conn, "SELECT COUNT(*) n FROM timeline_entries")["n"]
        again = ingest_event(self.conn, SARVAM.normalize(
            sarvam_webhook(call_id, run.touch_id, "IVR", ivr(False, [], LOAN_B), NOW)))
        self.assertEqual(again.status, "duplicate")
        self.assertEqual(one(self.conn, "SELECT COUNT(*) n FROM timeline_entries")["n"], before)


if __name__ == "__main__":
    unittest.main()
