from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from kairos_backend.exceptions import EVENTS
from kairos_backend.intent import (
    INTENTS, K13_EVENT, LLM_INTENT_SCHEMA, IntentClassifier, InboundMessage, LunaClient, extract_date,
    is_stop, redact, validate_llm_output,
)

NOW = datetime(2026, 11, 19, 10, 0)  # Thursday, IST local
TODAY = NOW.date()


class FakeTransport:
    def __init__(self, status=200, body=None, exc=None):
        self.calls = []
        self.status, self.body, self.exc = status, body, exc

    def __call__(self, url, headers, body, timeout):
        self.calls.append({"url": url, "headers": headers, "body": json.loads(body.decode("utf-8")), "timeout": timeout})
        if self.exc:
            raise self.exc
        raw = self.body if isinstance(self.body, bytes) else json.dumps(self.body).encode("utf-8")
        return self.status, raw


def responses_body(obj, usage=True, extra_items=()):
    text = obj if isinstance(obj, str) else json.dumps(obj)
    out = {"id": "resp_1", "object": "response", "status": "completed", "model": "gpt-6-luna",
           "output": list(extra_items) + [{"type": "message", "role": "assistant", "phase": "final_answer",
                                           "content": [{"type": "output_text", "text": text, "annotations": []}]}]}
    if usage:
        out["usage"] = {"input_tokens": 210, "output_tokens": 38, "total_tokens": 248,
                        "output_tokens_details": {"reasoning_tokens": 0}}
    return out


GOOD = {"intent": "NEEDS_TIME", "summary": "Borrower wants a few more days, no date.", "ambiguous": False, "agreed_date": None}


def clf(transport=None, key="sk-test"):
    t = transport or FakeTransport(body=responses_body(GOOD))
    return IntentClassifier(llm=LunaClient(api_key=key, transport=t, env={})), t


def msg(text=None, channel="whatsapp", **kw):
    return InboundMessage(borrower_id="BRW-0042", channel=channel, received_at=NOW, text=text, **kw)


# ---- structured inputs never call the LLM -----------------------------------------------------

@pytest.mark.parametrize("dtmf,intent", [("1", "CLAIMS_PAID"), ("2", "NEEDS_TIME"), ("3", "CALLBACK"),
                                         ("4", "HARDSHIP"), ("9", "STOP"), ("7", "OTHER")])
def test_dtmf_uses_rules_only(dtmf, intent):
    c, t = clf()
    r = c.classify(msg(channel="ivr", dtmf=dtmf, text="random words the ASR heard"))
    assert r.intent == intent and r.source == "rules" and r.llm_usage is None
    assert t.calls == []


@pytest.mark.parametrize("payload,intent,date_", [
    ("PAID", "CLAIMS_PAID", None), ("need_time", "NEEDS_TIME", None), ("TALK_TO_AGENT", "CALLBACK", None),
    ("PTP:2026-11-24", "PTP", "2026-11-24"), ("PAY_TOMORROW", "PTP", "2026-11-20"), ("STOP", "STOP", None),
    ("SOMETHING_NEW", "OTHER", None),
])
def test_button_payloads_use_rules_only(payload, intent, date_):
    c, t = clf()
    r = c.classify(msg(button_payload=payload))
    assert (r.intent, r.source, r.agreed_date) == (intent, "rules", date_)
    assert t.calls == []


@pytest.mark.parametrize("text", ["STOP", "stop", "Unsubscribe", "please STOP these messages", "Stop calling me",
                                  "msg mat bhejo", "call band karo", "opt out", "don't call me again"])
def test_stop_keywords_any_channel_never_call_llm(text):
    for ch in ("sms", "whatsapp", "email", "voicebot"):
        c, t = clf()
        r = c.classify(msg(text, channel=ch))
        assert r.intent == "STOP" and r.source == "rules" and r.confidence == 1.0
        assert t.calls == []


def test_stop_wins_over_other_rules():
    c, t = clf()
    assert c.classify(msg("galat number hai, STOP")).intent == "STOP"
    assert t.calls == []


