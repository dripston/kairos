"""Synthetic CM-03 world (SIMULATED): borrowers, loans, EMIs and past payments on the K03 spine, plus a
synthetic D7 bank feed. No real names or numbers: borrowers are "Synthetic Borrower <id>" and phones
are +910 followed by 9 digits (no Indian mobile number starts with 0).
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Dict, List, Optional

from spine.ledger import apply_payment

from .store import reference_key

EMI_CHOICES = (250_000, 320_000, 450_000, 500_000, 680_000, 810_000)   # paise


def synthetic_phone(i: int) -> str:
    return f"+910{100000000 + i:09d}"


def rrn(rng: random.Random) -> str:
    """A well-formed synthetic UPI RRN (12 digits)."""
    return f"{rng.randrange(10 ** 11, 10 ** 12)}"


@dataclass
class Fixture:
    borrower_id: str
    phone: str
    loan_ids: List[str]
    overdue: List[dict] = field(default_factory=list)       # unpaid past-due EMIs {obligation_id, loan_id, amount_paise}
    used_refs: List[str] = field(default_factory=list)      # references already applied in the spine
    closed_loan: bool = False
    lang: str = "en"


def add_borrower(conn, bid: str, phone: str, as_of: date, rng: random.Random, n_loans: int = 1, dpd: int = 20,
                 closed: bool = False, lang: str = "en") -> Fixture:
    """One synthetic borrower with `n_loans` 12-EMI personal loans. Past EMIs are paid (with references);
    EMIs from `dpd` days ago onward are unpaid; `closed` -> every EMI paid."""
    fx = Fixture(bid, phone, [], closed_loan=closed, lang=lang)
    with conn:
        conn.execute("INSERT INTO borrowers (borrower_id, full_name, phone, language) VALUES (?,?,?,?)",
                     (bid, f"Synthetic Borrower {bid}", phone, lang))
        for ch in ("whatsapp", "sms", "email", "ivr", "voicebot"):
            conn.execute("INSERT INTO consents (borrower_id, channel, consented, opted_out, updated_at) VALUES (?,?,?,0,?)",
                         (bid, ch, 0 if ch == "voicebot" else 1, as_of.isoformat()))
    for k in range(n_loans):
        loan_id = f"PL-{bid.split('-')[-1]}-{k + 1}"
        emi = rng.choice(EMI_CHOICES)
        first_due = as_of - timedelta(days=dpd) - timedelta(days=30 * rng.randint(3, 8))
        disbursed = first_due - timedelta(days=30)
        with conn:
            conn.execute("INSERT INTO loans (loan_id, borrower_id, product, principal_paise, disbursed_on) VALUES (?,?,?,?,?)",
                         (loan_id, bid, "Personal Loan", emi * 11, disbursed.isoformat()))
            for n in range(12):
                conn.execute("INSERT INTO obligations (obligation_id, loan_id, installment_no, due_date, amount_due_paise)"
                             " VALUES (?,?,?,?,?)", (f"{loan_id}-{n + 1:02d}", loan_id, n + 1,
                                                     (first_due + timedelta(days=30 * n)).isoformat(), emi))
        fx.loan_ids.append(loan_id)
        cutoff = as_of + timedelta(days=400) if closed else as_of - timedelta(days=dpd)
        for r in conn.execute("SELECT * FROM obligations WHERE loan_id=? ORDER BY installment_no", (loan_id,)).fetchall():
            due = date.fromisoformat(r["due_date"])
            if due < cutoff:
                ref = rrn(rng)
                apply_payment(conn, f"P-{r['obligation_id']}", loan_id, r["obligation_id"], r["amount_due_paise"],
                              due.isoformat(), "nach", ref)
                fx.used_refs.append(ref)
            elif due <= as_of:
                fx.overdue.append({"obligation_id": r["obligation_id"], "loan_id": loan_id, "amount_paise": r["amount_due_paise"],
                                   "due_date": r["due_date"]})
    return fx


def add_feed(conn, reference: str, amount_paise: int, credited_on: date, status: str = "SETTLED",
             borrower_id: Optional[str] = None, obligation_id: Optional[str] = None) -> None:
    """A synthetic D7 settlement record (what the bank says actually arrived)."""
    with conn:
        conn.execute("INSERT INTO cm03_bank_feed (reference, reference_key, amount_paise, credited_on, status, borrower_id,"
                     " obligation_id) VALUES (?,?,?,?,?,?,?)", (reference, reference_key(reference), amount_paise,
                                                                credited_on.isoformat(), status, borrower_id, obligation_id))


def settle_feed(conn, reference: str) -> None:
    with conn:
        conn.execute("UPDATE cm03_bank_feed SET status='SETTLED' WHERE reference_key=?", (reference_key(reference),))


DEMO_BORROWERS = (
    # id, phone, loans, dpd, closed, lang
    ("B-1042", "+910000001042", 2, 42, False, "hi"),
    ("B-2001", "+910000002001", 1, 12, False, "en"),
    ("B-3003", "+910000003003", 1, 0, True, "en"),
)
DEMO_FEED = {
    "settled": "512345678901",     # settled credit for B-1042's oldest overdue EMI
    "pending": "612345678902",     # pending credit for B-2001
}


def seed_demo(ledger, as_of: date) -> Dict[str, Fixture]:
    """Small fixed world for the /cm03/console demo. Idempotent (skips if already seeded)."""
    conn = ledger._spine()
    if conn.execute("SELECT 1 FROM borrowers WHERE borrower_id='B-1042'").fetchone():
        return {}
    rng = random.Random(1042)
    out = {}
    for bid, phone, loans, dpd, closed, lang in DEMO_BORROWERS:
        out[bid] = add_borrower(conn, bid, phone, as_of, rng, loans, max(dpd, 1), closed, lang)
    ob = out["B-1042"].overdue[0]
    add_feed(conn, DEMO_FEED["settled"], ob["amount_paise"], as_of - timedelta(days=1), "SETTLED", "B-1042", ob["obligation_id"])
    ob2 = out["B-2001"].overdue[0]
    add_feed(conn, DEMO_FEED["pending"], ob2["amount_paise"], as_of, "PENDING", "B-2001", ob2["obligation_id"])
    return out
