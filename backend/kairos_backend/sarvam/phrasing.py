"""sarvam-105b-conversations: rephrase an APPROVED CM-03 reply into natural English / Hinglish / Hindi.

Runs only AFTER the deterministic engine has chosen the state, the action and the reply key, and only for
small talk, clarification and empathy lines (PHRASE_KEYS). Before verification it may touch only the
scripted identity prompts (PRE_VERIFY_KEYS). The model sees the approved line and the target language,
nothing else: no borrower text (no prompt injection path), no account data.

Every candidate goes through validate() before it can be sent. Any failure -> the approved line verbatim.
  * length within limits of the approved line
  * digits, links, amounts and dates: only those already in the approved line (and the approved line is
    the engine's, which is PRE_VERIFY_SAFE before verification); every approved digit / link kept
  * no account words before verification unless the approved line had them
  * no threat / misleading-consequence / third-party words (template_compliance vocabulary) and no
    promises (waiver, discount, guarantee, ...) unless the approved line had them
  * same intent: per-key concept markers must survive, upper-case keywords (RESEND, NOC) and the lender
    name must survive, a hardship line gets no collection ask
  * no unfilled template slot the approved line did not have ("[lender name]", "{lender}", "<name>", "XXXX")
  * right script for the language
"""
from __future__ import annotations

import json
import re
import threading
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from .. import template_compliance as tc
from .client import CONVERSATIONS, SarvamClient

PHRASING_VERSION = "phrasing-2"

# Small talk / clarification / empathy only. Never balances, links, PTP confirmations, receipts, OTP codes.
PHRASE_KEYS = frozenset({"CLARIFY", "GOODBYE", "HARDSHIP", "HARDSHIP_ACK", "HANDOFF_HOLD", "ANYTHING_ELSE",
                         "VERIFIED_OK", "UNKNOWN_HANDOFF", "CALLBACK", "CALLBACK_CREATED", "LANG_SET"})
PRE_VERIFY_KEYS = frozenset({"GREET_VERIFY", "OTP_PROMPT", "ASK_NAME"})

MAX_CHARS = 480
MIN_RATIO, MAX_RATIO = 0.4, 1.8

_ANY = {
    "thanks": ["thank", "dhanyavaad", "dhanyawad", "shukriya", "धन्यवाद", "शुक्रिया"],
    "sorry": ["sorry", "dukh", "afsos", "maaf", "khed", "दुख", "खेद", "माफ़", "माफ"],
    "pause": ["pause", "paused", "rok", "band", "रोक", "बंद"],
    "team": ["team", "टीम"],
    "help": ["help", "madad", "sahayata", "मदद", "सहायता"],
    "verified": ["verif", "सत्यापन", "satyapan"],
    "call": ["call", "phone", "कॉल", "फ़ोन", "फोन"],
    "code": ["code", "otp", "कोड"],
    "name": ["name", "naam", "नाम"],
    "understand": ["sorry", "understand", "samajh", "माफ़", "माफ", "समझ", "didn't get", "did not get"],
    "language": ["english", "hindi", "हिंदी", "अंग्रेज़ी", "अंग्रेजी", "hinglish"],
}
CONCEPTS: Dict[str, List[str]] = {
    "CLARIFY": ["understand"], "GOODBYE": ["thanks"], "HARDSHIP": ["sorry", "pause", "team"],
    "HARDSHIP_ACK": ["sorry", "pause"], "HANDOFF_HOLD": ["team"], "ANYTHING_ELSE": ["help"],
    "VERIFIED_OK": ["verified"], "UNKNOWN_HANDOFF": ["team"], "CALLBACK": ["call"], "CALLBACK_CREATED": ["call"],
    "LANG_SET": ["language"], "GREET_VERIFY": ["code"], "OTP_PROMPT": ["code"], "ASK_NAME": ["name"],
}
NO_COLLECTION_ASK = frozenset({"HARDSHIP", "HARDSHIP_ACK"})
COLLECTION_ASK = ["pay", "payment", "bhugtan", "भुगतान", "link", "लिंक", "jaldi", "जल्दी", "emi", "ईएमआई"]
PROMISE_TERMS = ["waive", "waiver", "discount", "guarantee", "guaranteed", "promise", "settle", "settlement",
                 "write off", "write-off", "interest free", "interest-free", "no penalty", "no late fee", "free of cost",
                 "maaf kar", "maafi", "chhoot", "chhut", "pakka", "माफ कर", "माफ़ कर", "माफी", "छूट", "गारंटी", "वादा"]
