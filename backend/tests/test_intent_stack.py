"""K09 tiered intent stack: rules -> small model -> local LLM -> cloud LLM -> abstain."""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from kairos_backend import intent_stack as S
from kairos_backend.intent import IntentClassifier, InboundMessage, LunaClient
from kairos_backend.intent_corpus import CALIBRATION_SEED, TEST_SEED, TRAIN_SEED, generate, train_model
from kairos_backend.intent_stack import (
    LABELS, LOCAL_LLM_SCHEMA, IntentStack, NaiveBayesModel, OllamaClient, classify_reply, extract_entities,
    is_meaningful_reply, redact_for_llm, validate_local_output,
)

TODAY = date(2026, 11, 19)  # Thursday
GOLD = Path(__file__).parent / "data" / "intent_gold.jsonl"


# ---- fakes ---------------------------------------------------------------------------------------

class FakeModel:
    """Tier-2 stand-in: fixed probabilities, counts calls."""

    model_version = "fake-1"

    def __init__(self, probs=None):
        self.probs = probs or {"PTP": 0.99}
        self.calls = 0

    def predict_proba(self, text):
        self.calls += 1
        out = {lab: 0.0 for lab in LABELS}
        rest = 1.0 - sum(self.probs.values())
        out.update(self.probs)
        out["UNKNOWN"] += max(0.0, rest)
        return out

    def top_features(self, text, a, b, n=5):
        return ["w:fake"]


class FakeHttp:
    """Ollama transport: GET /api/tags and POST /api/chat."""

    def __init__(self, tags=("qwen2.5:3b",), chat=None, status=200, exc=None, tags_status=200):
        self.tags, self.chat, self.status, self.exc, self.tags_status = tags, chat, status, exc, tags_status
        self.calls = []

    def __call__(self, url, headers, body, timeout):
        self.calls.append({"url": url, "body": json.loads(body.decode()) if body else None, "timeout": timeout})
        if url.endswith("/api/tags"):
            if self.tags is None:
                raise ConnectionRefusedError("no server")
            return self.tags_status, json.dumps({"models": [{"name": t} for t in self.tags]}).encode()
        if self.exc:
            raise self.exc
        content = self.chat if isinstance(self.chat, str) else json.dumps(self.chat)
        return self.status, json.dumps({"model": "qwen2.5:3b", "message": {"role": "assistant", "content": content},
                                        "done": True}).encode()

    @property
    def chat_calls(self):
        return [c for c in self.calls if c["url"].endswith("/api/chat")]


class FakeLunaTransport:
    def __init__(self, obj):
        self.obj, self.calls = obj, []

    def __call__(self, url, headers, body, timeout):
        self.calls.append(json.loads(body.decode()))
        out = {"status": "completed", "model": "gpt-6-luna", "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
               "output": [{"type": "message", "phase": "final_answer",
                           "content": [{"type": "output_text", "text": json.dumps(self.obj)}]}]}
        return 200, json.dumps(out).encode()


def ollama(chat, **kw):
    t = FakeHttp(chat=chat, **kw)
    return OllamaClient(model="qwen2.5:3b", host="http://127.0.0.1:11434", transport=t, env={}), t


def luna(obj, key="sk-test"):
    t = FakeLunaTransport(obj)
    return LunaClient(api_key=key, transport=t, env={}), t


VAGUE = "hmm dekhte hain kya hota hai"   # no rule fires; real model abstains / fake model decides


def gold_rows():
    with open(GOLD, encoding="utf-8") as fh:
        return [json.loads(x) for x in fh if x.strip()]


# ---- determinism ---------------------------------------------------------------------------------

def test_same_input_same_output_across_instances_and_calls():
    msgs = [r["text"] for r in gold_rows()]
    a = [IntentStack().classify(m, TODAY).to_dict() for m in msgs]
    b = [IntentStack().classify(m, TODAY).to_dict() for m in msgs]
    s = IntentStack()
    c = [s.classify(m, TODAY).to_dict() for m in msgs] + [s.classify(m, TODAY).to_dict() for m in msgs]
    assert a == b == c[:len(msgs)] == c[len(msgs):]


def test_cache_is_keyed_by_normalized_text_and_day():
    s = IntentStack(model=FakeModel())
    r1 = s.classify("Kal  de dunga", TODAY)
    r2 = s.classify("  kal de DUNGA ", TODAY)
    assert r1 is r2                              # same normalised text -> the cached result
    assert s.classify("kal de dunga", TODAY + timedelta(days=1)).entities["date"] == "2026-11-21"
    assert S.text_hash("A  b", TODAY) == S.text_hash("a b", TODAY) != S.text_hash("a b", TODAY + timedelta(days=1))


