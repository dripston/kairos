"""K26 reports: the lender's five operational sheets + the CM-02 daily per-segment report.

Every report is READ-ONLY over the one SQLite file (K03 spine + K06 ledger) and is labelled
SIMULATED until real channel adapters are live. Customer = borrower id only (never a name, phone
or email); pass hash_ids=True for a keyed hash before sharing outside Kairos.

Sheets (column names exactly as in the lender's template "HDFC Reports.xlsx"):
  AICI           Customer, Channel, Attempted Intensity, Connected Intensity
  Data Dump      Customer, Channel, Date, Time, Disposition
  Trails         Customer, Channel, Disposition          (ordered trail, "Sent > Delivered > Responded")
  FTD            Customer, Channel, Latest Best Disposition
  Hourly Report  Customer, Time, Channel                 (one row per attempt, IST hour bucket)

Period: start/end accept a date, a datetime or an ISO string. A DATE means the whole IST day and an
end date is inclusive (start=end=day -> that one day). A DATETIME is an exact instant; the window is
[start, end). None = unbounded. All times are shown in IST (Asia/Kolkata, from the K01 policy).

Disposition model (CreditNirvana PRD): an ORDINAL scale per touch. Messages:
    No data < Sent < Delivered < Read < Clicked < Responded       ("Failed" ranks with No data)
Calls (ivr, voicebot):
    Attempted < No answer < Busy < Connected                      ("Failed"/"No data" rank below)
A touch's disposition is the HIGHEST label any source reports for it; a lower or older status never
overwrites a higher one (same rule as ledger.RANK, E04). Payments and STOP are separate facts and are
never dispositions of a message. The complete source -> label table is DISPOSITION_MAP below.
"""
from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
import os
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple, Union

from .ledger import LIVE, Ledger

PROVENANCE = "SIMULATED"
CALL_CHANNELS = ("ivr", "voicebot")

MESSAGE_SCALE = ("No data", "Sent", "Delivered", "Read", "Clicked", "Responded")
CALL_SCALE = ("Attempted", "No answer", "Busy", "Connected")
RANK = {"Failed": 0, "No data": 0,
        "Sent": 1, "Delivered": 2, "Read": 3, "Clicked": 4, "Responded": 5,       # messages
        "Attempted": 1, "No answer": 2, "Busy": 3, "Connected": 4}                 # calls
REACHED = {"Delivered", "Read", "Clicked", "Responded", "Connected"}  # "Connected Intensity"
# labels that describe the touch itself (one per touch); the others are borrower events with a time
TOUCH_LEVEL = {"No data", "Failed", "Sent", "Delivered", "Read", "Clicked", "Attempted"}

# Touch statuses that mean "dispatched to a provider" = an attempt. RESERVED (queued) and CANCELED
# (stopped before send) never left Kairos and appear in no sheet.
ATTEMPT_STATUSES = ("DISPATCHING", "UNCERTAIN", "SENT", "DELIVERED", "FAILED")

# Opt-out keywords (same set as the K07/K08 channel adapters): a "reply" that is a STOP is the
# consent fact, not a Responded disposition.
STOP_KEYWORDS = {"STOP", "STOPALL", "STOP ALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT", "OPTOUT", "OPT OUT",
                 "OPT-OUT", "रोकें", "बंद"}

# ---------------------------------------------------------------------------------------------
# THE mapping table. (source, value, message label, call label, note). None = not a disposition.
# The code below reads its labels from here; tests check the table and the behaviour agree.
# ---------------------------------------------------------------------------------------------
DISPOSITION_MAP: Tuple[Tuple[str, str, Optional[str], Optional[str], str], ...] = (
    ("touches.status", "RESERVED", None, None, "queued, never left Kairos: not an attempt"),
    ("touches.status", "CANCELED", None, None, "canceled before send: not an attempt"),
    ("touches.status", "DISPATCHING", "No data", "No data", "handed to the adapter, no receipt yet"),
    ("touches.status", "UNCERTAIN", "No data", "No data", "provider gave no answer; counted as an attempt, as for caps"),
    ("touches.status", "FAILED", "Failed", "Failed", "provider rejected / never received it: attempt that did not reach"),
    ("touches.status", "SENT", "Sent", "Attempted", "provider accepted"),
    ("touches.status", "DELIVERED", "Delivered", "Attempted",
     "ledger folds READ into DELIVERED; an answered call is Connected only via a call result > K01 threshold"),
    ("events.event_type", "touch_sent", "Sent", "Attempted", "K03 copy of SENT; spine-only events count as attempts"),
    ("events.event_type", "reply_received", "Responded", "Connected",
     "a reply proves a connected call; a STOP-keyword text is excluded (consent fact)"),
    ("events.event_type", "payment_received", None, None, "payment fact: daily report only, never a disposition"),
    ("events.event_type", "payment_reversed", None, None, "payment fact: daily report only, never a disposition"),
    ("events.event_type", "consent_changed", None, None, "STOP / opt-in fact, never a disposition"),
    ("audit.CALL_RESULT", "answered_seconds > threshold", None, "Connected", "K01 CALL_PICKUP (> 10 s)"),
    ("audit.CALL_RESULT", "answered_seconds <= threshold", None, "No answer", "short or no pickup is not a connect"),
    ("channel_events.status", "ACCEPTED", "Sent", "Attempted", "optional K07/K08 table"),
    ("channel_events.status", "SENT", "Sent", "Attempted", ""),
    ("channel_events.status", "DELIVERED", "Delivered", "Attempted", ""),
    ("channel_events.status", "READ", "Read", None, "SMS read is never tracked (E04): ignored for sms"),
    ("channel_events.status", "CLICKED", "Clicked", None, "a click is engagement, never a reply (E06)"),
    ("channel_events.status", "REPLY", "Responded", "Connected", "envelope.stop = STOP fact, excluded"),
    ("channel_events.status", "ANSWERED", None, "Connected", "Connected if duration_s > threshold, else No answer"),
    ("channel_events.status", "COMPLETED", None, "Connected", "Connected if duration_s > threshold, else No answer"),
    ("channel_events.status", "NO_ANSWER", None, "No answer", ""),
    ("channel_events.status", "BUSY", None, "Busy", ""),
    ("channel_events.status", "DECLINED", None, "Busy", "borrower rejected the call: reported as Busy"),
    ("channel_events.status", "FAILED", "Failed", "Failed", ""),
    ("channel_events.status", "UNKNOWN", "No data", "No data", ""),
)
_MAP = {(src, val): (msg, call) for src, val, msg, call, _ in DISPOSITION_MAP}

