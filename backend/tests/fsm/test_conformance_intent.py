"""k09.intent conformance: the LLM is called exactly when the declared machine reaches ASK_LLM, never for
structured input or STOP, and its answer is a value from the closed enum (or a fallback), never a transition."""
from __future__ import annotations

import random
from datetime import datetime

import pytest

from kairos_backend.intent import INTENTS, IntentClassifier, InboundMessage, LlmReply, is_stop
from kairos_backend.machines import INTENT
from tests.fsm.world import Coverage

TEXTS = ["pay kar diya", "maine bhar diya hai", "kal dunga", "25 ko pay karunga", "time chahiye",
         "aaj nahi ho payega", "hospital mein hoon, job chali gayi", "galat number hai", "STOP", "stop messages",
         "msg mat bhejo", "please call me", "I dispute this amount", "hmm", "ok", "who is this?", "?",
         "can you send the link", "paisa nahi hai abhi, 30 tarikh ko", "already paid yesterday, call me",
         "bhai thoda ruk jao", "2026-11-25", "kya hai ye", "nahi dunga", "talk to agent please"]
BUTTONS = ["PAID", "PAY_TOMORROW", "PTP:2026-11-25", "CALL_ME", "WHATEVER", "STOP", "unsubscribe"]


class SpyLLM:
    def __init__(self, rng):
        self.rng, self.calls = rng, 0
        self.mode = "valid"

    def classify(self, redacted_text, today, channel):
        self.calls += 1
        if self.mode == "error":
            raise TimeoutError("simulated")
        if self.mode == "invalid":
            return LlmReply(None, None, "schema: simulated")
        intent = self.rng.choice(INTENTS)
        return LlmReply({"intent": intent, "summary": "simulated", "ambiguous": self.rng.random() < 0.3,
                         "agreed_date": "2026-11-25" if intent == "PTP" and self.rng.random() < 0.5 else None}, None)


def random_msg(rng) -> InboundMessage:
    kind = rng.random()
    t = datetime(2026, 11, 20, 10)
    if kind < 0.15:
        return InboundMessage("B1", "ivr", t, dtmf=rng.choice(["1", "2", "3", "7", "9", "12"]))
    if kind < 0.3:
        return InboundMessage("B1", "whatsapp", t, button_payload=rng.choice(BUTTONS))
    if kind < 0.35:
        return InboundMessage("B1", "sms", t, text=rng.choice(["", "   "]))
    return InboundMessage("B1", rng.choice(["sms", "whatsapp"]), t, text=rng.choice(TEXTS))


def run(seed) -> Coverage:
    rng = random.Random(seed)
    cov = Coverage()
    for _ in range(300):
        m = random_msg(rng)
        llm = SpyLLM(rng)
        llm.mode = rng.choice(["valid", "valid", "invalid", "error", "none"])
        rules_only = IntentClassifier(llm=None).classify(m)  # what the rules alone decide
        text = (m.text or "").strip()
        facts = [("STRUCTURED", bool((m.dtmf or "").strip() or (m.button_payload or "").strip())),
                 ("EMPTY", not text), ("STOP_WORD", is_stop(text)),
                 ("TEXT_RULES", rules_only.source == "rules")]
        state = "STRUCTURED"
        for name, hit in facts:
            state, t = INTENT.step(name, "HIT" if hit else "PASS")
            cov.add(t)
            if hit:
                break
        if state == "ASK_LLM":
            ev = {"valid": "LLM_VALID", "invalid": "LLM_INVALID", "error": "LLM_ERROR", "none": "NO_LLM"}[llm.mode]
            state, t = INTENT.step("ASK_LLM", ev)
            cov.add(t)
        r = IntentClassifier(llm=None if llm.mode == "none" else llm).classify(m)
        assert r.source == state.lower(), (m, r)
        assert llm.calls == (1 if state == "LLM" or (state == "FALLBACK" and llm.mode in ("invalid", "error")) else 0)
        assert r.intent in INTENTS
        if state == "RULES":
            assert r == rules_only  # the LLM never changes a rules decision
    return cov


@pytest.mark.parametrize("seed", range(10))
def test_llm_is_reached_only_through_the_declared_path(seed):
    run(seed)


def test_every_intent_transition_is_exercised():
    cov = Coverage()
    for seed in range(10):
        cov.fired.update(run(seed).fired)
    assert cov.missing(INTENT) == []