def test_llm_tiers_are_called_once_per_normalized_text():
    local, t = ollama({"label": "NEEDS_TIME", "confidence": 0.9})
    s = IntentStack(model=FakeModel({"PTP": 0.3}), local_llm=local)
    for text in (VAGUE, VAGUE.upper(), "  " + VAGUE):
        assert s.classify(text, TODAY).label == "NEEDS_TIME"
    assert len(t.chat_calls) == 1


def test_model_file_has_version_seed_and_reproduces_byte_for_byte():
    data = json.loads(S.MODEL_PATH.read_text(encoding="utf-8"))
    assert data["version"] and data["seed"] == TRAIN_SEED and data["calibration_seed"] == CALIBRATION_SEED
    assert data["corpus_size"] >= 3000 and data["labels"] == list(LABELS)
    assert S.MODEL_PATH.stat().st_size < 400_000
    again = train_model()
    assert again["weights_sha256"] == data["weights_sha256"] and again["temperature"] == data["temperature"]


def test_model_probabilities_are_calibrated_distributions():
    m = NaiveBayesModel.load()
    p = m.predict_proba("kal pakka de dunga")
    assert abs(sum(p.values()) - 1) < 1e-9 and set(p) == set(LABELS)
    assert max(p, key=p.get) == "PTP"
    assert m.model_version.startswith(m.version) and f"seed{TRAIN_SEED}" in m.model_version


def test_corpus_is_seeded_multilingual_and_test_seed_differs():
    a, b = generate(TRAIN_SEED, 200), generate(TRAIN_SEED, 200)
    assert a == b and len(a) >= 3000
    assert {r["lang"] for r in a} >= {"en", "hi", "hing"}
    assert {r["label"] for r in a} == set(LABELS)
    assert any(re.search(r"[ऀ-ॿ]", r["text"]) for r in a)
    assert TEST_SEED != TRAIN_SEED != CALIBRATION_SEED
    assert generate(TEST_SEED, 50) != generate(TRAIN_SEED, 50)


# ---- safety precedence ---------------------------------------------------------------------------

SAFETY_CASES = [
    ("STOP", "STOP"), ("please stop sending these messages", "STOP"), ("message mat bhejo", "STOP"),
    ("मुझे मैसेज मत भेजो", "STOP"), ("unsubscribe", "STOP"), ("call band karo", "STOP"),
    ("papa hospital me admit hai", "HARDSHIP"), ("meri naukri chali gayi", "HARDSHIP"), ("माँ अस्पताल में हैं", "HARDSHIP"),
    ("I lost my job last month", "HARDSHIP"), ("suicide kar lunga", "HARDSHIP"), ("मैं आत्महत्या कर लूंगा", "HARDSHIP"),
    ("I want to end my life", "HARDSHIP"), ("tum log chor ho saale", "ABUSIVE"), ("you bloody idiots", "ABUSIVE"),
]


@pytest.mark.parametrize("text,label", SAFETY_CASES)
def test_no_lower_tier_overrides_a_safety_hit(text, label):
    model = FakeModel({"PAY_NOW": 0.999})
    local, lt = ollama({"label": "PAY_NOW", "confidence": 1.0})
    cloud, ct = luna({"intent": "PTP", "summary": "Will pay.", "ambiguous": False, "agreed_date": "2026-11-20"})
    r = IntentStack(model=model, local_llm=local, cloud_llm=cloud).classify(text, TODAY)
    assert (r.label, r.tier_used) == (label, "rules")
    assert model.calls == 0 and lt.chat_calls == [] and ct.calls == []
    assert r.evidence["escalated_to"] == []
    if label in ("HARDSHIP", "ABUSIVE"):
        assert r.needs_human


def test_self_harm_is_flagged_and_needs_human():
    r = IntentStack().classify("jeene ka mann nahi karta, khudkushi kar lunga", TODAY)
    assert r.label == "HARDSHIP" and r.needs_human and "self_harm" in r.safety_flags and r.confidence == 1.0
    assert r.evidence["hardship_kind"] == "self-harm"


def test_stop_beats_abuse_and_hardship_and_keeps_flags():
    r = IntentStack().classify("STOP calling me you idiots, I am in hospital", TODAY)
    assert r.label == "STOP" and set(r.safety_flags) >= {"stop", "abusive"}