SHEET_COLUMNS: Dict[str, List[str]] = {
    "aici": ["Customer", "Channel", "Attempted Intensity", "Connected Intensity"],
    "data_dump": ["Customer", "Channel", "Date", "Time", "Disposition"],
    "trails": ["Customer", "Channel", "Disposition"],
    "ftd": ["Customer", "Channel", "Latest Best Disposition"],
    "hourly": ["Customer", "Time", "Channel"],
}
SHEET_TITLES = {"aici": "AICI", "data_dump": "Data Dump", "trails": "Trails", "ftd": "FTD", "hourly": "Hourly Report"}
TRAIL_SEPARATOR = " > "

TimeArg = Union[None, str, date, datetime]


def is_call(channel: Optional[str]) -> bool:
    return channel in CALL_CHANNELS


def label_for(source: str, value: str, channel: Optional[str]) -> Optional[str]:
    """Look up DISPOSITION_MAP. None = not a disposition (or not applicable to this channel kind)."""
    pair = _MAP.get((source, value))
    if pair is None:
        return None
    return pair[1] if is_call(channel) else pair[0]


def is_stop_text(text: Optional[str]) -> bool:
    if not text:
        return False
    t = " ".join(text.strip().strip(".!").upper().split())
    return t in STOP_KEYWORDS or t.startswith("STOP ")


# ---------- small helpers ----------
def _table_exists(c, name: str) -> bool:
    return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _columns(c, name: str) -> set:
    return {r[1] for r in c.execute(f"PRAGMA table_info({name})")}


def optional_tables(ledger: Ledger) -> Dict[str, bool]:
    """Which tables from other branches are present in this database (never required)."""
    c = ledger._conn()
    return {t: _table_exists(c, t) for t in ("channel_events", "learner_arms", "decision_scores", "learner_context")}


def _parse_dt(ledger: Ledger, s) -> Optional[datetime]:
    """ISO datetime / date / datetime -> aware IST datetime. Naive values are IST (as in the ledger)."""
    if s is None or s == "":
        return None
    if isinstance(s, datetime):
        return ledger.policy.local(s)
    if isinstance(s, date):
        return ledger.policy.local(datetime.combine(s, time(0, 0)))
    s = str(s).strip()
    try:
        if len(s) == 10:
            return ledger.policy.local(datetime.combine(date.fromisoformat(s), time(0, 0)))
        return ledger.policy.local(datetime.fromisoformat(s.replace("Z", "+00:00")))
    except ValueError:
        return None


def window(ledger: Ledger, start: TimeArg = None, end: TimeArg = None) -> Tuple[Optional[datetime], Optional[datetime]]:
    """[lo, hi) in IST. A date start = 00:00 IST that day; a date end = 00:00 IST the day AFTER (inclusive)."""
    def one(v, is_end):
        if v is None or v == "":
            return None
        if isinstance(v, str):
            v = v.strip()
            try:
                v = date.fromisoformat(v) if len(v) == 10 else datetime.fromisoformat(v.replace("Z", "+00:00"))
            except ValueError:
                raise ValueError(f"not a date or datetime: {v!r}")
        if isinstance(v, datetime):
            return ledger.policy.local(v)
        d = v + timedelta(days=1) if is_end else v
        return ledger.policy.local(datetime.combine(d, time(0, 0)))
    lo, hi = one(start, False), one(end, True)
    if lo and hi and hi < lo:
        raise ValueError("end is before start")
    return lo, hi


def _in(at: datetime, lo: Optional[datetime], hi: Optional[datetime]) -> bool:
    return (lo is None or at >= lo) and (hi is None or at < hi)


def report_salt() -> str:
    return os.environ.get("KAIROS_REPORT_SALT", "kairos-k26-simulated")


def customer_ref(borrower_id: str, hash_ids: bool = False, salt: Optional[str] = None) -> str:
    """Borrower id, or a keyed hash of it for external sharing (HMAC-SHA256, salt per share)."""
    if not hash_ids:
        return borrower_id
    key = (salt if salt is not None else report_salt()).encode()
    return "CUST-" + hmac.new(key, borrower_id.encode(), hashlib.sha256).hexdigest()[:12].upper()


