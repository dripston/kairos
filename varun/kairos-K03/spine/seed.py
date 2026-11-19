"""K03: one borrower with two loans, built from the team dataset PL_Dummy_Collections_Data_500.

Nothing here invents loans or amounts: both loans are real rows of the file.
"""
from spine.config import TEST_PHONE
from spine.ledger import apply_payment, record_event

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
from spine.config import DATASET_AS_OF_DATE
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
