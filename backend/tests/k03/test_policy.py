"""Checks that K03 follows Ojas's K01 policy (policy/policy.json) and covers edge cases E01 and E15.

Run:  python3 -m unittest -v
"""
import os
import tempfile
import unittest
from datetime import datetime

from spine.config import DATASET_AS_OF_DATE
from spine.db import get_connection
from spine.ledger import apply_payment, loan_summary, reverse_payment
from spine.policy import channels, dpd_band, dpd_status, load_policy, policy_version
from spine import importer
from tests.k03.test_dataset import GOOD_ROWS, HEADER


class K01PolicyTests(unittest.TestCase):
    def test_reads_ojas_policy_file(self):
        self.assertEqual(load_policy()["policy_id"], "kairos-outreach-policy")
        self.assertEqual(policy_version(), "1.1.1")  # K01 v1.1.1 (Ojas pack e4d4d2a)

    def test_e01_band_boundaries_match_k01(self):
        # Same boundaries as K01 test cases T02-T07, T31, T32.
        expected = {-7: "NOT_IN_SCOPE", -6: "PRE_DUE", 0: "PRE_DUE", 1: "B1", 30: "B1", 31: "B2",
                    60: "B2", 61: "B3", 90: "B3", 91: "B4", 400: "B4"}
        for dpd, band in expected.items():
            self.assertEqual(dpd_band(dpd), band, f"DPD {dpd}")

    def test_e01_missing_dpd_is_invalid(self):
        self.assertEqual(dpd_band(None), "DPD_INVALID")
        self.assertEqual(dpd_status(None, "2026-08-07T00:00:00", datetime(2026, 8, 7, 10)), "DPD_MISSING")

    def test_e01_stale_dpd_after_24_hours(self):
        as_of = "2026-08-07T00:00:00"
        self.assertEqual(dpd_status(4, as_of, datetime(2026, 8, 7, 23)), "VALID")
        self.assertEqual(dpd_status(4, as_of, datetime(2026, 8, 8, 1)), "DPD_STALE")

    def test_channel_names_match_k01(self):
        self.assertEqual(set(channels()), {"sms", "whatsapp", "email", "ivr", "voicebot"})


class DatasetPolicyTests(unittest.TestCase):
    def load(self, rows):
        f = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False)
        f.write("\n".join([HEADER] + rows))
        f.close()
        self.addCleanup(os.unlink, f.name)
        conn = get_connection(":memory:")
        self.addCleanup(conn.close)
        return conn, importer.import_dataset(conn, f.name)

    def test_e01_missing_dpd_row_is_loaded_and_held(self):
        row = GOOD_ROWS[0].replace(",9440,1,2026-08-06,", ",9440,,2026-08-06,")
        conn, report = self.load([row])
        self.assertEqual((report["loaded"], report["rejected"]), (1, []))
        self.assertEqual(report["dpd_missing_held"], ["PL100001"])
        self.assertEqual(loan_summary(conn, "PL100001", DATASET_AS_OF_DATE)["dpd_band"], "DPD_INVALID")

    def test_bucket_that_disagrees_with_k01_is_flagged(self):
        # DPD 31 is B2 in K01, so a '0-30' bucket is wrong for it.
        row = GOOD_ROWS[2].replace(",30,2026-07-08,0-30,", ",31,2026-07-07,0-30,")
        _, report = self.load([row])
        self.assertEqual([b["loan_id"] for b in report["bucket_vs_policy_band"]], ["PL100031"])

    def test_dpd_as_of_saved_for_stale_check(self):
        conn, _ = self.load(GOOD_ROWS[:1])
        self.assertEqual(conn.execute("SELECT dpd_as_of FROM loans").fetchone()[0], "2026-08-07T00:00:00")


class K03PolicyAndE15Tests(unittest.TestCase):
    def setUp(self):
        from spine.seed import build_spine_from_dataset
        f = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False)
        f.write("\n".join([HEADER] + GOOD_ROWS))
        f.close()
        self.addCleanup(os.unlink, f.name)
        self.conn = get_connection(":memory:")
        self.addCleanup(self.conn.close)
        build_spine_from_dataset(self.conn, f.name)

    def s(self, loan_id):
        return loan_summary(self.conn, loan_id, DATASET_AS_OF_DATE)

    def test_k03_loans_use_k01_bands(self):
        self.assertEqual(self.s("PL100033")["dpd_band"], "CLEARED")   # overdue paid -> cancel its touches
        self.assertEqual(self.s("PL100034")["dpd_band"], "B1")        # DPD 4

    def test_every_event_cites_policy_version(self):
        versions = {r[0] for r in self.conn.execute("SELECT policy_version FROM events")}
        self.assertEqual(versions, {policy_version()})

    def test_e15_reversal_reopens_only_that_emi(self):
        self.assertTrue(reverse_payment(self.conn, "PAY-K03-0001", "2026-08-07", "UPI reversed by bank"))
        a, b = self.s("PL100033"), self.s("PL100034")
        self.assertEqual((a["outstanding_paise"], a["dpd"], a["dpd_band"]), (1_886_200, 15, "B1"))
        self.assertEqual(b["outstanding_paise"], 936_200)            # second loan untouched
        self.assertFalse(reverse_payment(self.conn, "PAY-K03-0001", "2026-08-07", "again"))  # no double reversal
        self.assertEqual(self.s("PL100033")["outstanding_paise"], 1_886_200)
        event = self.conn.execute("SELECT loan_id, obligation_id FROM events WHERE event_type = 'payment_reversed'").fetchone()
        self.assertEqual(tuple(event), ("PL100033", "PL100033-OD1"))

    def test_e15_partial_payment_does_not_clear(self):
        apply_payment(self.conn, "PAY-P1", "PL100034", "PL100034-OD1", 500_000, "2026-08-07", "upi", "UPI-P1")
        b = self.s("PL100034")
        self.assertEqual((b["outstanding_paise"], b["overdue"], b["dpd_band"]), (436_200, True, "B1"))


if __name__ == "__main__":
    unittest.main()
