import unittest

from kairos.db import connect
from kairos.ingest import IngestError, ingest_event
from kairos.adapters.fixtures import FixtureSarvamCallAdapter
from kairos.replay import STEPS, SMS, run_all, run_step
from kairos.seed import BORROWER_ID, LOAN_A, LOAN_B, TOUCH_REMIND_A, TOUCH_REMIND_B, TOUCH_SMS, seed
from kairos.views import borrower_view


def count(conn, table, where="1=1", *args):
    return conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}", args).fetchone()[0]


class JourneyTest(unittest.TestCase):
    def setUp(self):
        self.conn = connect(":memory:")
        seed(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_full_replay_passes_every_step(self):
        outcomes = run_all(self.conn)
        self.assertEqual([o["passed"] for o in outcomes], [True] * len(STEPS), [o["error"] for o in outcomes])

    def test_duplicate_creates_no_timeline_entry_or_side_effect(self):
        for i in range(4):
            run_step(self.conn, i)
        before = count(self.conn, "timeline_entries")
        outcome = run_step(self.conn, 4)
        self.assertEqual(outcome["events"][0]["status"], "duplicate")
        self.assertEqual(count(self.conn, "timeline_entries"), before)
        self.assertEqual(count(self.conn, "verified_payments"), 1)
        self.assertEqual(count(self.conn, "events", "event_type = 'touch.canceled'"), 1)
        self.assertEqual(count(self.conn, "duplicate_events"), 1)

    def test_replaying_the_whole_journey_twice_is_idempotent(self):
        run_all(self.conn)
        before = count(self.conn, "timeline_entries")
        for i in range(len(STEPS)):
            outcome = run_step(self.conn, i)
            self.assertTrue(all(e["status"] == "duplicate" for e in outcome["events"]))
        self.assertEqual(count(self.conn, "timeline_entries"), before)

    def test_delivery_intent_and_payment_stay_separate(self):
        run_step(self.conn, 0)  # delivery
        self.assertEqual(count(self.conn, "borrower_intents"), 0)
        run_step(self.conn, 1)  # needs time
        self.assertEqual(count(self.conn, "payment_claims"), 0)
        run_step(self.conn, 2)  # claim
        self.assertEqual(count(self.conn, "verified_payments"), 0)
        self.assertEqual(count(self.conn, "loans", "loan_id = ? AND status = 'OPEN'", LOAN_A), 1)
        run_step(self.conn, 3)  # ledger
        self.assertEqual(count(self.conn, "loans", "loan_id = ? AND status = 'CLEARED'", LOAN_A), 1)
        self.assertEqual(count(self.conn, "loans", "loan_id = ? AND status = 'OPEN'", LOAN_B), 1)
        self.assertEqual(count(self.conn, "touches", "touch_id = ? AND status = 'CANCELED'", TOUCH_REMIND_A), 1)
        self.assertEqual(count(self.conn, "touches", "touch_id = ? AND status = 'PENDING'", TOUCH_REMIND_B), 1)

    def test_events_are_joined_by_ids(self):
        run_all(self.conn)
        rows = self.conn.execute("SELECT event_type, borrower_id, loan_id, campaign_id, touch_id FROM events").fetchall()
        self.assertTrue(all(r["borrower_id"] == BORROWER_ID for r in rows))
        by_type = {}
        for r in rows:
            by_type.setdefault(r["event_type"], []).append(r)
        # Replies resolve to the WhatsApp touch and its campaign via the provider context ID.
        self.assertTrue(all(r["touch_id"] == "T-1042-0002" and r["campaign_id"] for r in by_type["borrower.message"]))
        self.assertEqual(by_type["provider.delivery"][0]["touch_id"], TOUCH_SMS)
        self.assertEqual(by_type["payment.claim_opened"][0]["loan_id"], LOAN_A)
        self.assertEqual(by_type["payment.verified"][0]["loan_id"], LOAN_A)
        self.assertEqual(by_type["touch.canceled"][0]["touch_id"], TOUCH_REMIND_A)

    def test_late_sent_callback_does_not_downgrade_delivered(self):
        run_step(self.conn, 0)
        ingest_event(self.conn, SMS.normalize({"provider": "sms-sandbox", "message_id": "sms-msg-7781",
                                               "status": "SENT", "status_at": "2026-11-18T10:00:30+05:30",
                                               "client_ref": TOUCH_SMS}))
        row = self.conn.execute("SELECT status, read_status FROM provider_deliveries").fetchone()
        self.assertEqual((row["status"], row["read_status"]), ("DELIVERED", "UNKNOWN"))

    def test_rejects_loan_from_another_borrower_and_unknown_touch(self):
        with self.assertRaises(IngestError):
            ingest_event(self.conn, {"event_type": "payment.verified", "source": "t", "provenance": "SIMULATED",
                                     "occurred_at": "2026-11-20T08:30:00+05:30", "dedupe_key": "x",
                                     "borrower_id": BORROWER_ID, "loan_id": "L-9999", "payload": {}})
        with self.assertRaises(IngestError):
            ingest_event(self.conn, SMS.normalize({"message_id": "m", "status": "DELIVRD",
                                                   "status_at": "2026-11-18T10:00:30+05:30", "client_ref": "T-nope"}))
        self.assertEqual(count(self.conn, "events", "dedupe_key = 'x'"), 0)

    def test_page_model_summary_and_next_action(self):
        view = borrower_view(self.conn, BORROWER_ID)
        self.assertEqual(view["next_action"]["action"], "SEND")
        for i in range(3):
            run_step(self.conn, i)
        view = borrower_view(self.conn, BORROWER_ID)
        self.assertEqual(view["next_action"]["action"], "WAIT")
        self.assertIn("awaiting ledger verification", view["next_action"]["reason"])
        run_step(self.conn, 3)
        view = borrower_view(self.conn, BORROWER_ID)
        line1, line2 = view["summary"]
        self.assertTrue(line1.startswith("Channel: ") and "T-1042-0001" in line1 and "read UNKNOWN" in line1)
        self.assertTrue(line2.startswith("Intent: ") and "ledger verified" in line2 and "evt-d7-whk-1" in line2)
        self.assertIn("supervisor WAIT", line2)
        self.assertEqual(view["position"], {"open": 1, "total": 2, "overdue": 5608})
        self.assertIn("Loan B", view["next_action"]["then"])
        times = [e["occurred_at"] for e in view["timeline"]]
        self.assertEqual(times, sorted(times))
        fixture_free = {e["category"] for e in view["timeline"] if not e["fixture_of"]}
        self.assertTrue({"DISPATCH", "PAYMENT_CLAIM", "EXECUTION"} <= fixture_free)

    def test_supervisor_reads_four_agent_outputs_and_cancels_in_scope(self):
        run_all(self.conn)
        wait = self.conn.execute("SELECT * FROM supervisor_decisions WHERE decision = 'WAIT'").fetchone()
        agents = [r["task_id"] for r in self.conn.execute(
            "SELECT task_id FROM agent_outputs WHERE event_id = ? ORDER BY task_id", (wait["inputs_event_id"],))]
        self.assertEqual(agents, ["K05", "K09", "K21", "K22"])
        self.assertTrue(wait["policy_version"] and wait["expires_at"])
        cancel = self.conn.execute("SELECT * FROM supervisor_decisions WHERE decision = 'CANCEL'").fetchone()
        self.assertEqual(cancel["scope_loan_id"], LOAN_A)
        executed = self.conn.execute(
            "SELECT touch_id, causation_event_id FROM events WHERE event_type = 'touch.canceled'").fetchall()
        self.assertEqual([(r["touch_id"], r["causation_event_id"]) for r in executed],
                         [(TOUCH_REMIND_A, cancel["event_id"])])


class SarvamCallTest(unittest.TestCase):
    """Sarvam IVR / voicebot outcomes through the same ingestion path (FIXTURE adapter)."""

    def setUp(self):
        self.conn = connect(":memory:")
        seed(self.conn)
        self.conn.execute(
            """INSERT INTO touches (touch_id, borrower_id, loan_id, campaign_id, channel, purpose, template_id,
                                    scheduled_for, status, updated_at)
               VALUES ('T-1042-0009', ?, ?, 'CMP-DPD31-60-NOV', 'IVR', 'NUDGE', 'ivr_emi_menu_hi_v1',
                       '2026-11-19T05:00:00+00:00', 'SENT', '2026-11-19T05:00:00+00:00')""", (BORROWER_ID, LOAN_B))
        self.adapter = FixtureSarvamCallAdapter()

    def call(self, **raw):
        base = {"call_id": "sv-1", "flow": "IVR", "status": "no-answer", "custom_ref": "T-1042-0009",
                "ended_at": "2026-11-19T10:31:00+05:30"}
        return ingest_event(self.conn, self.adapter.normalize({**base, **raw}))

    def test_ivr_no_answer_is_disposition_only_and_deduped(self):
        first = self.call()
        again = self.call()
        self.assertEqual((first.status, again.status), ("accepted", "duplicate"))
        self.assertEqual(count(self.conn, "call_outcomes"), 1)
        self.assertEqual(count(self.conn, "borrower_intents"), 0)

    def test_voicebot_emits_structured_intent_and_supervisor_decision(self):
        result = self.call(call_id="sv-2", flow="VOICEBOT", status="completed", duration_s=74,
                           intent={"intent": "NEEDS_TIME", "confidence": 0.8, "agreed_date": "2026-11-25T10:00:00+05:30",
                                   "reason": "Salary on the 25th"})
        types = [r.event_type for r in result.flatten()]
        self.assertEqual(types, ["call.outcome", "borrower.intent", "agent.outputs", "supervisor.decision"])
        d = self.conn.execute("SELECT decision, expires_at FROM supervisor_decisions").fetchone()
        self.assertEqual((d["decision"], d["expires_at"]), ("WAIT", "2026-11-25T04:30:00+00:00"))


if __name__ == "__main__":
    unittest.main()
