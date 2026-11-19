"""K27 cohort reassignment engine (CN PRD v3 §1/§2, the second independent rule engine).

    ReassignEngine.load().decide(state, feats, event_kind, cohorts, at, as_of) -> CohortDecision

Order (policy/K27-auto-churn/reassign_rules.json):
  1. precedence — STOP, verified payment, hardship, K13 holds ALWAYS win (no pin, rule or override beats them)
  2. an approved, unexpired manual override (maker-checker)
  3. rules — first match: `assign` (outright move, optional pin) or `recompute` (first-match cohorts)
  4. a pin is a priority FLOOR: while pinned, recompute may only move to a HIGHER-priority cohort
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Optional

from .cohorts import UNASSIGNED, CohortSet, load_json
from .dsl import evaluate, fields_of, in_scope, validate
from .state import BorrowerState, known_feature


@dataclass
class CohortDecision:
    cohort: str
    rule_id: str
    entry: Optional[str] = None          # restart | continue | None (journey default)
    pin: dict = field(default_factory=dict)
    why: list = field(default_factory=list)
    blocked: Optional[str] = None        # e.g. a pin kept the borrower

    def as_dict(self) -> dict:
        return asdict(self)


class ReassignEngine:
    def __init__(self, doc: Mapping[str, Any]):
        self.doc = dict(doc)
        self.precedence = self.doc["precedence"]
        self.rules = self.doc["rules"]
        for r in list(self.precedence) + list(self.rules):
            validate(r["when"], r["id"])
            bad = [f for f in fields_of(r["when"]) if not known_feature(f)]
            if bad:
                raise ValueError(f"{r['id']}: unknown fields {bad}")
            if r in self.rules and not (r.get("assign") or r.get("recompute")):
                raise ValueError(f"{r['id']}: needs assign or recompute")

    @classmethod
    def load(cls, base: Optional[Path] = None) -> "ReassignEngine":
        return cls(load_json("reassign_rules.json", base))

    @property
    def version(self) -> str:
        return self.doc.get("policy_version", "unversioned")

    def safety(self, feats: Mapping[str, Any]) -> Optional[CohortDecision]:
        for p in self.precedence:
            ok, why = evaluate(p["when"], feats)
            if ok:
                return CohortDecision(p["cohort"], p["id"], why=why)
        return None

    def decide(self, st: BorrowerState, feats: Mapping[str, Any], kind: str, cohorts: CohortSet, at: datetime,
               as_of: date) -> CohortDecision:
        region = st.region or st.static.get("state")
        s = self.safety(feats)
        if s is not None:
            return s
        ov = st.override
        if ov and ov.get("status") == "APPROVED" and (not ov.get("until") or ov["until"] >= as_of.isoformat()):
            return CohortDecision(ov["cohort"], f"OVERRIDE.{ov['override_id']}", entry=ov.get("entry"),
                                  why=[f"manual override by {ov.get('maker')} approved by {ov.get('checker')}: "
                                       f"{ov.get('reason')}"])
        pin = st.pin if st.pin and st.pin.get("until", "") >= as_of.isoformat() else {}
        for r in self.rules:
            on = r.get("on", ["*"])
            if kind not in on and not ("*" in on and kind != "tick"):
                continue
            if not in_scope(r.get("scope"), st.campaign_id, st.cohort, region):
                continue
            ok, why = evaluate(r["when"], feats)
            if not ok:
                continue
            if r.get("assign"):
                target = r["assign"]
                if target not in cohorts.by_id and target != UNASSIGNED:
                    raise ValueError(f"{r['id']}: unknown cohort {target}")
                p = {}
                if r.get("pin_days"):
                    p = {"cohort": target, "until": (as_of + timedelta(days=int(r["pin_days"]))).isoformat(),
                         "rule": r["id"]}
                return CohortDecision(target, r["id"], entry=r.get("entry"), pin=p, why=why)
            cid, expl = cohorts.assign(feats, st.borrower_id, st.campaign_id, region)
            if pin and pin["cohort"] != cid and cohorts.order.get(cid, 10**6) > cohorts.order.get(pin["cohort"], 10**6):
                return CohortDecision(pin["cohort"], pin["rule"], pin=pin, why=expl["matched"],
                                      blocked=f"PINNED until {pin['until']} (recompute said {cid})")
            return CohortDecision(cid, r["id"], entry=r.get("entry"), why=expl["matched"])
        return CohortDecision(st.cohort or UNASSIGNED, "NO_RULE", why=["no reassignment rule matched"])