# ---- free-text rules ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,intent", [
    ("thoda time chahiye", "NEEDS_TIME"), ("salary ke baad", "NEEDS_TIME"), ("abhi paise nahi hai", "NEEDS_TIME"),
    ("I need some more time please", "NEEDS_TIME"), ("next week pay karunga", "NEEDS_TIME"),
    ("pay kar diya", "CLAIMS_PAID"), ("maine bhar diya hai", "CLAIMS_PAID"), ("already paid yesterday", "CLAIMS_PAID"),
    ("kal hi jama kar diya", "CLAIMS_PAID"),
    ("accident ho gaya hai", "HARDSHIP"), ("papa hospital mein admit hain", "HARDSHIP"), ("meri naukri chali gayi", "HARDSHIP"),
    ("call me", "CALLBACK"), ("kisi agent se baat karni hai", "CALLBACK"),
    ("galat number", "WRONG_PERSON"), ("wrong number, I don't know him", "WRONG_PERSON"),
    ("maine ye loan nahi liya", "DISPUTE"), ("galat amount dikha rahe ho", "DISPUTE"),
])
def test_clear_phrases_resolve_by_rules(text, intent):
    c, t = clf()
    r = c.classify(msg(text))
    assert r.intent == intent and r.source == "rules" and not r.ambiguous
    assert t.calls == []
    assert r.summary.startswith("[whatsapp] ") and len(r.summary) <= 160


@pytest.mark.parametrize("text,date_", [
    ("25 ko kar dunga", "2026-11-25"), ("25 tarikh ko pay karunga", "2026-11-25"), ("kal de dunga", "2026-11-20"),
    ("parson bhar dunga", "2026-11-21"), ("I will pay tomorrow", "2026-11-20"), ("will pay on 2026-12-02", "2026-12-02"),
    ("5 ko dunga", "2026-12-05"),  # day already passed this month -> next month
    ("thoda time chahiye, 28 ko pakka kar dunga", "2026-11-28"), ("25 Nov", "2026-11-25"), ("kal", "2026-11-20"),
    ("I'll pay on 30/11/2026", "2026-11-30"), ("monday ko pay karunga", "2026-11-23"),
])
def test_ptp_with_dates(text, date_):
    c, t = clf()
    r = c.classify(msg(text))
    assert (r.intent, r.agreed_date, r.source) == ("PTP", date_, "rules")
    assert t.calls == []


def test_promise_without_date_is_needs_time_and_negated_today_is_not_a_date():
    c, _ = clf()
    assert c.classify(msg("pay kar dunga")).intent == "NEEDS_TIME"
    r = c.classify(msg("aaj nahi ho payega"))
    assert r.intent == "NEEDS_TIME" and r.agreed_date is None


def test_hardship_with_date_keeps_date_and_claims_paid_drops_it():
    c, _ = clf()
    r = c.classify(msg("hospital me hoon, 22 ko de dunga"))
    assert (r.intent, r.agreed_date) == ("HARDSHIP", "2026-11-22")
    r = c.classify(msg("kal bhar diya"))
    assert (r.intent, r.agreed_date) == ("CLAIMS_PAID", None)


def test_conflict_with_explicit_date_resolved_by_rules_and_flagged():
    c, t = clf()
    r = c.classify(msg("call me, I already paid on 2026-11-15"))
    assert r.source == "rules" and r.ambiguous and r.intent == "CLAIMS_PAID"
    assert t.calls == []


def test_extract_date_and_timezone():
    assert extract_date("25 ko", TODAY)[0].isoformat() == "2026-11-25"
    assert extract_date("parson", TODAY)[0].isoformat() == "2026-11-21"
    assert extract_date("2026-11-25", TODAY) == (TODAY.replace(day=25), True)
    assert extract_date("koi date nahi", TODAY) == (None, False)
    # 20:00 UTC on the 19th is the 20th in India: "kal" = 21st
    utc = datetime(2026, 11, 19, 20, 0, tzinfo=timezone.utc)
    c, _ = clf()
    r = c.classify(InboundMessage("B", "sms", utc, text="kal pay kar dunga"))
    assert r.agreed_date == "2026-11-21"


