"""K09 replay on the full K06 stack over the K03 spine: determinism, matched safety/budget, the
item-5 edge cases, and the CN10 cohort-cadence baseline."""
import os
import tempfile
import unittest
from datetime import datetime, timedelta

from kairos_backend.outcomes import DUPLICATE, LATE_REPLY, REPLY_ATTRIBUTED
from replay import cn10
from replay.k09 import CAMPAIGN_ID, START, StackRun, run_comparison, write_replay_dataset


def _run(arm="kairos", n=120, days=2, seed=7, budget=500):
    tmp = tempfile.mkdtemp()
    csv = write_replay_dataset(os.path.join(tmp, "data.csv"), n, seed, START)
    return StackRun(arm, csv, seed, days, os.path.join(tmp, f"{arm}.db"), budget, None)


def _sent(run):
    rows = run.ledger._conn().execute(
        "SELECT instruction_id, borrower_id, scheduled_at FROM touches WHERE status IN ('SENT','DELIVERED')"
        " ORDER BY scheduled_at, instruction_id").fetchall()
    return [dict(r) for r in rows]


def _at8(d):
    return datetime.combine(d, datetime.min.time()) + timedelta(hours=8)


class DeterminismAndSafety(unittest.TestCase):
    def test_same_seed_same_everything(self):
        kw = dict(drift_day=None, budget_per_case_paise=500, baselines=("cn10", "k15"))
        a = run_comparison(80, 3, 7, workdir=tempfile.mkdtemp(), **kw)
        b = run_comparison(80, 3, 7, workdir=tempfile.mkdtemp(), **kw)
        for arm in ("cn10", "k15"):
            self.assertEqual(a["baselines"][arm]["envelope_hash"], b["baselines"][arm]["envelope_hash"], arm)
        for arm in ("baseline", "kairos"):
            for key in ("envelope_hash", "meaningful_replies", "spend_paise", "verified_payments", "daily", "decisions"):
                self.assertEqual(a[arm][key], b[arm][key], f"{arm}.{key}")

    def test_matched_budget_caps_and_zero_breaches(self):
        r = run_comparison(150, 4, 3, drift_day=None, workdir=tempfile.mkdtemp())
        self.assertEqual(r["baseline_arm"], "cn10")
        self.assertEqual(r["baseline"]["borrowers"], r["kairos"]["borrowers"])
        pool = r["matched"]["budget_pool_paise"]
        self.assertIsInstance(pool, int)
        self.assertGreaterEqual(pool, r["matched"]["calibration"]["spend_paise"])
        for arm in ("baseline", "kairos"):
            self.assertEqual(r[arm]["cap_breaches_k01"], 0)
            self.assertEqual(r[arm]["k15_safety_violations"], {})
            self.assertLessEqual(r[arm]["spend_paise"], pool)
            self.assertEqual(r[arm]["outcomes"]["pending"], 0)
        self.assertIn("matched to cn10 fixed-cadence spend", r["matched"]["budget_basis"])
        self.assertIn("SIMULATED", r["provenance"])
        self.assertIn("K02", r["baseline_note"])

    def test_spine_holds_the_replay(self):
        """Borrowers, loans and EMIs come from the K03 importer; sends and replies are written back."""
        run = _run(n=60, days=1)
        run.run_day(0)
        c = run.ledger._conn()
        self.assertEqual(c.execute("SELECT COUNT(*) FROM loans").fetchone()[0], 60)
        self.assertEqual(c.execute("SELECT COUNT(*) FROM enrollments").fetchone()[0], 60)
        sent = c.execute("SELECT COUNT(*) FROM touches WHERE status IN ('SENT','DELIVERED')").fetchone()[0]
        self.assertEqual(c.execute("SELECT COUNT(*) FROM events WHERE event_type='touch_sent'").fetchone()[0], sent)
        # live band from the spine == the band the outcome was registered under
        row = c.execute("SELECT o.band, t.loan_id FROM touch_outcomes o JOIN touches t USING (instruction_id)"
                        " LIMIT 1").fetchone()
        loan = next(x for x in run.loans if x.loan_id == row["loan_id"])
        from spine.policy import dpd_band
        self.assertEqual(row["band"], dpd_band(loan.file_dpd))


