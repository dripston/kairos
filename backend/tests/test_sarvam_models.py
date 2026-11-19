"""Sarvam models (prism / sarvam-105b-conversations / deepseek): every test runs WITHOUT network.

Covers: key loading (.env precedence) and masking, fake transports per model, schema violations falling
through, timeouts, the circuit breaker, the call budget, the cache, redaction, safety precedence (prism can
never override a rule safety hit and never auto-STOPs), the voice rule, the CM-03 phrasing validator and
engine wiring, narrative fact checks, the repo secret scan and "no key -> everything off".
"""
from __future__ import annotations

import json
import logging
import re
import socket
import subprocess
from datetime import date, timedelta
from pathlib import Path

import pytest

from kairos_backend.intent_stack import IntentStack
from kairos_backend.sarvam import config as C
from kairos_backend.sarvam.client import (CHAT_PATH, CONVERSATIONS, DEEPSEEK, SYSTEMONE_PATH, CircuitBreaker,
                                          SarvamClient, reset_shared_client, shared_client)
from kairos_backend.sarvam.fixtures import FixtureTransport
from kairos_backend.sarvam.narrate import Narrator, fact_check
from kairos_backend.sarvam.phrasing import Phraser, validate
from kairos_backend.sarvam.prism import (PrismAnswer, PrismClassifier, parse_answers, parse_noul, parse_score,
                                         voice_rule)
from tests.cm03_helpers import B2001, Chat

TODAY = date(2026, 11, 19)
FAKE_KEY = "fake-" + "k3y" * 8                 # never sk_-shaped: the repo secret scan must stay clean
SK_LIKE = "sk" + "_" + "liveLooking" * 2       # built at runtime for the masking test only
REPO = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------------------------
# fake transports
# ---------------------------------------------------------------------------------------------
class Fake:
    """Answers from a function of (path, body dict); records every call."""

    def __init__(self, fn):
        self.fn, self.calls = fn, []

    def __call__(self, url, headers, body, timeout):
        req = json.loads(body.decode("utf-8"))
        self.calls.append({"url": url, "headers": dict(headers), "body": req, "timeout": timeout})
        out = self.fn(url, req)
        if isinstance(out, Exception):
            raise out
        status, data = out
        return status, (data if isinstance(data, bytes) else json.dumps(data).encode("utf-8"))


def prism_reply(label, conf=0.9, stop="no", hardship="no", self_harm="no", abusive="no", choice="intent"):
    return 200, {"answers": {choice: {"answer": label}, "stop_request": {"answer": stop},
                             "hardship": {"answer": hardship}, "self_harm": {"answer": self_harm},
                             "abusive": {"answer": abusive}, "confidence": {"answer": conf}}}


def chat_reply(text):
    return 200, {"model": "x", "choices": [{"message": {"role": "assistant", "content": text}}]}


def client(fn, **kw):
    t = Fake(fn)
    return SarvamClient(api_key=FAKE_KEY, transport=t, **kw), t


def stack_with(fn, **kw):
    c, t = client(fn, **kw)
    return IntentStack(prism=PrismClassifier(c)), t


VAGUE = "dekhte hain kya hota hai"       # rules have nothing; the small model is unsure


# ---------------------------------------------------------------------------------------------
# key loading and masking
# ---------------------------------------------------------------------------------------------
def test_dotenv_parser_quotes_comments_and_no_expansion():
    text = "# models\nSARVAM_API_KEY='abc def'\nexport OTHER=\"x y\"\nPLAIN=v1 # note\nHOME_REF=$HOME/x\nbad line\n"
    d = C.parse_dotenv(text)
    assert d == {"SARVAM_API_KEY": "abc def", "OTHER": "x y", "PLAIN": "v1", "HOME_REF": "$HOME/x"}


