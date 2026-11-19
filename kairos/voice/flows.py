"""IVR and voicebot call flows (K17 / K18), as deterministic state machines.

The same flow definitions drive three things:
  * the simulator (`kairos.voice.executor`), which plays a scripted borrower against them,
  * the exported agent specs (`python3 -m kairos.voice export` → kairos/voice/agents/*.json)
    to load into Sarvam once the MCP / voice-agent route is authorized,
  * prompt audio rendering through Sarvam TTS (`python3 -m kairos.voice render-prompts`).

Rules taken from the tracker and edge-case matrix:
  * E10: bounded prompt retries; no-answer / invalid digits / no input end with one
    normalized outcome and NO invented intent.
  * E11: nothing account-specific (amounts, loan numbers) is spoken before verification.
    These flows never verify identity by voice, so they never speak account values.
  * E07: needs-time without a date → ask once for a date; an agreed date → dated hold.
  * E08: callback requires voice consent; declined voice ends the call with no intent.
  * E09: hardship → neutral acknowledgement and handoff; reason is restricted.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple

LANGUAGE = "hi-IN"
LENDER = "aapke lender"  # brand name intentionally generic in the synthetic demo

# ------------------------------------------------------------------ IVR

IVR_FLOW_ID = "ivr_emi_menu_hi_v1"
IVR_MAX_ATTEMPTS = 3  # first prompt + 2 re-prompts

IVR_PROMPTS = {
    "greeting": f"Namaste. Yeh {LENDER} ki taraf se aapke loan ke baare mein ek zaroori call hai.",
    "menu": ("Agar aap EMI pehle hi bhar chuke hain, 1 dabaiye. Agar aapko thoda aur samay chahiye, 2 dabaiye. "
             "Hamare pratinidhi se baat karne ke liye 3 dabaiye. Menu dobara sunne ke liye 9 dabaiye."),
    "invalid": "Maaf kijiye, yeh vikalp sahi nahi hai.",
    "no_input": "Humein aapka jawab nahi mila.",
    "ack_paid": "Dhanyavaad. Hum aapka bhugtan verify karke WhatsApp par update denge.",
    "ack_time": "Dhanyavaad. Hum aapko WhatsApp par bhugtan ki tareekh ke liye sampark karenge.",
    "ack_callback": "Dhanyavaad. Hamare pratinidhi aapko jald call karenge.",
    "goodbye_unresolved": "Hum aapse baad mein sampark karenge. Dhanyavaad.",
}

IVR_MENU = {
    "1": {"intent": "CLAIMS_PAID", "ack": "ack_paid"},
    "2": {"intent": "NEEDS_TIME", "ack": "ack_time"},
    "3": {"intent": "CALLBACK", "ack": "ack_callback"},
}
IVR_REPEAT = "9"


@dataclass
class CallResult:
    flow: str                       # IVR | VOICEBOT
    flow_id: str
    disposition: str                # ANSWERED | COMPLETED | NO_ANSWER | BUSY | FAILED | DECLINED
    exit_reason: str
    transcript: List[Dict[str, str]] = field(default_factory=list)
    attempts: int = 0
    dtmf: Optional[str] = None
    intent: Optional[Dict[str, Any]] = None
    duration_s: int = 0

    def say(self, key_or_text: str, prompts: Dict[str, str]) -> None:
        self.transcript.append({"speaker": "bot", "text": prompts.get(key_or_text, key_or_text)})
        self.duration_s += 6

    def heard(self, text: str) -> None:
        self.transcript.append({"speaker": "borrower", "text": text})
        self.duration_s += 4


def run_ivr(answered: bool, keypresses: Iterable[Optional[str]], loan_id: Optional[str]) -> CallResult:
    """Play the IVR menu. `keypresses` yields one entry per prompt: a digit, or None for timeout."""
    if not answered:
        return CallResult("IVR", IVR_FLOW_ID, "NO_ANSWER", "no_answer")
    r = CallResult("IVR", IVR_FLOW_ID, "ANSWERED", "")
    r.say("greeting", IVR_PROMPTS)
    presses = iter(keypresses)
    while r.attempts < IVR_MAX_ATTEMPTS:
        r.attempts += 1
        r.say("menu", IVR_PROMPTS)
        key = next(presses, None)
        r.heard(f"[DTMF {key}]" if key is not None else "[no input]")
        if key in IVR_MENU:
            choice = IVR_MENU[key]
            r.dtmf = key
            r.say(choice["ack"], IVR_PROMPTS)
            r.disposition, r.exit_reason = "COMPLETED", f"dtmf_{key}"
            r.intent = {"intent": choice["intent"], "confidence": 1.0, "loan_id": loan_id,
                        "reason": f"Borrower pressed {key} in {IVR_FLOW_ID}"}
            return r
        if key == IVR_REPEAT:
            continue
        r.say("no_input" if key is None else "invalid", IVR_PROMPTS)
    r.say("goodbye_unresolved", IVR_PROMPTS)
    r.exit_reason = "max_retries_no_input" if key is None else "max_retries_invalid_digit"
    return r  # ANSWERED, no intent (E10)


# ------------------------------------------------------------------ voicebot

VOICEBOT_FLOW_ID = "voicebot_callback_hi_v1"
VOICEBOT_MAX_UNCLEAR = 2

VOICEBOT_PROMPTS = {
    "greeting": f"Namaste, main {LENDER} ki taraf se Kairos assistant bol rahi hoon. Yeh call record ho rahi hai.",
    "permission": "Kya abhi aapke EMI ke baare mein do minute baat kar sakte hain?",
    "declined": "Koi baat nahi. Hum aapko WhatsApp par sampark karenge. Dhanyavaad.",
    "ask_intent": "Aap apni EMI ke baare mein kya batana chahenge?",
    "unclear": "Maaf kijiye, main samajh nahi paayi. Kya aap dobara bata sakte hain?",
    "ask_date": "Samajh gayi. Aap kis tareekh tak bhugtan kar payenge?",
    "confirm_date": "Theek hai, {date} tak. Hum tab tak reminder nahi bhejenge aur us din bhugtan check karenge.",
    "no_date": "Koi baat nahi. Hum do din baad WhatsApp par tareekh poochhenge.",
    "ack_paid": "Dhanyavaad. Hum aapka bhugtan verify karke WhatsApp par update denge.",
    "ack_hardship": "Mujhe sunkar dukh hua. Main aapka maamla ek senior sahyogi ko de rahi hoon, woh aapse sampark karenge.",
    "ack_callback": "Zaroor. Hamare pratinidhi aapko jald call karenge.",
    "ack_stop": "Theek hai, hum aapko is vishay par aur call nahi karenge.",
    "goodbye": "Dhanyavaad, aapka din shubh ho.",
    "goodbye_unresolved": "Hum aapse WhatsApp par sampark karenge. Dhanyavaad.",
}

_PATTERNS: List[Tuple[str, Tuple[str, ...]]] = [
    ("STOP", ("call mat", "call na kare", "band karo", "stop", "pareshan mat")),
    ("HARDSHIP", ("accident", "hospital", "naukri chali", "job chali", "job gayi", "bimar", "death", "guzar",
                  "medical", "emergency")),
    ("CLAIMS_PAID", ("pay kar di", "pay kar diya", "bhar di", "bhar diya", "paid", "payment kar di", "jama kar")),
    ("CALLBACK", ("insaan se", "agent se", "kisi se baat", "manager", "callback", "baad mein call")),
    ("NEEDS_TIME", ("time chahiye", "samay chahiye", "thoda time", "salary", "tankhwah", "agle hafte", "baad mein pay",
                    "abhi nahi", "paise nahi")),
]
_YES = ("haan", "ha ", "ji", "bilkul", "theek", "boliye", "yes", "haanji")
_NO = ("nahi", "abhi nahi", "busy", "baad mein", "no")


def classify_utterance(text: str) -> Optional[str]:
    """Rule-based Hinglish intent spotting. Replace with Sarvam STT + LLM NLU when wired."""
    t = f" {text.lower()} "
    for intent, cues in _PATTERNS:
        if any(c in t for c in cues):
            return intent
    return None


def extract_date(text: str, today: datetime) -> Optional[datetime]:
    """'25 ko', '25 tarikh tak', 'kal', 'parson', 'agle hafte' → a date at 10:00 local time."""
    t = text.lower()
    base = today.replace(hour=10, minute=0, second=0, microsecond=0)
    if "parson" in t:
        return base + timedelta(days=2)
    if re.search(r"\bkal\b", t):
        return base + timedelta(days=1)
    if "agle hafte" in t or "next week" in t:
        return base + timedelta(days=7)
    m = re.search(r"\b([0-2]?\d|3[01])\s*(?:ko|tarikh|tareekh|tak|th|st|nd|rd)\b", t)
    if m:
        day = int(m.group(1))
        if 1 <= day <= 31:
            month, year = today.month, today.year
            if day <= today.day:
                month, year = (1, year + 1) if month == 12 else (month + 1, year)
            try:
                return base.replace(year=year, month=month, day=day)
            except ValueError:
                return None
    return None


def _is(text: str, words: Tuple[str, ...]) -> bool:
    t = f" {text.lower()} "
    return any(w in t for w in words)


def run_voicebot(answered: bool, utterances: Iterable[str], loan_id: Optional[str],
                 now: datetime) -> CallResult:
    """Play the consented voicebot callback against the borrower's utterances."""
    if not answered:
        return CallResult("VOICEBOT", VOICEBOT_FLOW_ID, "NO_ANSWER", "no_answer")
    r = CallResult("VOICEBOT", VOICEBOT_FLOW_ID, "ANSWERED", "")
    said = iter(utterances)

    def listen() -> str:
        text = next(said, "")
        r.heard(text or "[silence]")
        return text

    r.say("greeting", VOICEBOT_PROMPTS)
    r.say("permission", VOICEBOT_PROMPTS)
    reply = listen()
    if not reply or (_is(reply, _NO) and not _is(reply, _YES)):
        r.say("declined", VOICEBOT_PROMPTS)
        r.disposition, r.exit_reason = "DECLINED", "declined_voice"
        return r  # E08: declined voice, no intent

    r.say("ask_intent", VOICEBOT_PROMPTS)
    intent, text = None, ""
    while r.attempts < VOICEBOT_MAX_UNCLEAR:
        r.attempts += 1
        text = listen()
        intent = classify_utterance(text)
        if intent:
            break
        if r.attempts < VOICEBOT_MAX_UNCLEAR:
            r.say("unclear", VOICEBOT_PROMPTS)
    if not intent:
        r.say("goodbye_unresolved", VOICEBOT_PROMPTS)
        r.disposition, r.exit_reason = "COMPLETED", "unclear"
        return r  # no invented intent

    result: Dict[str, Any] = {"intent": intent, "confidence": 0.8, "loan_id": loan_id}
    if intent == "NEEDS_TIME":
        when = extract_date(text, now)
        if when is None:
            r.say("ask_date", VOICEBOT_PROMPTS)
            when = extract_date(listen(), now)
        if when is not None:
            r.say(VOICEBOT_PROMPTS["confirm_date"].format(date=when.strftime("%d %B")), VOICEBOT_PROMPTS)
            result["agreed_date"] = when.isoformat()
            result["reason"] = f"Promised to pay by {when.strftime('%d %b')}"
        else:
            r.say("no_date", VOICEBOT_PROMPTS)
            result["reason"] = "Needs time; no date given"
    elif intent == "CLAIMS_PAID":
        r.say("ack_paid", VOICEBOT_PROMPTS)
        result["reason"] = "Says EMI already paid; verification needed"
    elif intent == "HARDSHIP":
        r.say("ack_hardship", VOICEBOT_PROMPTS)
        result["reason"] = "Hardship reported (restricted detail)"  # E09: do not echo the borrower's detail
    elif intent == "CALLBACK":
        r.say("ack_callback", VOICEBOT_PROMPTS)
        result["reason"] = "Wants to speak to a person"
    elif intent == "STOP":
        r.say("ack_stop", VOICEBOT_PROMPTS)
        result["reason"] = "Asked not to be called"
    r.say("goodbye", VOICEBOT_PROMPTS)
    r.disposition, r.exit_reason, r.intent = "COMPLETED", f"intent_{intent.lower()}", result
    return r