ACCOUNT_TERMS = ["balance", "outstanding", "overdue", "due", "emi", "amount", "loan account", "baki", "baaki",
                 "बकाया", "बाकी", "ईएमआई", "राशि", "किस्त"]
MONEY_RX = re.compile(r"₹|\brs\.?\b|\binr\b|\brupees?\b|\brupay\w*|रुपये|रुपए", re.I)
URL_RX = re.compile(r"https?://\S+|www\.\S+", re.I)
DATE_RX = re.compile(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\b|"
                     r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday|today|tomorrow|kal|parso|tarikh|tareekh)\b|"
                     r"(?<![ऀ-ॿ])(तारीख|कल|परसों|आज)(?![ऀ-ॿ])", re.I)
FORMAT_RX = re.compile(r"```|\{|\}|as an ai|language model|i cannot rephrase|^here is", re.I)
DEVANAGARI = re.compile(r"[ऀ-ॿ]")
_DEV_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
_CAPS = re.compile(r"\b[A-Z]{3,}\b")


def _norm(s: str) -> str:
    return unicodedata.normalize("NFKC", s or "").translate(_DEV_DIGITS)


def _hits(text: str, terms: Sequence[str]) -> List[str]:
    low = text.lower()
    out = []
    for t in terms:
        if DEVANAGARI.search(t):
            if t in text:
                out.append(t)
        elif re.search(r"(?<![a-z])" + re.escape(t.lower()) + r"(?![a-z])", low):
            out.append(t)
    return out


def _forbidden_vocab() -> List[str]:
    return [*tc.THREAT_TERMS_EN, *tc.THREAT_TERMS_HI, *tc.MISLEADING_CONSEQUENCE_EN, *tc.MISLEADING_CONSEQUENCE_HI,
            *tc.THIRD_PARTY_EN, *tc.THIRD_PARTY_HI]


def validate(candidate: Optional[str], approved: str, key: str, lang: str, verified: bool,
             lender: Optional[str] = None) -> Optional[str]:
    """None when the candidate may be sent; else the first reason it may not."""
    if not isinstance(candidate, str) or not candidate.strip():
        return "empty"
    c, a = _norm(candidate.strip()), _norm(approved)
    if len(c) > MAX_CHARS or len(c) > len(a) * MAX_RATIO + 40 or len(c) < len(a) * MIN_RATIO:
        return "length"
    if {p.lower() for p in tc.unfilled_placeholders(c)} - {p.lower() for p in tc.unfilled_placeholders(a)}:
        return "placeholder"       # "[lender name]", "{lender}", "<name>", "XXXX": never sent, approved line instead
    if FORMAT_RX.search(c):
        return "format"
    ca, aa = set(re.findall(r"\d+", c)), set(re.findall(r"\d+", a))
    if ca - aa:
        return "digits_added"
    if aa - ca:
        return "digits_dropped"
    cu, au = set(URL_RX.findall(c)), set(URL_RX.findall(a))
    if cu - au:
        return "link_added"
    if au - cu:
        return "link_dropped"
    if MONEY_RX.search(c) and not MONEY_RX.search(a):
        return "amount"
    if {m.group(0).lower() for m in DATE_RX.finditer(c)} - {m.group(0).lower() for m in DATE_RX.finditer(a)}:
        return "date"
    if not verified and set(_hits(c, ACCOUNT_TERMS)) - set(_hits(a, ACCOUNT_TERMS)):
        return "account_detail_before_verification"
    bad = set(_hits(c, _forbidden_vocab())) - set(_hits(a, _forbidden_vocab()))
    if bad:
        return "forbidden_words"
    if set(_hits(c, PROMISE_TERMS)) - set(_hits(a, PROMISE_TERMS)):
        return "promise"
    if key in NO_COLLECTION_ASK and set(_hits(c, COLLECTION_ASK)) - set(_hits(a, COLLECTION_ASK)):
        return "collection_ask_in_hardship"
    for kw in set(_CAPS.findall(a)):
        if kw not in c:
            return "keyword_dropped"
    if lender and lender in a and lender not in c:
        return "lender_dropped"
    if set(_CAPS.findall(c)) - set(_CAPS.findall(a)):
        return "new_caps"          # "HUMARI TEAM", "REMINDER": shouting, or a keyword that was never approved
    for concept in CONCEPTS.get(key, []):
        if not any(t in c.lower() for t in _ANY[concept]):
            return "intent_changed"
    if lang == "hi" and not DEVANAGARI.search(c):
        return "wrong_script"
    if lang != "hi" and DEVANAGARI.search(c):
        return "wrong_script"
    return None