class EdgeCases(unittest.TestCase):
    def setUp(self):
        self.run = _run()
        self.run.run_day(0)
        self.sent = _sent(self.run)
        self.assertTrue(self.sent, "day 0 should send something")

    def _ob(self, bid):
        b = next(x for x in self.run.portfolio if x.borrower_id == bid)
        return b, self.run.loans[b.index].obligation_id

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
        self.run.ledger.record_stop(bid, _at8(START) + timedelta(hours=12), "sms")
        consents = self.run.ledger.borrower_state(bid)["consents"]
        self.assertTrue(all(c["opted_out"] for c in consents))    # STOP lands in the spine's consents
        b, ob = self._ob(bid)
        d = self._next_day()
        res = self.run.fsm.plan(bid, ob, CAMPAIGN_ID, None, self.run.candidates(b, d), _at8(d))
        self.assertEqual((res.decision, res.rule), ("CANCEL", "STOP_OPT_OUT"))

    def _next_day(self):
        """What run_day does first: the daily LMS refresh (else K01 holds stale DPD)."""
        d = START + timedelta(days=1)
        self.run.ledger.start_day(d)
        return d

    def test_stale_dpd_holds_outreach(self):
        b = next(x for x in self.run.portfolio if x.consents and not x.opted_out and not x.paid)
        d = START + timedelta(days=1)  # no LMS refresh today → spine DPD is 32 h old
        res = self.run.fsm.plan(b.borrower_id, self.run.loans[b.index].obligation_id, CAMPAIGN_ID, None,
                                self.run.candidates(b, d), _at8(d))
        self.assertEqual((res.decision, res.rule), ("WAIT", "DPD_VALID"))

    def test_payment_before_dispatch_blocks_the_send(self):
        d = self._next_day()
        for b in self.run.portfolio:  # first borrower that gets a SEND tomorrow
            if b.opted_out or b.paid or not b.consents:
                continue
            ob = self.run.loans[b.index].obligation_id
            res = self.run.fsm.plan(b.borrower_id, ob, CAMPAIGN_ID, None, self.run.candidates(b, d), _at8(d))
            if res.decision == "SEND":
                break
        else:
            self.fail("no SEND planned for day 1")
        loan = self.run.loans[b.index]
        paid = self.run.ledger.record_payment_verified(ob, loan.emi_paise, _at8(d) + timedelta(minutes=30),
                                                       payment_id="PAY-TEST", reference="UTR-TEST")
        self.assertTrue(paid["applied"] and paid["obligation_cleared"])
        out = self.run.fsm.dispatch(res.instruction_id, datetime.fromisoformat(res.send_at).replace(tzinfo=None))
        self.assertFalse(out.sent)
        self.assertEqual((out.decision, out.rule), ("CANCEL", "OBLIGATION_CLEARED"))
        self.assertNotIn(res.instruction_id, self.run.providers[res.channel].sent)


