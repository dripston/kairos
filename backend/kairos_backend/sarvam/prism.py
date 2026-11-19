"""sarvam-prism (POST /v2/systemone) as a CLASSIFIER: intent-stack decision tier + voice re-classification.

prism answers named questions about a free-text `state`:
    {"model": "sarvam-prism", "state": "...", "questions": {"<name>": {"type": "noul" | "choice" | "score",
     "instructions": "...", "criteria": {label: description} | [ordered labels]}}}

The response shape is not documented to us, so parse_answers() is defensive: it looks for the answers
under the usual containers (answers / results / outputs / output / data / response), as a dict keyed by
question name or a list of {name|question|id, answer|value|label|choice|score}. Anything it cannot read is
a schema violation and the caller falls through to the deterministic path.

prism only ever CLASSIFIES. What its answers may do is fixed here and in the callers:
  * intent stack: rule safety hits win (prism never runs after one); a prism "yes" on stop / hardship /
    self-harm / abuse is a review signal (needs_human), and a prism-only STOP is never returned as the
    label STOP, so no caller can auto-STOP on it.
  * voice: re-classifies a low-confidence call summary; it can only route to review or raise the
    confidence of the disposition the bot already chose, within the limits in VOICE_* below.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from .. import pii
from .client import PRISM, ModelReply, SarvamClient

PRISM_VERSION = "prism-tier-1"
NOUL = ("yes", "no", "unknown")
CONFIDENCE_SCALE = ["very_low", "low", "medium", "high", "very_high"]
_SCALE_VALUE = {"very_low": 0.1, "low": 0.3, "medium": 0.5, "high": 0.8, "very_high": 0.95}

SAFETY_QUESTIONS = {
    "stop_request": "Does the person ask to stop being contacted (opt out, unsubscribe, do not call / message)?",
    "hardship": "Does the person report a hardship: illness, accident, hospital, job or income loss, death in the "
                "family, disaster or severe financial distress?",
    "self_harm": "Does the person mention suicide, self-harm or wanting to die?",
    "abusive": "Is the person abusive, threatening or using slurs?",
}

# Intent labels with plain-language criteria (the intent stack's 17 labels).
INTENT_CRITERIA: Dict[str, str] = {
    "PAY_NOW": "paying right now / immediately",
    "PTP": "promises to pay on a specific date",
    "NEEDS_TIME": "wants more time but gives no date",
    "ALREADY_PAID": "says the payment is already made",
    "CALLBACK": "asks to be called back later",
    "HARDSHIP": "illness, accident, job loss, death, disaster or financial distress",
    "DISPUTE": "contests the loan, the amount or charges, alleges fraud, or threatens a complaint",
    "STOP": "asks to stop all messages or calls",
    "WRONG_NUMBER": "not the borrower / wrong number",
    "HUMAN": "wants to talk to a real person",
    "BALANCE_QUERY": "asks how much is due",
    "LINK_REQUEST": "asks how or where to pay, or for a payment link",
    "STATEMENT_REQUEST": "asks for a statement, receipt or NOC",
    "LANGUAGE_CHANGE": "asks to switch language",
    "ABUSIVE": "abusive or threatening",
    "GREETING": "greeting, thanks, ok or small talk",
    "UNKNOWN": "unclear, cannot tell",
}

# Voice: the 16 unified dispositions (voice/dispositions.py) with criteria.
DISPOSITION_CRITERIA: Dict[str, str] = {
    "PTP": "promised to pay the full amount on a date",
    "PAY_NOW_LINK": "will pay now, asked for a link",
    "ALREADY_PAID": "says it is already paid",
    "PARTIAL_PTP": "promised a part payment on a date",
    "NEEDS_TIME_NO_DATE": "needs time, no date given",
    "CALLBACK_REQUESTED": "asked to be called back at another time",
    "HARDSHIP": "illness, job loss, death, disaster or financial distress",
    "DISPUTE": "disputes the loan or the amount",
    "HUMAN_REQUESTED": "asked for a human agent",
    "STOP_REQUESTED": "asked not to be called or messaged again",
    "WRONG_PERSON": "not the borrower / wrong number",
    "THIRD_PARTY": "someone else answered for the borrower",
    "REFUSED_TO_PAY": "refused to pay",
    "LANGUAGE_BARRIER": "could not continue because of language",
    "ABUSIVE": "abusive or threatening",
    "NO_RESPONSE": "no meaningful response from the person",
}

# ---------------------------------------------------------------------------------------------
# Voice limits (documented in backend/README.md, "Voice engine": prism re-classification).
VOICE_AGREE_MIN = 0.8          # prism must pick the SAME disposition with at least this confidence
VOICE_MAX_UPLIFT = 0.25        # the bot's confidence may rise by at most this much
VOICE_BOT_FLOOR = 0.4          # ... and only when the bot itself said at least this much
VOICE_NEVER_UPGRADE = ("STOP_REQUESTED", "WRONG_PERSON", "THIRD_PARTY", "NO_RESPONSE")


def mask_for_model(text: str, known: Sequence[str] = ()) -> str:
    """pii.scrub (emails, phones) then the intent stack's LLM redaction (ids, amounts, names, every digit)."""
    from ..intent_stack import redact_for_llm   # lazy: intent_stack imports this module lazily too
    return redact_for_llm(pii.scrub(text or ""), known)


