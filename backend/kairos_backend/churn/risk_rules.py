"""K27 risk reclassification engine (CN PRD v3 §1/§2, first of the two independent rule engines).

    RiskEngine.load().evaluate(state, feats, event_kind, at, scope) -> RiskDecision | None

Deterministic: rules run in file order; the first rule whose `on`, scope and `when` match AND whose
action would change something applies (one tier change per event). Cooldown and hysteresis
(policy/K27-auto-churn/risk_rules.json) stop flapping; a blocked change is returned as
`suppressed` so the audit shows what would have happened and why it did not.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional

from .cohorts import load_json
from .dsl import evaluate, fields_of, in_scope, validate
from .state import TIERS, BorrowerState, known_feature


@dataclass
class RiskDecision:
    rule_id: str
    from_tier: str
    to_tier: str
    applied: bool
    reason: str
    freeze: Optional[bool] = None
    why: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


def _hours(a: str, b: datetime) -> float:
    return (b - datetime.fromisoformat(a)).total_seconds() / 3600


class RiskEngine:
    def __init__(self, doc: Mapping[str, Any]):
        self.doc = dict(doc)
        self.tiers = tuple(self.doc.get("tiers", TIERS))
        self.cooldown = float(self.doc.get("cooldown_hours", 48))
        self.reversal = float(self.doc.get("reversal_cooldown_hours", 120))
        self.rules = self.doc["rules"]
        ids = set()
        for r in self.rules:
            if r["id"] in ids:
                raise ValueError(f"duplicate risk rule {r['id']}")
            ids.add(r["id"])
            validate(r["when"], r["id"])
            bad = [f for f in fields_of(r["when"]) if not known_feature(f)]
            if bad:
                raise ValueError(f"{r['id']}: unknown fields {bad}")
            act = r["action"]
            for k in ("set", "floor", "max", "min"):
                if k in act and act[k] not in self.tiers:
                    raise ValueError(f"{r['id']}: unknown tier {act[k]}")

    @classmethod
    def load(cls, base: Optional[Path] = None) -> "RiskEngine":
        return cls(load_json("risk_rules.json", base))

    @property
    def version(self) -> str:
        return self.doc.get("policy_version", "unversioned")

    def _target(self, cur: str, act: dict) -> str:
        i = self.tiers.index(cur)
        if "set" in act:
            j = self.tiers.index(act["set"])
        elif "floor" in act:
            j = max(i, self.tiers.index(act["floor"]))
        elif "shift" in act:
            j = min(max(i + int(act["shift"]), 0), len(self.tiers) - 1)
            if "max" in act:
                j = min(j, max(i, self.tiers.index(act["max"])))
            if "min" in act:
                j = max(j, min(i, self.tiers.index(act["min"])))
        else:
            j = i
        return self.tiers[j]

    def evaluate(self, st: BorrowerState, feats: Mapping[str, Any], kind: str, at: datetime) -> Optional[RiskDecision]:
        suppressed = None
        for r in self.rules:
            on = r.get("on", ["*"])
            if kind not in on and not ("*" in on and kind != "tick"):
                continue
            if not in_scope(r.get("scope"), st.campaign_id, st.cohort, st.region or st.static.get("state")):
                continue
            ok, why = evaluate(r["when"], feats)
            if not ok:
                continue
            act = r["action"]
            if "freeze" in act:
                if bool(act["freeze"]) == st.risk_frozen:
                    continue
                return RiskDecision(r["id"], st.risk_tier, st.risk_tier, True,
                                    "FROZEN" if act["freeze"] else "UNFROZEN", bool(act["freeze"]), why)
            if st.risk_frozen and not r.get("applies_when_frozen"):
                suppressed = suppressed or RiskDecision(r["id"], st.risk_tier, self._target(st.risk_tier, act),
                                                        False, "FROZEN_HARDSHIP", None, why)
                continue
            to = self._target(st.risk_tier, act)
            if to == st.risk_tier:
                continue
            direction = 1 if self.tiers.index(to) > self.tiers.index(st.risk_tier) else -1
            if not r.get("bypass_cooldown") and st.risk_since:
                h = _hours(st.risk_since, at)
                if st.risk_last_dir and direction != st.risk_last_dir and h < self.reversal:
                    suppressed = suppressed or RiskDecision(r["id"], st.risk_tier, to, False,
                                                            f"HYSTERESIS({h:.0f}h<{self.reversal:.0f}h)", None, why)
                    continue
                if h < self.cooldown:
                    suppressed = suppressed or RiskDecision(r["id"], st.risk_tier, to, False,
                                                            f"COOLDOWN({h:.0f}h<{self.cooldown:.0f}h)", None, why)
                    continue
            return RiskDecision(r["id"], st.risk_tier, to, True, "UP" if direction > 0 else "DOWN", None, why)
        return suppressed