def test_salary_stop_is_hardship_not_opt_out():
    assert IntentStack().classify("salary stop ho gayi hai", TODAY).label == "HARDSHIP"


def test_gold_safety_recall_is_complete():
    s = IntentStack()
    for row in gold_rows():
        if row["label"] in ("STOP", "HARDSHIP"):
            assert s.classify(row["text"], TODAY).label == row["label"], row


def test_model_only_safety_label_goes_to_a_human():
    r = IntentStack(model=FakeModel({"HARDSHIP": 0.95})).classify(VAGUE, TODAY)
    assert r.label == "HARDSHIP" and r.tier_used == "small_model" and r.needs_human
    assert r.evidence["safety_unconfirmed_by_rules"] is True


# ---- tiers and abstain ---------------------------------------------------------------------------

def test_rules_answer_clear_messages_without_any_model():
    model = FakeModel({"UNKNOWN": 1.0})
    r = IntentStack(model=model).classify("link bhejo", TODAY)
    assert (r.label, r.tier_used) == ("LINK_REQUEST", "rules") and model.calls == 0
    assert r.evidence["rule_ids"] and all(rid in S.RULE_IDS for rid in r.evidence["rule_ids"])


def test_rule_conflict_is_resolved_by_model_among_candidates_only():
    model = FakeModel({"GREETING": 0.6, "ALREADY_PAID": 0.25, "DISPUTE": 0.15})
    r = IntentStack(model=model).classify("maine pay kar diya, ye galat amount hai", TODAY)
    assert r.evidence["rule_candidates"] == ["ALREADY_PAID", "DISPUTE"]
    assert (r.label, r.tier_used) == ("ALREADY_PAID", "small_model")


def test_confident_model_answers_and_unsure_model_escalates():
    r = IntentStack(model=FakeModel({"NEEDS_TIME": 0.9})).classify(VAGUE, TODAY)
    assert (r.label, r.tier_used) == ("NEEDS_TIME", "small_model") and r.evidence["model_version"] == "fake-1"
    r = IntentStack(model=FakeModel({"NEEDS_TIME": 0.5, "PTP": 0.4})).classify(VAGUE, TODAY)
    assert (r.label, r.tier_used, r.needs_human) == ("UNKNOWN", "abstain", True)


def test_abstain_when_every_tier_is_unsure():
    local, lt = ollama({"label": "UNKNOWN", "confidence": 0.9})
    cloud, ct = luna({"intent": "OTHER", "summary": "Unclear reply.", "ambiguous": True, "agreed_date": None})
    r = IntentStack(model=FakeModel({"PTP": 0.4}), local_llm=local, cloud_llm=cloud).classify(VAGUE, TODAY)
    assert (r.label, r.tier_used, r.needs_human) == ("UNKNOWN", "abstain", True)
    assert r.evidence["escalated_to"] == ["small_model", "local_llm", "cloud_llm"]
    assert len(lt.chat_calls) == 1 and len(ct.calls) == 1


def test_real_model_abstains_on_vague_text_offline():
    r = IntentStack().classify(VAGUE, TODAY)
    assert r.label == "UNKNOWN" and r.needs_human and r.tier_used == "abstain"


def test_local_llm_only_below_threshold_and_cloud_only_with_key(monkeypatch):
    local, lt = ollama({"label": "CALLBACK", "confidence": 0.9})
    IntentStack(model=FakeModel({"NEEDS_TIME": 0.9}), local_llm=local).classify(VAGUE, TODAY)
    assert lt.chat_calls == []                                    # tier 2 was sure
    r = IntentStack(model=FakeModel({"NEEDS_TIME": 0.5}), local_llm=local).classify(VAGUE, TODAY)
    assert (r.label, r.tier_used) == ("CALLBACK", "local_llm") and r.confidence <= 0.85
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    cloud, ct = luna({"intent": "DISPUTE", "summary": "Contests amount.", "ambiguous": False, "agreed_date": None}, key=None)
    r = IntentStack(model=FakeModel({"PTP": 0.4}), cloud_llm=cloud).classify(VAGUE, TODAY)
    assert r.tier_used == "abstain" and ct.calls == []            # no key -> never called
    cloud, ct = luna({"intent": "DISPUTE", "summary": "Contests amount.", "ambiguous": False, "agreed_date": None})
    r = IntentStack(model=FakeModel({"PTP": 0.4}), cloud_llm=cloud).classify("sir 98765 43210 " + VAGUE, TODAY)
    assert (r.label, r.tier_used) == ("DISPUTE", "cloud_llm")
    body = ct.calls[0]
    assert body["model"] == "gpt-6-luna" and body["reasoning"] == {"effort": "none"} and "tools" not in body
    assert not re.search(r"\d{3}", body["input"].split("Borrower message (redacted): ")[1])


