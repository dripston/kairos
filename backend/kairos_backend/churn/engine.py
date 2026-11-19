"""K27 auto-churn engine: live channel outcomes -> risk tier, cohort, journey, layer, lifecycle.

    eng = ChurnEngine(db_path)                          # ":memory:" for tests / replay
    eng.enroll(borrower_id, static_profile(...), campaign_id, as_of)
    eng.process(event)                                  # normalized event in -> transitions out (idempotent)
    eng.tick(as_of)                                     # daily: PTP due checks, expiries, tick rules, snapshots
    eng.allowed_channels(borrower_id, day)              # today's journey candidates for K09 / RL v2
    eng.recommend_layer(borrower_id, rec, evidence)     # RL v2 layer advice, accepted / rejected by rule
    eng.borrower(id) / eng.trace(id) / eng.coverage(as_of) / eng.analytics(...)

Pipeline per event (deterministic; every step returns rule ids + evidence event ids):
    dedupe (event_id) -> fold into features (older events never overwrite newer values)
    -> RISK engine (risk_rules.json; cooldown + hysteresis)  -> COHORT engine (reassign_rules.json;
    STOP / paid / hardship / K13 holds always win) -> journey entry (restart | continue)
    -> LIFECYCLE (stop rules + fsm.TRANSITIONS) -> LAYER rules (L1..L4)
Every change is a row in churn_transitions (engine, from, to, rule_id, evidence, region, campaign) and
the full per-event explanation is stored with the event, so GET /churn/borrowers/{id}/trace can replay
the reasoning step by step. Nothing here sends: K09 ranks the candidates, the K06 Supervisor gates.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta
from typing import Any, Iterable, Mapping, Optional, Union

from . import fsm
from .cohorts import UNASSIGNED, CohortSet, load_json
from .journeys import Journeys, StopPolicy
from .reassign_rules import ReassignEngine
from .risk_rules import RiskEngine
from .state import CHANNEL_LAYER, CHANNELS, TIERS, BorrowerState, ChurnEvent, _iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS churn_state (borrower_id TEXT PRIMARY KEY, state TEXT NOT NULL, updated_at TEXT);
CREATE TABLE IF NOT EXISTS churn_events (event_id TEXT PRIMARY KEY, borrower_id TEXT, occurred_at TEXT, kind TEXT,
  payload TEXT, result TEXT, seq INTEGER);
CREATE INDEX IF NOT EXISTS ix_churn_events_b ON churn_events (borrower_id, occurred_at);
CREATE TABLE IF NOT EXISTS churn_transitions (id INTEGER PRIMARY KEY AUTOINCREMENT, borrower_id TEXT, at TEXT, day TEXT,
  engine TEXT, from_value TEXT, to_value TEXT, rule_id TEXT, evidence TEXT, event_id TEXT, campaign_id TEXT,
  region TEXT, cohort TEXT, layer TEXT, detail TEXT);
CREATE INDEX IF NOT EXISTS ix_churn_tr_b ON churn_transitions (borrower_id, id);
CREATE TABLE IF NOT EXISTS churn_daily (day TEXT, campaign_id TEXT, cohort TEXT, risk_tier TEXT, region TEXT,
  layer TEXT, lifecycle TEXT, n INTEGER, PRIMARY KEY (day, campaign_id, cohort, risk_tier, region, layer, lifecycle));
CREATE TABLE IF NOT EXISTS churn_ticks (day TEXT PRIMARY KEY, at TEXT, summary TEXT);
CREATE TABLE IF NOT EXISTS churn_overrides (override_id TEXT PRIMARY KEY, borrower_id TEXT, cohort TEXT, entry TEXT,
  until TEXT, maker TEXT, checker TEXT, status TEXT, reason TEXT, created_at TEXT, decided_at TEXT);
CREATE TABLE IF NOT EXISTS churn_manual (manual_id TEXT PRIMARY KEY, borrower_id TEXT, day TEXT, channel TEXT,
  template_id TEXT, at TEXT, actor TEXT, reason TEXT, status TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS churn_config (key TEXT PRIMARY KEY, value TEXT, version TEXT, actor TEXT, at TEXT);
"""

# DSL field -> state flag key (for evidence event ids)
FIELD_FLAGS = {"stop": ["stop"], "hardship": ["hardship_until"], "hardship.supervisor": ["hardship_supervisor"],
               "dispute": ["dispute"], "k13.hold": ["k13_hold"], "ptp.status": ["ptp_status"], "ptp.date": ["ptp_date"],
               "ptp.days_to_date": ["ptp_date"], "ptp.days_since_change": ["ptp_status"],
               "ptp.broken_count": ["ptp_status"], "payment.state": ["payment_state"],
               "payment.claimed": ["payment_state"], "payment.verified": ["payment_state"],
               "payment.cleared": ["payment_state", "payment_cleared"], "payment.claim_hours": ["claim_at"],
               "intent.last": ["last_intent"], "reply.days_since": ["last_reply_at"],
               "reply.last_channel": ["last_reply_channel"]}