# ---- LLM path ----------------------------------------------------------------------------------

def test_ambiguous_text_calls_llm_once_with_verified_shape():
    c, t = clf()
    r = c.classify(msg("hmm dekhte hain kya hota hai"))
    assert len(t.calls) == 1
    call = t.calls[0]
    body = call["body"]
    assert call["url"] == "https://api.openai.com/v1/responses"
    assert call["headers"]["Authorization"] == "Bearer sk-test"
    assert call["headers"]["Content-Type"] == "application/json"
    assert body["model"] == "gpt-6-luna"
    assert body["reasoning"] == {"effort": "none"}
    assert "tools" not in body and "temperature" not in body
    assert body["store"] is False
    assert 0 < body["max_output_tokens"] <= 512
    fmt = body["text"]["format"]
    assert fmt["type"] == "json_schema" and fmt["strict"] is True and fmt["name"]
    schema = fmt["schema"]
    assert schema == LLM_INTENT_SCHEMA
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"]) == {"intent", "summary", "ambiguous", "agreed_date"}
    assert schema["properties"]["intent"]["enum"] == list(INTENTS)
    assert schema["properties"]["agreed_date"]["type"] == ["string", "null"]
    assert "BRW-0042" not in json.dumps(body)
    assert r.source == "llm" and r.intent == "NEEDS_TIME" and not r.ambiguous
    assert r.summary == GOOD["summary"]
    assert r.llm_usage["input_tokens"] == 210 and r.llm_usage["output_tokens"] == 38 and r.llm_usage["model"] == "gpt-6-luna"


def test_rule_conflict_without_date_calls_llm_once():
    c, t = clf()
    c.classify(msg("I have paid but please arrange a callback"))
    assert len(t.calls) == 1


def test_llm_ptp_and_commentary_item_is_ignored():
    good = {"intent": "PTP", "summary": "Borrower will pay after Diwali on the 28th.", "ambiguous": False,
            "agreed_date": "2026-11-28"}
    commentary = {"type": "message", "role": "assistant", "phase": "commentary",
                  "content": [{"type": "output_text", "text": "We need output JSON schema. classify."}]}
    t = FakeTransport(body=responses_body(good, extra_items=[commentary]))
    c, _ = clf(t)
    r = c.classify(msg("diwali ke baad dekhta hoon pakka"))
    assert (r.intent, r.agreed_date, r.source) == ("PTP", "2026-11-28", "llm")


def test_llm_ptp_without_date_becomes_needs_time():
    t = FakeTransport(body=responses_body({"intent": "PTP", "summary": "Will pay soon.", "ambiguous": False, "agreed_date": None}))
    c, _ = clf(t)
    r = c.classify(msg("dekhta hoon"))
    assert r.intent == "NEEDS_TIME" and r.ambiguous


def test_redaction_strips_identifiers_before_sending():
    c, t = clf()
    text = ("hmm sir mera number +91 98765 43210, email ravi.k@example.com, loan PL123456, "
            "₹12,500 bheje the UTR 412345678901, rs 300 aur 45000, upi ravi@okaxis. BRW-0042")
    c.classify(msg(text))
    sent = t.calls[0]["body"]["input"]
    for leaked in ("98765", "43210", "ravi.k@example.com", "PL123456", "123456", "12,500", "412345678901",
                   "45000", "300", "ravi@okaxis", "BRW-0042"):
        assert leaked not in sent, leaked
    for ph in ("[PHONE]", "[EMAIL]", "[ACCOUNT]", "[AMOUNT]", "[REF]", "[NUMBER]", "[UPI_ID]"):
        assert ph in sent, ph


def test_redact_unit_and_truncation():
    s = redact("My name is Ravi Kumar, call 9876543210 for loan account 556677889, paying 2026-11-25")
    assert "Ravi" not in s and "9876543210" not in s and "556677889" not in s and "2026-11-25" in s
    assert len(redact("x" * 2000)) == 500