def test_from_env_builds_optional_tiers_only_when_configured():
    s = IntentStack.from_env(env={})
    assert s.local_llm is None and s.cloud_llm is None
    s = IntentStack.from_env(env={"KAIROS_LOCAL_LLM_MODEL": "llama3.2:3b", "OLLAMA_HOST": "localhost:11434", "OPENAI_API_KEY": "k"})
    assert s.local_llm.model == "llama3.2:3b" and s.local_llm.host == "http://localhost:11434" and s.cloud_llm is not None


# ---- Ollama adapter ------------------------------------------------------------------------------

def test_ollama_request_shape_is_deterministic_and_schema_bound():
    local, t = ollama({"label": "PTP", "confidence": 0.8})
    rep = local.classify(redact_for_llm("Mr Sharma here, 9876543210, will pay 25 ko"))
    assert rep.data == {"label": "PTP", "confidence": 0.8}
    tags, chat = t.calls[0], t.chat_calls[0]
    assert tags["url"].endswith("/api/tags") and tags["body"] is None and tags["timeout"] < 1
    body = chat["body"]
    assert body["model"] == "qwen2.5:3b" and body["stream"] is False
    assert body["format"] == LOCAL_LLM_SCHEMA and body["format"]["additionalProperties"] is False
    assert body["options"]["temperature"] == 0 and isinstance(body["options"]["seed"], int)
    assert chat["timeout"] <= 5
    user = body["messages"][-1]["content"]
    assert "Sharma" not in user and not re.search(r"\d", user)
    assert "tools" not in body


@pytest.mark.parametrize("chat,kw", [
    ({"label": "MAYBE", "confidence": 0.9}, {}),
    ({"label": "PTP", "confidence": 0.9, "date": "2026-11-25"}, {}),
    ({"label": "PTP"}, {}),
    ({"label": "PTP", "confidence": 1.5}, {}),
    ({"label": "PTP", "confidence": True}, {}),
    ({"label": "PTP", "confidence": "high"}, {}),
    ("not json at all", {}),
    (["PTP", 0.9], {}),
    ({"label": "PTP", "confidence": 0.9}, {"status": 500}),
    ({"label": "PTP", "confidence": 0.9}, {"exc": TimeoutError("slow")}),
], ids=["bad-enum", "extra-key", "missing-key", "conf-gt-1", "conf-bool", "conf-str", "not-json", "array", "http-500", "timeout"])
def test_ollama_schema_violations_fall_through(chat, kw):
    local, t = ollama(chat, **kw)
    cloud, ct = luna({"intent": "NEEDS_TIME", "summary": "Wants time.", "ambiguous": False, "agreed_date": None})
    r = IntentStack(model=FakeModel({"PTP": 0.4}), local_llm=local, cloud_llm=cloud).classify(VAGUE, TODAY)
    assert r.tier_used == "cloud_llm" and r.label == "NEEDS_TIME"     # fell through to tier 4
    assert r.evidence["local_llm"]["error"] and r.evidence["local_llm"]["output"] is None
    assert len(t.chat_calls) == 1
    r2 = IntentStack(model=FakeModel({"PTP": 0.4}), local_llm=ollama(chat, **kw)[0]).classify(VAGUE, TODAY)
    assert (r2.label, r2.tier_used) == ("UNKNOWN", "abstain")


@pytest.mark.parametrize("tags,status", [(None, 200), (("llama3.2:3b",), 200), (("qwen2.5:3b",), 503)],
                         ids=["server-down", "model-not-pulled", "tags-error"])
def test_ollama_auto_detect_off_when_unavailable(tags, status):
    local, t = ollama({"label": "PTP", "confidence": 0.9}, tags=tags, tags_status=status)
    assert local.available() is False
    r = IntentStack(model=FakeModel({"PTP": 0.4}), local_llm=local).classify(VAGUE, TODAY)
    assert r.tier_used == "abstain" and t.chat_calls == [] and "local_llm" not in r.evidence["escalated_to"]
    local.available()
    assert len([c for c in t.calls if c["url"].endswith("/api/tags")]) == 1   # probed once


