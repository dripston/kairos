"""SIMULATED channel providers for sms, whatsapp, ivr and voicebot.

Every MockChannelProvider implements the executor's Provider protocol (send / status) and,
for each send, scripts the callbacks a real provider would deliver later — including the
messy parts: duplicate callbacks, callbacks arriving out of order, failed delivery, and a
provider that times out on the send call (the executor then sees UNKNOWN -> UNCERTAIN and
resolve_uncertain() asks status()).

Determinism: everything about one instruction is drawn from
random.Random(f"{seed}|{channel}|{instruction_id}") — the same (seed, instruction_id) always
gives the same outcome and the same callbacks, independent of send order, process or
PYTHONHASHSEED. Every result and every event carries provenance="SIMULATED".

Normalized callback events (what callbacks() returns, and what normalize.apply_callback eats):

    {"channel", "instruction_id", "provider_ref", "status", "at", "dtmf", "duration_s",
     "text", "intent", "provenance", "event_id", "seq"}

    at          seconds after the send (the provider's own timestamp for the event)
    seq         arrival order (list order). A "late" callback has a smaller `at` than
                the event before it — it happened earlier but arrived later.
    event_id    the provider's id for the event; a duplicate callback repeats it.

Status vocabulary by channel:
    sms       ACCEPTED, DELIVERED | FAILED, REPLY (inbound text, may be "STOP").
              Never READ: SMS has no read receipts, read stays UNKNOWN (K01 reply_attribution).
    whatsapp  SENT, DELIVERED | FAILED, READ, CLICKED (link click), REPLY (inbound text).
    ivr       one terminal call event: ANSWERED (dtmf digit or invalid digit or none, duration_s)
              | NO_ANSWER | BUSY | FAILED.
    voicebot  one terminal call event: COMPLETED (optional structured `intent`) | DECLINED
              | NO_ANSWER | BUSY.
"""
from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass, fields, replace
from typing import Dict, List, Optional, Union

from ..executor import ProviderResult

PROVENANCE = "SIMULATED"
CHANNELS = ("sms", "whatsapp", "ivr", "voicebot")

# Simulated per-touch charge in paise (labelled SIMULATED; real rates come from K07/K08/K17 contracts).
COST_PAISE = {"sms": 15, "whatsapp": 14, "ivr": 45, "voicebot": 90}

# Demo IVR menu (K17 owns the real one). Any other key press is an INVALID digit -> no intent.
IVR_MENU = {"1": "PAYMENT_LINK_REQUESTED", "2": "CALLBACK_REQUESTED", "9": "STOP"}
IVR_INVALID_DIGITS = ("0", "4", "5", "7", "*", "#")

# Structured intents a voicebot may return (the bot extracts them; we never invent one).
VOICEBOT_INTENTS = ("PROMISE_TO_PAY", "ALREADY_PAID", "CALLBACK_REQUESTED", "DISPUTE", "HARDSHIP")

SAMPLE_REPLIES = ("Will pay by Friday", "Paid already, please check", "Call me tomorrow", "ok",
                  "Kal tak pay kar dunga", "Who is this?")


@dataclass(frozen=True)
class ChannelProfile:
    """Rates are probabilities in [0, 1]. Conditional rates say what they are conditional on."""
    delivery_rate: float = 0.92        # message reached the handset (else FAILED callback)
    read_rate: float = 0.0             # | delivered  (whatsapp only; SMS read is never known)
    click_rate: float = 0.0            # | read       (whatsapp link click — reachability, not a reply)
    reply_rate: float = 0.0            # | delivered (sms) / read (whatsapp): inbound free-text reply
    stop_rate: float = 0.0             # | delivered / answered: borrower opts out (STOP text, digit 9, STOP intent)
    answer_rate: float = 0.0           # calls: picked up
    busy_rate: float = 0.0             # calls: | not answered
    valid_digit_rate: float = 0.0      # ivr: | answered, pressed a menu digit
    invalid_digit_rate: float = 0.0    # ivr: | answered, pressed a digit not in the menu
    intent_rate: float = 0.0           # voicebot: | completed, bot captured a structured intent
    decline_rate: float = 0.0          # voicebot: | answered, borrower hung up / declined (< 10 s)
    duplicate_rate: float = 0.0        # each callback is delivered twice with this probability
    late_rate: float = 0.0             # per send: one callback arrives after a newer one
    timeout_rate: float = 0.0          # send() times out -> UNKNOWN
    timeout_lost_share: float = 0.5    # | timeout: the provider never got it (status -> NOT_FOUND)
    reject_rate: float = 0.0           # send() synchronously REJECTED (invalid number, DND, template)

    def merged(self, overrides: Optional[dict]) -> "ChannelProfile":
        if not overrides:
            return self
        known = {f.name for f in fields(self)}
        bad = set(overrides) - known
        if bad:
            raise ValueError(f"unknown profile fields: {sorted(bad)}")
        return replace(self, **overrides)