def test_key_precedence_env_then_dotenv_var_then_backend_root_home(tmp_path):
    kd, be, root, home = (tmp_path / n for n in ("custom.env", "backend.env", "root.env", "home_key"))
    kd.write_text("SARVAM_API_KEY=from-kairos-dotenv\n")
    be.write_text("SARVAM_API_KEY=from-backend\n")
    root.write_text("SARVAM_API_KEY=from-root\n")
    home.write_text("from-home\n")
    srcs = lambda e: [(lab, p) for lab, p in [("KAIROS_DOTENV", Path(e["KAIROS_DOTENV"])) if e.get("KAIROS_DOTENV") else  # noqa: E731
                                              ("skip", tmp_path / "none"),
                                              ("backend/.env", be), (".env", root), ("~/.kairos/sarvam_api_key", home)]]
    env = {"SARVAM_API_KEY": "from-env", "KAIROS_DOTENV": str(kd)}
    assert C.load_api_key(env, srcs(env)) == ("from-env", "env")
    env = {"KAIROS_DOTENV": str(kd)}
    assert C.load_api_key(env, srcs(env)) == ("from-kairos-dotenv", "KAIROS_DOTENV")
    assert C.load_api_key({}, srcs({})) == ("from-backend", "backend/.env")
    be.write_text("# empty\nSARVAM_API_KEY=\n")
    assert C.load_api_key({}, srcs({})) == ("from-root", ".env")
    root.unlink()
    assert C.load_api_key({}, srcs({})) == ("from-home", "~/.kairos/sarvam_api_key")
    home.unlink()
    assert C.load_api_key({}, srcs({})) == (None, "none")


def test_key_sources_order_includes_kairos_dotenv_first():
    labels = [lab for lab, _ in C.key_sources({"KAIROS_DOTENV": "/x/.env"})]
    assert labels == ["KAIROS_DOTENV", "backend/.env", ".env", "~/.kairos/sarvam_api_key"]


def test_no_key_or_switched_off_means_no_client(monkeypatch):
    reset_shared_client()
    monkeypatch.setattr(C, "key_sources", lambda env=None: [])
    import kairos_backend.sarvam.client as cl
    monkeypatch.setattr(cl, "load_api_key", lambda e: C.load_api_key(e, []))
    assert shared_client({}) is None
    assert shared_client({"SARVAM_API_KEY": FAKE_KEY, "KAIROS_SARVAM_MODELS": "off"}) is None
    c = shared_client({"SARVAM_API_KEY": FAKE_KEY})
    assert c is not None and FAKE_KEY not in repr(c)
    reset_shared_client()
    assert IntentStack.from_env({"KAIROS_SARVAM_MODELS": "off", "SARVAM_API_KEY": FAKE_KEY}).prism is None


def test_suite_runs_with_models_off():
    import os
    assert C.models_switched_off(os.environ)
    assert IntentStack.from_env().prism is None


def test_key_never_in_errors_logs_or_repr(caplog):
    def boom(url, req):
        return RuntimeError(f"connection reset while sending {FAKE_KEY} {SK_LIKE}")
    c, t = client(boom)
    with caplog.at_level(logging.DEBUG, logger="kairos.sarvam"):
        rep = c.systemone("state", {"q": {"type": "noul", "instructions": "x"}})
        rep2 = SarvamClient(api_key=FAKE_KEY, transport=Fake(lambda u, r: (401, {"detail": f"bad key {FAKE_KEY}"}))).chat(
            CONVERSATIONS, [{"role": "user", "content": "hi"}])
    blob = " ".join([str(rep.error), str(rep.meta()), str(rep2.error), str(rep2.meta()), caplog.text, repr(c)])
    assert FAKE_KEY not in blob and SK_LIKE not in blob
    assert rep.error.startswith("transport") and rep2.error == "http_401"
    assert t.calls[0]["headers"]["api-subscription-key"] == FAKE_KEY            # only ever in the header
    assert FAKE_KEY not in json.dumps(t.calls[0]["body"])
    assert C.mask_secret(f"x {SK_LIKE} y", []) == "x sk_*** y"


# ---------------------------------------------------------------------------------------------
# client mechanics
# ---------------------------------------------------------------------------------------------
def test_timeout_falls_through_to_deterministic():
    s, t = stack_with(lambda u, r: socket.timeout("timed out"))
    base = IntentStack().classify(VAGUE, TODAY)
    r = s.classify(VAGUE, TODAY)
    assert r.label == base.label and r.tier_used == base.tier_used == "abstain"
    assert r.evidence["prism"]["error"].startswith("timeout")
    assert t.calls and t.calls[0]["timeout"] == 5.0