def test_ollama_disabled_without_model_env_and_latest_tag_matches():
    assert OllamaClient(env={}).available() is False
    t = FakeHttp(tags=("llama3.2:latest",))
    assert OllamaClient(model="llama3.2", transport=t, env={}).available() is True


def test_validate_local_output():
    assert validate_local_output({"label": "STOP", "confidence": 1}) is None
    assert validate_local_output({"label": "STOP", "confidence": float("nan")})
    assert validate_local_output("x")


# ---- redaction -----------------------------------------------------------------------------------

def test_redaction_masks_digits_names_phones_and_ids():
    text = ("Mr Ramesh Gupta here, my name is Sunita Rao, Ravi ji ko bolo. call +91 98765 43210 or ९८७६५४३२१० "
            "mail ravi.k@example.com upi ravi@okaxis loan PL123456 UTR 412345678901 ₹12,500 25 ko dunga BRW-0042")
    s = redact_for_llm(text, known=["BRW-0042"])
    for leaked in ("Ramesh", "Gupta", "Sunita", "Ravi", "98765", "ravi.k@example.com", "ravi@okaxis", "PL123456",
                   "412345678901", "12,500", "BRW-0042", "९८७६"):
        assert leaked not in s, leaked
    assert not re.search(r"\d", s)
    assert "[PHONE]" in s and "[EMAIL]" in s and "[NAME]" in s
    assert len(redact_for_llm("x" * 5000)) == 500


def test_evidence_never_contains_raw_text_or_digits():
    r = IntentStack().classify("Mr Sharma 9876543210 kal 25000 de dunga", TODAY)
    blob = json.dumps(r.to_dict(), ensure_ascii=False)
    assert "9876543210" not in blob and "Sharma" not in blob and "25000" not in blob


# ---- entities ------------------------------------------------------------------------------------

@pytest.mark.parametrize("text,date_,hint", [
    ("kal de dunga", "2026-11-20", None), ("parso pakka", "2026-11-21", None), ("parson bhar dunga", "2026-11-21", None),
    ("25 tareekh ko kar dunga", "2026-11-25", None), ("25 tarikh", "2026-11-25", None), ("5 ko dunga", "2026-12-05", None),
    ("next Monday pay karunga", "2026-11-23", None), ("monday ko", "2026-11-23", None), ("agle somvar", "2026-11-23", None),
    ("salary ke baad dunga", None, "after_salary"), ("after my salary", None, "after_salary"),
    ("कल दूंगा", "2026-11-20", None), ("परसों भर दूंगा", "2026-11-21", None), ("२५ तारीख को", "2026-11-25", None),
    ("सैलरी के बाद", None, "after_salary"), ("2 din me kar dunga", "2026-11-21", None), ("in 3 days", "2026-11-22", None),
    ("ek hafte me", "2026-11-26", None), ("month end tak", "2026-11-30", None), ("will pay on 2026-12-02", "2026-12-02", None),
    ("30/11/2026", "2026-11-30", None), ("diwali ke baad", None, "after_festival"), ("next week", None, "next_week"),
    ("agle mahine", None, "next_month"), ("koi date nahi", None, None),
])
def test_date_entities(text, date_, hint):
    e = extract_entities(text, TODAY)
    assert (e["date"], e["date_hint"]) == (date_, hint)


@pytest.mark.parametrize("text,amount", [("poora kal de dunga", "FULL"), ("aadha abhi baki baad me", "PARTIAL"),
                                         ("will pay the full amount", "FULL"), ("part payment kar sakta hu", "PARTIAL"),
                                         ("आधा दूंगा", "PARTIAL"), ("kal de dunga", None)])
def test_amount_type_entity(text, amount):
    assert extract_entities(text, TODAY)["amount_type"] == amount


def test_dates_only_kept_for_dated_labels():
    s = IntentStack()
    assert s.classify("kal hi bhar diya", TODAY).entities["date"] is None          # "kal" = yesterday here
    assert s.classify("abhi pay kar raha hu", TODAY).entities["date"] == TODAY.isoformat()
    r = s.classify("pay kar dunga", TODAY)
    assert r.label == "NEEDS_TIME" and r.entities["date"] is None                  # promise without a date
    r = s.classify("aaj nahi ho payega", TODAY)
    assert r.label == "NEEDS_TIME" and r.entities["date"] is None


