"""Engine room: `/demo/engines`, the churn engine and the learning loop, live (SIMULATED).

One projector page that replays ONE full-mode campaign (replay/live_full.py: K03 spine → K27 auto-churn → K09
learner → K06 Gate "Aladdin" → executor + mock adapters → DEV simulator → OutcomeFinalizer → learner) and shows,
day by day, what the two engines did. No new simulation logic: the run is `FullCampaign` on its own temp dir with
`day_seconds=0`; this module only WATCHES it (`_WatchRun` records the K27 engine's own bucket counts at each day
end, `_Campaign` keeps each event's position in its day for playback) and turns the published events into a
compact feed. Playback (play / pause / speed / step day / reset) is client-side display pacing over that feed:
the results never change.

    GET /demo/engines                 the page (static/engine_room.html: plain HTML/CSS/JS, no external assets)
    GET /demo/engines/api/run         ?seed=42&borrowers=300&days=14 → {"status": "computing"} while the run is
                                      computed (~8 s at 300 × 14), then {"status": "ready", feed…}. Cached per
                                      (seed, borrowers, days), at most CACHE_MAX runs

Feed (`status: "ready"`): `events` [{i, d, p, t, c, k, b?, n?, s?, x?}] (id, day, position in the day 0–1, clock,
component, kind, borrower id, synthetic first name, summary, detail subset), `truth` [{day, cohort, risk, layer,
lifecycle}] (the K27 engine's own counts at each day end; the page's counts are folded from `events` and must
equal them), `catalogue` (bucket labels and the K27 rule texts from policy/K27-auto-churn), `final` (the run's
report). Every number is SIMULATED. No PII: `BRW-<loan id>` and the K03 generator's synthetic first name only.
"""
from __future__ import annotations

import json
import threading
from collections import Counter, OrderedDict
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse

from replay import live as L
from replay import live_full as F

router = APIRouter(prefix="/demo/engines", tags=["Engine room: churn engine + learning loop (SIMULATED)"])
PAGE = Path(__file__).with_name("static") / "engine_room.html"
POLICY_DIR = Path(__file__).resolve().parents[2] / "policy" / "K27-auto-churn"
DEFAULT_SEED, DEFAULT_BORROWERS, DEFAULT_DAYS = 42, 300, 14
CACHE_MAX = 4
PROVENANCE = "SIMULATED"

# detail fields the page needs, per (component, kind); events not listed travel with no detail (they only pulse
# the loop and feed counters). `s` (the runner's own summary sentence) is kept for SUMMARY kinds only.
KEEP = {
    ("spine", "portfolio_loaded"): ("borrowers", "loans_enrolled", "bands"),
    ("spine", "budget_scope"): ("per_case_paise", "pool_paise", "days"),
    ("spine", "day_start"): ("date", "weekday", "quiet_day"),
    ("borrower", "world_change"): ("channel", "factor"),
    ("churn", "assigned"): ("cohort", "risk_tier", "layer", "lifecycle", "journey"),
    ("churn", "moved"): ("engine", "from", "to", "rule", "trigger", "why", "cohort", "risk_tier", "layer", "lifecycle"),
    ("learner", "contact"): ("channel", "band", "lane", "slot", "reply_chance", "replies_per_inr"),
    ("learner", "wait"): ("rule",),
    ("learner", "posterior"): ("channel", "band", "slot", "before", "after", "direction"),
    ("learner", "segments"): ("ref", "segments", "rule", "pacing"),
    ("learner", "switch"): ("from_channel", "to_channel", "band", "basis", "after_drift", "counts"),
    ("aladdin", "allowed"): ("stage", "channel"),
    ("aladdin", "blocked"): ("stage", "label", "rule", "channel"),
    ("aladdin", "rechecked"): ("stage", "channel"),
    ("executor", "dispatched"): ("channel", "cost_paise"),
    ("borrower", "replied"): ("channel",),
    ("borrower", "stop"): ("channel",),
    ("payments", "verified"): ("amount_paise", "source"),
    ("outcomes", "finalized"): ("count", "by_status", "learner_updates"),
    ("reports", "day_end"): ("cap_breaches", "sent", "spend_paise"),
}
SUMMARY = {("learner", "posterior"), ("learner", "segments"), ("learner", "switch"), ("borrower", "world_change"),
           ("payments", "verified"), ("spine", "day_start"), ("reports", "day_end")}