# ------------------------------------------------------------------ export

def agent_specs(webhook_url: str = "https://<kairos-host>/webhooks/sarvam") -> Dict[str, Dict[str, Any]]:
    """Portable definitions of both flows, for configuring the Sarvam agents."""
    callback = {
        "url": webhook_url, "method": "POST",
        "body": {"call_id": "string", "flow": "IVR|VOICEBOT", "status": "completed|answered|no-answer|busy|failed|declined",
                 "custom_ref": "Kairos touch_id passed when the call is placed", "ended_at": "ISO-8601 with offset",
                 "duration_s": "int", "attempts": "int", "exit_reason": "string", "dtmf": "digit or null",
                 "transcript": [{"speaker": "bot|borrower", "text": "string"}],
                 "intent": {"intent": "NEEDS_TIME|CLAIMS_PAID|CALLBACK|HARDSHIP|STOP", "confidence": "0-1",
                            "agreed_date": "ISO-8601 or null", "reason": "string", "loan_id": "string or null"}},
        "idempotency": "one outcome per call_id; repeated completions are ignored",
    }
    return {
        IVR_FLOW_ID: {
            "type": "IVR", "language": LANGUAGE, "tts": {"provider": "sarvam", "model": "bulbul:v3"},
            "prompts": IVR_PROMPTS, "menu": IVR_MENU, "repeat_digit": IVR_REPEAT,
            "max_attempts": IVR_MAX_ATTEMPTS, "on_no_answer": "NO_ANSWER, no intent",
            "on_max_retries": "ANSWERED, exit max_retries_*, no intent",
            "rules": ["Never speak amounts or loan numbers (no verification on IVR)",
                      "One normalized outcome per call (E10)"],
            "callback": callback,
        },
        VOICEBOT_FLOW_ID: {
            "type": "VOICEBOT", "language": LANGUAGE,
            "tts": {"provider": "sarvam", "model": "bulbul:v3"}, "stt": {"provider": "sarvam", "model": "saaras:v4"},
            "precondition": "Borrower has voice consent on file; otherwise the executor blocks the call (no dial)",
            "prompts": VOICEBOT_PROMPTS,
            "steps": ["greeting", "permission → declined ends call (DECLINED, no intent)",
                      f"ask_intent (max {VOICEBOT_MAX_UNCLEAR} tries; unclear ends with no intent)",
                      "NEEDS_TIME → ask_date once → confirm_date | no_date",
                      "CLAIMS_PAID → ack_paid (no confirmation of payment)",
                      "HARDSHIP → neutral ack + handoff, reason restricted",
                      "CALLBACK → ack_callback", "STOP → ack_stop", "goodbye"],
            "intents": {intent: list(cues) for intent, cues in _PATTERNS},
            "rules": ["Never speak amounts or loan numbers", "Do not confirm a payment; D7/LMS decide",
                      "Do not repeat hardship details back"],
            "callback": callback,
        },
    }
