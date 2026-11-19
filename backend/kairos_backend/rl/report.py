"""K09 RL v2 reports: learned effects per level, channel layers / transitions, per-segment daily learning,
and one borrower's decisions explained step by step. Everything is computed from the policy's finalized
touch records (ledger evidence) and its fitted hierarchy; deterministic."""
from __future__ import annotations

from collections import defaultdict
from typing import Optional

from .hier import Level
from .value import CH_LAYER, LAYER_NAMES

SLOT_NAME = {"08-11": "morning", "14-17": "afternoon", "17-19": "evening", "11-14": "midday"}


def _level_summary(lv: Level, top: int = 6, min_e: float = 1.0) -> dict:
    return {"level": lv.name, "kappa": round(lv.kappa, 2), "groups": len(lv.counts),
            "collapsed_to_parent": lv.kappa >= 1e5, "top_effects": lv.table(min_e=min_e, top=top)}


def drilldown(policy, min_e: float = 2.0, top: int = 8) -> list[str]:
    """Region x band x channel x slot effects relative to the band-level arm, EB-shrunk, as sentences
    ("whatsapp evening works 1.4x in Maharashtra B1")."""
    lv = Level("state x band x channel x slot", lambda r: (r.region, r.band, r.ch, r.slot))
    lv.fit((lv.keyfn(r), r.reply, r.p_arm) for r in policy.model.records)
    out = []
    for (reg, band, ch, slot), (o, e) in sorted(lv.counts.items(), key=lambda kv: -abs(lv.m[kv[0]] - 1)):
        if e < min_e:
            continue
        out.append(f"{ch} {SLOT_NAME.get(slot, slot)} works {lv.m[(reg, band, ch, slot)]:.2f}x in {reg} {band} "
                   f"({o:.0f} replies vs {e:.1f} expected; kappa {lv.kappa:.1f})")
        if len(out) >= top:
            break
    if not out:
        out.append(f"no state x band x channel x slot group differs from its band-level arm beyond noise "
                   f"(kappa {lv.kappa:.0f}: the level collapses onto its parent)")
    return out


def layers(policy) -> dict:
    """Learned channel transitions and the deterministic layer rule per cohort."""
    mdl = policy.model
    costs = policy.costs
    trans = [{"from": r["group"], "multiplier": r["multiplier"], "observed": r["observed"], "expected": r["expected"]}
             for r in mdl.trans.table()]
    # average arm reply chance per channel (over bands / slots seen), for the rule
    base = {}
    for ch in CH_LAYER:
        ps = [mdl.p_arm(b, ch, s) for b in ("B1", "B2", "B3", "B4") for s in ("08-11", "14-17", "17-19")]
        base[ch] = sum(ps) / len(ps)
    cohorts = sorted({r.cohort for r in mdl.records}) or ["C?"]
    rules, mermaid = [], ["flowchart LR"]
    for co in cohorts:
        for layer in (1, 2, 3):
            for out in ("D", "U"):
                best = None
                for ch in sorted(CH_LAYER):
                    if not costs.get(ch):
                        continue
                    k = (f"L{layer}", out, ch)
                    v = base[ch] * mdl.trans.get(k) * mdl.trans_cohort.get((co,) + k) / (costs[ch] / 100)
                    if best is None or v > best[0]:
                        best = (v, ch)
                nxt = CH_LAYER[best[1]]
                rules.append({"cohort": co, "after": f"{LAYER_NAMES[layer]} {'delivered, no reply' if out == 'D' else 'not delivered'}",
                              "next_channel": best[1], "next_layer": LAYER_NAMES[nxt],
                              "replies_per_inr": round(best[0], 4)})
                if co == cohorts[0]:
                    mermaid.append(f"  L{layer}{out}[\"L{layer} {'no reply' if out == 'D' else 'undelivered'}\"]"
                                   f" -->|{best[1]}| L{nxt}")
    mermaid.append(f"  P[\"paused (optimal stopping)\"] --> L4[\"{LAYER_NAMES[4]}\"]")
    return {"transitions": trans, "kappa": round(mdl.trans.kappa, 2), "cohort_kappa": round(mdl.trans_cohort.kappa, 2),
            "rules": rules, "mermaid_cohort": cohorts[0], "mermaid": "\n".join(mermaid)}


def segment_daily(policy) -> list[dict]:
    """Per executed day x cohort: finalized touches, replies, verified payments, STOPs, spend."""
    agg = defaultdict(lambda: [0, 0, 0, 0, 0])
    days = sorted({r.day for r in policy.model.records})
    first = days[0] if days else 0
    for r in policy.model.records:
        a = agg[(r.day - first + 1, r.cohort)]
        a[0] += 1
        a[1] += r.reply
        a[2] += r.paid
        a[3] += r.stop
        a[4] += policy.costs.get(r.ch, 0)
    return [{"day": d, "cohort": co, "touches": a[0], "replies": a[1], "verified_payments": a[2], "stops": a[3],
             "spend_inr": a[4] / 100, "replies_per_1000_inr": round(a[1] / a[4] * 100_000, 1) if a[4] else 0.0}
            for (d, co), a in sorted(agg.items())]


def borrower_trace(policy, bid: Optional[str] = None) -> dict:
    traces = policy.traces
    if not traces:
        return {}
    if bid is None:
        def score(b):
            h = policy.history.get(b, [])
            return (sum(1 for _, _, o in h if o == "R") > 0, len(h), -policy.meta[b].index)
        bid = max(sorted(traces), key=score)
    m = policy.meta[bid]
    return {"borrower_id": bid, "cohort": m.cohort, "region": m.region, "emi_inr": m.emi_paise / 100,
            "history": [{"day_ordinal": d, "channel": ch, "outcome": {"R": "replied", "D": "delivered, no reply",
                                                                      "U": "not delivered"}[o]}
                        for d, ch, o in policy.history.get(bid, [])],
            "paused": policy.paused.get(bid), "steps": traces[bid]}


def levels_report(policy) -> dict:
    mdl = policy.model
    return {
        "reply": [_level_summary(mdl.band)] + [_level_summary(lv) for lv in mdl.reply_levels],
        "pay_given_reply": {"global": round(mdl.q_global, 4), "levels": [_level_summary(lv, min_e=0.5)
                                                                          for lv in mdl.pay_levels]},
        "stop": {"stop_share_of_reply_propensity": round(mdl.sigma, 4),
                 "levels": [_level_summary(lv, min_e=0.2) for lv in mdl.stop_levels]},
        "drilldown": drilldown(policy),
        "layers": layers(policy),
        "segment_daily": segment_daily(policy),
        "learning_by_day": policy.snapshots,
        "borrower_trace": borrower_trace(policy),
    }
