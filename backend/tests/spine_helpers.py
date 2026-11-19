"""Test helpers for the K09 / SupervisorFSM tests: put a borrower on the K03 spine and enroll the loan.

Same shape as tests/test_ledger.py's add_loan: one borrower, one loan `<bid>-<loan>`, one EMI
`<bid>-<loan>-1` that is `dpd` days past due on `as_of`. Money in paise.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

ALL_CONSENT = {"sms": 1, "whatsapp": 1, "email": 1, "ivr": 1, "voicebot": 0}


def add_loan(lg, bid: str, as_of: date, loan: str = "A", dpd: int = 12, emi_paise: int = 500_000,
             consents: Optional[dict] = None, campaign: Optional[str] = None) -> str:
    """Create the borrower/loan/EMI if missing; enroll the loan in `campaign` when given. Returns the EMI id."""
    conn = lg._spine()
    loan_id = f"{bid}-{loan}"
    with conn:
        conn.execute("INSERT OR IGNORE INTO borrowers (borrower_id, full_name, phone, language) VALUES (?,?,?, 'en')",
                     (bid, f"Test {bid}", "+910000000000"))
        for ch, ok in (consents if consents is not None else ALL_CONSENT).items():
            conn.execute("INSERT OR IGNORE INTO consents (borrower_id, channel, consented, opted_out, updated_at)"
                         " VALUES (?,?,?,0,?)", (bid, ch, ok, as_of.isoformat()))
        conn.execute("INSERT OR IGNORE INTO loans (loan_id, borrower_id, product, principal_paise)"
                     " VALUES (?,?,'personal_loan',?)", (loan_id, bid, emi_paise * 12))
        conn.execute("INSERT OR IGNORE INTO obligations (obligation_id, loan_id, installment_no, due_date, amount_due_paise)"
                     " VALUES (?,?,1,?,?)", (f"{loan_id}-1", loan_id, (as_of - timedelta(days=dpd)).isoformat(), emi_paise))
    if campaign:
        lg.enroll(campaign, [loan_id])
    return f"{loan_id}-1"
