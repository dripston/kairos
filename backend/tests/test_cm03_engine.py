"""CM-03 conversation engine: the servicing intents end to end on the K03 spine (SIMULATED)."""
from __future__ import annotations

import pytest

from kairos_backend.cm03 import nlu as N
from kairos_backend.cm03.fraud import check_attachment, scan_text
from kairos_backend.cm03.store import reference_key, scrub
from kairos_backend.cm03.tools import verify_token
from tests.cm03_helpers import B1042, B2001, B3003, PENDING_REF, SETTLED_REF, UNREGISTERED, Chat


@pytest.fixture
def chat(policy, tmp_path):
    return Chat(policy, tmp_path)


def test_greet_then_otp_then_menu(chat):
    r = chat.say(B2001, "hello")
    assert r["state"] == "AWAIT_OTP" and not r["verified"]
    assert r["tool_calls"][:2] == ["lookup_account", "send_otp"]
    r = chat.say(B2001, chat.otp(B2001))
    assert r["verified"] and r["reply_keys"] == ["VERIFIED_MENU"]


def test_request_before_otp_is_parked_and_served_after(chat):
    r = chat.say(B2001, "what is my balance?")
    assert r["reply_keys"] == ["GREET_VERIFY"] and "₹" not in r["replies"][0]
    r = chat.say(B2001, chat.otp(B2001))
    assert r["reply_keys"][:2] == ["VERIFIED_OK", "BALANCE"] and "₹" in r["replies"][1]
    assert "BALANCE_SHARED" in r["outcomes"] and r["resolved"]


def test_payment_link_is_signed_for_this_borrower(chat):
    chat.verify(B1042)
    r = chat.say(B1042, "payment link bhejo")
    link = next(t for t in r["replies"] if "pay.kairos.example" in t)
    token = link.split("/p/")[1].split()[0]
    payload = verify_token(token)
    assert payload["b"] == "B-1042" and payload["k"] == "pay"
    assert verify_token(token[:-2] + "xx") is None          # tampered token rejected


def test_already_paid_settled_applies_via_spine_and_sends_receipt(chat):
    chat.say(B1042, "Kal EMI pay kar di thi")
    r = chat.say(B1042, chat.otp(B1042))
    assert r["reply_keys"] == ["VERIFIED_OK", "ASK_UTR"]
    r = chat.say(B1042, f"UTR {SETTLED_REF}")
    assert "CLAIM_VERIFIED_RECEIPT" in r["outcomes"] and "apply_verified_payment" in r["tool_calls"]
    paid = chat.ledger._conn().execute("SELECT COUNT(*) FROM payments WHERE reference=?", (SETTLED_REF,)).fetchone()[0]
    assert paid == 1
    r = chat.say(B1042, f"send receipt again, UTR {SETTLED_REF}")   # E16 repeated receipt: same identity, no new payment
    assert "RECEIPT_RESENT" in r["outcomes"]
    assert chat.ledger._conn().execute("SELECT COUNT(*) FROM payments WHERE reference=?", (SETTLED_REF,)).fetchone()[0] == 1


def test_already_paid_pending_is_recorded_never_marked_paid_then_recheck(chat):
    chat.verify(B2001)
    r = chat.say(B2001, f"I paid today, UTR {PENDING_REF}")
    assert "CLAIM_PENDING_RECHECK" in r["outcomes"]
    assert chat.ledger._conn().execute("SELECT COUNT(*) FROM payments WHERE reference=?", (PENDING_REF,)).fetchone()[0] == 0
    assert chat.engine.recheck_pending_claims() == []                     # still pending in D7
    from kairos_backend.cm03.world import settle_feed
    settle_feed(chat.ledger._spine(), PENDING_REF)
    changed = chat.engine.recheck_pending_claims()
    assert [c["status"] for c in changed] == ["VERIFIED"]
    assert chat.engine.get(r["conversation_id"]).outcomes[-1] == "CLAIM_VERIFIED_RECEIPT"


def test_ptp_within_window_creates_k13_hold(chat):
    chat.verify(B2001)
    r = chat.say(B2001, "thoda time chahiye")
    assert r["state"] == "AWAIT_PTP_DATE"
    r = chat.say(B2001, "24 tarikh ko")
    assert "PTP_RECORDED" in r["outcomes"] and "create_ptp" in r["tool_calls"]
    st = chat.ledger._conn().execute("SELECT ptp_date FROM borrower_state WHERE borrower_id='B-2001'").fetchone()
    assert st["ptp_date"] == "2026-11-24"