class ChurnEngine:
    def __init__(self, db_path: Union[str, "Any"] = ":memory:", cohorts: Optional[CohortSet] = None,
                 risk: Optional[RiskEngine] = None, reassign: Optional[ReassignEngine] = None,
                 journeys: Optional[Journeys] = None, ledger=None, stop_policy: Optional[StopPolicy] = None,
                 hitl=None, require_checker: bool = True, provenance: str = "SIMULATED", trace_events: bool = True):
        self.cohorts = cohorts or CohortSet.load()
        self.risk = risk or RiskEngine.load()
        self.reassign = reassign or ReassignEngine.load()
        self.journeys = journeys or Journeys.load()
        self.ledger = ledger
        self.stop_policy = stop_policy
        self.hitl = hitl
        self.require_checker = require_checker
        self.provenance = provenance
        self.trace_events = trace_events
        self._lock = threading.RLock()
        self.db = sqlite3.connect(str(db_path), check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self._cache: dict[str, BorrowerState] = {}
        self._seq = self.db.execute("SELECT COALESCE(MAX(seq),0) FROM churn_events").fetchone()[0]
        self.initial_tier = self.cohorts.doc.get("initial_risk_tier_by_cn10_cohort", {})
        self._load_saved_config()

    # ---------- config ----------
    def versions(self) -> dict:
        return {"cohorts": self.cohorts.version, "risk_rules": self.risk.version,
                "reassign_rules": self.reassign.version, "journeys": self.journeys.version}

    def _load_saved_config(self) -> None:
        r = self.db.execute("SELECT value FROM churn_config WHERE key='cohorts'").fetchone()
        if r:
            self.cohorts = CohortSet(json.loads(r["value"]))

    def put_cohorts(self, doc: Mapping[str, Any], actor: str, reason: str = "") -> dict:
        """Replace the cohort definitions (validated first; refused as a whole if anything is wrong).
        The change is versioned and audited; membership is recomputed on the next event / tick."""
        if not reason:
            raise ValueError("reason is required")
        cs = CohortSet(doc)  # raises ValueError
        with self._lock:
            self.db.execute("INSERT INTO churn_config VALUES ('cohorts',?,?,?,?) ON CONFLICT(key) DO UPDATE SET "
                            "value=excluded.value, version=excluded.version, actor=excluded.actor, at=excluded.at",
                            (json.dumps(cs.doc), cs.version, actor, datetime.now().isoformat(timespec="seconds")))
            self.db.execute("INSERT INTO churn_transitions (borrower_id, at, engine, from_value, to_value, rule_id, "
                            "detail) VALUES ('*', ?, 'config', ?, ?, 'CONFIG.PUT_COHORTS', ?)",
                            (datetime.now().isoformat(timespec="seconds"), self.cohorts.version, cs.version,
                             json.dumps({"actor": actor, "reason": reason, "n": len(cs.cohorts)})))
            self.cohorts = cs
        return {"version": cs.version, "cohorts": [c["id"] for c in cs.cohorts]}

    # ---------- state I/O ----------
    def state(self, borrower_id: str) -> BorrowerState:
        st = self._cache.get(borrower_id)
        if st is None:
            r = self.db.execute("SELECT state FROM churn_state WHERE borrower_id=?", (borrower_id,)).fetchone()
            if r is None:
                raise KeyError(borrower_id)
            st = BorrowerState.from_json(r["state"])
            self._cache[borrower_id] = st
        return st

    def _save(self, st: BorrowerState) -> None:
        self.db.execute("INSERT INTO churn_state VALUES (?,?,?) ON CONFLICT(borrower_id) DO UPDATE SET "
                        "state=excluded.state, updated_at=excluded.updated_at",
                        (st.borrower_id, st.to_json(), st.last_event_at or st.as_of))

    def borrower_ids(self) -> list[str]:
        return [r[0] for r in self.db.execute("SELECT borrower_id FROM churn_state ORDER BY borrower_id")]

    # ---------- enrolment ----------
    def enroll(self, borrower_id: str, static: dict, campaign_id: str, as_of: date,
               region: Optional[str] = None) -> dict:
        """Day-0 enrolment (idempotent): initial risk tier from the CN10 cohort, then cohort, journey, layer,
        lifecycle exactly as for any event (so Day 0 reproduces CN10 C1-C5)."""
        with self._lock:
            try:
                st = self.state(borrower_id)
                return {"borrower_id": borrower_id, "enrolled": False, "cohort": st.cohort}
            except KeyError:
                pass
            st = BorrowerState(borrower_id, static=dict(static), campaign_id=campaign_id,
                               region=region or static.get("state", ""))
            st.risk_tier = self.initial_tier.get(static.get("cn10_cohort", ""), "MEDIUM")
            at = datetime.combine(as_of, time(0, 0))
            st.as_of = as_of.isoformat()
            self._cache[borrower_id] = st
            res = self._evaluate(st, None, "enroll", at, as_of, {}, event_id=f"enroll:{borrower_id}")
            self._save(st)
            return {"borrower_id": borrower_id, "enrolled": True, **res}

    def enroll_from_spine(self, ledger, campaign_id: str, as_of: date, loan_ids: Optional[Iterable[str]] = None) -> int:
        """Enrol borrowers straight from the K03 spine (ledger-visible fields only)."""
        from replay import cn10
        from .state import static_profile
        c = ledger._conn()
        q = ("SELECT l.*, b.state AS b_state, b.language AS b_lang, o.obligation_id, o.amount_due_paise, o.due_date"
             " FROM loans l JOIN borrowers b ON b.borrower_id=l.borrower_id JOIN obligations o ON o.loan_id=l.loan_id"
             " WHERE o.installment_no=(SELECT MIN(installment_no) FROM obligations WHERE loan_id=l.loan_id"
             " AND amount_paid_paise < amount_due_paise)")
        rows = [r for r in c.execute(q).fetchall() if loan_ids is None or r["loan_id"] in set(loan_ids)]
        cut = cn10.high_ticket_threshold([r["total_outstanding_paise"] or r["principal_paise"] for r in rows])
        n = 0
        for r in rows:
            dpd = (as_of - date.fromisoformat(r["due_date"])).days
            prof = static_profile(dpd=dpd, as_of=as_of, pos_paise=r["total_outstanding_paise"] or r["principal_paise"],
                                  product=r["product"], risk_tag=r["risk_tag"] or "", bounce_string=r["bounce_string"] or "",
                                  state=r["b_state"] or "", language=r["b_lang"] or "en", emi_paise=r["amount_due_paise"],
                                  nach=bool(r["nach_mandate"]) if r["nach_mandate"] is not None else None,
                                  represent=bool(r["represent_eligible"]), loan_id=r["loan_id"],
                                  obligation_id=r["obligation_id"], high_ticket_pos_paise=cut)
            n += int(self.enroll(r["borrower_id"], prof, campaign_id, as_of)["enrolled"])
        return n

    # ---------- events ----------
    def process(self, event: Union[Mapping[str, Any], ChurnEvent]) -> dict:
        ev = event if isinstance(event, ChurnEvent) else ChurnEvent.from_dict(event)
        with self._lock:
            prior = self.db.execute("SELECT result FROM churn_events WHERE event_id=?", (ev.event_id,)).fetchone()
            if prior is not None:
                out = json.loads(prior["result"])
                return {**out, "duplicate": True, "transitions": [], "applied": False}
            st = self.state(ev.borrower_id)
            late = bool(st.last_event_at) and _iso(ev.occurred_at) < st.last_event_at
            at = max(ev.occurred_at, datetime.fromisoformat(st.last_event_at)) if st.last_event_at else ev.occurred_at
            as_of = max(at.date(), date.fromisoformat(st.as_of)) if st.as_of else at.date()
            changed = st.apply(ev)
            res = self._evaluate(st, ev, ev.kind, at, as_of, changed, event_id=ev.event_id)
            res.update({"event_id": ev.event_id, "duplicate": False, "late": late, "changed": changed,
                        "provenance": ev.provenance})
            self._seq += 1
            self.db.execute("INSERT INTO churn_events VALUES (?,?,?,?,?,?,?)",
                            (ev.event_id, ev.borrower_id, _iso(ev.occurred_at), ev.kind,
                             json.dumps(ev.as_dict(), default=str),
                             json.dumps(res if self.trace_events else {k: res[k] for k in ("event_id", "transitions")},
                                        default=str), self._seq))
            self._save(st)
            return res

    # ---------- the deterministic pipeline ----------
    def _evidence(self, st: BorrowerState, why: list, event_id: Optional[str]) -> list:
        ev = []
        if event_id:
            ev.append(event_id)
        text = " ".join(why)
        for fld, keys in FIELD_FLAGS.items():
            if fld + " " in text:
                ev += [st.ev[k] for k in keys if k in st.ev]
        for ch in CHANNELS:
            if f"{ch}." in text and st.latest.get(ch):
                ev.append(f"touch:{st.latest[ch]}")
        out, seen = [], set()
        for e in ev:
            if e not in seen:
                seen.add(e)
                out.append(e)
        return out

    def _record(self, st: BorrowerState, at: datetime, as_of: date, engine: str, frm: Any, to: Any, rule: str,
                why: list, event_id: Optional[str], detail: Optional[dict] = None) -> dict:
        evidence = self._evidence(st, why, event_id)
        row = {"engine": engine, "from": frm, "to": to, "rule_id": rule, "evidence": evidence, "why": why,
               "at": _iso(at), "day": as_of.isoformat(), **(detail or {})}
        self.db.execute("INSERT INTO churn_transitions (borrower_id, at, day, engine, from_value, to_value, rule_id, "
                        "evidence, event_id, campaign_id, region, cohort, layer, detail) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (st.borrower_id, _iso(at), as_of.isoformat(), engine, str(frm), str(to), rule,
                         json.dumps(evidence), event_id, st.campaign_id, st.region or st.static.get("state", ""),
                         st.cohort, st.layer, json.dumps({"why": why, **(detail or {})}, default=str)))
        return row

    def _evaluate(self, st: BorrowerState, ev: Optional[ChurnEvent], kind: str, at: datetime, as_of: date,
                  changed: dict, event_id: Optional[str], trigger: Optional[str] = None) -> dict:
        trace, transitions = [], []
        extra = ev.view() if ev is not None else {}
        feats = st.features(as_of, extra)
        # 1. risk
        rd = self.risk.evaluate(st, feats, kind, at)
        if rd is not None:
            trace.append({"step": "risk", **rd.as_dict()})
            if rd.applied:
                if rd.freeze is not None:
                    st.risk_frozen = rd.freeze
                    transitions.append(self._record(st, at, as_of, "risk_freeze", not rd.freeze, rd.freeze,
                                                    rd.rule_id, rd.why, event_id))
                else:
                    days_in = (as_of - date.fromisoformat(st.risk_since[:10])).days if st.risk_since else None
                    transitions.append(self._record(st, at, as_of, "risk", rd.from_tier, rd.to_tier, rd.rule_id, rd.why,
                                                    event_id, {"days_in_from": days_in}))
                    st.risk_last_dir = 1 if TIERS.index(rd.to_tier) > TIERS.index(rd.from_tier) else -1
                    st.risk_tier, st.risk_since = rd.to_tier, _iso(at)
                feats = st.features(as_of, extra)
        else:
            trace.append({"step": "risk", "result": "no rule matched"})
        # 2. cohort
        cd = self.reassign.decide(st, feats, kind, self.cohorts, at, as_of)
        trace.append({"step": "cohort", **cd.as_dict()})
        if cd.pin and cd.pin != st.pin and not cd.blocked:
            st.pin = cd.pin
        if cd.cohort != st.cohort:
            old = st.cohort
            days_in = (as_of - date.fromisoformat(st.cohort_since[:10])).days if st.cohort_since else None
            st.cohort, st.cohort_since, st.cohort_rule = cd.cohort, _iso(at), cd.rule_id
            transitions.append(self._record(st, at, as_of, "cohort", old or "-", cd.cohort, cd.rule_id, cd.why,
                                            event_id, {"days_in_from": days_in}))
            jid = self.cohorts.journey_of(cd.cohort, self.journeys.doc)
            if jid != st.journey_id:
                j = self.journeys.get(jid)
                entry = cd.entry or j.get("entry", "restart")
                old_j = st.journey_id
                st.journey_id = jid
                if entry == "restart" or not st.journey_entered:
                    st.journey_entered = as_of.isoformat()
                new_layer = j.get("start_layer", "L2") if entry == "restart" or not old_j else \
                    self.journeys._clamp(j, st.layer)
                transitions.append(self._record(st, at, as_of, "journey", old_j or "-", jid, cd.rule_id, cd.why,
                                                event_id, {"entry": entry}))
                if new_layer != st.layer:
                    transitions.append(self._record(st, at, as_of, "layer", st.layer, new_layer,
                                                    f"JOURNEY.{entry.upper()}", [f"journey {jid} entry {entry}"],
                                                    event_id))
                    st.layer, st.layer_since = new_layer, _iso(at)
            feats = st.features(as_of, extra)
        # 3. lifecycle (stop states)
        new, rule, until, why = self.journeys.lifecycle(st, feats, self.stop_policy)
        trig = trigger or kind
        if ev is not None and ev.kind == "payment" and str(ev.attrs.get("state", "")).upper() == "REVERSED":
            trig = "payment_reversed"
        if ev is not None and ev.kind == "opt_in":
            trig = "opt_in"
        ok = fsm.allowed(st.lifecycle, new, trig)
        trace.append({"step": "lifecycle", "state": new, "rule_id": rule, "until": until, "why": why,
                      "allowed": ok, "from": st.lifecycle})
        if new != st.lifecycle:
            if ok:
                transitions.append(self._record(st, at, as_of, "lifecycle", st.lifecycle, new, rule, why, event_id,
                                                {"until": until, "trigger": trig}))
                st.lifecycle, st.lifecycle_since, st.lifecycle_rule, st.lifecycle_until = new, _iso(at), rule, until
                if new == "EXHAUSTED_CAMPAIGN" and self.hitl is not None:
                    try:
                        self.hitl.act(st.borrower_id, "handoff", "system:k27", "operator",
                                      "no-response budget exhausted for this campaign", now=at)
                    except Exception as e:  # optional hook: never breaks the engine
                        trace.append({"step": "hitl_handoff", "error": str(e)})
            else:
                trace.append({"step": "lifecycle_blocked", "from": st.lifecycle, "to": new,
                              "need": fsm.TRANSITIONS.get(st.lifecycle, {}).get(new)})
        elif until != st.lifecycle_until:
            st.lifecycle_until = until
        feats = st.features(as_of, extra)
        # 4. layer
        ld = self.journeys.layer_move(st, feats, kind, at, extra.get("event.layer"))
        if ld is not None:
            trace.append({"step": "layer", **ld.as_dict()})
            transitions.append(self._record(st, at, as_of, "layer", ld.from_layer, ld.to_layer, ld.rule_id, ld.why,
                                            event_id, {"slot_shift": ld.slot_shift, "avoid_slot": ld.avoid_slot}))
            st.layer, st.layer_since = ld.to_layer, _iso(at)
            if ld.avoid_slot:
                st.layer_avoid_slots = sorted(set(st.layer_avoid_slots) | {ld.avoid_slot})
        else:
            trace.append({"step": "layer", "result": f"stay {st.layer}"})
        return {"borrower_id": st.borrower_id, "transitions": transitions, "trace": trace,
                "state": self.summary(st), "versions": self.versions()}

    def summary(self, st: BorrowerState) -> dict:
        return {"cohort": st.cohort, "risk_tier": st.risk_tier, "risk_frozen": st.risk_frozen,
                "journey": st.journey_id, "journey_entered": st.journey_entered, "layer": st.layer,
                "lifecycle": st.lifecycle, "lifecycle_until": st.lifecycle_until, "pin": st.pin,
                "campaign_id": st.campaign_id, "region": st.region or st.static.get("state", ""),
                "campaign_attempts": st.campaign_attempts, "campaign_replies": st.campaign_replies}

    # ---------- daily tick ----------
    def tick(self, as_of: date, borrower_ids: Optional[Iterable[str]] = None) -> dict:
        """Daily evaluation (idempotent per day): PTP due -> KEPT/BROKEN, hold/claim/pin expiries, tick rules,
        cohort sizes snapshot. Older days never re-run after a newer one."""
        with self._lock:
            done = self.db.execute("SELECT summary FROM churn_ticks WHERE day=?", (as_of.isoformat(),)).fetchone()
            if done is not None and borrower_ids is None:
                return {**json.loads(done["summary"]), "duplicate": True}
            last = self.db.execute("SELECT MAX(day) FROM churn_ticks").fetchone()[0]
            if last and as_of.isoformat() < last and borrower_ids is None:
                return {"day": as_of.isoformat(), "refused": "OLDER_THAN_LAST_TICK", "last_tick": last}
            at = datetime.combine(as_of, time(0, 5))
            moves = Counter()
            self.db.execute("BEGIN")
            try:
                for bid in (borrower_ids or self.borrower_ids()):
                    st = self.state(bid)
                    if st.as_of and st.as_of > as_of.isoformat():
                        continue
                    st.as_of = as_of.isoformat()
                    fl = st.flags
                    if fl.get("ptp_status") == "OPEN" and fl.get("ptp_date") and fl["ptp_date"] < as_of.isoformat():
                        paid = fl.get("payment_state") == "VERIFIED"
                        r = self.process_locked(ChurnEvent(f"tick:{as_of}:{bid}:ptp", bid, "ptp", at,
                                                           attrs={"state": "KEPT" if paid else "BROKEN",
                                                                  "ptp_id": fl["ptp_date"]}, source="k27.tick"))
                        for t in r["transitions"]:
                            moves[t["engine"]] += 1
                    tick_at = max(at, datetime.fromisoformat(st.last_event_at)) if st.last_event_at else at
                    res = self._evaluate(st, None, "tick", tick_at, as_of, {}, event_id=f"tick:{as_of}")
                    for t in res["transitions"]:
                        moves[t["engine"]] += 1
                    self._save(st)
                self._snapshot(as_of)
                summary = {"day": as_of.isoformat(), "borrowers": len(self._cache), "moves": dict(moves)}
                if borrower_ids is None:
                    self.db.execute("INSERT OR REPLACE INTO churn_ticks VALUES (?,?,?)",
                                    (as_of.isoformat(), _iso(at), json.dumps(summary)))
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise
            return summary

    def process_locked(self, ev: ChurnEvent) -> dict:
        return self.process(ev)

    def _snapshot(self, as_of: date) -> None:
        self.db.execute("DELETE FROM churn_daily WHERE day=?", (as_of.isoformat(),))
        rows = Counter()
        for r in self.db.execute("SELECT borrower_id FROM churn_state"):
            st = self.state(r[0])
            rows[(st.campaign_id, st.cohort, st.risk_tier, st.region or st.static.get("state", ""), st.layer,
                  st.lifecycle)] += 1
        self.db.executemany("INSERT INTO churn_daily VALUES (?,?,?,?,?,?,?,?)",
                            [(as_of.isoformat(), *k, n) for k, n in rows.items()])

    # ---------- K09 / RL v2 interface ----------
    def allowed_channels(self, borrower_id: str, day: date, consented: Optional[set] = None) -> dict:
        """Today's journey step for one borrower: allowed channels (layer-gated, step channels first),
        step purpose/tier/intensity, slots to avoid. K09 ranks among these; K06 gates."""
        st = self.state(borrower_id)
        feats = st.features(max(day, date.fromisoformat(st.as_of)) if st.as_of else day)
        out = self.journeys.allowed(st, feats, day, consented)
        manual = [dict(r) for r in self.db.execute(
            "SELECT * FROM churn_manual WHERE borrower_id=? AND day=? AND status='SCHEDULED'",
            (borrower_id, day.isoformat()))]
        if manual:
            # PRD §5.7 WATCH OUT: manual + automated on the same loan/day = double contact -> manual replaces
            out = {**out, "channels": [m["channel"] for m in manual], "manual": manual,
                   "reason": "MANUAL_SCHEDULE_REPLACES_JOURNEY_STEP"}
        out["borrower_id"], out["cohort"], out["risk_tier"] = borrower_id, st.cohort, st.risk_tier
        return out

    def recommend_layer(self, borrower_id: str, rec: Mapping[str, Any], evidence: Mapping[str, Any],
                        at: Optional[datetime] = None) -> dict:
        with self._lock:
            st = self.state(borrower_id)
            at = at or (datetime.fromisoformat(st.last_event_at) if st.last_event_at else datetime.now())
            as_of = date.fromisoformat(st.as_of) if st.as_of else at.date()
            feats = st.features(as_of)
            out = self.journeys.recommend_layer(st, feats, rec, evidence, at)
            why = [out["reason"], f"evidence={dict(evidence)}", f"recommender={rec.get('source', 'k09-rl-v2')}"]
            if out["accepted"]:
                out["transition"] = self._record(st, at, as_of, "layer", st.layer, out["to_layer"], out["rule_id"], why,
                                                 rec.get("recommendation_id"), {"source": "recommendation"})
                st.layer, st.layer_since = out["to_layer"], _iso(at)
            else:
                self._record(st, at, as_of, "layer_rejected", st.layer, out["to_layer"], out["rule_id"], why,
                             rec.get("recommendation_id"), {"source": "recommendation"})
            self._save(st)
            return out

    # ---------- campaigns, overrides, manual schedule ----------
    def start_campaign(self, campaign_id: str, borrower_ids: Iterable[str], as_of: date, actor: str = "system") -> dict:
        """Next campaign: no-response counters reset; EXHAUSTED_CAMPAIGN may return to ACTIVE (trigger new_campaign)."""
        moved = 0
        with self._lock:
            for bid in borrower_ids:
                st = self.state(bid)
                st.campaign_id, st.campaign_attempts, st.campaign_spend_paise, st.campaign_replies = campaign_id, 0, 0, 0
                at = datetime.combine(as_of, time(0, 1))
                r = self._evaluate(st, None, "campaign", at, as_of, {}, event_id=f"campaign:{campaign_id}",
                                   trigger="new_campaign")
                moved += len(r["transitions"])
                self._save(st)
        return {"campaign_id": campaign_id, "transitions": moved}

    def request_override(self, borrower_id: str, cohort: str, maker: str, reason: str, until: Optional[str] = None,
                         entry: Optional[str] = None, at: Optional[datetime] = None) -> dict:
        """Maker step. Never beats STOP / paid / hardship / K13 holds (precedence). Reason mandatory."""
        if not reason:
            raise ValueError("reason is required")
        if cohort not in self.cohorts.by_id and cohort != UNASSIGNED:
            raise ValueError(f"unknown cohort {cohort}")
        self.state(borrower_id)
        oid = "OVR-" + uuid.uuid5(uuid.NAMESPACE_URL, f"{borrower_id}|{cohort}|{maker}|{reason}|{until}").hex[:12]
        at = at or datetime.now()
        status = "PENDING" if self.require_checker else "APPROVED"
        with self._lock:
            self.db.execute("INSERT OR IGNORE INTO churn_overrides VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                            (oid, borrower_id, cohort, entry, until, maker, None, status, reason, _iso(at), None))
            if status == "APPROVED":
                return self._apply_override(oid, maker, at)
        return {"override_id": oid, "status": status}

    def approve_override(self, override_id: str, checker: str, approve: bool = True,
                         at: Optional[datetime] = None) -> dict:
        """Checker step: a different person than the maker (maker-checker, compatible with k06/hitl-control)."""
        r = self.db.execute("SELECT * FROM churn_overrides WHERE override_id=?", (override_id,)).fetchone()
        if r is None:
            raise KeyError(override_id)
        if r["status"] != "PENDING":
            return {"override_id": override_id, "status": r["status"]}
        if checker == r["maker"]:
            raise PermissionError("maker and checker must be different people")
        at = at or datetime.now()
        with self._lock:
            if not approve:
                self.db.execute("UPDATE churn_overrides SET status='REJECTED', checker=?, decided_at=? WHERE override_id=?",
                                (checker, _iso(at), override_id))
                st = self.state(r["borrower_id"])
                self._record(st, at, at.date(), "override_rejected", st.cohort, r["cohort"], f"OVERRIDE.{override_id}",
                             [f"rejected by {checker}"], None)
                return {"override_id": override_id, "status": "REJECTED"}
            self.db.execute("UPDATE churn_overrides SET status='APPROVED', checker=?, decided_at=? WHERE override_id=?",
                            (checker, _iso(at), override_id))
            return self._apply_override(override_id, checker, at)

    def _apply_override(self, oid: str, checker: str, at: datetime) -> dict:
        r = dict(self.db.execute("SELECT * FROM churn_overrides WHERE override_id=?", (oid,)).fetchone())
        st = self.state(r["borrower_id"])
        st.override = {"override_id": oid, "cohort": r["cohort"], "until": r["until"], "entry": r["entry"],
                       "maker": r["maker"], "checker": checker, "reason": r["reason"], "status": "APPROVED"}
        as_of = max(at.date(), date.fromisoformat(st.as_of)) if st.as_of else at.date()
        res = self._evaluate(st, None, "override", at, as_of, {}, event_id=f"override:{oid}", trigger="human")
        self._save(st)
        return {"override_id": oid, "status": "APPROVED", "transitions": res["transitions"], "state": res["state"]}

    def clear_override(self, borrower_id: str, actor: str, reason: str, at: Optional[datetime] = None) -> dict:
        if not reason:
            raise ValueError("reason is required")
        st = self.state(borrower_id)
        at = at or datetime.now()
        with self._lock:
            old = st.override
            st.override = {}
            self._record(st, at, at.date(), "override_cleared", old.get("cohort"), "-", "OVERRIDE.CLEARED",
                         [f"{actor}: {reason}"], None)
            res = self._evaluate(st, None, "override", at, at.date(), {}, event_id=None, trigger="human")
            self._save(st)
        return {"cleared": bool(old), "state": res["state"]}

    def schedule_manual(self, borrower_id: str, channel: str, at: datetime, actor: str, reason: str,
                        template_id: Optional[str] = None) -> dict:
        """PRD §5.7 manual scheduler hook: replaces that day's automated journey step for the borrower (no double
        contact). Dispatch still goes through the K06 gate (consent, caps, holds) — this only plans."""
        if not reason:
            raise ValueError("reason is required")
        if channel not in CHANNELS:
            raise ValueError(f"unknown channel {channel}")
        st = self.state(borrower_id)
        mid = "MAN-" + uuid.uuid5(uuid.NAMESPACE_URL, f"{borrower_id}|{channel}|{_iso(at)}").hex[:12]
        with self._lock:
            self.db.execute("INSERT OR IGNORE INTO churn_manual VALUES (?,?,?,?,?,?,?,?,?,?)",
                            (mid, borrower_id, at.date().isoformat(), channel, template_id, _iso(at), actor, reason,
                             "SCHEDULED", datetime.now().isoformat(timespec="seconds")))
            self._record(st, at, at.date(), "manual_schedule", "-", channel, f"MANUAL.{mid}", [f"{actor}: {reason}"],
                         None)
        return {"manual_id": mid, "status": "SCHEDULED", "lifecycle": st.lifecycle,
                "note": "K06 gate decides at reserve and dispatch; a paused / stopped borrower will be refused there"}

    def cancel_manual(self, manual_id: str, actor: str, reason: str) -> dict:
        if not reason:
            raise ValueError("reason is required")
        n = self.db.execute("UPDATE churn_manual SET status='CANCELED' WHERE manual_id=? AND status='SCHEDULED'",
                            (manual_id,)).rowcount
        return {"manual_id": manual_id, "canceled": bool(n)}

    # ---------- reads ----------
    def history(self, borrower_id: str, limit: int = 500) -> list[dict]:
        out = []
        for r in self.db.execute("SELECT * FROM churn_transitions WHERE borrower_id=? ORDER BY id LIMIT ?",
                                 (borrower_id, limit)):
            d = dict(r)
            d["evidence"] = json.loads(d["evidence"] or "[]")
            d["detail"] = json.loads(d["detail"] or "{}")
            out.append(d)
        return out

    def borrower(self, borrower_id: str, as_of: Optional[date] = None) -> dict:
        st = self.state(borrower_id)
        day = as_of or (date.fromisoformat(st.as_of) if st.as_of else date.today())
        return {"borrower_id": borrower_id, "provenance": self.provenance, **self.summary(st),
                "journey_day": self.journeys.day(st, st.features(day), day),
                "today": self.allowed_channels(borrower_id, day),
                "static": {k: v for k, v in st.static.items() if k not in ("bounce_string",)},
                "flags": st.flags, "history": self.history(borrower_id), "versions": self.versions(),
                "fsm_states": list(fsm.STATES)}

    def trace(self, borrower_id: str, as_of: Optional[date] = None) -> dict:
        """Step-by-step deterministic explanation: every event in event-time order with what it changed and every
        rule decision, then how today's state follows from the current features."""
        st = self.state(borrower_id)
        steps = []
        for r in self.db.execute("SELECT event_id, occurred_at, kind, payload, result FROM churn_events "
                                 "WHERE borrower_id=? ORDER BY occurred_at, seq", (borrower_id,)):
            res = json.loads(r["result"])
            steps.append({"event_id": r["event_id"], "occurred_at": r["occurred_at"], "kind": r["kind"],
                          "late": res.get("late"), "changed": res.get("changed"), "decisions": res.get("trace"),
                          "transitions": [(t["engine"], t["from"], t["to"], t["rule_id"], t["evidence"])
                                          for t in res.get("transitions", [])]})
        day = as_of or (date.fromisoformat(st.as_of) if st.as_of else date.today())
        feats = st.features(day)
        cohort, expl = self.cohorts.assign(feats, borrower_id, st.campaign_id, st.region)
        lc = self.journeys.lifecycle(st, feats, self.stop_policy)
        now = {"as_of": day.isoformat(), "cohort": st.cohort, "cohort_rule": st.cohort_rule,
               "membership_now": expl, "safety": (self.reassign.safety(feats).as_dict()
                                                   if self.reassign.safety(feats) else None),
               "lifecycle": {"state": st.lifecycle, "rule": st.lifecycle_rule, "until": st.lifecycle_until,
                             "recomputed": {"state": lc[0], "rule": lc[1], "why": lc[3]}},
               "risk": {"tier": st.risk_tier, "since": st.risk_since, "frozen": st.risk_frozen},
               "layer": {"layer": st.layer, "since": st.layer_since, "avoid_slots": st.layer_avoid_slots},
               "journey": {"id": st.journey_id, "entered": st.journey_entered,
                           "day": self.journeys.day(st, feats, day)},
               "today": self.journeys.allowed(st, feats, day)}
        return {"borrower_id": borrower_id, "provenance": self.provenance, "versions": self.versions(),
                "steps": steps, "transitions": self.history(borrower_id), "now": now}

    def coverage(self, as_of: date) -> dict:
        rows = []
        for bid in self.borrower_ids():
            st = self.state(bid)
            rows.append((bid, st.features(as_of), st.campaign_id, st.region))
        out = self.cohorts.coverage(rows)
        out["as_of"] = as_of.isoformat()
        out["current_membership"] = dict(Counter(self.state(b).cohort for b in self.borrower_ids()))
        return out

    def analytics(self, campaign_id: Optional[str] = None, region: Optional[str] = None,
                  cohort: Optional[str] = None, layer: Optional[str] = None) -> dict:
        """PRD §5.6 cuts: cohort sizes over time, flows between cohorts and layers (per region), risk transition
        matrix, rule-trigger frequency, average days-to-transition, per-channel dispositions by cohort.
        Filters: campaign, region (state), cohort, layer. SIMULATED unless fed LIVE events."""
        filt, args = [], []
        for col, val in (("campaign_id", campaign_id), ("region", region), ("cohort", cohort), ("layer", layer)):
            if val:
                filt.append(f"{col}=?")
                args.append(val)
        where = (" AND " + " AND ".join(filt)) if filt else ""
        sizes = defaultdict(dict)
        for r in self.db.execute(f"SELECT day, cohort, SUM(n) n FROM churn_daily WHERE 1=1{where} GROUP BY day, cohort "
                                 "ORDER BY day", args):
            sizes[r["day"]][r["cohort"]] = r["n"]
        by_layer = defaultdict(dict)
        for r in self.db.execute(f"SELECT day, layer, SUM(n) n FROM churn_daily WHERE 1=1{where} GROUP BY day, layer", args):
            by_layer[r["day"]][r["layer"]] = r["n"]
        by_lifecycle = defaultdict(dict)
        for r in self.db.execute(f"SELECT day, lifecycle, SUM(n) n FROM churn_daily WHERE 1=1{where} GROUP BY day, lifecycle",
                                 args):
            by_lifecycle[r["day"]][r["lifecycle"]] = r["n"]
        tw = where.replace("cohort=?", "(cohort=? OR from_value=?)") if cohort else where
        targs = []
        for col, val in (("campaign_id", campaign_id), ("region", region), ("cohort", cohort), ("layer", layer)):
            if val:
                targs += [val, val] if col == "cohort" else [val]

        def flows(engine):
            out = defaultdict(int)
            per_region = defaultdict(lambda: defaultdict(int))
            for r in self.db.execute(f"SELECT from_value, to_value, region, COUNT(*) n FROM churn_transitions "
                                     f"WHERE engine=?{tw} GROUP BY from_value, to_value, region", [engine] + targs):
                out[f"{r['from_value']} -> {r['to_value']}"] += r["n"]
                per_region[r["region"] or "-"][f"{r['from_value']} -> {r['to_value']}"] += r["n"]
            return dict(sorted(out.items(), key=lambda kv: -kv[1])), {k: dict(v) for k, v in per_region.items()}
        cohort_flows, cohort_flows_region = flows("cohort")
        layer_flows, layer_flows_region = flows("layer")
        lifecycle_flows, _ = flows("lifecycle")
        risk_matrix = defaultdict(lambda: defaultdict(int))
        dtt = defaultdict(list)
        for r in self.db.execute(f"SELECT from_value, to_value, detail FROM churn_transitions WHERE engine='risk'{tw}", targs):
            risk_matrix[r["from_value"]][r["to_value"]] += 1
            d = json.loads(r["detail"] or "{}").get("days_in_from")
            if d is not None:
                dtt[f"{r['from_value']} -> {r['to_value']}"].append(d)
        rules = Counter()
        for r in self.db.execute(f"SELECT rule_id, COUNT(*) n FROM churn_transitions WHERE 1=1{tw} GROUP BY rule_id", targs):
            rules[r["rule_id"]] = r["n"]
        disp = defaultdict(lambda: defaultdict(Counter))
        for bid in self.borrower_ids():
            st = self.state(bid)
            if (campaign_id and st.campaign_id != campaign_id) or (region and (st.region or st.static.get("state")) != region) \
                    or (cohort and st.cohort != cohort) or (layer and st.layer != layer):
                continue
            feats = st.features(date.fromisoformat(st.as_of) if st.as_of else date.today())
            for ch in CHANNELS:
                disp[st.cohort][ch][feats[f"{ch}.status"]] += 1
        return {"provenance": self.provenance, "filters": {"campaign": campaign_id, "region": region, "cohort": cohort,
                                                           "layer": layer},
                "cohort_sizes_by_day": dict(sizes), "layer_sizes_by_day": dict(by_layer),
                "lifecycle_by_day": dict(by_lifecycle),
                "cohort_flows": cohort_flows, "cohort_flows_by_region": cohort_flows_region,
                "layer_flows": layer_flows, "layer_flows_by_region": layer_flows_region,
                "lifecycle_flows": lifecycle_flows,
                "risk_transition_matrix": {k: dict(v) for k, v in risk_matrix.items()},
                "avg_days_to_transition": {k: round(sum(v) / len(v), 2) for k, v in dtt.items()},
                "rule_trigger_frequency": dict(rules.most_common()),
                "dispositions_by_cohort": {c: {ch: dict(cnt) for ch, cnt in v.items()} for c, v in disp.items()},
                "versions": self.versions()}

    def close(self) -> None:
        self.db.close()
