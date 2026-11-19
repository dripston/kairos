"""Business logic: apply payments, record events, calculate loan status and DPD."""
from datetime import date

from spine.config import AS_OF_DATE, REMAINING_EMIS_INCLUDES_OVERDUE
from spine.policy import dpd_band, policy_version


class PaymentError(ValueError):
    """Raised when a payment is invalid. Nothing is saved when this happens."""


def rupees(paise):
    return f"Rs.{paise / 100:,.2f}"


def record_event(conn, event_id, borrower_id, event_type, occurred_at,
                 channel=None, loan_id=None, obligation_id=None, details=None):
    """Save an event. Re-running with the same event_id does nothing (no duplicates).

    ON CONFLICT ... DO NOTHING skips ONLY a duplicate event_id. Any broken rule
    (missing obligation, wrong loan) still raises an error instead of being silently dropped.
    """
    conn.execute(
        """INSERT INTO events
           (event_id, borrower_id, loan_id, obligation_id, event_type, channel, occurred_at, details, policy_version)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT (event_id) DO NOTHING""",
        (event_id, borrower_id, loan_id, obligation_id, event_type, channel, occurred_at, details, policy_version()),
    )


def apply_payment(conn, payment_id, loan_id, obligation_id, amount_paise, paid_on, mode, reference):
    """Apply a payment to ONE specific EMI of ONE specific loan.

    Returns True if applied, False if this payment_id was already applied
    (so re-running a data load never counts the same money twice).
    """
    existing = conn.execute(
        "SELECT payment_id FROM payments WHERE payment_id = ? OR reference = ?",
        (payment_id, reference),
    ).fetchone()
    if existing:
        if existing["payment_id"] == payment_id:
            return False
        # Same bank reference reused for a different payment: likely a fake or edited screenshot.
        raise PaymentError(f"Reference {reference} is already used by payment {existing['payment_id']}")

    if amount_paise <= 0:
        raise PaymentError("Payment amount must be more than zero")

    obligation = conn.execute(
        """SELECT o.amount_due_paise, o.amount_paid_paise, l.borrower_id
           FROM obligations o JOIN loans l ON l.loan_id = o.loan_id
           WHERE o.obligation_id = ? AND o.loan_id = ?""",
        (obligation_id, loan_id),
    ).fetchone()
    if obligation is None:
        raise PaymentError(f"EMI {obligation_id} does not belong to loan {loan_id}")

    outstanding = obligation["amount_due_paise"] - obligation["amount_paid_paise"]
    if amount_paise > outstanding:
        raise PaymentError(
            f"Payment {rupees(amount_paise)} is more than the {rupees(outstanding)} still due on {obligation_id}"
        )

    with conn:  # all three steps succeed together, or none are saved
        conn.execute(
            """INSERT INTO payments (payment_id, loan_id, obligation_id, amount_paise, paid_on, mode, reference)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (payment_id, loan_id, obligation_id, amount_paise, paid_on, mode, reference),
        )
        conn.execute(
            "UPDATE obligations SET amount_paid_paise = amount_paid_paise + ? WHERE obligation_id = ?",
            (amount_paise, obligation_id),
        )
        record_event(conn, f"EV-{payment_id}", obligation["borrower_id"], "payment_received", paid_on,
                     loan_id=loan_id, obligation_id=obligation_id, details=f"{mode} ref {reference}")
    return True


def reverse_payment(conn, payment_id, reversed_on, reason):
    """The bank reversed a payment (edge case E15): the EMI becomes due again.
    Only that payment's own EMI changes; other EMIs and loans are untouched.
    Returns True if reversed now, False if it was already reversed (safe to re-run)."""
    payment = conn.execute(
        """SELECT p.*, l.borrower_id FROM payments p JOIN loans l ON l.loan_id = p.loan_id
           WHERE p.payment_id = ?""", (payment_id,)).fetchone()
    if payment is None:
        raise PaymentError(f"Unknown payment {payment_id}")
    if payment["reversed_on"]:
        return False
    with conn:
        conn.execute("UPDATE payments SET reversed_on = ?, reversal_reason = ? WHERE payment_id = ?",
                     (reversed_on, reason, payment_id))
        conn.execute("UPDATE obligations SET amount_paid_paise = amount_paid_paise - ? WHERE obligation_id = ?",
                     (payment["amount_paise"], payment["obligation_id"]))
        record_event(conn, f"EV-REV-{payment_id}", payment["borrower_id"], "payment_reversed", reversed_on,
                     loan_id=payment["loan_id"], obligation_id=payment["obligation_id"], details=reason)
    return True


def obligations_for_loan(conn, loan_id, as_of=AS_OF_DATE):
    """Each EMI of a loan with its outstanding amount, status and DPD."""
    result = []
    rows = conn.execute(
        "SELECT * FROM obligations WHERE loan_id = ? ORDER BY installment_no", (loan_id,)
    ).fetchall()
    for row in rows:
        outstanding = row["amount_due_paise"] - row["amount_paid_paise"]
        if outstanding == 0:
            status = "paid"
        elif row["amount_paid_paise"] > 0:
            status = "partially_paid"
        else:
            status = "due"
        # Unpaid EMI: days late (negative = days until due, for K01's PRE_DUE band). Paid EMI: 0.
        dpd = (as_of - date.fromisoformat(row["due_date"])).days if outstanding > 0 else 0
        result.append({**dict(row), "outstanding_paise": outstanding, "status": status, "dpd": dpd})
    return result


def future_emis(loan):
    """EMIs not yet in the obligations table (dataset loans only list the overdue EMI)."""
    remaining = loan["remaining_emis"] if "remaining_emis" in loan.keys() else None
    if remaining is None:
        return 0
    return max(remaining - (1 if REMAINING_EMIS_INCLUDES_OVERDUE else 0), 0)


def loan_summary(conn, loan_id, as_of=AS_OF_DATE):
    """A loan is closed only when every one of ITS OWN EMIs is fully paid
    and no future EMIs are left."""
    loan = conn.execute("SELECT * FROM loans WHERE loan_id = ?", (loan_id,)).fetchone()
    if loan is None:
        raise ValueError(f"Unknown loan {loan_id}")
    obligations = obligations_for_loan(conn, loan_id, as_of)
    if not obligations:
        raise ValueError(f"Loan {loan_id} has no EMIs")
    outstanding = sum(o["outstanding_paise"] for o in obligations)
    unpaid = [o["dpd"] for o in obligations if o["outstanding_paise"] > 0]
    dpd = max(unpaid) if unpaid else 0  # the oldest unpaid EMI decides how late the loan is
    keys = loan.keys()
    held = "file_dpd" in keys and loan["source"] == "dataset" and loan["file_dpd"] is None
    if held:
        band = "DPD_INVALID"            # K01: missing DPD -> HOLD, never guess it
    elif not unpaid:
        band = "CLEARED"                # K01: obligation cleared -> CANCEL queued touches
    else:
        band = dpd_band(dpd)
    return {
        **dict(loan),
        "status": "closed" if outstanding == 0 and future_emis(loan) == 0 else "open",
        "overdue": bool(unpaid) and dpd > 0,
        "outstanding_paise": outstanding,
        "dpd": dpd,
        "dpd_band": band,
        "obligations": obligations,
    }


def borrower_summary(conn, borrower_id, as_of=AS_OF_DATE):
    loan_ids = [r["loan_id"] for r in conn.execute(
        "SELECT loan_id FROM loans WHERE borrower_id = ? ORDER BY loan_id", (borrower_id,))]
    return [loan_summary(conn, loan_id, as_of) for loan_id in loan_ids]


def is_contactable(conn, borrower_id, channel):
    """True only if the borrower consented to this channel and has not opted out."""
    row = conn.execute(
        "SELECT consented, opted_out FROM consents WHERE borrower_id = ? AND channel = ?",
        (borrower_id, channel),
    ).fetchone()
    return row is not None and row["consented"] == 1 and row["opted_out"] == 0