def test_ptp_beyond_window_asks_once_then_refers(chat):
    chat.verify(B2001)
    r = chat.say(B2001, "I will pay on 20 Dec")
    assert r["reply_keys"] == ["PTP_TOO_FAR"]
    r = chat.say(B2001, "no, 20 Dec")
    assert r["handoff_reason"] == "PTP_BEYOND_WINDOW"


def test_hardship_is_empathetic_and_hands_off_with_hold(chat):
    chat.verify(B2001)
    r = chat.say(B2001, "I lost my job last month")
    assert r["reply_keys"] == ["HARDSHIP"] and r["handoff_reason"] == "HARDSHIP" and not r["resolved"]
    assert "₹" not in r["replies"][0] and "link" not in r["replies"][0].lower()
    assert chat.ledger._conn().execute("SELECT hardship_until FROM borrower_state WHERE borrower_id='B-2001'").fetchone()[0]


def test_dispute_and_callback_create_tickets(chat):
    chat.verify(B2001)
    r = chat.say(B2001, "The EMI amount is wrong")
    assert r["handoff_reason"] == "DISPUTE"
    chat.verify(B1042)
    r = chat.say(B1042, "please call me back")
    assert r["handoff_reason"] == "CALLBACK_REQUESTED"
    assert chat.ledger._conn().execute("SELECT COUNT(*) FROM tickets").fetchone()[0] >= 2


def test_statement_noc_and_loan_details(chat):
    chat.verify(B3003)
    r = chat.say(B3003, "I need my NOC")
    assert "NOC_SENT" in r["outcomes"]
    chat.verify(B2001)
    r = chat.say(B2001, "send NOC")
    assert "NOC_INFO" in r["outcomes"]
    r = chat.say(B2001, "statement bhejo")
    assert "DOC_SENT" in r["outcomes"] and "docs.kairos.example" in r["replies"][0]
    r = chat.say(B2001, "loan details please")
    assert "LOAN_DETAILS_SHARED" in r["outcomes"]


def test_wrong_number_closes_without_disclosure(chat):
    r = chat.say(B2001, "galat number hai")
    assert r["reply_keys"] == ["WRONG_NUMBER"] and r["state"] == "CLOSED" and r["resolved"]
    assert "send_otp" not in r["tool_calls"]


def test_language_switch_to_hindi_and_back(chat):
    r = chat.say(B2001, "Hindi mein baat karo")
    assert r["lang"] == "hi" and any("नमस्ते" in t or "हिंदी" in t for t in r["replies"])
    chat.say(B2001, chat.otp(B2001))
    r = chat.say(B2001, "English please")
    assert r["lang"] == "en" and r["reply_keys"] == ["LANG_SET"]


def test_unknown_clarifies_once_then_hands_off(chat):
    chat.verify(B2001)
    assert chat.say(B2001, "blue sky tomato")["reply_keys"] == ["CLARIFY"]
    r = chat.say(B2001, "hmm what")
    assert r["handoff_reason"] == "UNKNOWN_AFTER_CLARIFY"
    assert chat.say(B2001, "balance?")["reply_keys"] == ["HANDOFF_HOLD"]    # humans own it now


def test_webhook_retry_is_idempotent(chat):
    a = chat.say(B2001, "hello", message_id="wamid.X1")
    b = chat.say(B2001, "hello", message_id="wamid.X1")
    assert b["duplicate"] and b["replies"] == a["replies"]
    assert chat.ledger._conn().execute("SELECT COUNT(*) FROM cm03_otps").fetchone()[0] == 1


def test_nlu_and_helpers():
    assert N.extract_reference("UTR 512345678901") == "512345678901"
    assert N.extract_reference("I don't have the reference right now") is None
    assert N.extract_otp("my code is 123456") == "123456"
    assert N.fix_typos("ballance please") == "balance please"
    assert N.detect_lang("mera balance kya hai") == "hi"
    assert reference_key(" ５１２345678901​") == "512345678901"
    assert "123456" not in scrub("code 123456 from +910000001042") and "1042" in scrub("+910000001042")
    assert scan_text("what is my balance") == []
    assert check_attachment({"meta": {"software": "Android Screenshot"}, "ocr": {"payee": "kairosdemo.collect@upi"}},
                            __import__("datetime").datetime(2026, 11, 20), None) == []
