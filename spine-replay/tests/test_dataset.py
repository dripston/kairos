"""Tests for loading the team dataset. Uses a small sample in the same format as
PL_Dummy_Collections_Data_500.csv (phone numbers replaced with obvious test numbers).

Run:  python3 -m unittest -v
"""
import os
import tempfile
import unittest
from unittest import mock

from spine import importer
from spine.config import DATASET_AS_OF_DATE
from spine.db import get_connection, integrity_problems
from spine.ledger import PaymentError, apply_payment, is_contactable, loan_summary

HEADER = ("Loan ID,Name,Mobile Number,Product,EMI Amount,POS,TOS,DPD,Bounce Date,Bucket,"
          "Bounce String (last 6 months),NACH/ECS Mandate (T/F),Re-presentment Eligibility (T/F),"
          "State,Age,Employment Type,Remaining EMIs,Risk Tagging,Payment Link  ")
GOOD_ROWS = [
    'PL100001,Karan Kumar,9000000001,Personal Loan,2976,7839,9440,1,2026-08-06,0-30,"0,1,1,0,0,1",T,F,Maharashtra,32,Self-Employed,3,C3 - Medium Risk,https://pay.lender.example/pl/PL100001  ',
    'PL100010,Pooja Reddy,9000000010,Personal Loan,23688,908643,925569,7,2026-07-31,0-30,"NA,NA,NA,NA,NA,1",T,T,Punjab,25,Salaried,36,C1 - First-Time Bounce,https://pay.lender.example/pl/PL100010  ',
    'PL100031,Meera Banerjee,9000000031,Personal Loan,7436,8088,9774,30,2026-07-08,0-30,"0,0,0,0,0,1",T,T,Bihar,42,Salaried,1,C2 - Low Risk,https://pay.lender.example/pl/PL100031  ',
    'PL100033,Ashok Yadav,9000000033,Personal Loan,18862,144904,147672,15,2026-07-23,0-30,"1,0,0,0,0,1",T,T,Delhi,40,Salaried,7,C3 - Medium Risk,https://pay.lender.example/pl/PL100033  ',
    'PL100034,Ashok Yadav,9000000034,Personal Loan,9362,64467,69199,4,2026-08-03,0-30,"1,0,0,0,0,1",F,F,Rajasthan,51,Salaried,6,C3 - Medium Risk,https://pay.lender.example/pl/PL100034  ',
]
BAD_ROWS = [
    ',No Loan Id,9000000099,Personal Loan,100,100,100,1,2026-08-06,0-30,"0,0,0,0,0,1",T,T,Delhi,30,Salaried,1,C2 - Low Risk,https://x',
    'PL900002,Bad Date,9000000098,Personal Loan,100,100,100,1,06-08-2026,0-30,"0,0,0,0,0,1",T,T,Delhi,30,Salaried,1,C2 - Low Risk,https://x',
    'PL900003,Bad Phone,12345,Personal Loan,100,100,100,1,2026-08-06,0-30,"0,0,0,0,0,1",T,T,Delhi,30,Salaried,1,C2 - Low Risk,https://x',
    'PL100001,Duplicate Loan,9000000097,Personal Loan,100,100,100,1,2026-08-06,0-30,"0,0,0,0,0,1",T,T,Delhi,30,Salaried,1,C2 - Low Risk,https://x',
]


class DatasetImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False)
        self.tmp.write("\n".join([HEADER] + GOOD_ROWS + BAD_ROWS) + "\n\n")
        self.tmp.close()
        self.conn = get_connection(":memory:")
        self.report = importer.import_dataset(self.conn, self.tmp.name)

    def tearDown(self):
        self.conn.close()
        os.unlink(self.tmp.name)

    def count(self, table):
        return self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def test_good_rows_load_and_bad_rows_are_rejected_with_reasons(self):
        self.assertEqual(self.report["loaded"], 5)
        self.assertEqual(len(self.report["rejected"]), 4)
        reasons = " ".join(r["reason"] for r in self.report["rejected"])
        for expected in ("missing Loan ID", "Bounce Date", "10 digits", "appears twice"):
            self.assertIn(expected, reasons)

    def test_no_orphan_records(self):
        self.assertEqual(integrity_problems(self.conn), [])

    def test_stray_spaces_removed(self):
        link = self.conn.execute("SELECT payment_link FROM loans WHERE loan_id = 'PL100001'").fetchone()[0]
        self.assertEqual(link, "https://pay.lender.example/pl/PL100001")

    def test_same_name_stays_two_borrowers(self):
        rows = self.conn.execute("SELECT borrower_id FROM borrowers WHERE full_name = 'Ashok Yadav'").fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual(self.report["duplicate_names"], 2)

    def test_dpd_matches_file_when_using_dataset_date(self):
        self.assertEqual(self.report["dpd_mismatches"], [])
        self.assertEqual(loan_summary(self.conn, "PL100001", DATASET_AS_OF_DATE)["dpd"], 1)

    def test_dpd_30_in_bucket_0_30_agrees_with_k01(self):
        # K01 policy.json: B1 is 1-30, so the file's '0-30' bucket is right for DPD 30.
        self.assertEqual(self.report["bucket_vs_policy_band"], [])
        self.assertEqual(loan_summary(self.conn, "PL100031", DATASET_AS_OF_DATE)["dpd_band"], "B1")

    def test_bucket_saved(self):
        self.assertEqual(self.conn.execute("SELECT bucket FROM loans WHERE loan_id = 'PL100031'").fetchone()[0], "0-30")

    def test_amounts_stored_in_paise(self):
        loan = loan_summary(self.conn, "PL100001", DATASET_AS_OF_DATE)
        self.assertEqual(loan["outstanding_paise"], 297_600)
        self.assertEqual(loan["principal_paise"], 783_900)

    def test_na_bounce_string_kept(self):
        value = self.conn.execute("SELECT bounce_string FROM loans WHERE loan_id = 'PL100010'").fetchone()[0]
        self.assertEqual(value, "NA,NA,NA,NA,NA,1")

    def test_loading_twice_adds_nothing(self):
        before = {t: self.count(t) for t in ("borrowers", "loans", "obligations", "consents")}
        again = importer.import_dataset(self.conn, self.tmp.name)
        self.assertEqual(again["loaded"], 0)
        self.assertEqual(again["already_present"], 5)
        self.assertEqual(before, {t: self.count(t) for t in ("borrowers", "loans", "obligations", "consents")})

    def test_no_consent_means_not_contactable(self):
        self.assertEqual(self.count("consents"), 0)
        self.assertFalse(is_contactable(self.conn, "BRW-PL100001", "sms"))

    def test_paying_overdue_emi_clears_dpd_but_loan_stays_open(self):
        apply_payment(self.conn, "PAY-1", "PL100001", "PL100001-OD1", 297_600, "2026-08-07", "upi", "UPI-1")
        loan = loan_summary(self.conn, "PL100001", DATASET_AS_OF_DATE)
        self.assertEqual((loan["dpd"], loan["status"], loan["overdue"]), (0, "open", False))

    def test_payment_on_one_dataset_loan_does_not_touch_another(self):
        apply_payment(self.conn, "PAY-2", "PL100033", "PL100033-OD1", 1_886_200, "2026-08-07", "upi", "UPI-2")
        other = loan_summary(self.conn, "PL100034", DATASET_AS_OF_DATE)
        self.assertEqual(other["outstanding_paise"], 936_200)

    def test_payment_to_wrong_loan_rejected(self):
        with self.assertRaises(PaymentError):
            apply_payment(self.conn, "PAY-3", "PL100034", "PL100033-OD1", 100, "2026-08-07", "upi", "UPI-3")


class AssumedConsentTests(unittest.TestCase):
    def test_assumed_consent_is_labelled(self):
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as f:
            f.write("\n".join([HEADER] + GOOD_ROWS[:1]))
        conn = get_connection(":memory:")
        try:
            with mock.patch.object(importer, "DATASET_ASSUMED_CONSENT", ("sms",)):
                importer.import_dataset(conn, f.name)
            self.assertTrue(is_contactable(conn, "BRW-PL100001", "sms"))
            self.assertFalse(is_contactable(conn, "BRW-PL100001", "whatsapp"))
            source = conn.execute("SELECT source FROM consents").fetchone()[0]
            self.assertEqual(source, "assumed")
        finally:
            conn.close()
            os.unlink(f.name)


if __name__ == "__main__":
    unittest.main()


