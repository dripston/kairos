"""Creates the synthetic borrower: one person, two loans.

Loan A (personal loan): 3 EMIs, all paid      -> should be CLOSED
Loan B (phone on EMI):  EMI 1 unpaid, 34 days late, EMI 2 not yet due -> should be OPEN

Safe to run many times: nothing gets duplicated.
(ON CONFLICT ... DO NOTHING skips only duplicates; invalid rows still raise errors.)
"""
from spine.config import TEST_PHONE
from spine.db import create_schema
from spine.ledger import apply_payment, record_event

BORROWER_ID = "BRW-0001"
LOAN_A = "LN-A-0001"
LOAN_B = "LN-B-0001"

CONSENTS = [
    # (channel, consented, opted_out)
    ("whatsapp", 1, 0),
    ("sms", 1, 0),
    ("email", 0, 0),   # no consent: lets teammates test that email is skipped
    ("ivr", 0, 0),
    ("voicebot", 0, 0),
]

LOANS = [
    # (loan_id, product, principal in paise, disbursed_on)
    (LOAN_A, "personal_loan", 1_500_000, "2026-07-05"),
    (LOAN_B, "consumer_durable", 600_000, "2026-09-16"),
]

OBLIGATIONS = [
    # (obligation_id, loan_id, installment_no, due_date, amount_due in paise)
    ("OB-A-1", LOAN_A, 1, "2026-08-05", 500_000),
    ("OB-A-2", LOAN_A, 2, "2026-09-05", 500_000),
    ("OB-A-3", LOAN_A, 3, "2026-10-05", 500_000),
    ("OB-B-1", LOAN_B, 1, "2026-10-16", 300_000),
    ("OB-B-2", LOAN_B, 2, "2026-12-16", 300_000),
]

PAYMENTS = [
    # (payment_id, loan_id, obligation_id, amount in paise, paid_on, mode, reference)
    ("PAY-0001", LOAN_A, "OB-A-1", 500_000, "2026-08-03", "upi", "UPI-TEST-0001"),
    ("PAY-0002", LOAN_A, "OB-A-2", 500_000, "2026-09-04", "upi", "UPI-TEST-0002"),
    ("PAY-0003", LOAN_A, "OB-A-3", 500_000, "2026-10-05", "upi", "UPI-TEST-0003"),
]


def build_spine(conn):
    create_schema(conn)
    with conn:
        conn.execute(
            """INSERT INTO borrowers (borrower_id, full_name, phone, email, language)
               VALUES (?, ?, ?, ?, ?) ON CONFLICT (borrower_id) DO NOTHING""",
            (BORROWER_ID, "Asha Test Borrower", TEST_PHONE, "asha.test@example.com", "en"),
        )
        for channel, consented, opted_out in CONSENTS:
            conn.execute(
                """INSERT INTO consents (borrower_id, channel, consented, opted_out, updated_at)
                   VALUES (?, ?, ?, ?, ?) ON CONFLICT (borrower_id, channel) DO NOTHING""",
                (BORROWER_ID, channel, consented, opted_out, "2026-07-05"),
            )
        for loan_id, product, principal, disbursed_on in LOANS:
            conn.execute(
                """INSERT INTO loans (loan_id, borrower_id, product, principal_paise, disbursed_on)
                   VALUES (?, ?, ?, ?, ?) ON CONFLICT (loan_id) DO NOTHING""",
                (loan_id, BORROWER_ID, product, principal, disbursed_on),
            )
        for obligation_id, loan_id, number, due_date, amount in OBLIGATIONS:
            conn.execute(
                """INSERT INTO obligations
                   (obligation_id, loan_id, installment_no, due_date, amount_due_paise)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT (obligation_id) DO NOTHING""",
                (obligation_id, loan_id, number, due_date, amount),
            )

    # Payments go through the same function the real system will use.
    for payment in PAYMENTS:
        apply_payment(conn, *payment)

    # A reminder about Loan B's overdue EMI, and the borrower's reply.
    with conn:
        record_event(conn, "EV-TOUCH-0001", BORROWER_ID, "touch_sent", "2026-10-20T10:00:00",
                     channel="whatsapp", loan_id=LOAN_B, obligation_id="OB-B-1",
                     details="Reminder for EMI 1")
        record_event(conn, "EV-REPLY-0001", BORROWER_ID, "reply_received", "2026-10-21T19:30:00",
                     channel="whatsapp", loan_id=LOAN_B, obligation_id="OB-B-1",
                     details="Will pay by month end")


# ---------------------------------------------------------------------------
# K03 borrower built from the team dataset PL_Dummy_Collections_Data_500.
#
# The dataset has one loan per row and no customer ID, so no person has two loans.
# For K03 we take two real rows and link them to ONE customer (a labelled synthetic link):
#   PL100033  Ashok Yadav  EMI Rs.18,862  DPD 15  C3 - Medium Risk   -> "Loan A"
#   PL100034  Ashok Yadav  EMI Rs.9,362   DPD 4   C3 - Medium Risk   -> "Loan B"
# Everything else (amounts, DPD, bounce date, risk tag, payment link) comes from the file.
# Added because the file does not have them: consent, one payment, two contact events.
# The real-looking mobile number is replaced with TEST_PHONE so nothing can reach a real person.
# ---------------------------------------------------------------------------
from spine.config import DATASET_AS_OF_DATE  # noqa: E402
from spine.importer import create_schema as _schema, find_rows, insert_parsed_loan  # noqa: E402

DATASET_CUSTOMER_ID = "CUST-K03-0001"
DATASET_LOAN_A, DATASET_LOAN_B = "PL100033", "PL100034"
DATASET_CONSENT = {"whatsapp": 1, "sms": 1, "email": 0, "ivr": 0, "voicebot": 0}


def build_spine_from_dataset(conn, csv_path):
    _schema(conn)
    loan_a, loan_b = find_rows(csv_path, (DATASET_LOAN_A, DATASET_LOAN_B))
    with conn:
        for r in (loan_a, loan_b):
            insert_parsed_loan(conn, r, DATASET_AS_OF_DATE, borrower_id=DATASET_CUSTOMER_ID,
                               phone=TEST_PHONE, consents=DATASET_CONSENT)

    # The borrower pays Loan A's bounced EMI in full (synthetic UPI payment).
    apply_payment(conn, "PAY-K03-0001", DATASET_LOAN_A, f"{DATASET_LOAN_A}-OD1", loan_a["emi_paise"],
                  DATASET_AS_OF_DATE.isoformat(), "upi", "UPI-K03-0001")

    # A reminder about Loan B's bounced EMI, and the borrower's reply.
    with conn:
        record_event(conn, "EV-K03-TOUCH-1", DATASET_CUSTOMER_ID, "touch_sent", "2026-08-05T10:00:00",
                     channel="whatsapp", loan_id=DATASET_LOAN_B, obligation_id=f"{DATASET_LOAN_B}-OD1",
                     details="Reminder for bounced EMI")
        record_event(conn, "EV-K03-REPLY-1", DATASET_CUSTOMER_ID, "reply_received", "2026-08-06T19:30:00",
                     channel="whatsapp", loan_id=DATASET_LOAN_B, obligation_id=f"{DATASET_LOAN_B}-OD1",
                     details="Will pay on salary day")