SYSTEM_PROMPT = (
    "You rephrase ONE approved customer-service line for a loan-servicing WhatsApp assistant in India. "
    "Keep exactly the same meaning. The user message lists must_keep tokens: copy every one of them EXACTLY as "
    "written, same spelling and same letter case, never translated, transliterated or upper-cased (the lender "
    "name stays exactly as given). Do not write any other word in ALL CAPITAL letters. "
    "Do not add facts, amounts, dates, promises, threats, consequences or questions that are not in the line. "
    "Be warm, short and natural. Reply with the rephrased line only, no quotes, no explanations."
)
# Validator reasons a second, corrective call may fix (keeping / formatting slips). Content violations
# (amount, threat, promise, link added, account words, intent changed) are never retried: verbatim at once.
RETRYABLE = frozenset({"lender_dropped", "keyword_dropped", "digits_dropped", "link_dropped", "new_caps", "length",
                       "wrong_script", "format", "intent_changed"})
STYLE = {"en": "plain, friendly Indian English",
         "hinglish": "natural Hinglish (Hindi in Latin script mixed with English), as Indians type on WhatsApp",
         "hi": "simple, polite Hindi in Devanagari script"}


@dataclass
class PhraseResult:
    text: str
    used_model: bool
    reason: str                    # ACCEPTED | NOT_ELIGIBLE | <validator reason> | MODEL_ERROR:<...>
    meta: Dict[str, Any] = field(default_factory=dict)

    def envelope(self) -> Dict[str, Any]:
        return {**self.meta, "version": self.meta.get("version"), "phrasing_version": PHRASING_VERSION,
                "used_model": self.used_model, "outcome": self.reason}


