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