def test_circuit_breaker_opens_then_half_opens():
    now = [0.0]
    br = CircuitBreaker(threshold=3, cooldown_s=60, clock=lambda: now[0])
    c, t = client(lambda u, r: (500, {"e": 1}), breaker=br)
    for i in range(5):
        c.systemone(f"s{i}", {})
    assert len(t.calls) == 3 and br.state == "open" and c.stats.skipped_breaker == 2
    now[0] = 61
    assert br.state == "half_open"
    c.systemone("s9", {})
    assert len(t.calls) == 4 and br.state == "open"


def test_budget_cap_and_cache():
    c, t = client(lambda u, r: prism_reply("PTP"), max_calls={"prism": 2})
    a = c.systemone("same", {})
    b = c.systemone("same", {})
    assert a.ok and b.ok and b.cached and len(t.calls) == 1
    c.systemone("other", {})
    r = c.systemone("third", {})
    assert r.error == "budget exhausted" and len(t.calls) == 2


def test_chat_endpoint_body_and_schema():
    c, t = client(lambda u, r: chat_reply("hello"))
    rep = c.chat(DEEPSEEK, [{"role": "user", "content": "x"}], reasoning_effort="low")
    assert rep.ok and rep.data == {"text": "hello"}
    call = t.calls[0]
    assert call["url"].endswith(CHAT_PATH) and call["body"]["reasoning_effort"] == "low"
    assert call["body"]["temperature"] == 0.0 and call["body"]["model"] == DEEPSEEK
    c2, _ = client(lambda u, r: (200, {"choices": []}))
    assert c2.chat(DEEPSEEK, []).error == "schema: no message content"


# ---------------------------------------------------------------------------------------------
# prism parsing (the response shape is not documented: be defensive)
# ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("resp", [
    {"answers": {"intent": {"answer": "PTP"}, "confidence": {"answer": "high"}}},
    {"results": [{"name": "intent", "value": "ptp"}, {"name": "confidence", "score": 0.8}]},
    {"output": {"intent": "PTP", "confidence": 80}},
    {"intent": {"label": "PTP", "confidence": 0.8}},
    {"data": {"answers": {"intent": ["PTP"], "confidence": "0.8"}}},
])
def test_parse_answers_shapes(resp):
    a = parse_answers(resp, "intent", ["PTP", "UNKNOWN"])
    assert a.label == "PTP" and a.error is None and a.confidence == pytest.approx(0.8)


def test_parse_helpers():
    assert [parse_noul(v) for v in ("YES", True, "likely", "no", False, "maybe", None)] == \
        ["yes", "yes", "yes", "no", "no", "unknown", "unknown"]
    assert parse_score(7) == 0.7 and parse_score("very_high") == 0.95 and parse_score("x") is None
    assert parse_answers({"foo": 1}, "intent", ["PTP"]).error.startswith("schema")
    assert parse_answers({"answers": {"intent": "MAYBE", "confidence": 0.9}}, "intent", ["PTP"]).label is None


@pytest.mark.parametrize("bad", [
    (200, {"answers": {"intent": {"answer": "NOT_A_LABEL"}, "confidence": {"answer": 0.99}}}),
    (200, {"unexpected": "shape"}),
    (200, b"not json"),
    (200, {"answers": {"intent": {"answer": "PTP"}}}),          # no confidence
    (503, {"error": "busy"}),
])
def test_schema_violations_fall_through(bad):
    s, _ = stack_with(lambda u, r: bad)
    base = IntentStack().classify(VAGUE, TODAY)
    r = s.classify(VAGUE, TODAY)
    assert (r.label, r.tier_used, r.needs_human) == (base.label, base.tier_used, base.needs_human)


def test_prism_answers_when_confident_and_is_recorded():
    s, t = stack_with(lambda u, r: prism_reply("NEEDS_TIME", 0.92))
    r = s.classify(VAGUE, TODAY)
    assert r.label == "NEEDS_TIME" and r.tier_used == "prism" and r.confidence == 0.85   # capped
    assert r.evidence["prism"]["model"] == "sarvam-prism" and "latency_ms" in r.evidence["prism"]
    q = t.calls[0]["body"]
    assert t.calls[0]["url"].endswith(SYSTEMONE_PATH) and q["model"] == "sarvam-prism"
    assert q["questions"]["intent"]["type"] == "choice" and "PTP" in q["questions"]["intent"]["criteria"]
    assert {k: q["questions"][k]["type"] for k in ("stop_request", "hardship", "self_harm", "abusive", "confidence")} == \
        {"stop_request": "noul", "hardship": "noul", "self_harm": "noul", "abusive": "noul", "confidence": "score"}