def churn_snapshot(churn) -> dict:
    """The K27 engine's own counts per bucket (cohort, risk tier, contact layer, lifecycle status)."""
    states = [churn.state(b) for b in churn.borrower_ids()]
    out = {}
    for key, attr in (("cohort", "cohort"), ("risk", "risk_tier"), ("layer", "layer"), ("lifecycle", "lifecycle")):
        out[key] = dict(sorted(Counter(str(getattr(s, attr)) for s in states).items()))
    return out


class _WatchRun(F.FullStackRun):
    """The full-mode run, unchanged, plus the churn engine's own bucket counts after every day (read-only)."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.truth: list = []

    def send_phase(self, day: int):
        super().send_phase(day)
        self.truth.append({"day": day + 1, **churn_snapshot(self.churn)})


class _Campaign(F.FullCampaign):
    """FullCampaign with the watcher run and each event's position in its day (for client-side playback)."""
    run_cls = _WatchRun

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.positions: list = []

    def _publish(self, e: dict):
        self.positions.append(float(e.get("_pos") or 0.0))
        super()._publish(e)


def compact(e: dict, pos: float) -> dict:
    """One published full-mode event → the page's compact form (only the fields it shows)."""
    key = (e["component"], e["kind"])
    out = {"i": e["id"], "d": e["day"], "p": round(min(1.0, max(0.0, pos)), 5), "t": e["t"][11:16],
           "c": e["component"], "k": e["kind"]}
    if e.get("borrower_id"):
        out["b"] = e["borrower_id"]
        if e.get("first_name"):
            out["n"] = e["first_name"]
    if key in SUMMARY and e.get("summary"):
        out["s"] = e["summary"]
    keep = KEEP.get(key)
    if keep:
        d = e.get("detail") or {}
        out["x"] = {k: d[k] for k in keep if k in d and d[k] is not None}
    return out


def fold_buckets(events: list, upto_day: Optional[int] = None) -> dict:
    """Bucket counts folded from the feed's churn events (what the page does): per borrower, the last
    assigned / moved event wins. `upto_day` stops after that day's events."""
    cur: dict = {}
    for e in events:
        if upto_day is not None and e["d"] > upto_day:
            break
        if e["c"] != "churn":
            continue
        x = e.get("x") or {}
        cur[e["b"]] = (x.get("cohort"), x.get("risk_tier"), x.get("layer"), x.get("lifecycle"))
    out = {}
    for i, key in enumerate(("cohort", "risk", "layer", "lifecycle")):
        out[key] = dict(sorted(Counter(str(v[i]) for v in cur.values()).items()))
    return out


def _policy(name: str) -> dict:
    try:
        return json.loads((POLICY_DIR / name).read_text())
    except (OSError, ValueError):
        return {}


def _rule_texts() -> dict:
    """rule id → its own description in the K27 policy files (DRAFT), for Technical mode."""
    out: dict = {}

    def walk(o, src):
        if isinstance(o, dict):
            rid = o.get("id")
            if isinstance(rid, str) and "." in rid:
                txt = o.get("description") or o.get("why") or o.get("note") or ""
                if txt and rid not in out:
                    out[rid] = {"text": str(txt), "source": src}
            for v in o.values():
                walk(v, src)
        elif isinstance(o, list):
            for v in o:
                walk(v, src)

    for f in ("reassign_rules.json", "risk_rules.json", "journeys.json"):
        walk(_policy(f), f"policy/K27-auto-churn/{f}")
    return out