class CN10Cadence(unittest.TestCase):
    def test_cohort_rules(self):
        self.assertEqual(cn10.cohort_of("C3 - Medium Risk", "0,0,0,0,0,1", 100, None), "C3")   # tag wins
        self.assertEqual(cn10.cohort_of("C2 - Low Risk", "1,1,1,1,1,1", 100, None), "C2")
        self.assertEqual(cn10.cohort_of("", "NA,NA,NA,NA,NA,1", 100, None), "C1")              # first bounce
        self.assertEqual(cn10.cohort_of("", "0,0,0,0,0,1", 100, None), "C1")
        self.assertEqual(cn10.cohort_of("", "0,0,0,0,1,1", 100, None), "C3")    # 2/6 = 33 %
        self.assertEqual(cn10.cohort_of("", "1,0,0,0,1,1", 100, None), "C4")    # 3/6 = 50 % > 42 %
        self.assertEqual(cn10.cohort_of("", "0,0,1" + ",0" * 11 + ",1", 100, None), "C2")  # 2/15 = 13.3 % (long history)
        self.assertEqual(cn10.cohort_of("", "0,0,0,0,0,0,0,1,1,1", 100, None), "C4")    # 3 consecutive (30 %)
        self.assertEqual(cn10.cohort_of("C2 - Low Risk", "0,0,0,0,0,1", 900, 500), "C5")       # high ticket
        self.assertEqual(cn10.high_ticket_threshold(list(range(1, 101))), 91)
        self.assertEqual(cn10.high_ticket_threshold([1, 2], setting=7), 7)

    def test_calendar_cycle_and_templates(self):
        self.assertEqual(cn10.cycle_day(1), 1)
        self.assertEqual(cn10.cycle_day(10), 10)
        self.assertEqual(cn10.cycle_day(11), 1)                 # repeat from day 1
        self.assertIsNone(cn10.cycle_day(11, repeat=False))     # or stop
        self.assertIsNone(cn10.cycle_day(0))                    # Day 0 = bounce detection, no send
        self.assertEqual(cn10.steps_for("C1", 3, "en"), [("sms", 1, "CN10_SMS_T1_EN")])
        self.assertEqual([s[0] for s in cn10.steps_for("C4", 10, "hi")], ["whatsapp", "sms", "ivr"])
        self.assertEqual(cn10.steps_for("C5", 8, "en", represent_eligible=True),
                         [("whatsapp", 3, "CN10_WA_T3_NACH_EN")])
        self.assertEqual(cn10.steps_for("C4", 8, "en"), [("whatsapp", 3, "CN10_WA_T3_EN")])
        self.assertEqual(cn10.steps_for("C2", 2, "en"), [])

    def test_cn10_arm_runs_through_the_gate_and_caps(self):
        run = _run(arm="cn10", n=120, days=2)
        run.run()
        r = run.report()
        self.assertEqual(r["cap_breaches_k01"], 0)
        self.assertEqual(r["k15_safety_violations"], {})
        self.assertGreater(r["contacts_sent"], 0)
        self.assertTrue(r["cn10"]["cohorts"])
        # same-day multi-channel steps are planned one by one; the 1/day cap blocks the extras
        sent_per_day = run.ledger._conn().execute(
            "SELECT MAX(n) FROM (SELECT COUNT(*) n FROM touches WHERE status IN ('SENT','DELIVERED')"
            " GROUP BY borrower_id, day)").fetchone()[0]
        self.assertEqual(sent_per_day, 1)
        tpls = {t for (t,) in run.ledger._conn().execute("SELECT DISTINCT template_id FROM touches")}
        self.assertTrue(tpls and all(t.startswith("CN10_") for t in tpls))


if __name__ == "__main__":
    unittest.main()


class KairosOptionsReplay(unittest.TestCase):
    OPTS = {"decay": 0.7, "payment_lambda": 0.1, "value_floor_rel": 0.5, "recency": "gap", "affinity_strength": 10,
            "stop_penalty": 20, "stop_max": 0.05}

    def test_options_are_deterministic_safe_and_recorded(self):
        kw = dict(drift_day=1, budget_per_case_paise=None, kairos_opts=self.OPTS)
        a = run_comparison(120, 5, 11, workdir=tempfile.mkdtemp(), **kw)
        b = run_comparison(120, 5, 11, workdir=tempfile.mkdtemp(), **kw)
        for key in ("envelope_hash", "meaningful_replies", "spend_paise", "verified_payments", "daily", "decisions"):
            self.assertEqual(a["kairos"][key], b["kairos"][key], key)
        self.assertEqual({k: a["matched"]["kairos_options"][k] for k in self.OPTS}, self.OPTS)
        self.assertEqual(a["kairos"]["cap_breaches_k01"], 0)
        self.assertEqual(a["kairos"]["k15_safety_violations"], {})
        self.assertLessEqual(a["kairos"]["spend_paise"], a["matched"]["budget_pool_paise"])
        # the baseline arm never sees Kairos options
        plain = run_comparison(120, 5, 11, workdir=tempfile.mkdtemp(), drift_day=1)
        self.assertEqual(plain["baseline"]["envelope_hash"], a["baseline"]["envelope_hash"])
        self.assertEqual(plain["matched"]["budget_pool_paise"], a["matched"]["budget_pool_paise"])

    def test_replay_default_and_legacy_options(self):
        from replay.k09 import LEGACY_KAIROS_OPTIONS, KairosOptions
        self.assertEqual((KairosOptions().decay, KairosOptions().value_floor_rel, KairosOptions().stop_penalty),
                         (0.7, 0.5, 25.0))
        legacy = KairosOptions.from_dict(LEGACY_KAIROS_OPTIONS)
        self.assertIsNone(legacy.decay)
        self.assertIsNone(legacy.value_floor_rel)
        self.assertEqual(legacy.changed(), {"decay": None, "value_floor_rel": None, "stop_penalty": 0.0})