# ---------- the disposition stream ----------
@dataclass
class Disposition:
    borrower_id: str
    channel: str
    at: datetime
    label: str
    instruction_id: Optional[str]
    source: str
    is_reply: bool = False

    @property
    def rank(self) -> int:
        return RANK[self.label]


@dataclass
class _Touch:
    instruction_id: str
    borrower_id: str
    channel: str
    at: datetime
    status: str
    loan_id: Optional[str]
    campaign_id: Optional[str]
    day: str
    est_cost_paise: int = 0
    actual_cost_paise: Optional[int] = None
    reconciled: bool = False
    events: List[Disposition] = field(default_factory=list)

    @property
    def attempted(self) -> bool:
        return self.status in ATTEMPT_STATUSES

    @property
    def best(self) -> Optional[Disposition]:
        return _best(self.events)

    @property
    def reached(self) -> bool:
        b = self.best
        return b is not None and b.label in REACHED


def _best(events: Iterable[Disposition]) -> Optional[Disposition]:
    """Highest rank wins; on a tie the latest. A lower or older label never overwrites a higher one."""
    best = None
    for e in events:
        if best is None or (e.rank, e.at) > (best.rank, best.at):
            best = e
    return best


@dataclass
class _Stream:
    touches: Dict[str, _Touch]
    events: List[Disposition]


_SOURCE_PRIORITY = {"channel_events": 0, "audit": 1, "events": 2, "touches": 3}


def collect(ledger: Ledger, campaign_id: Optional[str] = None) -> _Stream:
    """All dispositions known to the database, deduplicated, each linked to its touch where possible."""
    c = ledger._conn()
    threshold = ledger.policy.call_pickup_threshold_s
    touches: Dict[str, _Touch] = {}
    q, args = "SELECT * FROM touches WHERE status NOT IN ('RESERVED','CANCELED')", ()
    if campaign_id:
        q, args = q + " AND campaign_id=?", (campaign_id,)
    for r in c.execute(q + " ORDER BY scheduled_at, instruction_id", args):
        touches[r["instruction_id"]] = _Touch(
            r["instruction_id"], r["borrower_id"], r["channel"], _parse_dt(ledger, r["scheduled_at"]), r["status"],
            r["loan_id"], r["campaign_id"], r["day"], r["est_cost_paise"] or 0, r["actual_cost_paise"],
            bool(r["reconciled"]))

    raw: List[Disposition] = []
    for t in touches.values():
        lab = label_for("touches.status", t.status, t.channel)
        if lab:
            raw.append(Disposition(t.borrower_id, t.channel, t.at, lab, t.instruction_id, "touches"))

    # K03 spine events. touch_sent "EV-<instruction_id>" belongs to that touch; any other touch_sent
    # (seed data, other writers) becomes a spine-only attempt — unless a campaign filter is set.
    spine_replies = []
    for e in c.execute("SELECT * FROM events WHERE event_type IN ('touch_sent','reply_received') ORDER BY occurred_at, event_id"):
        at = _parse_dt(ledger, e["occurred_at"])
        if at is None:
            continue
        if e["event_type"] == "touch_sent":
            iid = e["event_id"][3:] if e["event_id"].startswith("EV-") else None
            if iid in touches:
                t = touches[iid]
                raw.append(Disposition(t.borrower_id, t.channel, t.at, label_for("events.event_type", "touch_sent", t.channel),
                                       iid, "events"))
            elif iid is not None and c.execute("SELECT 1 FROM touches WHERE instruction_id=?", (iid,)).fetchone():
                continue  # belongs to a ledger touch outside the campaign filter
            elif not campaign_id and e["channel"]:
                t = _Touch(e["event_id"], e["borrower_id"], e["channel"], at, "SENT", e["loan_id"], None,
                           at.date().isoformat())
                touches[t.instruction_id] = t
                raw.append(Disposition(t.borrower_id, t.channel, at, label_for("events.event_type", "touch_sent", t.channel),
                                       t.instruction_id, "events"))
        else:
            if is_stop_text(e["details"]):
                continue  # STOP is a consent fact, never Responded
            spine_replies.append((e["borrower_id"], e["channel"], at, "events"))

    # attribution index: the latest attempted touch on the same borrower (+channel) at or before the event
    by_bc: Dict[Tuple[str, Optional[str]], List[_Touch]] = defaultdict(list)
    for t in sorted(touches.values(), key=lambda t: (t.at, t.instruction_id)):
        if t.attempted:
            by_bc[(t.borrower_id, t.channel)].append(t)
            by_bc[(t.borrower_id, None)].append(t)

    def attribute(bid: str, ch: Optional[str], at: datetime) -> Optional[_Touch]:
        last = None
        for t in by_bc.get((bid, ch), ()):
            if t.at <= at:
                last = t
            else:
                break
        return last

    def borrower_event(bid, ch, at, src, msg_or_call_label: Callable[[Optional[str]], Optional[str]], is_reply=False):
        t = attribute(bid, ch, at)
        if t is None and campaign_id:
            return  # cannot be tied to this campaign's touches
        channel = t.channel if t is not None else (ch or "unknown")
        lab = msg_or_call_label(channel)
        if lab:
            raw.append(Disposition(bid, channel, at, lab, t.instruction_id if t else None, src, is_reply))

    for bid, ch, at, src in spine_replies:
        borrower_event(bid, ch, at, src, lambda chn: label_for("events.event_type", "reply_received", chn), True)

    # K06 call results (audit). Connected only above the K01 pickup threshold.
    for a in c.execute("SELECT borrower_id, ts, detail FROM audit WHERE type='CALL_RESULT' ORDER BY id"):
        det = json.loads(a["detail"] or "{}")
        at = _parse_dt(ledger, a["ts"])
        ch = det.get("channel") or "ivr"
        secs = float(det.get("answered_seconds") or 0)
        value = "answered_seconds > threshold" if secs > threshold else "answered_seconds <= threshold"
        if at is not None:
            borrower_event(a["borrower_id"], ch, at, "audit", lambda chn, v=value: label_for("audit.CALL_RESULT", v, chn))

    # optional K07/K08 channel_events (another branch). Receipt-level detail: READ, CLICKED, BUSY ...
    if _table_exists(c, "channel_events"):
        cols = _columns(c, "channel_events")
        env_col = "envelope" if "envelope" in cols else "NULL"
        rcv_col = "received_at" if "received_at" in cols else "NULL"
        for r in c.execute(f"SELECT instruction_id, channel, status, {rcv_col} AS received_at, {env_col} AS envelope"
                           " FROM channel_events ORDER BY rowid"):
            env = {}
            try:
                env = json.loads(r["envelope"]) if r["envelope"] else {}
            except ValueError:
                env = {}
            if env.get("ignored_reason") in ("SMS_READ_NOT_TRACKED", "UNKNOWN_INSTRUCTION", "DUPLICATE"):
                continue
            t = touches.get(r["instruction_id"])
            if t is None:
                continue
            status = (r["status"] or "").upper()
            ch = t.channel
            at = _parse_dt(ledger, env.get("occurred_at")) or _parse_dt(ledger, r["received_at"]) or t.at
            if status == "READ" and ch == "sms":
                continue
            if status == "REPLY":
                if env.get("stop") or is_stop_text(env.get("text")):
                    continue
                lab = label_for("channel_events.status", "REPLY", ch)
                raw.append(Disposition(t.borrower_id, ch, at, lab, t.instruction_id, "channel_events", True))
                continue
            if status in ("ANSWERED", "COMPLETED"):
                secs = float(env.get("duration_s") or 0)
                lab = "Connected" if secs > threshold else "No answer"
                lab = lab if is_call(ch) else None
            else:
                lab = label_for("channel_events.status", status, ch)
            if lab:
                raw.append(Disposition(t.borrower_id, ch, at, lab, t.instruction_id, "channel_events"))

    # dedupe: one row per (touch, touch-level label); one per (borrower, channel, label, second) for
    # borrower events. The more precise source wins (channel_events carry the receipt time).
    kept: Dict[tuple, Disposition] = {}
    for d in raw:
        if d.label in TOUCH_LEVEL and d.instruction_id:
            key = ("T", d.instruction_id, d.label)
        else:
            key = ("E", d.borrower_id, d.channel, d.label, d.at.replace(microsecond=0).isoformat())
        cur = kept.get(key)
        if cur is None or _SOURCE_PRIORITY[d.source] < _SOURCE_PRIORITY[cur.source]:
            if cur is not None and cur.is_reply:
                d.is_reply = True
            kept[key] = d
        elif d.is_reply:
            cur.is_reply = True
    events = sorted(kept.values(), key=lambda d: (d.at, d.borrower_id, d.channel, d.rank, d.label))
    for d in events:
        if d.instruction_id in touches:
            touches[d.instruction_id].events.append(d)
    return _Stream(touches, events)