def test_prism_low_confidence_or_unknown_falls_through():
    for rep in (prism_reply("NEEDS_TIME", 0.5), prism_reply("UNKNOWN", 0.99)):
        s, _ = stack_with(lambda u, r, rep=rep: rep)
        assert s.classify(VAGUE, TODAY).tier_used == "abstain"


def test_prism_must_pick_among_rule_candidates():
    text = "maine pay kar diya, ye galat amount hai"      # rules: ALREADY_PAID + DISPUTE candidates
    s, _ = stack_with(lambda u, r: prism_reply("GREETING", 0.95))
    s.model = None                                        # no small model: rules -> prism directly
    r = s.classify(text, TODAY)
    assert r.label != "GREETING"


def test_redaction_before_prism():
    s, t = stack_with(lambda u, r: prism_reply("UNKNOWN", 0.2))
    s.classify("Mr Sharma here, call 9876543210 or mail ravi.k@example.com, loan PL100328, dekhte hain", TODAY,
               known_names=["B-1042"])
    state = t.calls[0]["body"]["state"]
    for leak in ("9876543210", "ravi.k@example.com", "Sharma", "PL100328"):
        assert leak not in state
    assert not re.search(r"\d", state.split("Message:", 1)[1])


# ---------------------------------------------------------------------------------------------
# safety precedence
# ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("text,label", [("STOP sending me messages", "STOP"),
                                        ("papa hospital me admit hain", "HARDSHIP"),
                                        ("khudkushi kar lunga", "HARDSHIP"), ("tum log chor ho saale", "ABUSIVE")])
def test_rule_safety_hits_never_reach_prism(text, label):
    s, t = stack_with(lambda u, r: prism_reply("GREETING", 0.99))
    r = s.classify(text, TODAY)
    assert r.label == label and r.tier_used == "rules" and t.calls == []


def test_prism_stop_alone_never_auto_stops():
    s, _ = stack_with(lambda u, r: prism_reply("STOP", 0.95, stop="yes"))
    r = s.classify(VAGUE, TODAY)
    assert r.label == "UNKNOWN" and r.needs_human and "stop_unconfirmed" in r.safety_flags
    assert r.evidence["safety_unconfirmed_by_rules"] is True and r.legacy_intent == "OTHER"


@pytest.mark.parametrize("kw,label,flag", [({"hardship": "yes"}, "HARDSHIP", "hardship_unconfirmed"),
                                           ({"self_harm": "yes"}, "HARDSHIP", "self_harm_unconfirmed"),
                                           ({"abusive": "yes"}, "ABUSIVE", "abusive_unconfirmed")])
def test_prism_safety_yes_goes_to_a_human(kw, label, flag):
    s, _ = stack_with(lambda u, r: prism_reply("GREETING", 0.95, **kw))
    r = s.classify(VAGUE, TODAY)
    assert r.label == label and r.needs_human and flag in r.safety_flags


def test_models_off_stack_output_unchanged_on_gold():
    rows = [json.loads(x) for x in (Path(__file__).parent / "data" / "intent_gold.jsonl").read_text().splitlines() if x]
    a = [IntentStack().classify(r["text"], TODAY).to_dict() for r in rows]
    b = [IntentStack(prism=None).classify(r["text"], TODAY).to_dict() for r in rows]
    assert a == b and not any("prism" in d["evidence"] for d in a)


# ---------------------------------------------------------------------------------------------
# voice rule
# ---------------------------------------------------------------------------------------------
def ans(label, conf, **kw):
    return PrismAnswer(label, conf, **kw)


