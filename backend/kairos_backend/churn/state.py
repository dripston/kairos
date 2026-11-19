"""K27 per-borrower state: normalized events in, features out.

    ChurnEvent.from_dict(d)          one normalized event from ANY channel (see KINDS)
    BorrowerState.apply(event)       fold the event into the state; returns the features it changed
    BorrowerState.features(as_of)    lazy flat mapping the DSL reads (FEATURE_CATALOGUE documents it)

Rules (CLAUDE.md, K01, K05):
  * delivery / read / click are reach, never a reply; SMS read is never tracked (E04).
  * every feature carries the event time that set it; an OLDER event never overwrites a NEWER value
    (no rewind). Per-touch disposition levels only ever go up (PRD ordinal, like ledger.RANK).
  * a payment claim is intent; only a VERIFIED payment can exit the borrower.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Iterator, Mapping, Optional

from .dsl import ORDINAL, RANK

CHANNELS = ("sms", "whatsapp", "email", "ivr", "voicebot")
CALL_CHANNELS = ("ivr", "voicebot")
LAYERS = ("L1", "L2", "L3", "L4")
LAYER_CHANNELS = {"L1": ("sms", "email"), "L2": ("whatsapp",), "L3": ("ivr", "voicebot"), "L4": ()}
CHANNEL_LAYER = {ch: layer for layer, chs in LAYER_CHANNELS.items() for ch in chs}
TIERS = ("LOW", "MEDIUM", "HIGH", "CRITICAL")
WINDOWS = (3, 7, 14, 30)
LEVEL_NAMES = {"sent": 1, "delivered": 2, "read": 3, "clicked": 4, "replied": 5}

KINDS = ("delivery", "reply", "stop", "opt_in", "call", "intent", "ptp", "payment", "hold", "attempt")
DELIVERY_LEVEL = {"ACCEPTED": 1, "SENT": 1, "DELIVERED": 2, "READ": 3, "CLICKED": 4, "FAILED": 0,
                  "BOUNCED": 0, "UNDELIVERED": 0, "UNKNOWN": 1}
CONNECTED = {"ANSWERED", "COMPLETED", "CONNECTED"}
NOT_CONNECTED = {"NO_ANSWER", "BUSY", "FAILED", "DECLINED", "NOT_CONNECTED", "CANCELED"}
IVR_MENU = {"1": "PAYMENT_LINK_REQUESTED", "2": "CALLBACK_REQUESTED", "9": "STOP"}  # = channels.mock

# K17 thresholds (policy/K17-voice-dispositions/thresholds.json on k17-k18/voice-engine, 0.1.0-draft).
VOICE_ACCEPT_AT = {"STOP_REQUESTED": 0.5, "HARDSHIP": 0.5, "ABUSIVE": 0.6, "DISPUTE": 0.6, "PTP": 0.8,
                   "PARTIAL_PTP": 0.8, "ALREADY_PAID": 0.6, "CALLBACK_REQUESTED": 0.7, "HUMAN_REQUESTED": 0.6,
                   "PAY_NOW_LINK": 0.6, "NEEDS_TIME_NO_DATE": 0.6, "REFUSED_TO_PAY": 0.6,
                   "WRONG_PERSON": 0.8, "THIRD_PARTY": 0.8, "LANGUAGE_BARRIER": 0.6}
VOICE_MEANINGFUL_MIN = 0.6
NON_REPLY_DISPOSITIONS = {"NO_RESPONSE", "UNCLEAR", "WRONG_PERSON", "THIRD_PARTY", ""}
PICKUP_THRESHOLD_S = 10   # K01 stop_for_day_triggers.CALL_PICKUP


# Same keyword set as the K07/K08 adapters (channels/normalize.py) and K26 reports.
STOP_KEYWORDS = {"STOP", "STOPALL", "STOP ALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT", "OPTOUT", "OPT OUT",
                 "OPT-OUT", "रोकें", "बंद"}


def is_stop_text(text: Optional[str]) -> bool:
    if not text:
        return False
    t = " ".join(text.strip().strip(".!").upper().split())
    return t in STOP_KEYWORDS or t.startswith("STOP ")


def _dt(v: Any) -> datetime:
    if isinstance(v, datetime):
        return v.replace(tzinfo=None) if v.tzinfo is None else v.astimezone().replace(tzinfo=None)
    s = str(v).replace("Z", "+00:00")
    d = datetime.fromisoformat(s)
    if d.tzinfo is not None:  # keep wall-clock IST for +05:30, else convert to IST
        from datetime import timezone
        d = d.astimezone(timezone(timedelta(hours=5, minutes=30))).replace(tzinfo=None)
    return d


def _iso(d: datetime) -> str:
    return d.isoformat(timespec="seconds")


@dataclass
class ChurnEvent:
    """One normalized event. `event_id` is the idempotency key; `occurred_at` orders events."""
    event_id: str
    borrower_id: str
    kind: str
    occurred_at: datetime
    channel: Optional[str] = None
    status: Optional[str] = None
    touch_id: Optional[str] = None
    attrs: dict = field(default_factory=dict)
    provenance: str = "SIMULATED"
    source: str = ""

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "ChurnEvent":
        kind = str(d.get("kind") or "").lower()
        if kind not in KINDS:
            raise ValueError(f"unknown event kind {kind!r} (one of {KINDS})")
        for k in ("event_id", "borrower_id", "occurred_at"):
            if not d.get(k):
                raise ValueError(f"event needs {k}")
        ch = d.get("channel")
        if ch is not None and ch not in CHANNELS:
            raise ValueError(f"unknown channel {ch!r}")
        known = {"event_id", "borrower_id", "kind", "occurred_at", "channel", "status", "touch_id", "attrs",
                 "provenance", "source"}
        attrs = dict(d.get("attrs") or {})
        attrs.update({k: v for k, v in d.items() if k not in known})
        return cls(str(d["event_id"]), str(d["borrower_id"]), kind, _dt(d["occurred_at"]), ch,
                   (str(d["status"]).upper() if d.get("status") else None), d.get("touch_id"), attrs,
                   d.get("provenance", "SIMULATED"), d.get("source", ""))

    def as_dict(self) -> dict:
        out = asdict(self)
        out["occurred_at"] = _iso(self.occurred_at)
        return out

    def view(self) -> dict:
        """event.* features for rule conditions."""
        a = self.attrs
        return {"event.kind": self.kind, "event.channel": self.channel, "event.status": self.status,
                "event.layer": CHANNEL_LAYER.get(self.channel or ""), "event.intent": a.get("intent"),
                "event.disposition": a.get("disposition"), "event.confidence": a.get("confidence"),
                "event.dtmf": a.get("dtmf"), "event.ptp_state": (a.get("state") if self.kind == "ptp" else None),
                "event.payment_state": (a.get("state") if self.kind == "payment" else None),
                "event.hold": a.get("hold"), "event.duration_s": a.get("duration_s"),
                "event.source": self.source}


@dataclass
class BorrowerState:
    borrower_id: str
    static: dict = field(default_factory=dict)         # loan/borrower fields at enrolment (spine)
    campaign_id: str = ""
    region: str = ""
    touches: dict = field(default_factory=dict)        # channel -> touch_id -> [first_at, level, failed, replied]
    latest: dict = field(default_factory=dict)         # channel -> touch_id (latest by first_at)
    calls: dict = field(default_factory=dict)          # channel -> call facts
    flags: dict = field(default_factory=dict)          # stop, hardship_until, dispute, k13_hold, ptp_*, payment_*
    ts: dict = field(default_factory=dict)             # feature -> event time that set it (no rewind)
    ev: dict = field(default_factory=dict)             # feature -> evidence event id
    cohort: str = ""
    cohort_since: str = ""
    cohort_rule: str = ""
    risk_tier: str = "MEDIUM"
    risk_since: str = ""
    risk_last_dir: int = 0
    risk_frozen: bool = False
    journey_id: str = ""
    journey_entered: str = ""                          # date (restart anchor)
    layer: str = "L2"
    layer_since: str = ""
    layer_avoid_slots: list = field(default_factory=list)
    lifecycle: str = "ACTIVE"
    lifecycle_since: str = ""
    lifecycle_rule: str = ""
    lifecycle_until: str = ""
    pin: dict = field(default_factory=dict)            # {cohort, until, rule}
    override: dict = field(default_factory=dict)       # approved manual override {cohort, until, id}
    campaign_attempts: int = 0
    campaign_spend_paise: int = 0
    campaign_replies: int = 0
    last_event_at: str = ""
    as_of: str = ""                                    # last tick date

    # ---------- persistence ----------
    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, default=str)

    @classmethod
    def from_json(cls, s: str) -> "BorrowerState":
        return cls(**json.loads(s))

    # ---------- time-ordered setters ----------
    def _set(self, key: str, value: Any, ev: ChurnEvent, changed: dict, force: bool = False) -> bool:
        at = _iso(ev.occurred_at)
        prev_at = self.ts.get(key)
        if not force and prev_at is not None and at < prev_at:
            changed.setdefault("_ignored_older", []).append(key)
            return False
        old = self.flags.get(key)
        self.flags[key] = value
        self.ts[key] = at
        self.ev[key] = ev.event_id
        if old != value:
            changed[key] = [old, value]
        return True

    def _touch(self, ev: ChurnEvent, level: int, failed: bool = False, replied: bool = False) -> dict:
        ch = ev.channel
        tid = ev.touch_id or f"{ev.event_id}"
        t = self.touches.setdefault(ch, {}).get(tid)
        at = _iso(ev.occurred_at)
        changed: dict = {}
        if t is None:
            t = [at, level, int(failed), int(replied)]
            self.touches[ch][tid] = t
            changed[f"{ch}.touch"] = tid
        else:
            if at < t[0]:
                t[0] = at  # first seen earlier than we thought
            if level > t[1]:
                changed[f"{ch}.level"] = [ORDINAL[t[1]], ORDINAL[level]]
                t[1] = level
            if failed and t[1] == 0:
                t[2] = 1
            if replied and not t[3]:
                t[3] = 1
        cur = self.latest.get(ch)
        if cur is None or self.touches[ch][cur][0] <= t[0]:
            self.latest[ch] = tid
        # keep 30 days + a margin
        if len(self.touches[ch]) > 60:
            for old in sorted(self.touches[ch], key=lambda k: self.touches[ch][k][0])[:-60]:
                if old != self.latest[ch]:
                    del self.touches[ch][old]
        return changed

    # ---------- the fold ----------
    def apply(self, ev: ChurnEvent) -> dict:
        """Apply one event. Returns {feature: [old, new]} (+ "_ignored_older" for rewinds refused)."""
        changed: dict = {}
        a, k, ch = ev.attrs, ev.kind, ev.channel
        if k == "attempt":
            changed.update(self._touch(ev, 1))
            self.campaign_attempts += 1
            self.campaign_spend_paise += int(a.get("cost_paise") or 0)
        elif k == "delivery":
            status = (ev.status or "UNKNOWN").upper()
            if ch == "sms" and status == "READ":
                changed["_ignored"] = "SMS_READ_NOT_TRACKED"
                return changed
            lvl = DELIVERY_LEVEL.get(status, 1)
            changed.update(self._touch(ev, lvl, failed=status in ("FAILED", "BOUNCED", "UNDELIVERED")))
            if ch == "email" and status == "BOUNCED" and a.get("hard", True):
                self._set("consent_out.email", True, ev, changed)
            if ch == "whatsapp" and status == "FAILED" and a.get("not_on_whatsapp"):
                self._set("consent_out.whatsapp", True, ev, changed)
        elif k == "reply":
            text = str(a.get("text") or "")
            if is_stop_text(text) or str(a.get("intent") or "").upper() == "STOP":
                return self.apply(ChurnEvent(ev.event_id, ev.borrower_id, "stop", ev.occurred_at, ch, None,
                                             ev.touch_id, a, ev.provenance, ev.source))
            changed.update(self._touch(ev, 5, replied=True))
            self._set("last_reply_at", _iso(ev.occurred_at), ev, changed)
            self._set("last_reply_channel", ch, ev, changed)
            self.campaign_replies += 1
            if a.get("intent"):
                changed.update(self._intent(ev, str(a["intent"]).upper(), a))
        elif k == "stop":
            if ch:  # a STOP proves the touch arrived; it is a consent fact, never a Responded reply
                changed.update(self._touch(ev, 2))
            self._set("stop", True, ev, changed)
        elif k == "opt_in":
            if self._set("stop", False, ev, changed) and ch:
                self._set(f"consent_out.{ch}", False, ev, changed)
        elif k == "call":
            changed.update(self._call(ev))
        elif k == "intent":
            # a CM-03 inbound message is a reply on its channel (default WhatsApp)
            if ch is None:
                ev.channel = ch = "whatsapp"
            intent = str(a.get("intent") or "OTHER").upper()
            if intent == "STOP":
                return self.apply(ChurnEvent(ev.event_id, ev.borrower_id, "stop", ev.occurred_at, ch, None,
                                             ev.touch_id, a, ev.provenance, ev.source))
            changed.update(self._touch(ev, 5, replied=True))
            self._set("last_reply_at", _iso(ev.occurred_at), ev, changed)
            self._set("last_reply_channel", ch, ev, changed)
            self.campaign_replies += 1
            changed.update(self._intent(ev, intent, a))
        elif k == "ptp":
            state = str(a.get("state") or "").upper()
            if state not in ("OPEN", "KEPT", "BROKEN"):
                raise ValueError("ptp event needs state OPEN|KEPT|BROKEN")
            if self._set("ptp_status", state, ev, changed):
                if state == "OPEN" and a.get("date"):
                    self._set("ptp_date", str(a["date"])[:10], ev, changed)
                if state == "BROKEN":
                    seen = set(self.flags.get("broken_ptp_ids") or [])
                    pid = a.get("ptp_id") or self.flags.get("ptp_date") or ev.event_id
                    if pid not in seen:
                        seen.add(pid)
                        self.flags["broken_ptp_ids"] = sorted(seen)
                        changed["ptp.broken_count"] = [len(seen) - 1, len(seen)]
        elif k == "payment":
            state = str(a.get("state") or "").upper()
            order = {"NONE": 0, "CLAIMED": 1, "VERIFIED": 2}
            cur = self.flags.get("payment_state") or "NONE"
            if state == "REVERSED":
                self._set("payment_state", "REVERSED", ev, changed)
                self._set("payment_cleared", False, ev, changed)
            elif state in order:
                # a verified payment is a fact even if it arrives late; a claim never downgrades it
                if cur == "REVERSED" or order[state] >= order.get(cur, 0):
                    self._set("payment_state", state, ev, changed, force=state == "VERIFIED")
                if state == "VERIFIED":
                    self._set("payment_cleared", bool(a.get("cleared", True)), ev, changed, force=True)
                    if self.flags.get("ptp_status") == "OPEN":
                        self._set("ptp_status", "KEPT", ev, changed, force=True)
                if state == "CLAIMED":
                    self._set("claim_at", _iso(ev.occurred_at), ev, changed)
            else:
                raise ValueError("payment event needs state CLAIMED|VERIFIED|REVERSED")
        elif k == "hold":
            hold = str(a.get("hold") or "").upper()
            active = bool(a.get("active", True))
            if hold == "HARDSHIP":
                until = a.get("until") or _iso(ev.occurred_at + timedelta(days=int(a.get("days", 2))))
                self._set("hardship_until", until if active else "", ev, changed)
                self._set("hardship_supervisor", bool(a.get("supervisor", False)) and active, ev, changed)
            elif hold == "DISPUTE":
                self._set("dispute", active, ev, changed)
            elif hold in ("CALLBACK_PENDING", "SUPERVISOR_HOLD", "ABUSIVE", "LEGAL_THREAT", "FRAUD_CLAIM"):
                self._set("k13_hold", hold if active else "", ev, changed)
            else:
                raise ValueError(f"unknown hold {hold!r}")
        if ev.occurred_at and (not self.last_event_at or _iso(ev.occurred_at) > self.last_event_at):
            self.last_event_at = _iso(ev.occurred_at)
        return changed

    def _intent(self, ev: ChurnEvent, intent: str, a: dict) -> dict:
        changed: dict = {}
        self._set("last_intent", intent, ev, changed)
        if intent in ("PTP", "PROMISE_TO_PAY") and a.get("ptp_date"):
            self._set("ptp_status", "OPEN", ev, changed)
            self._set("ptp_date", str(a["ptp_date"])[:10], ev, changed)
        elif intent in ("CLAIMS_PAID", "ALREADY_PAID"):
            if (self.flags.get("payment_state") or "NONE") in ("NONE", "REVERSED"):
                self._set("payment_state", "CLAIMED", ev, changed)
                self._set("claim_at", _iso(ev.occurred_at), ev, changed)
        elif intent == "HARDSHIP":
            days = int(a.get("days", 2))
            self._set("hardship_until", _iso(ev.occurred_at + timedelta(days=days)), ev, changed)
        elif intent == "DISPUTE":
            self._set("dispute", True, ev, changed)
        elif intent in ("CALLBACK", "CALLBACK_REQUESTED", "HUMAN_REQUESTED"):
            self._set("k13_hold", "CALLBACK_PENDING", ev, changed)
        return changed

    def _call(self, ev: ChurnEvent) -> dict:
        ch, a = ev.channel, ev.attrs
        if ch not in CALL_CHANNELS:
            raise ValueError("call events are for ivr / voicebot")
        status = (ev.status or "").upper()
        dur = float(a.get("duration_s") or 0)
        c = self.calls.setdefault(ch, {"no_answer_streak": 0, "connects": [], "at": ""})
        at = _iso(ev.occurred_at)
        changed: dict = {}
        connected = status in CONNECTED or (status not in NOT_CONNECTED and dur > 0)
        disp = str(a.get("disposition") or "").upper()
        conf = float(a.get("confidence") or 0)
        dtmf = a.get("dtmf")
        responded = False
        if ch == "ivr" and dtmf in IVR_MENU:
            responded = True
        if ch == "voicebot" and disp and disp not in NON_REPLY_DISPOSITIONS and conf >= VOICE_MEANINGFUL_MIN:
            responded = True
        level = 1 if not connected else (5 if responded else (3 if dur > PICKUP_THRESHOLD_S else 2))
        changed.update(self._touch(ev, level, failed=not connected, replied=responded))
        if at >= c.get("at", ""):
            c["at"] = at
            c["answered"] = connected
            c["dtmf"] = dtmf
            c["disposition"] = disp or None
            c["confidence"] = conf
            old = c["no_answer_streak"]
            c["no_answer_streak"] = 0 if connected else old + 1
            if old != c["no_answer_streak"]:
                changed[f"{ch}.no_answer_streak"] = [old, c["no_answer_streak"]]
            if not connected and a.get("slot"):
                c["last_failed_slot"] = a["slot"]
        else:
            changed.setdefault("_ignored_older", []).append(f"{ch}.call")
        if connected:
            c["connects"] = sorted(set(c["connects"]) | {at})[-20:]
        if responded:
            self._set("last_reply_at", at, ev, changed)
            self._set("last_reply_channel", ch, ev, changed)
            self.campaign_replies += 1
        # effects of the disposition (thresholds = K17 draft); IVR 9 = STOP
        if ch == "ivr" and dtmf == "9":
            self._set("stop", True, ev, changed)
        if ch == "ivr" and dtmf == "2":
            self._set("k13_hold", "CALLBACK_PENDING", ev, changed)
        if disp and conf >= VOICE_ACCEPT_AT.get(disp, 1.01):
            if disp == "STOP_REQUESTED":
                self._set("stop", True, ev, changed)
            elif disp == "HARDSHIP":
                self._set("hardship_until", _iso(ev.occurred_at + timedelta(days=2)), ev, changed)
            elif disp in ("DISPUTE",):
                self._set("dispute", True, ev, changed)
            elif disp in ("ABUSIVE",):
                self._set("k13_hold", "ABUSIVE", ev, changed)
            elif disp in ("PTP", "PARTIAL_PTP") and a.get("ptp_date"):
                self._set("ptp_status", "OPEN", ev, changed)
                self._set("ptp_date", str(a["ptp_date"])[:10], ev, changed)
            elif disp == "ALREADY_PAID":
                if (self.flags.get("payment_state") or "NONE") in ("NONE", "REVERSED"):
                    self._set("payment_state", "CLAIMED", ev, changed)
                    self._set("claim_at", at, ev, changed)
            elif disp in ("CALLBACK_REQUESTED", "HUMAN_REQUESTED"):
                self._set("k13_hold", "CALLBACK_PENDING", ev, changed)
            if disp != "NO_RESPONSE":
                self._set("last_intent", disp, ev, changed)
        return changed

    # ---------- features ----------
    def features(self, as_of: date, extra: Optional[dict] = None) -> "Features":
        return Features(self, as_of, extra or {})


def static_profile(*, dpd: Optional[int], as_of: date, pos_paise: Optional[int] = None, product: str = "",
                   risk_tag: str = "", bounce_string: str = "", state: str = "", language: str = "en",
                   emi_paise: Optional[int] = None, nach: Optional[bool] = None, represent: bool = False,
                   loan_id: str = "", obligation_id: str = "", high_ticket_pos_paise: Optional[int] = None) -> dict:
    """The static half of the PRD data model, from K03 spine fields (ledger-visible only)."""
    from replay import cn10
    score, prior, longest = cn10.delinquency_score(bounce_string)
    return {"dpd0": dpd, "as_of0": as_of.isoformat(), "pos_paise": pos_paise, "product": product,
            "risk_tag": risk_tag or "", "bounce_string": bounce_string or "", "delinquency_score": score,
            "prior_bounces": prior, "max_consecutive_bounces": longest, "state": state or "",
            "language": language or "en", "emi_paise": emi_paise, "nach": nach, "represent_eligible": bool(represent),
            "loan_id": loan_id, "obligation_id": obligation_id,
            "high_ticket": bool(high_ticket_pos_paise is not None and pos_paise is not None
                                and pos_paise > high_ticket_pos_paise),
            "cn10_cohort": cn10.cohort_of(risk_tag, bounce_string, pos_paise, high_ticket_pos_paise)}


class Features(Mapping):
    """Lazy, read-only flat feature view. Keys are listed in FEATURE_CATALOGUE."""

    def __init__(self, st: BorrowerState, as_of: date, extra: dict):
        self.st, self.as_of, self.extra, self._cache = st, as_of, extra, {}

    def __getitem__(self, key: str) -> Any:
        if key in self._cache:
            return self._cache[key]
        v = self._resolve(key)
        self._cache[key] = v
        return v

    def get(self, key, default=None):
        try:
            v = self[key]
        except KeyError:
            return default
        return default if v is None else v

    def __iter__(self) -> Iterator[str]:
        return iter(FEATURE_CATALOGUE)

    def __len__(self) -> int:
        return len(FEATURE_CATALOGUE)

    def _days_ago(self, iso: Optional[str]) -> Optional[int]:
        if not iso:
            return None
        return (self.as_of - date.fromisoformat(iso[:10])).days

    def _count(self, chans, min_level: int, days: int, *, replied=None, failed=None, max_level=None) -> int:
        n = 0
        for ch in chans:
            for first, level, f, r in self.st.touches.get(ch, {}).values():
                age = self._days_ago(first)
                if age is None or age < 0 or age >= days:
                    continue
                if level < min_level or (max_level is not None and level > max_level):
                    continue
                if replied is not None and bool(r) != replied:
                    continue
                if failed is not None and bool(f) != failed:
                    continue
                n += 1
        return n

    def _resolve(self, key: str) -> Any:
        st, s, fl = self.st, self.st.static, self.st.flags
        if key in self.extra:
            return self.extra[key]
        if key.startswith("event."):
            return None
        if key == "dpd":
            return None if s.get("dpd0") is None else s["dpd0"] + (self.as_of - date.fromisoformat(s["as_of0"])).days
        if key == "band":
            from spine.policy import dpd_band
            return dpd_band(self["dpd"])
        if key in s:
            return s[key]
        simple = {"cohort": st.cohort, "risk_tier": st.risk_tier, "risk_rank": TIERS.index(st.risk_tier),
                  "layer": st.layer, "lifecycle": st.lifecycle, "journey": st.journey_id,
                  "campaign": st.campaign_id, "region": st.region or s.get("state", ""),
                  "campaign.attempts": st.campaign_attempts, "campaign.spend_paise": st.campaign_spend_paise,
                  "campaign.replies": st.campaign_replies, "risk_frozen": st.risk_frozen}
        if key in simple:
            return simple[key]
        if key == "stop":
            return bool(fl.get("stop"))
        if key.startswith("consent_out."):
            return bool(fl.get(key))
        if key == "hardship":
            u = fl.get("hardship_until")
            return bool(u) and u[:10] >= self.as_of.isoformat()
        if key == "hardship.supervisor":
            return bool(fl.get("hardship_supervisor"))
        if key == "dispute":
            return bool(fl.get("dispute"))
        if key == "k13.hold":
            return fl.get("k13_hold") or ""
        if key == "ptp.status":
            return fl.get("ptp_status") or "NONE"
        if key == "ptp.date":
            return fl.get("ptp_date")
        if key == "ptp.days_to_date":
            d = fl.get("ptp_date")
            return None if not d else (date.fromisoformat(d) - self.as_of).days
        if key == "ptp.days_since_change":
            return self._days_ago(st.ts.get("ptp_status"))
        if key == "ptp.broken_count":
            return len(fl.get("broken_ptp_ids") or [])
        if key == "payment.state":
            return fl.get("payment_state") or "NONE"
        if key == "payment.claimed":
            return fl.get("payment_state") == "CLAIMED"
        if key == "payment.verified":
            return fl.get("payment_state") == "VERIFIED"
        if key == "payment.cleared":
            return fl.get("payment_state") == "VERIFIED" and bool(fl.get("payment_cleared"))
        if key == "payment.claim_hours":
            c = fl.get("claim_at")
            if not c:
                return None
            return (datetime.combine(self.as_of, datetime.min.time()) - _dt(c)).total_seconds() / 3600
        if key == "intent.last":
            return fl.get("last_intent")
        if key == "intent.days_since":
            return self._days_ago(st.ts.get("last_intent"))
        if key == "reply.days_since":
            return self._days_ago(fl.get("last_reply_at"))
        if key == "reply.last_channel":
            return fl.get("last_reply_channel")
        if key == "voice.no_answer_streak":
            return sum(st.calls.get(ch, {}).get("no_answer_streak", 0) for ch in CALL_CHANNELS)
        if key in ("voice.connected_7d", "voice.connected_14d"):
            days = int(key.split("_")[1][:-1])
            return sum(1 for ch in CALL_CHANNELS for t in st.calls.get(ch, {}).get("connects", [])
                       if 0 <= self._days_ago(t) < days)
        if key == "no_contact_channels_7d":
            n = 0
            for ch in CHANNELS:
                att = self._count([ch], 0, 7)
                if att >= 2 and self._count([ch], 2, 7) == 0:
                    n += 1
            return n
        parts = key.split(".")
        if len(parts) == 2 and parts[0] in LAYERS:
            return self._layer_feature(parts[0], parts[1])
        if len(parts) == 2 and parts[0] in CHANNELS + ("any",):
            ch, f = parts
            chans = CHANNELS if ch == "any" else (ch,)
            if ch in CALL_CHANNELS and f in ("answered", "dtmf", "disposition", "confidence", "no_answer_streak",
                                             "last_failed_slot"):
                return st.calls.get(ch, {}).get(f)
            if f == "status":
                if ch == "any":
                    return max((self[f"{c}.status"] for c in CHANNELS), key=lambda x: _rank(x), default="No data")
                tid = st.latest.get(ch)
                return "No data" if tid is None else ORDINAL[st.touches[ch][tid][1]]
            if f == "days_since":
                tid = st.latest.get(ch) if ch != "any" else None
                if ch == "any":
                    ds = [self[f"{c}.days_since"] for c in CHANNELS]
                    ds = [d for d in ds if d is not None]
                    return min(ds) if ds else None
                return None if tid is None else self._days_ago(st.touches[ch][tid][0])
            name, _, win = f.rpartition("_")
            if win.endswith("d") and win[:-1].isdigit():
                days = int(win[:-1])
                if name == "attempts":
                    return self._count(chans, 0, days)
                if name == "failed":
                    return self._count(chans, 0, days, failed=True)
                if name == "read_no_reply":
                    return self._count(chans, 3, days, replied=False, max_level=4)
                if name == "delivered_no_reply":
                    return self._count(chans, 2, days, replied=False, max_level=4)
                if name in LEVEL_NAMES:
                    return self._count(chans, LEVEL_NAMES[name], days)
        raise KeyError(key)

    def _layer_feature(self, layer: str, f: str) -> Any:
        chans = LAYER_CHANNELS[layer]
        name, _, win = f.rpartition("_")
        if not (win.endswith("d") and win[:-1].isdigit()):
            raise KeyError(f"{layer}.{f}")
        days = int(win[:-1])
        if name == "attempts":
            return self._count(chans, 0, days)
        if name == "read_no_reply":
            return self._count(chans, 3, days, replied=False, max_level=4)
        if name == "delivered_no_reply":
            return self._count(chans, 2, days, replied=False, max_level=4)
        if name == "no_answer":
            return self._count(chans, 0, days, failed=True)
        if name in LEVEL_NAMES:
            return self._count(chans, LEVEL_NAMES[name], days)
        raise KeyError(f"{layer}.{f}")


def _rank(label: str) -> int:
    return RANK.get(label, 0)


# Documented feature names (N in 3/7/14/30). Used by validation and the PRD "field picker".
FEATURE_CATALOGUE = (
    ["dpd", "band", "pos_paise", "product", "risk_tag", "bounce_string", "delinquency_score", "prior_bounces",
     "max_consecutive_bounces", "state", "language", "emi_paise", "nach", "represent_eligible", "high_ticket",
     "cn10_cohort", "cohort", "risk_tier", "risk_rank", "layer", "lifecycle", "journey", "campaign", "region",
     "campaign.attempts", "campaign.spend_paise", "campaign.replies", "stop", "hardship", "hardship.supervisor",
     "dispute", "k13.hold", "ptp.status", "ptp.date", "ptp.days_to_date", "ptp.days_since_change",
     "ptp.broken_count", "payment.state", "payment.claimed", "payment.verified", "payment.cleared",
     "payment.claim_hours", "intent.last", "intent.days_since", "reply.days_since", "reply.last_channel",
     "voice.no_answer_streak", "voice.connected_7d", "voice.connected_14d", "no_contact_channels_7d",
     "risk_frozen"]
    + [f"consent_out.{c}" for c in CHANNELS]
    + [f"{c}.{f}" for c in CHANNELS + ("any",) for f in ("status", "days_since")]
    + [f"{c}.{n}_{w}d" for c in CHANNELS + ("any",) for n in
       ("attempts", "failed", "read_no_reply", "delivered_no_reply", *LEVEL_NAMES) for w in WINDOWS]
    + [f"{c}.{f}" for c in CALL_CHANNELS for f in ("answered", "dtmf", "disposition", "confidence",
                                                     "no_answer_streak", "last_failed_slot")]
    + [f"{l}.{n}_{w}d" for l in LAYERS[:3] for n in ("attempts", "read_no_reply", "delivered_no_reply", "no_answer",
                                                       *LEVEL_NAMES) for w in WINDOWS]
    + ["event.kind", "event.channel", "event.status", "event.layer", "event.intent", "event.disposition",
       "event.confidence", "event.dtmf", "event.ptp_state", "event.payment_state", "event.hold",
       "event.duration_s", "event.source"]
)
_FEATURE_SET = set(FEATURE_CATALOGUE)


def known_feature(name: str) -> bool:
    return name in _FEATURE_SET