def _ist_time(dt: datetime) -> Tuple[str, str]:
    return dt.date().isoformat(), dt.strftime("%H:%M:%S")


def _final(rows: List[dict], columns: List[str]) -> List[dict]:
    return [{k: r[k] for k in columns} for r in rows]


# ---------- the five sheets ----------
def aici(ledger: Ledger, start: TimeArg = None, end: TimeArg = None, *, campaign_id: Optional[str] = None,
         hash_ids: bool = False, salt: Optional[str] = None, stream: Optional[_Stream] = None) -> List[dict]:
    """Attempted Intensity = dispatched touches (ATTEMPT_STATUSES, incl. spine-only touch_sent) scheduled in
    the period, per customer per channel. Connected Intensity = those of them whose best disposition
    reached the borrower (Delivered/Read/Clicked/Responded; Connected for calls), judged on every
    outcome known now, even if the receipt arrived after the period end."""
    lo, hi = window(ledger, start, end)
    s = stream or collect(ledger, campaign_id)
    agg: Dict[Tuple[str, str], List[int]] = defaultdict(lambda: [0, 0])
    for t in s.touches.values():
        if t.attempted and _in(t.at, lo, hi):
            a = agg[(t.borrower_id, t.channel)]
            a[0] += 1
            a[1] += int(t.reached)
    rows = [{"Customer": customer_ref(b, hash_ids, salt), "Channel": ch, "Attempted Intensity": n,
             "Connected Intensity": k} for (b, ch), (n, k) in agg.items()]
    rows.sort(key=lambda r: (r["Customer"], r["Channel"]))
    return _final(rows, SHEET_COLUMNS["aici"])