@pytest.mark.parametrize("text,label", [
    ("abhi pay kar raha hu", "PAY_NOW"), ("kitna baaki hai", "BALANCE_QUERY"), ("statement bhejo", "STATEMENT_REQUEST"),
    ("hindi me bolo", "LANGUAGE_CHANGE"), ("agent se baat karni hai", "HUMAN"), ("galat number hai", "WRONG_NUMBER"),
    ("namaste ji", "GREETING"), ("👍", "GREETING"), ("payment link expired, send again", "LINK_REQUEST"),
    ("sirr alredy paymnt kr diya hai", "ALREADY_PAID"), ("plz cal me tmrw", "CALLBACK"),
])
def test_multilingual_and_typo_rules(text, label):
    assert IntentStack().classify(text, TODAY).label == label


# ---- wiring --------------------------------------------------------------------------------------

def msg(text):
    return InboundMessage(borrower_id="BRW-0042", channel="whatsapp", received_at=datetime(2026, 11, 19, 10), text=text)


def test_intent_classifier_default_is_unchanged_and_stack_is_opt_in(monkeypatch):
    monkeypatch.delenv("KAIROS_INTENT_STACK", raising=False)
    legacy = IntentClassifier().classify(msg("link bhejo"))
    assert legacy.stack is None and legacy.source == "fallback"
    r = IntentClassifier(use_stack=True).classify(msg("link bhejo"))
    assert r.intent == "OTHER" and r.source == "rules" and r.stack["label"] == "LINK_REQUEST"
    r = IntentClassifier(use_stack=True).classify(msg("bhai kal pakka kar dunga"))
    assert (r.intent, r.agreed_date, r.stack["tier_used"]) == ("PTP", "2026-11-20", "rules")
    r = IntentClassifier(use_stack=True).classify(msg("abhi pay kar raha hu"))
    assert (r.intent, r.agreed_date) == ("PTP", "2026-11-19")
    r = IntentClassifier(use_stack=True).classify(msg(VAGUE))
    assert (r.intent, r.source, r.ambiguous) == ("OTHER", "fallback", True)
    monkeypatch.setenv("KAIROS_INTENT_STACK", "1")
    assert IntentClassifier().classify(msg("galat number")).stack["label"] == "WRONG_NUMBER"


def test_intent_classifier_stack_uses_its_llm_as_cloud_tier(monkeypatch):
    monkeypatch.delenv("KAIROS_LOCAL_LLM_MODEL", raising=False)
    cloud, ct = luna({"intent": "CALLBACK", "summary": "Wants a call later.", "ambiguous": False, "agreed_date": None})
    r = IntentClassifier(llm=cloud, use_stack=True, stack=IntentStack(model=FakeModel({"PTP": 0.3}), cloud_llm=cloud)).classify(msg(VAGUE))
    assert (r.intent, r.source, r.summary) == ("CALLBACK", "llm", "Wants a call later.")
    assert r.llm_usage["total_tokens"] == 2 and len(ct.calls) == 1
    assert "BRW-0042" not in json.dumps(ct.calls[0])


def test_classify_reply_flag(monkeypatch):
    monkeypatch.delenv("KAIROS_INTENT_STACK", raising=False)
    assert S.STACK_DEFAULT_FOR_INGESTION is True
    assert classify_reply("link bhejo", TODAY).label == "LINK_REQUEST"
    assert classify_reply("link bhejo", TODAY, use_stack=False).tier_used in ("legacy_rules", "abstain")
    monkeypatch.setenv("KAIROS_INTENT_STACK", "0")
    assert classify_reply("galat number", TODAY).tier_used == "legacy_rules"
    assert classify_reply("galat number", TODAY).label == "WRONG_NUMBER"


def test_meaningful_reply_rule():
    assert not is_meaningful_reply("STOP") and not is_meaningful_reply("WRONG_NUMBER")
    assert all(is_meaningful_reply(lab) for lab in LABELS if lab not in ("STOP", "WRONG_NUMBER"))


def test_stack_eval_report_decision_matches_default():
    """The committed eval report justifies the ingestion default."""
    rep = Path(__file__).resolve().parents[1] / "reports" / "intent_eval.json"
    if not rep.exists():
        pytest.skip("run scripts/eval_intent.py")
    d = json.loads(rep.read_text(encoding="utf-8"))
    assert d["decision"]["stack_beats_current_rules_on_gold"] is S.STACK_DEFAULT_FOR_INGESTION
    assert d["gold"]["stack"]["safety"]["STOP"]["recall"] == 1.0
    assert d["gold"]["stack"]["safety"]["HARDSHIP"]["recall"] == 1.0
