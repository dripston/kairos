"""Tests for K03. Each test gets a fresh in-memory database, so tests never affect each other.

Run:  python3 -m unittest -v
"""
import sqlite3
import unittest

from spine.db import get_connection, integrity_problems
from spine.ledger import (PaymentError, apply_payment, is_contactable, loan_summary,
                          record_event)
from spine.seed import BORROWER_ID, LOAN_A, LOAN_B, build_spine


class SpineTests(unittest.TestCase):
    def setUp(self):
        self.conn = get_connection(":memory:")
        build_spine(self.conn)

    def tearDown(self):
        self.conn.close()

    def count(self, table):
        return self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    # ---- "Done when" criteria ----

    def test_no_orphan_records(self):
        self.assertEqual(integrity_problems(self.conn), [])

    def test_every_event_joins_to_right_obligation(self):
        rows = self.conn.execute(
            """SELECT e.event_id, e.borrower_id AS ev_borrower, l.borrower_id AS loan_borrower,
                      e.loan_id AS ev_loan, o.loan_id AS ob_loan
               FROM events e
               JOIN obligations o ON o.obligation_id = e.obligation_id
               JOIN loans l ON l.loan_id = o.loan_id
               WHERE e.event_type != 'consent_changed'"""
        ).fetchall()
        self.assertEqual(len(rows), self.count("events"))  # every loan event found its obligation
        for row in rows:
            self.assertEqual(row["ev_loan"], row["ob_loan"], row["event_id"])
            self.assertEqual(row["ev_borrower"], row["loan_borrower"], row["event_id"])

    def test_loan_a_closed_loan_b_open(self):
        self.assertEqual(loan_summary(self.conn, LOAN_A)["status"], "closed")
        self.assertEqual(loan_summary(self.conn, LOAN_B)["status"], "open")

    # ---- Payments ----

    def test_payment_on_loan_b_does_not_touch_loan_a(self):
        before = loan_summary(self.conn, LOAN_A)
        apply_payment(self.conn, "PAY-T1", LOAN_B, "OB-B-1", 300_000, "2026-11-19", "upi", "UPI-T1")
        after = loan_summary(self.conn, LOAN_A)
        self.assertEqual(before["obligations"], after["obligations"])

    def test_partial_payment_keeps_emi_open(self):
        apply_payment(self.conn, "PAY-T2", LOAN_B, "OB-B-1", 100_000, "2026-11-19", "upi", "UPI-T2")
        emi = loan_summary(self.conn, LOAN_B)["obligations"][0]
        self.assertEqual(emi["status"], "partially_paid")
        self.assertEqual(emi["outstanding_paise"], 200_000)
        self.assertEqual(loan_summary(self.conn, LOAN_B)["status"], "open")

    def test_paying_all_of_loan_b_closes_it(self):
        apply_payment(self.conn, "PAY-T3", LOAN_B, "OB-B-1", 300_000, "2026-11-19", "upi", "UPI-T3")
        apply_payment(self.conn, "PAY-T4", LOAN_B, "OB-B-2", 300_000, "2026-11-19", "upi", "UPI-T4")
        self.assertEqual(loan_summary(self.conn, LOAN_B)["status"], "closed")

    def test_emi_from_wrong_loan_rejected(self):
        with self.assertRaises(PaymentError):
            apply_payment(self.conn, "PAY-T5", LOAN_A, "OB-B-1", 100_000, "2026-11-19", "upi", "UPI-T5")
        self.assertEqual(self.count("payments"), 3)

    def test_overpayment_rejected(self):
        with self.assertRaises(PaymentError):
            apply_payment(self.conn, "PAY-T6", LOAN_B, "OB-B-1", 300_001, "2026-11-19", "upi", "UPI-T6")

    def test_zero_or_negative_payment_rejected(self):
        for amount in (0, -500):
            with self.assertRaises(PaymentError):
                apply_payment(self.conn, "PAY-T7", LOAN_B, "OB-B-1", amount, "2026-11-19", "upi", "UPI-T7")

    def test_same_payment_twice_counted_once(self):
        self.assertTrue(apply_payment(self.conn, "PAY-T8", LOAN_B, "OB-B-1", 100_000, "2026-11-19", "upi", "UPI-T8"))
        self.assertFalse(apply_payment(self.conn, "PAY-T8", LOAN_B, "OB-B-1", 100_000, "2026-11-19", "upi", "UPI-T8"))
        self.assertEqual(loan_summary(self.conn, LOAN_B)["obligations"][0]["amount_paid_paise"], 100_000)

    def test_reused_bank_reference_rejected(self):
        with self.assertRaises(PaymentError):
            apply_payment(self.conn, "PAY-T9", LOAN_B, "OB-B-1", 100_000, "2026-11-19", "upi", "UPI-TEST-0001")

    # ---- DPD ----

    def test_loan_b_dpd_and_band(self):
        loan = loan_summary(self.conn, LOAN_B)
        self.assertEqual(loan["dpd"], 34)
        self.assertEqual(loan["dpd_band"], "B2")  # K01: B2 is 31-60

    def test_paid_loan_has_zero_dpd(self):
        loan = loan_summary(self.conn, LOAN_A)
        self.assertEqual((loan["dpd"], loan["dpd_band"]), (0, "CLEARED"))

    # ---- Consent ----

    def test_consent(self):
        self.assertTrue(is_contactable(self.conn, BORROWER_ID, "whatsapp"))
        self.assertTrue(is_contactable(self.conn, BORROWER_ID, "sms"))
        self.assertFalse(is_contactable(self.conn, BORROWER_ID, "email"))
        self.assertFalse(is_contactable(self.conn, "BRW-UNKNOWN", "sms"))

    def test_opt_out_blocks_contact(self):
        self.conn.execute("UPDATE consents SET opted_out = 1 WHERE borrower_id = ?", (BORROWER_ID,))
        self.assertFalse(is_contactable(self.conn, BORROWER_ID, "whatsapp"))

    # ---- Database guards ----

    def test_event_without_obligation_rejected(self):
        with self.assertRaises(sqlite3.IntegrityError):
            record_event(self.conn, "EV-BAD-1", BORROWER_ID, "touch_sent", "2026-11-19", channel="sms",
                         loan_id=LOAN_B)

    def test_event_with_mismatched_loan_rejected(self):
        with self.assertRaises(sqlite3.IntegrityError):
            record_event(self.conn, "EV-BAD-2", BORROWER_ID, "touch_sent", "2026-11-19", channel="sms",
                         loan_id=LOAN_A, obligation_id="OB-B-1")

    def test_running_seed_twice_creates_no_duplicates(self):
        tables = ("borrowers", "consents", "loans", "obligations", "payments", "events")
        before = {t: self.count(t) for t in tables}
        build_spine(self.conn)
        self.assertEqual(before, {t: self.count(t) for t in tables})


if __name__ == "__main__":
    unittest.main()
