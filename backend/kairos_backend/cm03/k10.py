"""K10 (Ojas, policy/K10-inbound-conversation): the rules CM-03 reads when it runs in `identity_mode="k10"`.

- identity = registered number + name confirmation (`name_matches`), 2 attempts, OTP step-up only when
  `identity.otp_step_up.enabled` is true in conversation_policy.json;
- Kaira's replies for identity, safety and exception turns come from K10 `scripts.csv` (en / hinglish / hi),
  rendered at runtime, never copied into code;
- safety intents K10 lists that our NLU does not classify: ABUSIVE, LEGAL_THREAT, FRAUD_CLAIM (end the
  automated conversation and route, K13 routed_exceptions) and FORECLOSURE_REQUEST (route, never serve).

The detectors are deliberately narrow (explicit phrases only): a miss falls through to the normal flow,
which never discloses before verification anyway.
"""
from __future__ import annotations

import csv
import json
import re
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional

from ..policy import policy_dir

K10_DIRNAME = "K10-inbound-conversation"
LANGS = ("en", "hinglish", "hi")

# conversation_policy.json identity.name_match.normalise lists these honorifics.
HONORIFICS = frozenset({"mr", "mrs", "ms", "shri", "smt", "ji", "sri", "shrimati", "kumari", "dr"})


def _dir() -> Path:
    return policy_dir() / K10_DIRNAME


@lru_cache(maxsize=None)
def conversation_policy() -> dict:
    with open(_dir() / "conversation_policy.json", encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=None)
def scripts() -> Dict[str, Dict[str, str]]:
    with open(_dir() / "scripts.csv", encoding="utf-8", newline="") as f:
        return {r["key"]: {lang: r[lang] for lang in LANGS} for r in csv.DictReader(f)}


def otp_step_up() -> bool:
    return bool(((conversation_policy().get("identity") or {}).get("otp_step_up") or {}).get("enabled"))


def max_name_attempts() -> int:
    return int(((conversation_policy().get("identity") or {}).get("name_match") or {}).get("max_attempts", 2))


def policy_version() -> str:
    return conversation_policy()["policy_version"]


def render(key: str, lang: str, **kw) -> str:
    """Render a K10 script. Every placeholder must be supplied: a missing one raises (never ships '{x}')."""
    row = scripts()[key]
    text = row.get(lang) or row["en"]
    return text.format(**kw)


def placeholders(key: str) -> List[str]:
    return sorted(set(re.findall(r"\{(\w+)\}", " ".join(scripts()[key].values()))))


def date_options(today: date) -> Dict[str, str]:
    """{date_plus_1}, {date_plus_2}: K13 needs_time asks for a date within target_ptp_within_days (2)."""
    return {"date_plus_1": (today + timedelta(days=1)).strftime("%d %b"),
            "date_plus_2": (today + timedelta(days=2)).strftime("%d %b")}


# ------------------------------------------------------------------ name match (K10 section 4)
def _norm_tokens(name: str) -> List[str]:
    toks = re.findall(r"[a-zऀ-ॿ]+", (name or "").lower())
    return [t for t in toks if t not in HONORIFICS]


def _one_indel(a: str, b: str) -> bool:
    """True when a and b differ by exactly one inserted or deleted letter (no substitution)."""
    if abs(len(a) - len(b)) != 1:
        return False
    s, l = (a, b) if len(a) < len(b) else (b, a)
    return any(l[:i] + l[i + 1:] == s for i in range(len(l)))


def _edits(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def word_matches(typed: str, record: str) -> bool:
    """Names up to 6 letters allow one missing or extra letter, never a swapped one (Rajesh != Ramesh);
    longer words allow up to 2 edits."""
    if typed == record:
        return True
    if len(record) <= 6:
        return _one_indel(typed, record)
    return _edits(typed, record) <= 2


def name_matches(typed: str, on_record: str) -> bool:
    """Full name, first name alone, or first + last name pass (honorifics and case ignored)."""
    t, r = _norm_tokens(typed), _norm_tokens(on_record)
    if not t or not r:
        return False
    if len(t) == len(r) and all(word_matches(a, b) for a, b in zip(t, r)):
        return True
    if len(t) == 1:
        return word_matches(t[0], r[0])
    if len(t) == 2 and len(r) >= 2:
        return word_matches(t[0], r[0]) and word_matches(t[1], r[-1])
    return False


def first_name(on_record: str) -> str:
    toks = [w for w in re.findall(r"\S+", on_record or "") if w.lower() not in HONORIFICS]
    return toks[0] if toks else ""


# ------------------------------------------------------------------ safety / routed intents
ABUSIVE, LEGAL_THREAT, FRAUD_CLAIM, FORECLOSURE = "ABUSIVE", "LEGAL_THREAT", "FRAUD_CLAIM", "FORECLOSURE_REQUEST"

_RX = lambda p: re.compile(p, re.I)  # noqa: E731
_ABUSIVE = _RX(r"\b(idiot|stupid|bastard|bloody fool|shut up|go to hell|chutiya|kamina|kameena|harami|"
               r"bewakoof|nalayak|saala|saale|f+u+c+k\w*|bitch|moron)s?\b")
_LEGAL = _RX(r"\b(legal notice|lawyer|advocate|\bsue\b|i will sue|consumer (court|forum)|court case|file (a )?case|"
             r"police complaint|take you to court)\b")
_FRAUD_CLAIM = _RX(r"\b(identity (theft|stolen)|loan (was )?taken in my name|someone (took|has taken) (a|this) loan|"
                   r"(this|the) loan is (a )?fraud|fraud(ulent)? loan|i never (took|applied for) (this|any) loan)\b")
_FORECLOSE = _RX(r"\b(fore-?clos\w*|pre-?clos\w*|foreclosure (quote|amount|letter)|close (my|the) loan (early|now|fully))\b")


def safety_intent(text: str) -> Optional[str]:
    """K10 priority among the safety intents: ABUSIVE > LEGAL_THREAT > FRAUD_CLAIM."""
    t = text or ""
    if _ABUSIVE.search(t):
        return ABUSIVE
    if _LEGAL.search(t):
        return LEGAL_THREAT
    if _FRAUD_CLAIM.search(t):
        return FRAUD_CLAIM
    return None


def is_foreclosure(text: str) -> bool:
    return bool(_FORECLOSE.search(text or ""))