def data_dump(ledger: Ledger, start: TimeArg = None, end: TimeArg = None, *, campaign_id: Optional[str] = None,
              hash_ids: bool = False, salt: Optional[str] = None, stream: Optional[_Stream] = None) -> List[dict]:
    """Every disposition event in the period, one row each, IST date and time, chronological."""
    lo, hi = window(ledger, start, end)
    rows = []
    for d in (stream or collect(ledger, campaign_id)).events:
        if _in(d.at, lo, hi):
            dt, tm = _ist_time(d.at)
            rows.append({"Customer": customer_ref(d.borrower_id, hash_ids, salt), "Channel": d.channel,
                         "Date": dt, "Time": tm, "Disposition": d.label, "_r": d.rank})
    rows.sort(key=lambda r: (r["Date"], r["Time"], r["Customer"], r["Channel"], r["_r"]))
    return _final(rows, SHEET_COLUMNS["data_dump"])


def trails(ledger: Ledger, start: TimeArg = None, end: TimeArg = None, *, campaign_id: Optional[str] = None,
           hash_ids: bool = False, salt: Optional[str] = None, stream: Optional[_Stream] = None) -> List[dict]:
    """The ordered trail of dispositions per customer per channel in the period (chronological; equal
    times in ordinal order), joined with ' > ', e.g. 'Sent > Delivered > Sent > Responded'."""
    lo, hi = window(ledger, start, end)
    trail: Dict[Tuple[str, str], List[str]] = defaultdict(list)
    for d in (stream or collect(ledger, campaign_id)).events:  # already in (time, rank) order
        if _in(d.at, lo, hi):
            trail[(d.borrower_id, d.channel)].append(d.label)
    rows = [{"Customer": customer_ref(b, hash_ids, salt), "Channel": ch, "Disposition": TRAIL_SEPARATOR.join(labels)}
            for (b, ch), labels in trail.items()]
    rows.sort(key=lambda r: (r["Customer"], r["Channel"]))
    return _final(rows, SHEET_COLUMNS["trails"])


def ftd(ledger: Ledger, start: TimeArg = None, end: TimeArg = None, *, campaign_id: Optional[str] = None,
        hash_ids: bool = False, salt: Optional[str] = None, stream: Optional[_Stream] = None) -> List[dict]:
    """Latest Best Disposition = the highest ordinal label reached in the period per customer per
    channel (ties -> the latest). A later, lower status never replaces it."""
    lo, hi = window(ledger, start, end)
    per: Dict[Tuple[str, str], List[Disposition]] = defaultdict(list)
    for d in (stream or collect(ledger, campaign_id)).events:
        if _in(d.at, lo, hi):
            per[(d.borrower_id, d.channel)].append(d)
    rows = [{"Customer": customer_ref(b, hash_ids, salt), "Channel": ch, "Latest Best Disposition": _best(ds).label}
            for (b, ch), ds in per.items()]
    rows.sort(key=lambda r: (r["Customer"], r["Channel"]))
    return _final(rows, SHEET_COLUMNS["ftd"])


def hour_bucket(dt: datetime) -> str:
    """IST hour bucket, e.g. '2026-11-20 10:00-11:00'."""
    h = dt.replace(minute=0, second=0, microsecond=0)
    return f"{h.date().isoformat()} {h:%H:%M}-{(h + timedelta(hours=1)):%H:%M}"


def hourly(ledger: Ledger, start: TimeArg = None, end: TimeArg = None, *, campaign_id: Optional[str] = None,
           hash_ids: bool = False, salt: Optional[str] = None, stream: Optional[_Stream] = None) -> List[dict]:
    """One row per attempt in the period: the customer, the IST hour bucket it went out in, the channel."""
    lo, hi = window(ledger, start, end)
    rows = []
    for t in (stream or collect(ledger, campaign_id)).touches.values():
        if t.attempted and _in(t.at, lo, hi):
            rows.append({"Customer": customer_ref(t.borrower_id, hash_ids, salt), "Time": hour_bucket(t.at),
                         "Channel": t.channel, "_at": t.at})
    rows.sort(key=lambda r: (r["Time"], r["Customer"], r["Channel"], r["_at"]))
    return _final(rows, SHEET_COLUMNS["hourly"])


SHEETS: Dict[str, Callable[..., List[dict]]] = {
    "aici": aici, "data_dump": data_dump, "trails": trails, "ftd": ftd, "hourly": hourly}


def report_pack(ledger: Ledger, start: TimeArg = None, end: TimeArg = None, *, campaign_id: Optional[str] = None,
                hash_ids: bool = False, salt: Optional[str] = None) -> Dict[str, List[dict]]:
    """All five sheets for one period, from a single pass over the database."""
    s = collect(ledger, campaign_id)
    return {name: fn(ledger, start, end, campaign_id=campaign_id, hash_ids=hash_ids, salt=salt, stream=s)
            for name, fn in SHEETS.items()}


# ---------- CM-02 daily per-segment report ----------
_COHORT = re.compile(r"^\s*(C\d)\b")


def _paise_inr(p: int) -> float:
    return round(p / 100, 2)