def test_voice_rule_limits():
    assert voice_rule("PTP", 0.6, ans("PTP", 0.95)).new_confidence == 0.85            # +0.25 max
    assert voice_rule("PTP", 0.6, ans("PTP", 0.95)).action == "UPGRADE"
    assert voice_rule("PTP", 0.3, ans("PTP", 0.95)).action == "NONE"                  # bot below floor
    assert voice_rule("PTP", 0.6, ans("PTP", 0.7)).action == "NONE"                   # prism not sure enough
    assert voice_rule("PTP", 0.6, ans("ALREADY_PAID", 0.95)).action == "REVIEW"       # disagreement
    assert voice_rule("STOP_REQUESTED", 0.4, ans("STOP_REQUESTED", 0.99)).action == "NONE"   # never upgraded
    assert voice_rule("WRONG_PERSON", 0.6, ans("WRONG_PERSON", 0.99)).action == "NONE"
    r = voice_rule("PTP", 0.6, ans("PTP", 0.95, hardship="yes"))
    assert r.action == "REVIEW" and "hardship" in r.reason                              # safety only adds review
    assert voice_rule("HARDSHIP", 0.45, ans("HARDSHIP", 0.9, hardship="yes")).action == "UPGRADE"
    assert voice_rule("PTP", 0.6, PrismAnswer(None, None, error="schema: x")).action == "NONE"


@pytest.fixture
def venv(policy, tmp_path):
    from tests.test_voice_engine import VOICE_TPL, D
    from kairos_backend.contracts import RecordingSink
    from kairos_backend.executor import Executor, FakeProvider
    from kairos_backend.learner import BetaBernoulliLearner
    from kairos_backend.ledger import Ledger
    from kairos_backend.outcomes import OutcomeFinalizer
    from kairos_backend.voice.ingest import VoiceIngester

    def make(fn):
        lg = Ledger(policy, tmp_path / f"v{len(made)}.db")
        lg.upsert_campaign("C1", budget_per_case_paise=100_000, start_date=D, end_date=D + timedelta(days=20))
        lg.upsert_template(VOICE_TPL, "voicebot", "hi")
        lg.start_day(D)
        fin = OutcomeFinalizer(lg, BetaBernoulliLearner(lg))
        c, t = client(fn)

        class E:
            pass
        e = E()
        e.lg, e.fin, e.policy, e.t = lg, fin, policy, t
        e.ing = VoiceIngester(lg, finalizer=fin, sink=RecordingSink(), prism=PrismClassifier(c))
        e.ex = Executor(lg, {"voicebot": FakeProvider("voicebot")})
        made.append(lg)
        return e
    made = []
    yield make
    for lg in made:
        lg.close()


def test_voice_low_confidence_ptp_upgraded_when_prism_agrees(venv):
    from tests.test_voice_engine import D, payload, place_call
    e = venv(lambda u, r: prism_reply("PTP", 0.95, choice="disposition"))
    _, iid = place_call(e, "B-V1", dpd=12)
    promise = (D + timedelta(days=3)).isoformat()
    r = e.ing.ingest(payload("c-v1", iid, "PTP", 0.65, ptp_date=promise, call_summary="Borrower promised to pay on date"))
    assert r["action"] == "PTP_HOLD" and r["confidence"] == 0.9
    ma = r["envelope"]["payload"]["model_assist"]
    assert ma["action"] == "UPGRADE" and ma["bot_confidence"] == 0.65 and ma["model"] == "sarvam-prism"
    assert "latency_ms" in ma and "version" in ma


def test_voice_prism_disagreement_routes_to_review_and_never_acts(venv):
    from tests.test_voice_engine import D, payload, place_call
    e = venv(lambda u, r: prism_reply("ALREADY_PAID", 0.95, choice="disposition"))
    _, iid = place_call(e, "B-V2", dpd=12)
    r = e.ing.ingest(payload("c-v2", iid, "PTP", 0.65, ptp_date=(D + timedelta(days=3)).isoformat(),
                             call_summary="Borrower said something about payment"))
    assert r["effective_disposition"] == "NEEDS_TIME_NO_DATE" and r["action"] == "DOWNGRADE_NEEDS_TIME_NO_DATE"
    assert "review_ticket_id" in r["effects"]


def test_voice_low_confidence_stop_keeps_supervisor_hold(venv):
    from tests.test_voice_engine import payload, place_call
    e = venv(lambda u, r: prism_reply("STOP_REQUESTED", 0.99, stop="yes", choice="disposition"))
    _, iid = place_call(e, "B-V3", dpd=12)
    r = e.ing.ingest(payload("c-v3", iid, "STOP_REQUESTED", 0.45, call_summary="Borrower asked not to call"))
    assert r["action"] == "SUPERVISOR_HOLD_AND_REVIEW" and r["confidence"] == 0.45


