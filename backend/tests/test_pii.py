"""Red-team RT-F08: tickets never store the borrower's raw name, phone or an e-mail (kairos_backend.pii)."""
import json
from datetime import datetime

from kairos_backend.exceptions import ExceptionEvent
from kairos_backend.ledger import Ledger
from kairos_backend.pii import scrub


def test_scrub_masks_indian_phones_emails_and_the_borrowers_name():
    ident = {"name": "Asha  Verma", "phone": "+91 0123456789"}
    text = ("asha verma (+910123456789, 0123-456-789, alt +91 98765 43210, 9876543210, 987-654-3210) "
            "mailed asha.v@example.co.in; ref UPI 412345678901 on 2026-11-20 10:00, chat:id0123456789x")
    out = scrub(text, name=ident["name"], phone=ident["phone"], name_as="B1")
    assert out == ("B1 ([phone], [phone], alt [phone], [phone], [phone]) mailed [email]; "
                   "ref UPI 412345678901 on 2026-11-20 10:00, chat:id[phone]x")
    assert scrub(None) == "" and scrub("PTP for Rs.4,500 on 2026-11-25") == "PTP for Rs.4,500 on 2026-11-25"


def test_scrub_is_linear_on_long_adversarial_text():
    """Borrower replies are unbounded (RT-INJ-01 sends 200k characters): no pattern may retry from every position."""
    import time

    for text in ("A" * 200_000, " " * 200_000, "1" * 200_000, "0 " * 100_000, "a." * 100_000):
        t0 = time.perf_counter()
        scrub(text + " x@y.in +91 0123456789", name="Synthetic Asha Verma", phone="+910123456789", name_as="B1")
        assert time.perf_counter() - t0 < 2, text[:4]


def test_ticket_fields_are_scrubbed_at_rest(policy, tmp_path):
    lg = Ledger(policy, tmp_path / "pii.db")
    with lg._spine() as conn:
        conn.execute("INSERT INTO borrowers (borrower_id, full_name, phone, language) VALUES"
                     " ('B1', 'Synthetic Asha Verma', '+910123456789', 'en')")
    out = lg.apply_exception("B1", ExceptionEvent("DISPUTE", "B1"), datetime(2026, 11, 20, 10),
                             summary=("Synthetic Asha Verma disputes the EMI", "Reach on +91 01234 56789 / a@b.in"),
                             evidence_ids=["chat:+910123456789", "msg-42"])
    t = lg._conn().execute("SELECT * FROM tickets WHERE ticket_id=?", (out["ticket_id"],)).fetchone()
    assert (t["summary_line_1"], t["summary_line_2"]) == ("B1 disputes the EMI", "Reach on [phone] / [email]")
    assert json.loads(t["evidence_ids"]) == ["chat:[phone]", "msg-42"]
    lg.close()


def test_free_text_reasons_and_replies_are_scrubbed_at_rest(policy, tmp_path):
    """RT-F09: reversal reason (audit + K03 payments/events), pause/resume reason and reply text."""
    from datetime import date

    from spine.ledger import apply_payment

    lg = Ledger(policy, tmp_path / "pii9.db")
    with lg._spine() as conn:
        conn.execute("INSERT INTO borrowers (borrower_id, full_name, phone, language) VALUES"
                     " ('B1', 'Synthetic Asha Verma', '+910123456789', 'en')")
        conn.execute("INSERT INTO loans (loan_id, borrower_id, product, principal_paise) VALUES ('L1','B1','pl',100000)")
        conn.execute("INSERT INTO obligations (obligation_id, loan_id, installment_no, due_date, amount_due_paise)"
                     " VALUES ('L1-1','L1',1,'2026-11-01',5000)")
    apply_payment(lg._spine(), "PAY-1", "L1", "L1-1", 1000, "2026-11-20", "upi", "UTR-1")
    who = "Synthetic Asha Verma +91 01234 56789 asha@example.in"
    assert lg.record_payment_reversed("PAY-1", datetime(2026, 11, 20, 11), f"Bank reversal for {who}")
    lg.set_outbound_paused(True, reason=f"pause: {who}")
    lg.set_outbound_paused(False, reason="borrower 9876543210 complained")
    lg.record_response("B1", datetime(2026, 11, 20, 12), "sms", details=f"This is {who}, call me")
    c = lg._conn()
    assert c.execute("SELECT reversal_reason FROM payments").fetchone()[0] == "Bank reversal for B1 [phone] [email]"
    reasons = dict(c.execute("SELECT type, reason FROM audit WHERE type IN"
                             " ('PAYMENT_REVERSED', 'OUTBOUND_PAUSED', 'OUTBOUND_RESUMED')").fetchall())
    assert reasons == {"PAYMENT_REVERSED": "Bank reversal for B1 [phone] [email]",
                       # no borrower in scope: the name cannot be known, phones and e-mails are still masked
                       "OUTBOUND_PAUSED": "pause: Synthetic Asha Verma [phone] [email]",
                       "OUTBOUND_RESUMED": "borrower [phone] complained"}
    details = dict(c.execute("SELECT event_type, details FROM events WHERE event_type IN"
                             " ('payment_reversed', 'reply_received')").fetchall())
    assert details == {"payment_reversed": "Bank reversal for B1 [phone] [email]",
                       "reply_received": "This is B1 [phone] [email], call me"}
    lg.close()