_REALISTIC = {
    "sms": ChannelProfile(delivery_rate=0.92, reply_rate=0.03, stop_rate=0.01, duplicate_rate=0.08,
                          late_rate=0.08, timeout_rate=0.02, reject_rate=0.01),
    "whatsapp": ChannelProfile(delivery_rate=0.90, read_rate=0.70, click_rate=0.20, reply_rate=0.12,
                               stop_rate=0.01, duplicate_rate=0.08, late_rate=0.10, timeout_rate=0.02,
                               reject_rate=0.02),
    "ivr": ChannelProfile(answer_rate=0.35, busy_rate=0.15, valid_digit_rate=0.45, invalid_digit_rate=0.20,
                          stop_rate=0.02, duplicate_rate=0.10, timeout_rate=0.02),
    "voicebot": ChannelProfile(answer_rate=0.40, busy_rate=0.10, decline_rate=0.25, intent_rate=0.55,
                               stop_rate=0.02, duplicate_rate=0.10, timeout_rate=0.02),
}

PROFILES: Dict[str, Dict[str, ChannelProfile]] = {
    "realistic": _REALISTIC,
    # Happy path: everything is delivered/answered, no noise. Good for UI demos.
    "perfect": {
        "sms": ChannelProfile(delivery_rate=1.0),
        "whatsapp": ChannelProfile(delivery_rate=1.0, read_rate=1.0),
        "ivr": ChannelProfile(answer_rate=1.0, valid_digit_rate=1.0),
        "voicebot": ChannelProfile(answer_rate=1.0, intent_rate=1.0),
    },
    # Stress the normalizer: lots of duplicates, reordering and timeouts.
    "chaos": {ch: p.merged({"duplicate_rate": 0.5, "late_rate": 0.5, "timeout_rate": 0.2})
              for ch, p in _REALISTIC.items()},
}


def resolve_profile(channel: str, profile: Union[None, str, dict, ChannelProfile]) -> ChannelProfile:
    """profile may be a name ("realistic" | "perfect" | "chaos"), a ChannelProfile, or a dict of
    overrides on top of "realistic" (optionally {"base": "perfect", ...})."""
    if isinstance(profile, ChannelProfile):
        return profile
    if profile is None or isinstance(profile, str):
        name = profile or "realistic"
        if name not in PROFILES:
            raise ValueError(f"unknown profile {name!r}; known: {sorted(PROFILES)}")
        return PROFILES[name][channel]
    overrides = dict(profile)
    base = overrides.pop("base", "realistic")
    return PROFILES[base][channel].merged(overrides)


@dataclass
class _Script:
    outcome: str                      # ACCEPTED | REJECTED | TIMEOUT_ACCEPTED | TIMEOUT_LOST
    provider_ref: Optional[str]
    events: List[dict]
    error: Optional[str] = None