def catalogue() -> dict:
    cohorts = _policy("cohorts.json").get("cohorts") or []
    layers = _policy("journeys.json").get("layers") or {}
    return {
        "cohorts": [{"id": c.get("id"), "text": c.get("description") or ""} for c in cohorts if c.get("id")],
        "layers": {k: {"name": v.get("name"), "channels": v.get("channels")} for k, v in layers.items()},
        "risk_tiers": _policy("risk_rules.json").get("tiers") or ["LOW", "MEDIUM", "HIGH", "CRITICAL"],
        "lifecycle": ["ACTIVE", "PAUSED_PTP", "PAUSED_CLAIM", "PAUSED_HOLD", "HARDSHIP_HUMAN", "EXHAUSTED_CAMPAIGN",
                      "STOPPED_OPT_OUT", "PAID_EXIT"],
        "rules": _rule_texts(),
        "bands": {b: L.BAND_WORD.get(b, b) for b in F.SEGMENT_BANDS},
        "channels": {c: L.CH_NAME.get(c, c) for c in F.SEGMENT_CHANNELS},
        "policy_status": "DRAFT (policy/K27-auto-churn)",
    }


def build_feed(seed: int = DEFAULT_SEED, borrowers: int = DEFAULT_BORROWERS, days: int = DEFAULT_DAYS,
               workdir: Optional[str] = None) -> dict:
    """Run the full-mode campaign once (real modules, SIMULATED data) and return the page's feed."""
    c = _Campaign(seed=seed, borrowers=borrowers, days=days, day_seconds=0, workdir=workdir).start()
    c.join(600)
    if c.status != "finished":
        raise RuntimeError(c.error or f"run ended with status {c.status}")
    events = [compact(e, p) for e, p in zip(c.events, c.positions)]
    st = c.state(0, 1)
    rep = (st.get("final") or {}).get("report") or {}
    return {
        "status": "ready", "provenance": PROVENANCE, "seed": seed, "borrowers": borrowers, "days": days,
        "start": F.k09.START.isoformat(), "drift_day": F.DRIFT_DAY + 1, "day_seconds": 15.0,
        "label": "SIMULATED: synthetic borrowers (K03 generator), DEV simulator with a day-7 WhatsApp drift, mock "
                 "channel adapters (dry-run), the real K27 / K09 / K06 modules",
        "budget": c.budget, "events": events, "truth": list(getattr(c._run, "truth", [])),
        "catalogue": catalogue(),
        "final": {k: rep.get(k) for k in ("contacts_sent", "spend_paise", "meaningful_replies", "verified_payments",
                                          "opt_outs", "cap_breaches_k01", "k15_violations") if k in rep},
        "counters": st.get("counters", {}).get("kairos"),
    }


class _Runs:
    """Feeds per (seed, borrowers, days), computed once in a background thread."""

    def __init__(self):
        self._lock = threading.Lock()
        self._feeds: "OrderedDict[tuple, dict]" = OrderedDict()
        self._busy: dict = {}

    def get(self, key: tuple) -> dict:
        with self._lock:
            if key in self._feeds:
                self._feeds.move_to_end(key)
                return self._feeds[key]
            if key not in self._busy:
                t = threading.Thread(target=self._compute, args=(key,), daemon=True, name=f"engine-room-{key}")
                self._busy[key] = {"status": "computing", "seed": key[0], "borrowers": key[1], "days": key[2]}
                t.start()
            return dict(self._busy[key])

    def _compute(self, key: tuple):
        try:
            feed = build_feed(*key)
        except Exception as exc:  # surfaced to the page; the API keeps running
            with self._lock:
                self._busy[key] = {"status": "error", "error": f"{type(exc).__name__}: {exc}", "seed": key[0]}
            return
        with self._lock:
            self._feeds[key] = feed
            while len(self._feeds) > CACHE_MAX:
                self._feeds.popitem(last=False)
            self._busy.pop(key, None)


runs = _Runs()


@router.get("", response_class=HTMLResponse)
def engine_room_page():
    return HTMLResponse(PAGE.read_text(encoding="utf-8"))


@router.get("/api/run")
def engine_room_run(seed: int = Query(DEFAULT_SEED, ge=0, le=2**31 - 1),
                    borrowers: int = Query(DEFAULT_BORROWERS, ge=50, le=600),
                    days: int = Query(DEFAULT_DAYS, ge=8, le=21)):
    return runs.get((seed, borrowers, days))
