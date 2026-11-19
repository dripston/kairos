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
    rules, mermaid = [], ["flowchart LR"] + [f"  L{i}[\"{LAYER_NAMES[i]}\"]" for i in (1, 2, 3)]
    for co in cohorts:
        for layer in (1, 2, 3):
            for out in ("D", "U"):
                best = (0.0, "none")
                for ch in sorted(CH_LAYER):
                    if not costs.get(ch) or base[ch] < policy.min_reply_prob:   # channels that can qualify
                        continue
                    k = (f"L{layer}", out, ch)
                    v = base[ch] * mdl.trans.get(k) * mdl.trans_cohort.get((co,) + k) / (costs[ch] / 100)
                    if v > best[0]:
                        best = (v, ch)
                nxt = CH_LAYER.get(best[1], 4)
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


def print_levels(rep: dict, full: bool = False) -> None:
    """Console view of RL2Policy.report() (scripts/run_k09_replay.py --policy rl2 [--report-levels])."""
    lv = rep["levels"]
    st = rep["stats"]
    print(f"\nK09 RL v2 {rep['version']}: evaluated {st.get('evaluated', 0):,} borrower-days "
          f"({rep['borrowers_evaluated_per_second']} /s), SEND {st.get('SEND', 0):,}, WAIT {st.get('WAIT', 0):,}, "
          f"paused for good {rep['paused']}")
    lam = [round(d["lambda"] * 100, 4) for d in rep["pacing"]]
    print(f"Price of money lambda (replies per ₹, by day): {lam}")
    print("Learned reply effects per level (kappa = shrinkage strength estimated from between-group spread):")
    for x in lv["reply"]:
        top = ", ".join(f"{e['group']} x{e['multiplier']}" for e in x["top_effects"][:3])
        print(f"  {x['level']:30} kappa {x['kappa']:>9} groups {x['groups']:>5}"
              f"{'  (collapses to parent)' if x['collapsed_to_parent'] else ''}  {top}")
    print(f"P(pay | reply) {lv['pay_given_reply']['global']}; STOP share of reply propensity "
          f"{lv['stop']['stop_share_of_reply_propensity']}")
    for s in lv["drilldown"][:5]:
        print("  " + s)
    if not full:
        print("(--report-levels for layers, the per-segment daily table and a borrower trace)")
        return
    print("\nChannel layers: learned next step after a touch without a reply")
    for r in lv["layers"]["rules"]:
        print(f"  {r['cohort']}: after {r['after']:42} -> {r['next_channel']:9} ({r['next_layer']}, "
              f"{r['replies_per_inr']} replies/₹)")
    print("```mermaid\n" + lv["layers"]["mermaid"] + "\n```")
    print("\nPer-segment daily learning (cohort x executed day):")
    print(f"  {'day':>3} {'cohort':6} {'touches':>7} {'replies':>7} {'paid':>5} {'STOP':>5} {'spend ₹':>8} {'rep/₹1k':>8}")
    for r in lv["segment_daily"]:
        print(f"  {r['day']:>3} {r['cohort']:6} {r['touches']:>7} {r['replies']:>7} {r['verified_payments']:>5} "
              f"{r['stops']:>5} {r['spend_inr']:>8.2f} {r['replies_per_1000_inr']:>8}")
    print("\nBest arm per band by day (posterior replies/₹):")
    for s in lv["learning_by_day"]:
        print(f"  day {s['day']:>2} n={s['finalized_touches']:>5} fatigue {s['fatigue']}  " +
              "  ".join(f"{b}: {v}" for b, v in s["best_arm_by_band"].items()))
    t = lv["borrower_trace"]
    if t:
        print(f"\nBorrower trace {t['borrower_id']} ({t['cohort']}, {t['region']}, EMI ₹{t['emi_inr']:,.0f}):")
        for s in t["steps"]:
            b = s["best_arm"]
            f = " x ".join(f"{n.split(' (')[0]} {v}" for n, v in s["factors"])
            print(f"  day {s['day']:>2} {s['band']} recent {s['recent_touches']} prev {s['previous']}: "
                  f"{s['decision']} {b['channel']} {b['slot']} p={b['p_reply']} V/paisa={b['value_per_paisa']} "
                  f"vs lambda {s['lambda']} — {s['why']}\n         p = {f}")
        for h in t["history"]:
            print(f"    outcome: {h['channel']} -> {h['outcome']}")
        if t["paused"]:
            print(f"    paused: {t['paused']}")
