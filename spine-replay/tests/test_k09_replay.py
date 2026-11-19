"""K09 replay on the full K06 stack: determinism, matched safety and the item-5 edge cases."""
import os
import tempfile
import unittest
from datetime import datetime, timedelta

from replay.k09 import CAMPAIGN_ID, START, StackRun, run_comparison
from replay.portfolio import generate_portfolio

from kairos_backend.outcomes import DUPLICATE, LATE_REPLY, REPLY_ATTRIBUTED


def _run(arm="kairos", n=120, days=2, seed=7, budget=5.0):
    tmp = tempfile.mkdtemp()
    run = StackRun(arm, generate_portfolio(n, seed), seed, days, os.path.join(tmp, f"{arm}.db"), budget, None)
    return run


def _sent(run):
    rows = run.ledger._conn().execute(
        "SELECT instruction_id, borrower_id, scheduled_at FROM touches WHERE status IN ('SENT','DELIVERED')"
        " ORDER BY scheduled_at, instruction_id").fetchall()
    return [dict(r) for r in rows]


class DeterminismAndSafety(unittest.TestCase):
    def test_same_seed_same_everything(self):
        a = run_comparison(80, 3, 7, drift_day=None, budget_per_case_inr=5.0, workdir=tempfile.mkdtemp())
        b = run_comparison(80, 3, 7, drift_day=None, budget_per_case_inr=5.0, workdir=tempfile.mkdtemp())
        for arm in ("baseline", "kairos"):
            for key in ("envelope_hash", "meaningful_replies", "spend_inr", "verified_payments", "daily", "decisions"):
                self.assertEqual(a[arm][key], b[arm][key], f"{arm}.{key}")

    def test_matched_budget_caps_and_zero_breaches(self):
        r = run_comparison(150, 4, 3, drift_day=None, workdir=tempfile.mkdtemp())
        self.assertEqual(r["baseline"]["borrowers"], r["kairos"]["borrowers"])
        for arm in ("baseline", "kairos"):
            self.assertEqual(r[arm]["cap_breaches_k01"], 0)
            self.assertEqual(r[arm]["k15_safety_violations"], {})
            self.assertLessEqual(r[arm]["spend_inr"], r["matched"]["budget_pool_inr"] + 1e-6)
            self.assertEqual(r[arm]["outcomes"]["pending"], 0)
        self.assertIn("matched to fixed-cadence spend", r["matched"]["budget_basis"])


class EdgeCases(unittest.TestCase):
    def setUp(self):
        self.run = _run()
        self.run.run_day(0)
        self.sent = _sent(self.run)
        self.assertTrue(self.sent, "day 0 should send something")

    def test_duplicate_callbacks_count_once(self):
        t = self.sent[0]
        at = datetime.fromisoformat(t["scheduled_at"]).replace(tzinfo=None)
        fin = self.run.finalizer
        fin.record_reach(t["instruction_id"], True, at + timedelta(minutes=1))
        self.assertFalse(fin.record_reach(t["instruction_id"], True, at + timedelta(minutes=2)))
        first = fin.record_reply(t["borrower_id"], at + timedelta(hours=1), meaningful=True)
        again = fin.record_reply(t["borrower_id"], at + timedelta(hours=1), meaningful=True)
        self.assertIn(first, (REPLY_ATTRIBUTED, DUPLICATE))  # the simulator may already have replied
        self.assertEqual(again, DUPLICATE)
        fin.finalize_due(at + timedelta(days=2))
        n = self.run.ranker.n_updates
        fin.finalize_due(at + timedelta(days=3))           # re-finalizing never double counts
        self.assertEqual(self.run.ranker.n_updates, n)

    def test_late_reply_does_not_change_reward(self):
        t = self.sent[0]
        at = datetime.fromisoformat(t["scheduled_at"]).replace(tzinfo=None)
        fin = self.run.finalizer
        fin.finalize_due(at + timedelta(hours=25))
        before = (fin.metrics(CAMPAIGN_ID)["replies"], self.run.ranker.n_updates)
        code = fin.record_reply(t["borrower_id"], at + timedelta(hours=30), meaningful=True)
        self.assertEqual(code, LATE_REPLY)
        self.assertEqual((fin.metrics(CAMPAIGN_ID)["replies"], self.run.ranker.n_updates), before)
        self.assertGreaterEqual(fin.metrics(CAMPAIGN_ID)["late_replies"], 1)

    def test_stop_cancels_everything_next_day(self):
        bid = self.sent[0]["borrower_id"]
        self.run.ledger.record_stop(bid, datetime.combine(START, datetime.min.time()) + timedelta(hours=20), "sms")
        b = next(x for x in self.run.portfolio if x.borrower_id == bid)
        d = self._next_day()
        res = self.run.fsm.plan(bid, f"{bid}-EMI", CAMPAIGN_ID, "B1", self.run.candidates(b, d),
                                datetime.combine(d, datetime.min.time()) + timedelta(hours=8))
        self.assertEqual(res.decision, "CANCEL")
        self.assertEqual(res.rule, "STOP_OPT_OUT")

    def _next_day(self):
        """What run_day does first: new DPD feed (else K01 holds stale DPD) and day roll-over."""
        d = START + timedelta(days=1)
        self.run._refresh_dpd(1)
        self.run.ledger.start_day(d)
        return d

    def test_stale_dpd_holds_outreach(self):
        b = next(x for x in self.run.portfolio if x.consents and not x.opted_out)
        d = START + timedelta(days=1)  # no new DPD feed loaded → DPD is 25 h old
        res = self.run.fsm.plan(b.borrower_id, f"{b.borrower_id}-EMI", CAMPAIGN_ID, "B1", self.run.candidates(b, d),
                                datetime.combine(d, datetime.min.time()) + timedelta(hours=8))
        self.assertEqual((res.decision, res.rule), ("WAIT", "DPD_VALID"))

    def test_payment_before_dispatch_blocks_the_send(self):
        d = self._next_day()
        plan_at = datetime.combine(d, datetime.min.time()) + timedelta(hours=8)
        for b in self.run.portfolio:  # first borrower that gets a SEND tomorrow
            if b.opted_out or b.paid or not b.consents:
                continue
            res = self.run.fsm.plan(b.borrower_id, f"{b.borrower_id}-EMI", CAMPAIGN_ID, "B1",
                                    self.run.candidates(b, d), plan_at)
            if res.decision == "SEND":
                break
        else:
            self.fail("no SEND planned for day 1")
        self.run.ledger.record_payment_verified(b.borrower_id, f"{b.borrower_id}-EMI", 5000.0,
                                                plan_at + timedelta(minutes=30))
        out = self.run.fsm.dispatch(res.instruction_id, datetime.fromisoformat(res.send_at).replace(tzinfo=None))
        self.assertFalse(out.sent)
        self.assertEqual(out.decision, "CANCEL")
        self.assertNotIn(res.instruction_id, self.run.providers[res.channel].sent)


if __name__ == "__main__":
    unittest.main()
