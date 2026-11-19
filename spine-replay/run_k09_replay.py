"""K09 vs fixed cadence on the full K06 stack (matched seed, budget and caps). SIMULATED.

    python3 run_k09_replay.py                        # 5,000 borrowers, 14 days, seed 42, WhatsApp drift from day 7
    python3 run_k09_replay.py --borrowers 1000 --days 7
    python3 run_k09_replay.py --no-drift             # DEV simulator without the E17 drift scenario
    python3 run_k09_replay.py --check-determinism    # run the adaptive arm twice and compare

Writes reports/k09_replay_report.json. Stdlib only (Python 3.9+).
"""
from __future__ import annotations

import argparse
import json
import os
import time

from replay import settings as S
from replay.k09 import run_comparison


def pct(new, old):
    return "n/a" if not old else f"{(new - old) / old * 100:+.1f}%"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--borrowers", type=int, default=5000)
    p.add_argument("--days", type=int, default=S.CAMPAIGN_DAYS)
    p.add_argument("--seed", type=int, default=S.SEED)
    p.add_argument("--drift-day", type=int, default=7, help="1-based day WhatsApp response drops (E17)")
    p.add_argument("--no-drift", action="store_true")
    p.add_argument("--check-determinism", action="store_true")
    p.add_argument("--budget-per-case", type=float, default=None,
                   help="fixed ₹ per borrower for both arms (default: match the fixed cadence's spend)")
    a = p.parse_args()
    drift = None if a.no_drift else a.drift_day - 1

    t0 = time.perf_counter()
    r = run_comparison(a.borrowers, a.days, a.seed, drift, a.budget_per_case)
    b, k, m = r["baseline"], r["kairos"], r["matched"]
    print(f"\n{r['provenance']}")
    print(f"Matched: seed {m['seed']}, {m['borrowers']:,} borrowers, {m['days']} days from {m['start']}, "
          f"budget ₹{m['budget_pool_inr']:,.2f} ({m['budget_basis']}), caps {m['caps']}")
    print(f"Simulator: {k['simulator']}\n")
    rows = [
        ("Attempted (unique)", b["funnel_unique_borrowers"]["attempted"], k["funnel_unique_borrowers"]["attempted"]),
        ("Reached (unique)", b["funnel_unique_borrowers"]["reached"], k["funnel_unique_borrowers"]["reached"]),
        ("Meaningful reply (unique)", b["funnel_unique_borrowers"]["meaningful_reply"],
         k["funnel_unique_borrowers"]["meaningful_reply"]),
        ("Verified payment (unique)", b["funnel_unique_borrowers"]["verified_payment"],
         k["funnel_unique_borrowers"]["verified_payment"]),
        ("Contacts sent", b["contacts_sent"], k["contacts_sent"]),
        ("Spend ₹", b["spend_inr"], k["spend_inr"]),
        ("Meaningful replies", b["meaningful_replies"], k["meaningful_replies"]),
        ("Replies per ₹1,000", b["replies_per_1000_inr"], k["replies_per_1000_inr"]),
        ("Verified payments", b["verified_payments"], k["verified_payments"]),
        ("Opt-outs (STOP)", b["opt_outs"], k["opt_outs"]),
        ("K01 cap breaches", b["cap_breaches_k01"], k["cap_breaches_k01"]),
        ("K15 safety violations", sum(b["k15_safety_violations"].values()), sum(k["k15_safety_violations"].values())),
        ("Decisions / second", b["decisions_per_second"], k["decisions_per_second"]),
    ]
    print(f"{'':28}{'Fixed':>12}{'K09':>12}{'Change':>10}")
    for name, x, y in rows:
        fx = f"{x:,.2f}" if isinstance(x, float) else f"{x:,}" if x is not None else "-"
        fy = f"{y:,.2f}" if isinstance(y, float) else f"{y:,}" if y is not None else "-"
        print(f"{name:28}{fx:>12}{fy:>12}{pct(y or 0, x or 0):>10}")

    print("\nSupervisor decisions  fixed:", b["decisions"], " K09:", k["decisions"])
    print("Top WAIT/CANCEL rules  K09:", k["wait_rules"])
    print("Outcomes (K09)       :", k["outcomes"])
    for band in ("B1", "B2"):
        print(f"\nScores {band} (posterior replies/₹)   before → after")
        before = {(x["channel"], x["slot"]): x for x in r["scores_before"][band]}
        for x in r["scores_after"][band][:5]:
            bx = before.get((x["channel"], x["slot"]))
            print(f"  {x['channel']:9} {x['slot']:10} {bx['replies_per_inr'] if bx else '   -':>6} → "
                  f"{x['replies_per_inr']:>6}  (mean {x['mean']}, n={x['n_obs']}, {x['source']})")
    sw = [x for v in r["preference_switches"].values() for x in v]
    print("\nIn-campaign channel switches (K09 preferred channel by posterior replies/₹, end of day):")
    for x in sw or [{"band": "-", "day": "-", "date": "-", "from": "none", "to": "none"}]:
        print(f"  {x['band']} day {x['day']} ({x['date']}): {x['from']} → {x['to']}")
    cc = r["cohort_close"]
    print(f"Cohort close: {len(cc['carry_forward_candidates'])} arm estimates could carry forward, "
          f"{cc['do_not_carry_sparse']} sparse; {cc['status']}")
    for arm in ("baseline", "kairos"):
        rr = r[arm]
        thr = rr["decisions_per_second"]
        print(f"Throughput {arm:8}: {thr} decisions/s → 50,000 decisions in {50000 / thr / 60:.1f} min "
              f"(limit {S.PLANNING_TIME_LIMIT_SECONDS // 60} min)" if thr else f"Throughput {arm}: n/a")

    if a.check_determinism:
        r2 = run_comparison(a.borrowers, a.days, a.seed, drift, a.budget_per_case)
        same = all(r2[arm][key] == r[arm][key] for arm in ("baseline", "kairos")
                   for key in ("envelope_hash", "meaningful_replies", "spend_inr", "verified_payments", "daily"))
        r["determinism"] = {"identical": same,
                            "envelope_hash": {arm: (r[arm]["envelope_hash"], r2[arm]["envelope_hash"])
                                              for arm in ("baseline", "kairos")}}
        print(f"\nDeterministic replay: {'IDENTICAL' if same else 'DIFFERENT'} "
              f"(CRM envelope hash K09 {r['kairos']['envelope_hash']} / {r2['kairos']['envelope_hash']})")

    os.makedirs("reports", exist_ok=True)
    path = os.path.join("reports", "k09_replay_report.json")
    with open(path, "w") as f:
        json.dump(r, f, indent=2, default=str)
    print(f"\nReport: {path}   ledger DBs: {r['dbs']}   ({time.perf_counter() - t0:.0f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