def _q(kind: str, instructions: str, criteria: Any = None) -> Dict[str, Any]:
    q: Dict[str, Any] = {"type": kind, "instructions": instructions}
    if criteria is not None:
        q["criteria"] = criteria
    return q


def intent_questions() -> Dict[str, Any]:
    qs = {"intent": _q("choice", "Which ONE label best describes what the borrower wants? Use UNKNOWN when unsure.",
                       dict(INTENT_CRITERIA))}
    qs.update({k: _q("noul", v) for k, v in SAFETY_QUESTIONS.items()})
    qs["confidence"] = _q("score", "How confident is the intent label?", list(CONFIDENCE_SCALE))
    return qs


def disposition_questions() -> Dict[str, Any]:
    qs = {"disposition": _q("choice", "Which ONE disposition best describes this finished collections call?",
                            dict(DISPOSITION_CRITERIA))}
    qs.update({k: _q("noul", v) for k, v in SAFETY_QUESTIONS.items()})
    qs["confidence"] = _q("score", "How confident is the disposition?", list(CONFIDENCE_SCALE))
    return qs


# ---------------------------------------------------------------------------------------------
# Defensive response parsing
# ---------------------------------------------------------------------------------------------

_CONTAINERS = ("answers", "results", "outputs", "output", "data", "response", "result", "questions", "decisions")
_VALUE_KEYS = ("answer", "value", "label", "choice", "selected", "decision", "result", "output", "prediction",
               "score", "class")
_NAME_KEYS = ("name", "question", "id", "key", "question_name")


def _find_answer_map(resp: Any, names: Sequence[str], depth: int = 0) -> Optional[Dict[str, Any]]:
    if depth > 3:
        return None
    if isinstance(resp, dict):
        if any(n in resp for n in names):
            return resp
        for k in _CONTAINERS:
            if k in resp:
                got = _find_answer_map(resp[k], names, depth + 1)
                if got is not None:
                    return got
    if isinstance(resp, list):
        out = {}
        for item in resp:
            if isinstance(item, dict):
                nm = next((item[k] for k in _NAME_KEYS if isinstance(item.get(k), str)), None)
                if nm in names:
                    out[nm] = item
        if out:
            return out
    return None


def _scalar(v: Any, depth: int = 0) -> Any:
    if isinstance(v, dict) and depth < 3:
        for k in _VALUE_KEYS:
            if k in v:
                return _scalar(v[k], depth + 1)
        return None
    if isinstance(v, list) and len(v) == 1:
        return _scalar(v[0], depth + 1)
    return v


def _norm_label(v: Any) -> str:
    return re.sub(r"[\s\-]+", "_", str(v).strip()).upper()


def parse_choice(v: Any, labels: Sequence[str]) -> Optional[str]:
    s = _scalar(v)
    if not isinstance(s, str):
        return None
    lab = _norm_label(s)
    return lab if lab in labels else None


def parse_noul(v: Any) -> str:
    """yes / no / unknown. 'likely' counts as yes (fail closed: it only ever adds a human review)."""
    s = _scalar(v)
    if isinstance(s, bool):
        return "yes" if s else "no"
    t = str(s).strip().lower() if s is not None else ""
    if t in ("yes", "y", "true", "1", "likely", "probably", "haan"):
        return "yes"
    if t in ("no", "n", "false", "0", "unlikely", "nahi"):
        return "no"
    return "unknown"


def parse_score(v: Any, scale: Sequence[str] = CONFIDENCE_SCALE) -> Optional[float]:
    s = _scalar(v)
    if isinstance(s, bool) or s is None:
        return None
    if isinstance(s, (int, float)):
        f = float(s)
    else:
        t = str(s).strip().lower().replace(" ", "_").replace("-", "_")
        if t in _SCALE_VALUE:
            return _SCALE_VALUE[t]
        try:
            f = float(t.rstrip("%"))
        except ValueError:
            return None
    if f != f or f < 0:
        return None
    if f <= 1:
        return f
    if f <= 10:
        return f / 10
    if f <= 100:
        return f / 100
    return None


@dataclass
class PrismAnswer:
    label: Optional[str]                     # the choice, or None when unreadable / not allowed
    confidence: Optional[float]
    stop: str = "unknown"
    hardship: str = "unknown"
    self_harm: str = "unknown"
    abusive: str = "unknown"
    error: Optional[str] = None
    meta: Dict[str, Any] = field(default_factory=dict)

    @property
    def safety_yes(self) -> List[str]:
        return [k for k in ("stop", "self_harm", "hardship", "abusive") if getattr(self, k) == "yes"]

    def as_dict(self) -> Dict[str, Any]:
        return {"label": self.label, "confidence": self.confidence, "stop": self.stop, "hardship": self.hardship,
                "self_harm": self.self_harm, "abusive": self.abusive, "error": self.error}