@pytest.mark.parametrize("transport", [
    FakeTransport(body=responses_body("not json at all")),
    FakeTransport(body=responses_body({**GOOD, "intent": "MAYBE_PAY"})),
    FakeTransport(body=responses_body({**GOOD, "summary": "x" * 161})),
    FakeTransport(body=responses_body({**GOOD, "agreed_date": "25-11-2026"})),
    FakeTransport(body=responses_body({**GOOD, "agreed_date": "2026-02-30"})),
    FakeTransport(body=responses_body({**GOOD, "ambiguous": "no"})),
    FakeTransport(body=responses_body({**GOOD, "extra": 1})),
    FakeTransport(body=responses_body({"intent": "OTHER"})),
    FakeTransport(status=500, body={"error": {"message": "server error"}}),
    FakeTransport(status=429, body={"error": {"message": "rate limit"}}),
    FakeTransport(body=b"<html>bad gateway</html>"),
    FakeTransport(body={**responses_body(GOOD), "status": "incomplete",
                        "incomplete_details": {"reason": "max_output_tokens"}}),
    FakeTransport(exc=TimeoutError("timed out")),
    FakeTransport(exc=OSError("connection reset")),
], ids=["not-json", "bad-enum", "long-summary", "bad-date-format", "impossible-date", "non-bool", "extra-key",
        "missing-keys", "http-500", "http-429", "html", "incomplete", "timeout", "oserror"])
def test_invalid_or_failed_llm_falls_back(transport):
    c, t = clf(transport)
    r = c.classify(msg("hmm dekhte hain"))
    assert (r.intent, r.source, r.ambiguous, r.agreed_date) == ("OTHER", "fallback", True, None)
    assert len(t.calls) == 1 and len(r.summary) <= 160


def test_refusal_falls_back_with_usage_recorded():
    body = responses_body(GOOD)
    body["output"][0]["content"] = [{"type": "refusal", "refusal": "I can't help with that."}]
    c, _ = clf(FakeTransport(body=body))
    r = c.classify(msg("hmm"))
    assert r.source == "fallback" and r.llm_usage["total_tokens"] == 248


def test_missing_api_key_falls_back_without_network(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    t = FakeTransport(body=responses_body(GOOD))
    c = IntentClassifier(llm=LunaClient(transport=t))
    r = c.classify(msg("hmm dekhte hain"))
    assert r.source == "fallback" and r.intent == "OTHER" and t.calls == []


def test_api_key_from_env(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
    t = FakeTransport(body=responses_body(GOOD))
    IntentClassifier(llm=LunaClient(transport=t)).classify(msg("hmm"))
    assert t.calls[0]["headers"]["Authorization"] == "Bearer sk-env"


def test_no_llm_configured_falls_back():
    r = IntentClassifier().classify(msg("hmm dekhte hain"))
    assert (r.intent, r.source, r.ambiguous) == ("OTHER", "fallback", True)


def test_client_that_raises_never_reaches_caller():
    class Boom(LunaClient):
        def classify(self, *a, **k):
            raise RuntimeError("bug")
    r = IntentClassifier(llm=Boom(api_key="k")).classify(msg("hmm"))
    assert r.source == "fallback"


# ---- contract alignment ------------------------------------------------------------------------

def test_k13_mapping_uses_real_exception_events():
    assert set(K13_EVENT) == set(INTENTS)
    for intent, ev in K13_EVENT.items():
        assert ev is None or ev in EVENTS, (intent, ev)


def test_validate_llm_output_accepts_good():
    assert validate_llm_output(GOOD) is None
    assert validate_llm_output({**GOOD, "intent": "PTP", "agreed_date": "2026-11-25"}) is None


def test_is_stop_is_not_triggered_by_lowercase_mid_sentence():
    assert not is_stop("salary stop ho gayi hai")
    assert is_stop("STOP") and is_stop("stop")