class Phraser:
    def __init__(self, client: SarvamClient, model: str = CONVERSATIONS, lender: Optional[str] = None):
        self.client, self.model, self.lender = client, model, lender
        self._lock = threading.Lock()
        self.stats: Dict[str, Any] = {"eligible": 0, "accepted": 0, "rejected": {}, "model_errors": {},
                                      "unique_answers": 0, "unique_rejected": 0, "retries": 0,
                                      "accepted_after_retry": 0}

    def _count(self, bucket: str, reason: str) -> None:
        with self._lock:
            d = self.stats[bucket]
            d[reason] = d.get(reason, 0) + 1

    @staticmethod
    def eligible(key: str, verified: bool) -> bool:
        return key in PHRASE_KEYS if verified else key in PRE_VERIFY_KEYS

    def must_keep(self, approved: str) -> List[str]:
        a = _norm(approved)
        keep = [self.lender] if self.lender and self.lender in a else []
        keep += sorted(set(_CAPS.findall(a)))
        keep += sorted(set(URL_RX.findall(a)))
        keep += sorted(set(re.findall(r"\d+", a)), key=lambda x: (len(x), x))
        return keep

    def build_messages(self, approved: str, style: str) -> List[Dict[str, str]]:
        keep = self.must_keep(approved)
        return [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(
                    {"style": STYLE[style], "approved_line": approved, "must_keep": keep,
                     "rule": "Copy each must_keep token exactly, same letter case. No other ALL-CAPS words."},
                    ensure_ascii=False)}]

    def _ask(self, messages: List[Dict[str, str]]):
        return self.client.chat(self.model, messages, temperature=0.0, top_p=1.0, max_tokens=220)

    def phrase(self, key: str, approved: str, lang: str, hinglish: bool, verified: bool) -> PhraseResult:
        if not self.eligible(key, verified):
            return PhraseResult(approved, False, "NOT_ELIGIBLE")
        with self._lock:
            self.stats["eligible"] += 1
        style = "hi" if lang == "hi" else ("hinglish" if hinglish else "en")
        vlang = "hi" if lang == "hi" else "en"
        msgs = self.build_messages(approved, style)
        try:
            rep = self._ask(msgs)
        except Exception as e:   # defensive
            self._count("model_errors", type(e).__name__)
            return PhraseResult(approved, False, f"MODEL_ERROR:{type(e).__name__}", {"model": self.model})
        meta = rep.meta()
        if not rep.ok:
            self._count("model_errors", (rep.error or "error").split(":")[0])
            return PhraseResult(approved, False, f"MODEL_ERROR:{rep.error}", meta)
        first_cached = rep.cached
        cand = _clean(rep.data["text"])
        why = validate(cand, approved, key, vlang, verified, self.lender)
        if why in RETRYABLE:
            # one corrective call, same validator; the answer it gives is cached like any other
            retry = msgs + [{"role": "assistant", "content": cand},
                            {"role": "user", "content": f"Not accepted ({why}). Rewrite it once more following every "
                                                        f"rule. must_keep, exactly: {json.dumps(self.must_keep(approved), ensure_ascii=False)}. "
                                                        "Reply with the line only."}]
            try:
                rep2 = self._ask(retry)
            except Exception:
                rep2 = None
            if rep2 is not None and rep2.ok:
                with self._lock:
                    self.stats["retries"] += 1
                cand2 = _clean(rep2.data["text"])
                why2 = validate(cand2, approved, key, vlang, verified, self.lender)
                if why2 is None:
                    cand, why, meta = cand2, None, {**rep2.meta(), "retried_after": why}
                    with self._lock:
                        self.stats["accepted_after_retry"] += 1
        if not first_cached:
            with self._lock:
                self.stats["unique_answers"] += 1
                self.stats["unique_rejected"] += bool(why)
        if why:
            self._count("rejected", why)
            return PhraseResult(approved, False, why, meta)
        with self._lock:
            self.stats["accepted"] += 1
        return PhraseResult(cand, True, "ACCEPTED", meta)

    def summary(self) -> Dict[str, Any]:
        s = self.stats
        rej = sum(s["rejected"].values())
        answered = s["accepted"] + rej
        return {"eligible_replies": s["eligible"], "model_answers": answered, "accepted": s["accepted"],
                "rejected_by_validator": rej, "rejected_by_reason": dict(sorted(s["rejected"].items())),
                "model_errors": dict(s["model_errors"]),
                "validator_rejection_rate": round(rej / answered, 4) if answered else None,
                "unique_model_answers": s["unique_answers"], "unique_rejected": s["unique_rejected"],
                "unique_rejection_rate": round(s["unique_rejected"] / s["unique_answers"], 4)
                if s["unique_answers"] else None,
                "corrective_retries": s["retries"], "accepted_after_retry": s["accepted_after_retry"],
                "note": "per reply sent (cache hits re-validated) and per distinct request (first answer not cached)"}


def _clean(text: str) -> str:
    return text.strip().strip('"').strip("“”").strip()


def shared_phraser(env=None, lender: Optional[str] = None) -> Optional[Phraser]:
    from .client import shared_client
    from .config import feature_enabled
    if not feature_enabled("phrasing", env):
        return None
    c = shared_client(env)
    return Phraser(c, lender=lender) if c else None