def parse_answers(resp: Any, choice_name: str, labels: Sequence[str]) -> PrismAnswer:
    names = [choice_name, *SAFETY_QUESTIONS, "confidence"]
    amap = _find_answer_map(resp, names)
    if amap is None:
        return PrismAnswer(None, None, error="schema: no answers found")
    label = parse_choice(amap.get(choice_name), labels)
    conf = parse_score(amap.get("confidence"))
    if conf is None and isinstance(amap.get(choice_name), dict):   # a per-answer confidence, if given
        for k in ("confidence", "probability", "score"):
            if k in amap[choice_name]:
                conf = parse_score(amap[choice_name][k])
                break
    a = PrismAnswer(label, conf, parse_noul(amap.get("stop_request")), parse_noul(amap.get("hardship")),
                    parse_noul(amap.get("self_harm")), parse_noul(amap.get("abusive")))
    if label is None:
        a.error = f"schema: {choice_name} not one of the allowed labels"
    elif conf is None:
        a.error = "schema: confidence missing or not a number"
    return a


# ---------------------------------------------------------------------------------------------
# The two uses
# ---------------------------------------------------------------------------------------------

class PrismClassifier:
    def __init__(self, client: SarvamClient):
        self.client = client

    @property
    def model(self) -> str:
        return PRISM

    def _ask(self, state: str, questions: Dict[str, Any], choice: str, labels: Sequence[str]) -> PrismAnswer:
        try:
            rep: ModelReply = self.client.systemone(state, questions)
        except Exception as e:   # defensive: an adapter bug never reaches the caller
            return PrismAnswer(None, None, error=f"client error: {type(e).__name__}", meta={"model": PRISM})
        meta = rep.meta()
        if not rep.ok:
            return PrismAnswer(None, None, error=rep.error, meta=meta)
        a = parse_answers(rep.data, choice, labels)
        a.meta = meta
        return a

    def classify_intent(self, masked_text: str) -> PrismAnswer:
        state = ("A borrower in India replied to a loan-repayment reminder (English, Hindi or Hinglish). Personal "
                 "data and digits are masked. Only classify the message; do not decide any action. "
                 f"Message: {masked_text}")
        return self._ask(state, intent_questions(), "intent", list(INTENT_CRITERIA))

    def classify_call(self, masked_summary: str, bot_disposition: str) -> PrismAnswer:
        state = ("Summary of a finished loan-collections voice call in India (personal data and digits masked). "
                 "Only classify the call; do not decide any action. "
                 f"Summary: {masked_summary}")
        return self._ask(state, disposition_questions(), "disposition", list(DISPOSITION_CRITERIA))


def shared_prism(env=None) -> Optional[PrismClassifier]:
    from .client import shared_client
    from .config import feature_enabled
    if not feature_enabled("prism", env):
        return None
    c = shared_client(env)
    return PrismClassifier(c) if c else None


def shared_voice_prism(env=None) -> Optional[PrismClassifier]:
    from .client import shared_client
    from .config import feature_enabled
    if not feature_enabled("voice", env):
        return None
    c = shared_client(env)
    return PrismClassifier(c) if c else None


# ---------------------------------------------------------------------------------------------
# Voice rule (pure): what a prism answer may do to a low-confidence disposition
# ---------------------------------------------------------------------------------------------

@dataclass
class VoiceAssist:
    action: str                     # NONE | UPGRADE | REVIEW
    new_confidence: Optional[float]
    reason: str


def voice_rule(bot_disposition: str, bot_confidence: float, a: PrismAnswer) -> VoiceAssist:
    """Pure. Never changes the disposition, never lowers a confidence, never touches a safety outcome
    except by adding a human review."""
    if a.error or a.label is None or a.confidence is None:
        return VoiceAssist("NONE", None, f"PRISM_UNUSABLE:{a.error or 'no answer'}")
    safety = a.safety_yes
    bot_safety = {"STOP_REQUESTED": "stop", "HARDSHIP": "hardship", "ABUSIVE": "abusive"}.get(bot_disposition)
    new_safety = [s for s in safety if not (s == bot_safety or (s == "self_harm" and bot_disposition == "HARDSHIP"))]
    if new_safety:
        return VoiceAssist("REVIEW", None, "PRISM_SAFETY_SIGNAL:" + ",".join(new_safety))
    if a.label != bot_disposition:
        return VoiceAssist("REVIEW", None, f"PRISM_DISAGREES:{a.label}")
    if bot_disposition in VOICE_NEVER_UPGRADE:
        return VoiceAssist("NONE", None, "PRISM_AGREES_NO_UPGRADE_FOR_THIS_DISPOSITION")
    if bot_confidence < VOICE_BOT_FLOOR or a.confidence < VOICE_AGREE_MIN:
        return VoiceAssist("NONE", None, "PRISM_AGREES_BELOW_LIMITS")
    new = round(min(a.confidence, bot_confidence + VOICE_MAX_UPLIFT), 4)
    if new <= bot_confidence:
        return VoiceAssist("NONE", None, "PRISM_AGREES_NO_GAIN")
    return VoiceAssist("UPGRADE", new, "PRISM_AGREES")
