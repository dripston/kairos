"""K27 journeys, channel layers and stop rules (policy/K27-auto-churn/journeys.json).

    Journeys.load()
    j.day(state, feats, as_of)                     -> journey day (CN10: DPD cycle day; behaviour: since entry)
    j.allowed(state, feats, as_of)                 -> today's steps + allowed channels (layer-gated) = candidates
    j.layer_move(state, feats, kind, at)           -> LayerDecision | None   (deterministic layer rules)
    j.recommend_layer(state, feats, rec, evidence, at) -> accept / reject an RL layer recommendation, by rule
    j.lifecycle(state, feats, at, stop_policy)     -> (lifecycle state, rule id, until, why)

A journey step yields CANDIDATES only; K09 (Rechurn / RL v2) ranks channel x slot among the allowed
channels and the K06 Supervisor gates each send. Nothing here sends anything.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from .cohorts import load_json
from .dsl import evaluate, fields_of, in_scope, validate
from .state import CHANNEL_LAYER, LAYERS, LAYER_CHANNELS, BorrowerState, known_feature

StopPolicy = Callable[[Mapping[str, Any], BorrowerState], Optional[dict]]


@dataclass
class LayerDecision:
    rule_id: str
    from_layer: str
    to_layer: str
    slot_shift: bool = False
    avoid_slot: Optional[str] = None
    why: list = field(default_factory=list)
    source: str = "rule"

    def as_dict(self) -> dict:
        return asdict(self)


def _lrank(layer: str) -> int:
    return LAYERS.index(layer)


class Journeys:
    def __init__(self, doc: Mapping[str, Any]):
        self.doc = dict(doc)
        self.journeys = self.doc["journeys"]
        self.layer_rules = self.doc.get("layer_rules", [])
        self.stop_rules = self.doc.get("stop_rules", [])
        self.cooldown_h = float(self.doc.get("layer_move_cooldown_hours", 24))
        self.rec = self.doc.get("layer_recommendation", {})
        for jid, j in self.journeys.items():
            for l in j.get("layers", []):
                if l not in LAYERS:
                    raise ValueError(f"journey {jid}: unknown layer {l}")
            if j.get("start_layer") not in LAYERS:
                raise ValueError(f"journey {jid}: bad start_layer")
            for d in j.get("days", []):
                for s in d["steps"]:
                    for ch in s["channels"]:
                        if ch not in CHANNEL_LAYER:
                            raise ValueError(f"journey {jid} day {d['day']}: unknown channel {ch}")
        for r in self.layer_rules + self.stop_rules:
            validate(r["when"], r["id"])
            bad = [f for f in fields_of(r["when"]) if not known_feature(f) and not f.startswith("campaign.no_response")]
            if bad:
                raise ValueError(f"{r['id']}: unknown fields {bad}")

    @classmethod
    def load(cls, base: Optional[Path] = None) -> "Journeys":
        return cls(load_json("journeys.json", base))

    @property
    def version(self) -> str:
        return self.doc.get("policy_version", "unversioned")

    def get(self, jid: str) -> dict:
        return self.journeys.get(jid) or self.journeys["NONE"]

    # ---------- days and steps ----------
    def day(self, st: BorrowerState, feats: Mapping[str, Any], as_of: date) -> Optional[int]:
        j = self.get(st.journey_id)
        length = int(j.get("length", 1))
        if j.get("anchor") == "dpd_cycle":
            dpd = feats.get("dpd")
            if dpd is None or dpd < 1:
                return None
            if dpd > length and not j.get("repeat", True):
                return None
            return (dpd - 1) % length + 1
        if not st.journey_entered:
            return None
        n = (as_of - date.fromisoformat(st.journey_entered)).days + 1
        if n < 1:
            return None
        if n > length:
            if not j.get("repeat"):
                return None
            n = (n - 1) % length + 1
        return n

    def steps(self, jid: str, day: Optional[int]) -> list[dict]:
        if day is None:
            return []
        for d in self.get(jid).get("days", []):
            if d["day"] == day:
                return d["steps"]
        return []

    def layer_channels(self, j: dict, layer: str) -> list[str]:
        mode = j.get("layer_mode", "ceiling")
        if layer == "L4":
            return []
        if mode == "off":
            return list(j.get("channels", []))
        if mode == "exact":
            ok = set(LAYER_CHANNELS[layer])
        else:
            ok = {ch for l in LAYERS[:_lrank(layer) + 1] for ch in LAYER_CHANNELS[l]}
        return [ch for ch in j.get("channels", []) if ch in ok]

    def allowed(self, st: BorrowerState, feats: Mapping[str, Any], as_of: date,
                consented: Optional[set] = None) -> dict:
        """Today's journey candidates. channels = ordered allowed channels (step channels first)."""
        j = self.get(st.journey_id)
        out = {"journey": st.journey_id, "layer": st.layer, "lifecycle": st.lifecycle, "day": None, "steps": [],
               "channels": [], "substituted": False, "avoid_slots": list(st.layer_avoid_slots), "reason": ""}
        if st.lifecycle != "ACTIVE":
            out["reason"] = f"LIFECYCLE_{st.lifecycle}"
            return out
        if not j.get("automated", True):
            out["reason"] = "JOURNEY_NOT_AUTOMATED" + ("_HANDOFF" if j.get("handoff") else "")
            return out
        if st.layer == "L4":
            out["reason"] = "LAYER_L4_HUMAN"
            return out
        day = self.day(st, feats, as_of)
        out["day"] = day
        steps = self.steps(st.journey_id, day)
        if not steps:
            out["reason"] = "NO_STEP_TODAY"
            return out
        layer_ok = self.layer_channels(j, st.layer)
        if consented is not None:
            layer_ok = [c for c in layer_ok if c in consented]
        chans, sub = [], False
        for s in steps:
            want = [c for c in s["channels"] if c in layer_ok]
            if not want:
                sub = True
            for c in want:
                if c not in chans:
                    chans.append(c)
            out["steps"].append({**s, "allowed": want})
        if sub or j.get("k09_free_within_layer", True):
            for c in layer_ok:
                if c not in chans:
                    chans.append(c)
        out["channels"], out["substituted"] = chans, sub and bool(chans)
        out["reason"] = "STEP" if chans else "NO_ALLOWED_CHANNEL"
        return out

    # ---------- layers ----------
    def _clamp(self, j: dict, layer: str) -> str:
        if layer == "L4":
            return "L4"
        allowed = [l for l in j.get("layers", LAYERS) if l != "L4"] or ["L2"]
        if layer in allowed:
            return layer
        # nearest allowed layer, preferring the cheaper one
        return min(allowed, key=lambda l: (abs(_lrank(l) - _lrank(layer)), _lrank(l)))

    def layer_move(self, st: BorrowerState, feats: Mapping[str, Any], kind: str, at: datetime,
                   event_layer: Optional[str] = None) -> Optional[LayerDecision]:
        j = self.get(st.journey_id)
        cool = bool(st.layer_since) and (at - datetime.fromisoformat(st.layer_since)).total_seconds() / 3600 < self.cooldown_h
        for r in self.layer_rules:
            on = r.get("on")
            if on and kind not in on:
                continue
            frm = r.get("from", "*")
            if frm != "*" and st.layer not in frm:
                continue
            if not in_scope(r.get("scope"), st.campaign_id, st.cohort, st.region or st.static.get("state")):
                continue
            ok, why = evaluate(r["when"], feats)
            if not ok:
                continue
            to = r["to"]
            if to == "event.layer":
                to = event_layer
            elif to == "journey.start":
                to = j.get("start_layer", "L2")
            if not to:
                continue
            to = self._clamp(j, to)
            if to == st.layer:
                continue
            if r.get("only_down") and _lrank(to) >= _lrank(st.layer):
                continue
            if cool and not r.get("bypass_cooldown"):
                continue
            avoid = None
            if r.get("slot_shift"):
                avoid = feats.get("voicebot.last_failed_slot") or feats.get("ivr.last_failed_slot")
            return LayerDecision(r["id"], st.layer, to, bool(r.get("slot_shift")), avoid, why)
        return None

    def recommend_layer(self, st: BorrowerState, feats: Mapping[str, Any], rec: Mapping[str, Any],
                        evidence: Mapping[str, Any], at: datetime) -> dict:
        """Accept or reject a K09 RL v2 layer recommendation, by rule (journeys.json layer_recommendation)."""
        to = str(rec.get("layer") or "")
        j = self.get(st.journey_id)

        def out(ok, rid, reason):
            return {"accepted": ok, "rule_id": rid, "reason": reason, "from_layer": st.layer, "to_layer": to}
        if st.lifecycle != "ACTIVE":
            return out(False, "REC.NOT_ACTIVE", f"lifecycle {st.lifecycle}")
        if to not in LAYERS:
            return out(False, "REC.UNKNOWN_LAYER", to)
        if "L4" in (to, st.layer):
            return out(False, "REC.L4_HUMAN_ONLY", "only rules or a person move into or out of L4")
        if to == st.layer:
            return out(False, "REC.SAME_LAYER", "already there")
        if to not in j.get("layers", ()):
            return out(False, "REC.OUT_OF_JOURNEY", f"journey {st.journey_id} allows {j.get('layers')}")
        if st.layer_since and (at - datetime.fromisoformat(st.layer_since)).total_seconds() / 3600 < self.cooldown_h:
            return out(False, "REC.COOLDOWN", f"last layer move {st.layer_since}")
        if _lrank(to) < _lrank(st.layer):
            return out(True, "REC.ACCEPT_DOWN", "cheaper layer")
        if to == "L3" and (feats.get("risk_rank") or 0) < 1:
            return out(False, "REC.L3_RISK_TOO_LOW", "voice needs risk MEDIUM+")
        n = int(evidence.get("n_obs") or 0)
        lift = float(evidence.get("expected_lift") or 0)
        if n < int(self.rec.get("min_evidence_obs_for_up", 20)) or lift < float(self.rec.get("min_expected_lift_for_up", 1.1)):
            return out(False, "REC.UP_INSUFFICIENT_EVIDENCE", f"n_obs={n}, expected_lift={lift}")
        return out(True, "REC.ACCEPT_UP", f"n_obs={n}, expected_lift={lift}")

    # ---------- stop rules / lifecycle ----------
    def no_response_budget(self, st: BorrowerState, feats: Mapping[str, Any],
                           stop_policy: Optional[StopPolicy]) -> tuple[int, str]:
        if stop_policy is not None:
            hint = stop_policy(feats, st)
            if hint and hint.get("budget") is not None:
                return int(hint["budget"]), f"stop_policy:{hint.get('source', 'k09-rl')}"
        b = self.get(st.journey_id).get("no_response_budget") or self.doc.get("default_no_response_budget", {})
        return int(b.get("attempts", 10)), f"journey:{st.journey_id}"

    def lifecycle(self, st: BorrowerState, feats: Mapping[str, Any], stop_policy: Optional[StopPolicy] = None
                  ) -> tuple[str, str, str, list]:
        budget, src = self.no_response_budget(st, feats, stop_policy)
        extra = {"campaign.no_response_budget": budget,
                 "campaign.no_response_budget_left": budget - st.campaign_attempts}
        view = _Overlay(feats, extra)
        for r in self.stop_rules:
            if not in_scope(r.get("scope"), st.campaign_id, st.cohort, st.region or st.static.get("state")):
                continue
            ok, why = evaluate(r["when"], view)
            if ok:
                until = ""
                fl = st.flags
                if r["state"] == "PAUSED_PTP" and fl.get("ptp_date"):
                    until = (date.fromisoformat(fl["ptp_date"]) + timedelta(days=1)).isoformat()
                elif r["state"] == "PAUSED_CLAIM" and fl.get("claim_at"):
                    until = (datetime.fromisoformat(fl["claim_at"]) + timedelta(hours=48)).isoformat(timespec="seconds")
                elif r["state"] == "HARDSHIP_HUMAN":
                    until = fl.get("hardship_until") or ""
                if r["state"] == "EXHAUSTED_CAMPAIGN":
                    why = why + [f"no_response_budget={budget} ({src})"]
                return r["state"], r["id"], until, why
        return "ACTIVE", "STOP.NONE", "", ["no stop rule matched"]


class _Overlay(Mapping):
    def __init__(self, base, extra):
        self.base, self.extra = base, extra

    def __getitem__(self, k):
        return self.extra[k] if k in self.extra else self.base[k]

    def get(self, k, d=None):
        return self.extra[k] if k in self.extra else self.base.get(k, d)

    def __iter__(self):
        return iter(list(self.extra) + list(self.base))

    def __len__(self):
        return len(self.extra) + len(self.base)