class _Bands:
    """DPD band of a loan at the START of a day (00:00 IST): payments made on/after that day are
    rolled back and payments reversed on/after it still count, so a payment today does not move
    the loan into CLEARED for today's report. Same rules as the ledger: oldest unpaid EMI decides,
    negative DPD = PRE_DUE, dataset loan without DPD = DPD_INVALID (K01 HOLD)."""

    def __init__(self, ledger: Ledger, day: date):
        self.ledger, self.c, self.day, self.cache = ledger, ledger._conn(), day.isoformat(), {}

    def of(self, loan_id: Optional[str]) -> str:
        if loan_id is None:
            return "UNKNOWN"
        if loan_id not in self.cache:
            self.cache[loan_id] = self._compute(loan_id)
        return self.cache[loan_id]

    def _compute(self, loan_id: str) -> str:
        c = self.c
        ln = c.execute("SELECT source, file_dpd FROM loans WHERE loan_id=?", (loan_id,)).fetchone()
        if ln is None:
            return "UNKNOWN"
        if ln["source"] == "dataset" and ln["file_dpd"] is None:
            return "DPD_INVALID"
        unpaid = []
        for o in c.execute("SELECT * FROM obligations WHERE loan_id=?", (loan_id,)):
            later = c.execute("SELECT COALESCE(SUM(amount_paise),0) FROM payments WHERE obligation_id=? AND paid_on>=?"
                              " AND reversed_on IS NULL", (o["obligation_id"], self.day)).fetchone()[0]
            undone = c.execute("SELECT COALESCE(SUM(amount_paise),0) FROM payments WHERE obligation_id=? AND paid_on<?"
                               " AND reversed_on IS NOT NULL AND reversed_on>=?",
                               (o["obligation_id"], self.day, self.day)).fetchone()[0]
            if o["amount_paid_paise"] - later + undone < o["amount_due_paise"]:
                unpaid.append(o["due_date"])
        if not unpaid:
            return "CLEARED"
        dpd = (date.fromisoformat(self.day) - date.fromisoformat(min(unpaid))).days
        return self.ledger.policy.band_for(dpd) or "NOT_IN_SCOPE"


def _new_metrics() -> dict:
    return {"attempts": 0, "reach": 0, "meaningful_replies": 0, "responders": set(),
            "spend_reserved_paise": 0, "spend_actual_paise": 0, "spend_committed_paise": 0, "unreconciled": 0,
            "cap_breaches": 0}


def _finish_metrics(m: dict) -> dict:
    out = dict(m)
    out["responders"] = len(m["responders"])
    for k in ("spend_reserved", "spend_actual", "spend_committed"):
        out[f"{k}_inr"] = _paise_inr(m[f"{k}_paise"])
    spend_inr = m["spend_committed_paise"] / 100
    out["replies_per_1000_inr"] = round(m["meaningful_replies"] * 1000 / spend_inr, 2) if spend_inr else None
    return out


def _learning(ledger: Ledger, day: date, borrowers: set) -> dict:
    """K09 learner snapshot if its tables exist (another branch). Never fails when they are absent."""
    c = ledger._conn()
    present = {t: _table_exists(c, t) for t in ("learner_arms", "decision_scores", "learner_context")}
    out = {"available": any(present.values()), "tables": present, "arms": [], "decision_share": [],
           "decisions_today": 0, "learner_context": None}
    if present["learner_arms"]:
        cols = _columns(c, "learner_arms")
        if {"alpha", "beta"} <= cols:
            keys = [k for k in ("band", "channel", "slot") if k in cols]
            n_col = "n_obs" if "n_obs" in cols else None
            for r in c.execute(f"SELECT * FROM learner_arms ORDER BY {', '.join(keys) or 'rowid'}"):
                a, b = float(r["alpha"]), float(r["beta"])
                out["arms"].append({**{k: r[k] for k in keys},
                                    "posterior_mean": round(a / (a + b), 4) if a + b > 0 else None,
                                    "alpha": a, "beta": b, "n": r[n_col] if n_col else None})
    if present["decision_scores"]:
        cols = _columns(c, "decision_scores")
        if "at" in cols:
            arm_cols = [k for k in ("band", "chosen_channel", "chosen_slot") if k in cols]
            counts: Dict[tuple, int] = defaultdict(int)
            total = 0
            for r in c.execute("SELECT * FROM decision_scores"):
                at = _parse_dt(ledger, r["at"])
                if at is None or at.date() != day:
                    continue
                if "borrower_id" in cols and borrowers and r["borrower_id"] not in borrowers:
                    continue
                total += 1
                counts[tuple(r[k] for k in arm_cols)] += 1
            out["decisions_today"] = total
            for key in sorted(counts, key=lambda k: tuple("" if v is None else str(v) for v in k)):
                row = dict(zip(arm_cols, key))
                out["decision_share"].append({**row, "decisions": counts[key],
                                              "share": round(counts[key] / total, 4) if total else None})
    if present["learner_context"]:
        cols = sorted(_columns(c, "learner_context"))
        n = c.execute("SELECT COUNT(*) FROM learner_context").fetchone()[0]
        out["learner_context"] = {"rows": n, "columns": cols}  # schema owned by K09; reported, not interpreted
    return out