def test_voice_high_confidence_or_prism_down_never_calls_or_changes(venv):
    from tests.test_voice_engine import D, payload, place_call
    e = venv(lambda u, r: socket.timeout("timed out"))
    _, iid = place_call(e, "B-V4", dpd=12)
    r = e.ing.ingest(payload("c-v4", iid, "PTP", 0.65, ptp_date=(D + timedelta(days=3)).isoformat(),
                             call_summary="Borrower promised"))
    assert r["action"] == "DOWNGRADE_NEEDS_TIME_NO_DATE" and r["confidence"] == 0.65
    _, iid2 = place_call(e, "B-V5", dpd=12, hh=11)
    n = len(e.t.calls)
    e.ing.ingest(payload("c-v5", iid2, "PTP", 0.9, ptp_date=(D + timedelta(days=3)).isoformat(), call_summary="x"))
    assert len(e.t.calls) == n


# ---------------------------------------------------------------------------------------------
# CM-03 phrasing validator + engine
# ---------------------------------------------------------------------------------------------
HARD = ("I am sorry to hear about your situation, and thank you for telling us. I have paused reminders for now, and "
        "a member of our team will reach out to discuss the options available to you.")
CLAR = ("Sorry, I did not get that. Reply 1 Balance & due date, 2 Payment link, 3 I have already paid, 4 I need more "
        "time, 5 Statement / NOC, 6 Talk to a person.")


@pytest.mark.parametrize("cand,why", [
    (HARD + " Please pay ₹5,000.", "digits_added"),
    (HARD + " Pay in rupees today.", "amount"),
    (HARD + " Otherwise legal action follows.", "forbidden_words"),
    (HARD + " We will waive your fee.", "promise"),
    (HARD + " https://x.example/p", "link_added"),
    (HARD + " Kal tak.", "date"),
    ("Sorry. Our team will call. Reminders are paused. You can pay via the app whenever.", "collection_ask_in_hardship"),
    ("Ok.", "length"),
    (HARD * 3, "length"),
    ("Thanks for reaching out, our team will reach out on the options available to you, take care of yourself now.",
     "intent_changed"),
    ("```" + HARD, "format"),
    ("आपकी स्थिति जानकर दुख हुआ। रिमाइंडर रोक दिए हैं, हमारी टीम संपर्क करेगी और विकल्प बताएगी।", "wrong_script"),
])
def test_validator_rejections(cand, why):
    assert validate(cand, HARD, "HARDSHIP", "en", True) == why


def test_validator_accepts_faithful_rephrasings():
    hing = ("Yeh sunkar dukh hua, batane ke liye shukriya. Abhi ke liye reminders rok diye hain, aur hamari team "
            "aapse options discuss karne ke liye contact karegi.")
    assert validate(hing, HARD, "HARDSHIP", "en", True) is None
    clar = "Sorry, samajh nahi aaya. Reply karein: 1 Balance & due date, 2 Payment link, 3 Already paid, 4 More time, 5 Statement / NOC, 6 Person se baat."
    assert validate(clar, CLAR, "CLARIFY", "en", True) is None
    assert validate(clar.replace("6 Person se baat.", ""), CLAR, "CLARIFY", "en", True) == "digits_dropped"
    otp = "Please reply with the 6-digit code sent to your registered WhatsApp number. Reply RESEND for a new code."
    assert validate("Kripya registered WhatsApp number par aaya 6-digit code bhejein. Naya code chahiye to RESEND likhein.",
                    otp, "OTP_PROMPT", "en", False) is None
    assert validate("Kripya 6-digit code bhejein, apna balance jaanne ke liye. Naya code: RESEND.", otp, "OTP_PROMPT",
                    "en", False) == "account_detail_before_verification"
    assert validate("Kripya 6-digit code bhejein. Naya code ke liye reply karein.", otp, "OTP_PROMPT", "en",
                    False) == "keyword_dropped"


