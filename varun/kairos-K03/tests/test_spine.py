"""K03 tests on the dataset borrower: CUST-K03-0001 = loans PL100033 + PL100034 of
PL_Dummy_Collections_Data_500 (tracker 'Datasets' sheet). Each test gets a fresh in-memory database.

The two rows are embedded in tests/test_dataset.py (phone numbers replaced) so the tests run
without the CSV in Git; test_embedded_rows_equal_the_real_file proves they are exact copies.

Run:  python3 -m unittest -v
"""
import csv
import os
import sqlite3
import tempfile
import unittest

from spine.config import DATASET_AS_OF_DATE
from spine.dataset_file import find_dataset
from spine.db import get_connection, integrity_problems
from spine.k01_gate import k01_decision
from spine.ledger import PaymentError, apply_payment, is_contactable, loan_summary, record_event
from spine.seed import DATASET_CUSTOMER_ID as CUSTOMER, DATASET_LOAN_A as LOAN_A, DATASET_LOAN_B as LOAN_B
from spine.seed import build_spine_from_dataset
from tests.test_dataset import GOOD_ROWS, HEADER

OB_A, OB_B = f"{LOAN_A}-OD1", f"{LOAN_B}-OD1"
EMI_A, EMI_B = 1_886_200, 936_200            # Rs.18,862 and Rs.9,362 from the file, in paise
DAY = DATASET_AS_OF_DATE.isoformat()


class SpineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False)
        self.tmp.write("\n".join([HEADER] + GOOD_ROWS))
        self.tmp.close()
        self.conn = get_connection(":memory:")
        build_spine_from_dataset(self.conn, self.tmp.name)

    def tearDown(self):
        self.conn.close()
        os.unlink(self.tmp.name)

    def count(self, table):
        return self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def s(self, loan_id):
        return loan_summary(self.conn, loan_id)

    # ---- Requirement: one synthetic borrower with two loans, consent, obligations, payment records ----

    def test_one_borrower_two_loans_with_every_record_type(self):
        self.assertEqual(self.count("borrowers"), 1)
        loans = [r[0] for r in self.conn.execute("SELECT loan_id FROM loans WHERE borrower_id = ? ORDER BY loan_id", (CUSTOMER,))]
        self.assertEqual(loans, [LOAN_A, LOAN_B])
        for table in ("consents", "obligations", "payments", "events"):
            self.assertGreater(self.count(table), 0, table)

    # ---- Done when: each event joins to the right obligation ----

    def test_no_orphan_records(self):
        self.assertEqual(integrity_problems(self.conn), [])

    def test_every_event_joins_to_right_obligation(self):
        rows = self.conn.execute(
            """SELECT e.event_id, e.borrower_id AS ev_borrower, l.borrower_id AS loan_borrower,
                      e.loan_id AS ev_loan, o.loan_id AS ob_loan
               FROM events e
               JOIN obligations o ON o.obligation_id = e.obligation_id
               JOIN loans l ON l.loan_id = o.loan_id""").fetchall()
        self.assertEqual(len(rows), self.count("events"))
        for row in rows:
            self.assertEqual(row["ev_loan"], row["ob_loan"], row["event_id"])
            self.assertEqual(row["ev_borrower"], row["loan_borrower"], row["event_id"])

    def test_event_without_obligation_rejected(self):
        with self.assertRaises(sqlite3.IntegrityError):
            record_event(self.conn, "EV-BAD-1", CUSTOMER, "touch_sent", DAY, channel="sms", loan_id=LOAN_B)

    def test_event_pointing_at_other_loans_emi_rejected(self):
        with self.assertRaises(sqlite3.IntegrityError):
            record_event(self.conn, "EV-BAD-2", CUSTOMER, "touch_sent", DAY, channel="sms", loan_id=LOAN_A, obligation_id=OB_B)

    # ---- Done when: clearing one loan leaves the other open ----

    def test_clearing_loan_a_leaves_loan_b_open(self):
        a, b = self.s(LOAN_A), self.s(LOAN_B)
        self.assertEqual((a["outstanding_paise"], a["overdue"], a["dpd_band"]), (0, False, "CLEARED"))
        self.assertEqual((b["outstanding_paise"], b["overdue"], b["status"], b["dpd"]), (EMI_B, True, "open", 4))

    def test_payment_on_loan_b_does_not_touch_loan_a(self):
        before = self.s(LOAN_A)["obligations"]
        apply_payment(self.conn, "PAY-T1", LOAN_B, OB_B, EMI_B, DAY, "upi", "UPI-T1")
        self.assertEqual(before, self.s(LOAN_A)["obligations"])
        self.assertEqual(self.s(LOAN_B)["dpd_band"], "CLEARED")

    # ---- K01 (dependency): the cleared loan's touch is cancelled, the open loan can be contacted ----

    def test_k01_cancels_cleared_loan_and_allows_open_loan(self):
        self.assertEqual(k01_decision(self.conn, LOAN_A, "sms"), ("CANCEL", "OBLIGATION_CLEARED"))
        self.assertEqual(k01_decision(self.conn, LOAN_B, "sms"), ("SEND", "ELIGIBLE"))
        self.assertEqual(k01_decision(self.conn, LOAN_B, "email"), ("WAIT", "NO_CONSENT"))

    # ---- Payment safety ----

    def test_partial_payment_keeps_emi_open(self):
        apply_payment(self.conn, "PAY-T2", LOAN_B, OB_B, 100_000, DAY, "upi", "UPI-T2")
        emi = self.s(LOAN_B)["obligations"][0]
        self.assertEqual((emi["status"], emi["outstanding_paise"]), ("partially_paid", EMI_B - 100_000))
        self.assertEqual(self.s(LOAN_B)["status"], "open")

    def test_emi_from_wrong_loan_rejected(self):
        with self.assertRaises(PaymentError):
            apply_payment(self.conn, "PAY-T5", LOAN_A, OB_B, 100_000, DAY, "upi", "UPI-T5")
        self.assertEqual(self.count("payments"), 1)

    def test_overpayment_rejected(self):
        with self.assertRaises(PaymentError):
            apply_payment(self.conn, "PAY-T6", LOAN_B, OB_B, EMI_B + 1, DAY, "upi", "UPI-T6")

    def test_zero_or_negative_payment_rejected(self):
        for amount in (0, -500):
            with self.assertRaises(PaymentError):
                apply_payment(self.conn, "PAY-T7", LOAN_B, OB_B, amount, DAY, "upi", "UPI-T7")

    def test_same_payment_twice_counted_once(self):
        self.assertTrue(apply_payment(self.conn, "PAY-T8", LOAN_B, OB_B, 100_000, DAY, "upi", "UPI-T8"))
        self.assertFalse(apply_payment(self.conn, "PAY-T8", LOAN_B, OB_B, 100_000, DAY, "upi", "UPI-T8"))
        self.assertEqual(self.s(LOAN_B)["obligations"][0]["amount_paid_paise"], 100_000)

    def test_reused_bank_reference_rejected(self):
        with self.assertRaises(PaymentError):
            apply_payment(self.conn, "PAY-T9", LOAN_B, OB_B, 100_000, DAY, "upi", "UPI-K03-0001")

    # ---- Consent ----

    def test_consent(self):
        self.assertTrue(is_contactable(self.conn, CUSTOMER, "whatsapp"))
        self.assertTrue(is_contactable(self.conn, CUSTOMER, "sms"))
        self.assertFalse(is_contactable(self.conn, CUSTOMER, "email"))
        self.assertFalse(is_contactable(self.conn, "BRW-UNKNOWN", "sms"))

    def test_opt_out_blocks_contact(self):
        self.conn.execute("UPDATE consents SET opted_out = 1 WHERE borrower_id = ?", (CUSTOMER,))
        self.assertFalse(is_contactable(self.conn, CUSTOMER, "whatsapp"))


class DatasetFileTests(unittest.TestCase):
    """The embedded test rows must be exact copies of the real file (phone numbers excepted)."""

    def test_embedded_rows_equal_the_real_file(self):
        path = find_dataset()
        if not path:
            self.skipTest("PL_Dummy_Collections_Data_500.csv not found - put it next to main.py to run this check")
        with open(path, newline="", encoding="utf-8-sig") as f:
            real = {r["Loan ID"].strip(): {k.strip(): (v or "").strip() for k, v in r.items()} for r in csv.DictReader(f)}
        embedded = list(csv.DictReader([HEADER] + GOOD_ROWS))
        for row in embedded:
            row = {k.strip(): (v or "").strip() for k, v in row.items()}
            file_row = real[row["Loan ID"]]
            for column, value in row.items():
                if column != "Mobile Number":
                    self.assertEqual(value, file_row[column], f"{row['Loan ID']} {column}")


if __name__ == "__main__":
    unittest.main()