def daily_segment_report(ledger: Ledger, campaign_id: str, day: Union[date, str]) -> dict:
    """CM-02: spend, response and learning per segment for one campaign day.

    Segments: DPD band (at the start of the day) x channel, and risk_tag cohort (C1-C5) x channel when
    the spine carries risk tags. Per segment: attempts, reach, meaningful replies (borrower replies,
    never delivery/read/click/STOP), spend reserved / actual / committed in paise and rupees, replies
    per ₹1,000 committed, cap breaches. Verified payments are reported separately (per band and per
    cohort, never per channel: a payment is not a disposition of a message). Plus K09 learning when
    its tables exist."""
    if isinstance(day, str):
        day = date.fromisoformat(day)
    c = ledger._conn()
    camp = c.execute("SELECT * FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
    if camp is None:
        raise KeyError(f"unknown campaign {campaign_id}")
    bands = _Bands(ledger, day)
    enrolled = {r["loan_id"]: r["borrower_id"] for r in
                c.execute("SELECT loan_id, borrower_id FROM enrollments WHERE campaign_id=?", (campaign_id,))}
    borrowers = set(enrolled.values())
    risk = {}
    for loan_id in enrolled:
        r = c.execute("SELECT risk_tag FROM loans WHERE loan_id=?", (loan_id,)).fetchone()
        m = _COHORT.match(r["risk_tag"] or "") if r else None
        if m:
            risk[loan_id] = m.group(1)
    has_cohorts = bool(risk)

    def cohort(loan_id):
        return risk.get(loan_id, "UNTAGGED")

    seg: Dict[tuple, dict] = defaultdict(_new_metrics)     # (band, channel)
    coh: Dict[tuple, dict] = defaultdict(_new_metrics)     # (cohort, channel)

    def both(loan_id, channel):
        out = [seg[(bands.of(loan_id), channel)]]
        if has_cohorts:
            out.append(coh[(cohort(loan_id), channel)])
        return out

    stream = collect(ledger, campaign_id)
    d_iso = day.isoformat()
    for t in stream.touches.values():
        if t.day != d_iso:
            continue
        for m in both(t.loan_id, t.channel):
            if t.attempted:
                m["attempts"] += 1
                m["reach"] += int(t.reached)
    # spend: every live reservation of the day, including queued (RESERVED) ones
    live = ",".join("?" * len(LIVE))
    for r in c.execute(f"SELECT loan_id, channel, est_cost_paise, actual_cost_paise, reconciled FROM touches"
                       f" WHERE campaign_id=? AND day=? AND status IN ({live})", (campaign_id, d_iso, *LIVE)):
        for m in both(r["loan_id"], r["channel"]):
            m["spend_reserved_paise"] += r["est_cost_paise"] or 0
            if r["reconciled"]:
                m["spend_actual_paise"] += r["actual_cost_paise"] or 0
                m["spend_committed_paise"] += r["actual_cost_paise"] or 0
            else:
                m["unreconciled"] += 1
                m["spend_committed_paise"] += r["est_cost_paise"] or 0
    # meaningful replies on this day, attributed to this campaign's touches (any earlier day)
    for d in stream.events:
        if d.is_reply and d.at.date() == day:
            t = stream.touches.get(d.instruction_id)
            if t is None:
                continue
            for m in both(t.loan_id, d.channel):
                m["meaningful_replies"] += 1
                m["responders"].add(d.borrower_id)
    # cap breaches (independent recount from the ledger) for enrolled borrowers on this day
    for b in ledger.cap_breaches():
        if b["day"] != d_iso or b["borrower_id"] not in borrowers:
            continue
        loan = c.execute("SELECT loan_id FROM obligations WHERE obligation_id=?", (b["ob"],)).fetchone()
        loan_id = loan["loan_id"] if loan else None
        seg[(b["band"], b["channel"])]["cap_breaches"] += 1
        if has_cohorts:
            coh[(cohort(loan_id), b["channel"])]["cap_breaches"] += 1

    # verified payments: separate facts, per band / cohort
    pay_band: Dict[str, dict] = defaultdict(lambda: {"verified_payments": 0, "verified_paise": 0,
                                                     "reversals": 0, "reversed_paise": 0, "payers": set()})
    pay_coh: Dict[str, dict] = defaultdict(lambda: {"verified_payments": 0, "verified_paise": 0,
                                                    "reversals": 0, "reversed_paise": 0, "payers": set()})
    for p in c.execute("SELECT * FROM payments WHERE paid_on=? OR reversed_on=?", (d_iso, d_iso)):
        verified = p["paid_on"] == d_iso and p["reversed_on"] is None
        reversed_today = p["reversed_on"] == d_iso
        if p["loan_id"] not in enrolled or not (verified or reversed_today):
            continue
        targets = [pay_band[bands.of(p["loan_id"])]] + ([pay_coh[cohort(p["loan_id"])]] if has_cohorts else [])
        for m in targets:
            if verified:
                m["verified_payments"] += 1
                m["verified_paise"] += p["amount_paise"]
                m["payers"].add(enrolled[p["loan_id"]])
            if reversed_today:
                m["reversals"] += 1
                m["reversed_paise"] += p["amount_paise"]

    def pay_rows(d, key):
        rows = []
        for k in sorted(d, key=_band_sort(ledger) if key == "band" else str):
            m = d[k]
            rows.append({key: k, "verified_payments": m["verified_payments"], "verified_paise": m["verified_paise"],
                         "verified_inr": _paise_inr(m["verified_paise"]), "payers": len(m["payers"]),
                         "reversals": m["reversals"], "reversed_paise": m["reversed_paise"],
                         "reversed_inr": _paise_inr(m["reversed_paise"])})
        return rows

    ch_order = {ch: i for i, ch in enumerate(ledger.policy.raw.get("channels", []))}
    band_key = _band_sort(ledger)
    seg_rows = [{"band": b, "channel": ch, **_finish_metrics(m)}
                for (b, ch), m in sorted(seg.items(), key=lambda kv: (band_key(kv[0][0]), ch_order.get(kv[0][1], 99), kv[0][1]))]
    coh_rows = [{"cohort": k, "channel": ch, **_finish_metrics(m)}
                for (k, ch), m in sorted(coh.items(), key=lambda kv: (kv[0][0], ch_order.get(kv[0][1], 99), kv[0][1]))]
    total = _new_metrics()
    for (_, _), m in seg.items():
        for k, v in m.items():
            if k == "responders":
                total[k] |= v
            else:
                total[k] += v
    totals = _finish_metrics(total)
    pays = pay_rows(pay_band, "band")
    totals["verified_payments"] = sum(r["verified_payments"] for r in pays)
    totals["verified_paise"] = sum(r["verified_paise"] for r in pays)
    totals["verified_inr"] = _paise_inr(totals["verified_paise"])
    notes = ["Bands are the loan's DPD band at 00:00 IST on the day (payments made that day rolled back).",
             "Meaningful replies = borrower replies (never delivery, read, click or STOP), attributed to the "
             "campaign touch they answer.",
             "replies_per_1000_inr uses committed spend: the actual charge where reconciled, else the estimate."]
    if not has_cohorts:
        notes.append("No risk_tag on the enrolled loans: cohort breakdown omitted.")
    learning = _learning(ledger, day, borrowers)
    if not learning["available"]:
        notes.append("K09 learner tables not present: learning section empty.")
    return {"report": "daily_segment", "provenance": PROVENANCE, "campaign_id": campaign_id, "day": d_iso,
            "policy_version": ledger.policy.version, "segments": seg_rows, "cohorts": coh_rows,
            "payments_by_band": pays, "payments_by_cohort": pay_rows(pay_coh, "cohort") if has_cohorts else [],
            "totals": totals, "learning": learning, "notes": notes}


def _band_sort(ledger: Ledger):
    order = {b["id"]: i for i, b in enumerate(ledger.policy.raw.get("dpd_bands", []))}
    extra = {"CLEARED": 90, "NOT_IN_SCOPE": 91, "DPD_INVALID": 92, "UNKNOWN": 93}
    return lambda b: (order.get(b, extra.get(b, 99)), str(b))


DAILY_SEGMENT_COLUMNS = ["dimension", "segment", "channel", "attempts", "reach", "meaningful_replies", "responders",
                         "spend_reserved_paise", "spend_reserved_inr", "spend_actual_paise", "spend_actual_inr",
                         "spend_committed_paise", "spend_committed_inr", "unreconciled", "replies_per_1000_inr",
                         "cap_breaches"]
DAILY_PAYMENT_COLUMNS = ["dimension", "segment", "verified_payments", "verified_paise", "verified_inr", "payers",
                         "reversals", "reversed_paise", "reversed_inr"]


def daily_segment_sheets(rep: dict) -> Dict[str, List[dict]]:
    """Flatten the daily report into CSV-ready sheets (one row per segment)."""
    segs = [{"dimension": "dpd_band", "segment": r["band"], **r} for r in rep["segments"]]
    segs += [{"dimension": "risk_cohort", "segment": r["cohort"], **r} for r in rep["cohorts"]]
    pays = [{"dimension": "dpd_band", "segment": r["band"], **r} for r in rep["payments_by_band"]]
    pays += [{"dimension": "risk_cohort", "segment": r["cohort"], **r} for r in rep["payments_by_cohort"]]
    out = {"daily_segment": [{k: r[k] for k in DAILY_SEGMENT_COLUMNS} for r in segs],
           "daily_payments": [{k: r[k] for k in DAILY_PAYMENT_COLUMNS} for r in pays]}
    if rep["learning"]["arms"]:
        out["daily_learning_arms"] = rep["learning"]["arms"]
    if rep["learning"]["decision_share"]:
        out["daily_decision_share"] = rep["learning"]["decision_share"]
    return out


# ---------- CSV ----------
def rows_to_csv(rows: List[dict], columns: Optional[List[str]] = None) -> str:
    cols = columns or (list(rows[0].keys()) if rows else [])
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({k: "" if r.get(k) is None else r.get(k) for k in cols})
    return buf.getvalue()


def export_csv(rows: List[dict], path: Union[str, Path], columns: Optional[List[str]] = None) -> Path:
    """Write rows to a UTF-8 CSV (header from `columns`, else the first row; an empty sheet keeps its
    header when the sheet's columns are known)."""
    from .safeio import atomic_write_text
    return atomic_write_text(path, rows_to_csv(rows, columns))  # temp file + rename: Ctrl-C safe


def export_pack(reports: Dict[str, List[dict]], folder: Union[str, Path]) -> List[Path]:
    """One CSV per sheet: <folder>/<name>.csv. Known sheets (also "daily_segment_<day>") keep their
    headers even when empty."""
    folder = Path(folder)
    cols = {**SHEET_COLUMNS, "daily_segment": DAILY_SEGMENT_COLUMNS, "daily_payments": DAILY_PAYMENT_COLUMNS}

    def columns_for(name: str) -> Optional[List[str]]:
        base = re.sub(r"_\d{4}-\d{2}-\d{2}$", "", name)   # daily sheets may carry a _YYYY-MM-DD suffix
        return cols.get(name) or cols.get(base)
    return [export_csv(rows, folder / f"{name}.csv", columns_for(name)) for name, rows in reports.items()]