def test_phraser_eligibility_pre_verify_only_identity_prompts():
    assert Phraser.eligible("OTP_PROMPT", False) and Phraser.eligible("ASK_NAME", False)
    for k in ("HARDSHIP", "CALLBACK", "BALANCE", "PAY_LINK", "PTP_CONFIRM", "WRONG_NUMBER", "STOP_ACK"):
        assert not Phraser.eligible(k, False)
    for k in ("BALANCE", "PAY_LINK", "PTP_CONFIRM", "RECEIPT", "OTP_WRONG", "STOP_ACK", "FRAUD_REFUSE"):
        assert not Phraser.eligible(k, True)
    assert Phraser.eligible("HARDSHIP", True) and Phraser.eligible("CLARIFY", True)


def test_engine_phrasing_accepted_and_recorded(policy, tmp_path):
    hing = ("Yeh sunkar dukh hua, batane ke liye shukriya. Abhi ke liye reminders rok diye hain, aur hamari team "
            "aapse options discuss karne ke liye contact karegi.")
    c, t = client(lambda u, r: chat_reply(hing))
    chat = Chat(policy, tmp_path)
    chat.engine.phraser = Phraser(c)
    chat.verify(B2001)
    r = chat.say(B2001, "I lost my job last month")
    assert r["reply_keys"] == ["HARDSHIP"] and r["replies"][0] == hing and r["handoff_reason"] == "HARDSHIP"
    reply = [e for e in chat.engine.timeline(r["conversation_id"]) if e["kind"] == "REPLY"][-1]
    assert reply["payload"]["phrasing"]["outcome"] == "ACCEPTED" and reply["payload"]["phrasing"]["model"] == CONVERSATIONS
    sent = [b for call in t.calls for m in call["body"]["messages"] for b in [m["content"]]]
    assert not any("lost my job" in s for s in sent)        # the borrower's text never reaches the model


def test_engine_phrasing_rejected_sends_verbatim_and_no_disclosure(policy, tmp_path):
    c, t = client(lambda u, r: chat_reply(json.loads(r["messages"][-1]["content"])["approved_line"] + " Pay ₹4,999 now."))
    chat = Chat(policy, tmp_path)
    chat.engine.phraser = Phraser(c)
    r = chat.say(B2001, "what is my balance?")             # pre-verify: GREET_VERIFY (identity prompt)
    assert r["reply_keys"] == ["GREET_VERIFY"] and "₹" not in r["replies"][0]
    chat.verify(B2001)
    r = chat.say(B2001, "I lost my job last month")
    assert "₹" not in r["replies"][0] and r["replies"][0].startswith("I am sorry")
    s = chat.engine.phraser.summary()
    assert s["accepted"] == 0 and s["rejected_by_validator"] >= 2 and s["validator_rejection_rate"] == 1.0


def test_engine_pre_verify_hardship_never_phrased(policy, tmp_path):
    c, t = client(lambda u, r: chat_reply("x"))
    chat = Chat(policy, tmp_path)
    chat.engine.phraser = Phraser(c)
    r = chat.say(B2001, "I lost my job last month")        # K10 before verification: HARDSHIP verbatim
    assert "HARDSHIP" in r["reply_keys"]
    asked = [json.loads(call["body"]["messages"][-1]["content"])["approved_line"] for call in t.calls]
    assert not any("sorry" in a.lower() for a in asked)


# ---------------------------------------------------------------------------------------------
# deepseek narratives
# ---------------------------------------------------------------------------------------------
def test_fact_check():
    facts = json.dumps({"ticket_id": "TK-1", "borrower_id": "B-1", "count": 3, "day": "2026-11-20"})
    ok = "Case TK-1 for B-1 has 3 events on 2026-11-20."
    assert fact_check(ok, facts, ["TK-1", "B-1"], 300) is None
    assert fact_check(ok + " 37 more.", facts, ["TK-1"], 300) == "number_not_in_facts"
    assert fact_check(ok + " Mail a@b.co", facts, ["TK-1"], 300) == "pii"
    assert fact_check("Case for B-1.", facts, ["TK-1", "B-1"], 300) == "missing_key_field"
    assert fact_check(ok + " We recommend a waiver.", facts, ["TK-1"], 300) == "decision_language"
    assert fact_check(ok, facts, [], 10) == "length"