class SyntheticDatasetTests(unittest.TestCase):
    """The generated dataset must load cleanly through the same importer as the team file."""

    @classmethod
    def setUpClass(cls):
        from spine.synthetic_dataset import write_dataset
        cls.dir = tempfile.mkdtemp()
        cls.path = os.path.join(cls.dir, "synthetic.csv")
        write_dataset(cls.path, 2000, seed=42)
        cls.conn = get_connection(":memory:")
        cls.report = importer.import_dataset(cls.conn, cls.path)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def test_every_row_loads(self):
        self.assertEqual((self.report["loaded"], self.report["rejected"]), (2000, []))

    def test_dpd_matches_and_buckets_agree_with_k01(self):
        self.assertEqual(self.report["dpd_mismatches"], [])
        self.assertEqual(self.report["bucket_vs_policy_band"], [])

    def test_no_phone_can_reach_a_real_mobile(self):
        phones = [r[0] for r in self.conn.execute("SELECT phone FROM borrowers")]
        self.assertTrue(all(p.startswith("0") and len(p) == 10 for p in phones))

    def test_consent_comes_from_file_and_opt_outs_block_contact(self):
        self.assertTrue(self.report["consent_from_file"])
        sources = {r[0] for r in self.conn.execute("SELECT DISTINCT source FROM consents")}
        self.assertEqual(sources, {"provided"})
        opted = self.conn.execute(
            "SELECT borrower_id, channel FROM consents WHERE opted_out = 1 AND consented = 1 LIMIT 1").fetchone()
        self.assertIsNotNone(opted)
        self.assertFalse(is_contactable(self.conn, opted[0], opted[1]))

    def test_all_dpd_bands_and_risk_tags_present(self):
        bands = {loan_summary(self.conn, r[0], DATASET_AS_OF_DATE)["dpd_band"]
                 for r in self.conn.execute("SELECT loan_id FROM loans")}
        self.assertEqual(bands, {"B1", "B2", "B3", "B4", "DPD_INVALID"})
        tags = {r[0][:2] for r in self.conn.execute("SELECT DISTINCT risk_tag FROM loans")}
        self.assertEqual(tags, {"C1", "C2", "C3", "C4", "C5"})

    def test_same_seed_same_file(self):
        from spine.synthetic_dataset import write_dataset
        other = os.path.join(self.dir, "again.csv")
        write_dataset(other, 2000, seed=42)
        with open(self.path) as a, open(other) as b:
            self.assertEqual(a.read(), b.read())


class K03FromDatasetTests(unittest.TestCase):
    """K03 'Done when' checks, using the borrower built from PL_Dummy_Collections_Data_500 rows."""

    def setUp(self):
        from spine.seed import build_spine_from_dataset
        self.tmp = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False)
        self.tmp.write("\n".join([HEADER] + GOOD_ROWS))
        self.tmp.close()
        self.conn = get_connection(":memory:")
        build_spine_from_dataset(self.conn, self.tmp.name)

    def tearDown(self):
        self.conn.close()
        os.unlink(self.tmp.name)

    def summary(self, loan_id):
        return loan_summary(self.conn, loan_id, DATASET_AS_OF_DATE)

    def test_one_customer_two_loans_from_dataset(self):
        loans = self.conn.execute("SELECT loan_id, source FROM loans WHERE borrower_id = 'CUST-K03-0001' "
                                  "ORDER BY loan_id").fetchall()
        self.assertEqual([tuple(l) for l in loans], [("PL100033", "dataset"), ("PL100034", "dataset")])

    def test_amounts_come_from_the_file(self):
        self.assertEqual(self.summary("PL100034")["obligations"][0]["amount_due_paise"], 936_200)
        self.assertEqual(self.summary("PL100034")["dpd"], 4)

    def test_clearing_loan_a_leaves_loan_b_open(self):
        a, b = self.summary("PL100033"), self.summary("PL100034")
        self.assertEqual((a["outstanding_paise"], a["overdue"]), (0, False))
        self.assertEqual((b["outstanding_paise"], b["overdue"], b["status"]), (936_200, True, "open"))

    def test_every_event_joins_to_the_right_obligation(self):
        rows = self.conn.execute(
            """SELECT e.loan_id AS ev_loan, o.loan_id AS ob_loan FROM events e
               JOIN obligations o ON o.obligation_id = e.obligation_id""").fetchall()
        self.assertEqual(len(rows), 3)  # payment + reminder + reply
        self.assertTrue(all(r["ev_loan"] == r["ob_loan"] for r in rows))
        self.assertEqual(integrity_problems(self.conn), [])

    def test_consent_labelled_and_real_phone_replaced(self):
        self.assertTrue(is_contactable(self.conn, "CUST-K03-0001", "whatsapp"))
        self.assertFalse(is_contactable(self.conn, "CUST-K03-0001", "email"))
        self.assertEqual({r[0] for r in self.conn.execute("SELECT source FROM consents")}, {"assumed"})
        phone = self.conn.execute("SELECT phone FROM borrowers").fetchone()[0]
        self.assertEqual(phone, "+910000000000")

    def test_running_twice_creates_no_duplicates(self):
        from spine.seed import build_spine_from_dataset
        counts = [self.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                  for t in ("borrowers", "loans", "obligations", "payments", "events", "consents")]
        build_spine_from_dataset(self.conn, self.tmp.name)
        again = [self.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                 for t in ("borrowers", "loans", "obligations", "payments", "events", "consents")]
        self.assertEqual(counts, again)