class MockChannelProvider:
    """Provider-protocol stand-in for one channel. See module docstring."""

    def __init__(self, channel: str, seed: int = 0,
                 profile: Union[None, str, dict, ChannelProfile] = "realistic",
                 cost_paise: Optional[int] = None):
        if channel not in CHANNELS:
            raise ValueError(f"MockChannelProvider handles {CHANNELS}, not {channel!r}")
        self.channel = channel
        self.name = f"mock-{channel}"
        self.provenance = PROVENANCE
        self.seed = seed
        self.profile = resolve_profile(channel, profile)
        self.cost_paise = COST_PAISE[channel] if cost_paise is None else cost_paise
        self.sent: List[str] = []                 # instruction ids the provider actually holds, in send order
        self._scripts: Dict[str, _Script] = {}

    # ---------- Provider protocol ----------
    def send(self, touch: dict) -> ProviderResult:
        iid = touch["instruction_id"]
        first = iid not in self._scripts
        s = self.script(iid)
        if s.outcome == "REJECTED":
            return ProviderResult("REJECTED", error=s.error)
        if first and s.outcome != "TIMEOUT_LOST":
            self.sent.append(iid)
        if s.outcome.startswith("TIMEOUT"):
            return ProviderResult("UNKNOWN", error="PROVIDER_TIMEOUT")
        return ProviderResult("ACCEPTED", provider_ref=s.provider_ref, cost_paise=self.cost_paise)

    def status(self, instruction_id: str, provider_ref: Optional[str] = None) -> str:
        """What the provider's own records say (ACCEPTED | DELIVERED | NOT_FOUND)."""
        s = self._scripts.get(instruction_id)
        if s is None or s.outcome in ("REJECTED", "TIMEOUT_LOST") or instruction_id not in self.sent:
            return "NOT_FOUND"
        delivered = {"DELIVERED", "READ", "CLICKED", "REPLY", "ANSWERED", "COMPLETED", "DECLINED"}
        return "DELIVERED" if any(e["status"] in delivered for e in s.events) else "ACCEPTED"

    # ---------- callbacks ----------
    def callbacks(self, instruction_id: str) -> List[dict]:
        """The callback sequence (arrival order) for a send. Empty if it was never sent or never
        reached the provider. Returns copies, so callers may mutate them."""
        s = self._scripts.get(instruction_id)
        if s is None or instruction_id not in self.sent:
            return []
        return [dict(e) for e in s.events]

    # ---------- scripting ----------
    def script(self, instruction_id: str) -> _Script:
        """The deterministic script for one instruction (drawn once, then cached)."""
        s = self._scripts.get(instruction_id)
        if s is None:
            s = self._draw(instruction_id)
            self._scripts[instruction_id] = s
        return s

    def _draw(self, iid: str) -> _Script:
        rng = random.Random(f"{self.seed}|{self.channel}|{iid}")
        p = self.profile
        ref = f"SIM-{self.channel.upper()}-" + hashlib.sha1(f"{self.seed}|{iid}".encode()).hexdigest()[:12]
        if rng.random() < p.reject_rate:
            return _Script("REJECTED", None, [], error=rng.choice(("INVALID_NUMBER", "DND_REGISTERED", "TEMPLATE_MISMATCH")))
        outcome = "ACCEPTED"
        if rng.random() < p.timeout_rate:
            outcome = "TIMEOUT_LOST" if rng.random() < p.timeout_lost_share else "TIMEOUT_ACCEPTED"
        raw = getattr(self, f"_events_{self.channel}")(rng, p)
        events = self._noise(rng, p, raw) if outcome != "TIMEOUT_LOST" else []
        for i, e in enumerate(events):
            e.update(channel=self.channel, instruction_id=iid, provider_ref=ref, provenance=PROVENANCE, seq=i)
            e.setdefault("dtmf", None)
            e.setdefault("duration_s", None)
            e.setdefault("text", None)
            e.setdefault("intent", None)
            e.setdefault("error", None)
            e["event_id"] = f"{ref}-{e.pop('_n')}"
        return _Script(outcome, ref, events)

    @staticmethod
    def _ev(status: str, at: float, n: int, **kw) -> dict:
        return {"status": status, "at": round(at, 1), "_n": n, **kw}

    def _events_sms(self, rng: random.Random, p: ChannelProfile) -> List[dict]:
        t = rng.uniform(0.5, 3)
        ev = [self._ev("ACCEPTED", t, 0)]
        t += rng.uniform(2, 40)
        if rng.random() >= p.delivery_rate:
            return ev + [self._ev("FAILED", t, 1, error=rng.choice(("ABSENT_SUBSCRIBER", "HANDSET_OFF", "DND")))]
        ev.append(self._ev("DELIVERED", t, 1))
        if rng.random() < p.stop_rate:
            ev.append(self._ev("REPLY", t + rng.uniform(30, 3600), 2, text=rng.choice(("STOP", "stop", "Stop"))))
        elif rng.random() < p.reply_rate:
            ev.append(self._ev("REPLY", t + rng.uniform(30, 3600), 2, text=rng.choice(SAMPLE_REPLIES)))
        return ev

    def _events_whatsapp(self, rng: random.Random, p: ChannelProfile) -> List[dict]:
        t = rng.uniform(0.5, 2)
        ev = [self._ev("SENT", t, 0)]
        t += rng.uniform(1, 30)
        if rng.random() >= p.delivery_rate:
            return ev + [self._ev("FAILED", t, 1, error=rng.choice(("131026_UNDELIVERABLE", "131047_REENGAGEMENT")))]
        ev.append(self._ev("DELIVERED", t, 1))
        if rng.random() >= p.read_rate:
            return ev
        t += rng.uniform(10, 7200)
        ev.append(self._ev("READ", t, 2))
        if rng.random() < p.click_rate:
            t += rng.uniform(2, 120)
            ev.append(self._ev("CLICKED", t, 3, text="https://pay.example/link"))
        if rng.random() < p.stop_rate:
            ev.append(self._ev("REPLY", t + rng.uniform(5, 600), 4, text="STOP"))
        elif rng.random() < p.reply_rate:
            ev.append(self._ev("REPLY", t + rng.uniform(5, 600), 4, text=rng.choice(SAMPLE_REPLIES)))
        return ev

    def _events_ivr(self, rng: random.Random, p: ChannelProfile) -> List[dict]:
        t = rng.uniform(15, 40)  # ring time
        if rng.random() >= p.answer_rate:
            return [self._ev("BUSY" if rng.random() < p.busy_rate else "NO_ANSWER", t, 0, duration_s=0)]
        dur = round(rng.uniform(4, 90), 1)
        if rng.random() < p.stop_rate:
            digit = "9"
        else:
            r = rng.random()
            if r < p.valid_digit_rate:
                digit = rng.choice([d for d in IVR_MENU if IVR_MENU[d] != "STOP"])
            elif r < p.valid_digit_rate + p.invalid_digit_rate:
                digit = rng.choice(IVR_INVALID_DIGITS)
            else:
                digit = None  # answered, listened, pressed nothing
        return [self._ev("ANSWERED", t + dur, 0, dtmf=digit, duration_s=dur)]

    def _events_voicebot(self, rng: random.Random, p: ChannelProfile) -> List[dict]:
        t = rng.uniform(15, 40)
        if rng.random() >= p.answer_rate:
            if rng.random() < p.busy_rate:
                return [self._ev("BUSY", t, 0, duration_s=0)]
            return [self._ev("NO_ANSWER", t, 0, duration_s=0)]
        if rng.random() < p.decline_rate:
            dur = round(rng.uniform(1, 8), 1)
            return [self._ev("DECLINED", t + dur, 0, duration_s=dur)]
        dur = round(rng.uniform(20, 180), 1)
        intent = None
        if rng.random() < p.stop_rate:
            intent = {"name": "STOP"}
        elif rng.random() < p.intent_rate:
            name = rng.choice(VOICEBOT_INTENTS)
            intent = {"name": name, "confidence": round(rng.uniform(0.7, 0.99), 2)}
            if name == "PROMISE_TO_PAY":
                intent["ptp_in_days"] = rng.randint(1, 5)
        return [self._ev("COMPLETED", t + dur, 0, duration_s=dur, intent=intent)]

    @staticmethod
    def _noise(rng: random.Random, p: ChannelProfile, events: List[dict]) -> List[dict]:
        """Duplicates (same event_id, delivered twice) and one late/out-of-order arrival."""
        out: List[dict] = []
        for e in events:
            out.append(e)
            if rng.random() < p.duplicate_rate:
                out.append(dict(e))
        if len(out) >= 2 and rng.random() < p.late_rate:
            i = rng.randrange(len(out) - 1)  # this one arrives after its successor
            out[i], out[i + 1] = out[i + 1], out[i]
        return out