def test_narrator_labels_ai_and_falls_back():
    rows = [{"ts": "2026-11-20T10:00", "actor": "system", "type": "GATE", "decision": "BLOCK", "rule": "K01.CAP",
             "reason": "cap reached", "policy_version": "1.1.0"}]
    good, _ = client(lambda u, r: chat_reply("The touch was blocked (BLOCK) under rule K01.CAP because the cap was reached."))
    n = Narrator(good).explain_decision(rows)
    assert n.ai_generated and n.label.startswith("AI-generated (deepseekv4.1-flash-1017)") and n.meta["model"] == DEEPSEEK
    bad, _ = client(lambda u, r: chat_reply("Blocked under K01.CAP; 12 other borrowers too."))
    n = Narrator(bad).explain_decision(rows)
    assert not n.ai_generated and n.reason == "number_not_in_facts" and n.text.startswith("Latest decision: BLOCK")
    n = Narrator(None).explain_decision(rows)
    assert not n.ai_generated and n.reason == "NO_MODEL"


def test_narrator_input_is_scrubbed():
    c, t = client(lambda u, r: chat_reply("x"))
    Narrator(c).case_summary({"ticket_id": "TK-9", "borrower_id": "B-9", "type": "HARDSHIP",
                              "summary_line_1": "caller +91 98765 43210 mail ravi@example.com loan PL100328"}, [])
    body = json.dumps(t.calls[0]["body"])
    assert "98765" not in body and "ravi@example.com" not in body and "PL100328" not in body


def test_k05_requires_ids_and_intent():
    lines = ["Channel: SMS T01 sent 19 Nov 10:30, delivered [T-1042-0001, evt-1].",
             "Intent: NEEDS_TIME (whatsapp) [evt-2]."]
    c, _ = client(lambda u, r: chat_reply("An SMS was delivered [T-1042-0001, evt-1]; the borrower asked for time."))
    n = Narrator(c).k05_summary(lines)
    assert not n.ai_generated and n.reason == "missing_key_field"


# ---------------------------------------------------------------------------------------------
# stand-in fixture transport: injected bad outputs are always caught
# ---------------------------------------------------------------------------------------------
def test_fixture_transport_injected_bad_are_caught():
    ft = FixtureTransport(recorded={}, bad_every=2)
    c = SarvamClient(api_key=FAKE_KEY, transport=ft, live=False)
    p = Phraser(c)
    for line in (HARD, HARD.replace("now", "today"), HARD.replace("you.", "you all."), HARD + " Take care."):
        p.phrase("HARDSHIP", line, "en", False, True)
    s = p.summary()
    injected = sum(1 for x in ft.log if x["injected_bad"])
    assert injected == 2 and s["rejected_by_validator"] == 2 and s["accepted"] == 2


# ---------------------------------------------------------------------------------------------
# repo secret scan
# ---------------------------------------------------------------------------------------------
# The requested pattern sk_[A-Za-z0-9_]{10,}, anchored so identifiers that merely END in "sk_" (risk_score_...,
# task_...) do not match.
SECRET = re.compile(r"(?<![A-Za-z0-9])sk_[A-Za-z0-9_]{10,}")


def _repo_files():
    try:
        out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=REPO,
                             capture_output=True, text=True, check=True).stdout.splitlines()
        return [REPO / p for p in out]
    except Exception:
        skip = {".git", ".venv", "node_modules", "__pycache__", "var", "data"}
        return [p for p in REPO.rglob("*") if p.is_file() and not (set(p.parts) & skip)]


def test_repo_has_no_sarvam_style_secrets():
    hits = []
    for p in _repo_files():
        if not p.is_file() or p.stat().st_size > 5_000_000:
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if SECRET.search(text):
            hits.append(str(p.relative_to(REPO)))
    assert hits == [], f"secret-looking tokens in: {hits}"


def test_no_dotenv_file_is_tracked_and_env_is_ignored():
    try:
        tracked = subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True).stdout
    except Exception:
        pytest.skip("git not available")
    assert not [p for p in tracked.splitlines() if Path(p).name == ".env"]
    for p in ("backend/.env", ".env", "crm/.env"):
        r = subprocess.run(["git", "check-ignore", "-q", p], cwd=REPO)
        assert r.returncode == 0, f"{p} is not git-ignored"
