"""K09 vs fixed cadence on the full K06 stack over the K03 spine (matched seed, borrowers, budget, caps).
Everything is SIMULATED. Run from backend/:

    python3 scripts/run_k09_replay.py                        # 5,000 borrowers, 14 days, seed 42, WhatsApp drift day 7
    python3 scripts/run_k09_replay.py --borrowers 600 --days 7
    python3 scripts/run_k09_replay.py --baseline k15         # K15 placeholder schedule instead of CN10
    python3 scripts/run_k09_replay.py --baseline both        # CN10 (budget calibration) and K15, same budget
    python3 scripts/run_k09_replay.py --no-drift             # DEV simulator without the E17 drift scenario
    python3 scripts/run_k09_replay.py --explore              # K09 uses seeded Thompson draws (default: posterior mean, as in PRODUCTION)
    python3 scripts/run_k09_replay.py --no-context           # K09 without the borrower-context layer
    python3 scripts/run_k09_replay.py --dataset data/synthetic_50k.csv   # a K03 dataset file as-is
    python3 scripts/run_k09_replay.py --check-determinism    # run everything twice and compare
    python3 scripts/run_k09_replay.py --kairos-opts '{"decay": 0.8}'   # override K09 decision options (replay.k09.KairosOptions)
    python3 scripts/run_k09_replay.py --kairos-opts '{"stop_penalty": 0}'   # PR #17 default (no opt-out penalty)
    python3 scripts/run_k09_replay.py --legacy-k09           # the K09 default before k09/optimize (no decay, floor, STOP penalty)

Baselines: cn10 = CreditNirvana's own "Personal Loan 10-Day Post-Due Digital Collections Strategy"
(HDFC project), a stand-in until the official fixed-cadence policy file (K02) arrives; k15 = the K15
placeholder schedule. Writes reports/k09_replay_report.json (gitignored). Stdlib only (Python 3.9+).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # backend/ (no install needed)

from kairos_backend.safeio import atomic_write_text, interruptible  # noqa: E402
from replay import settings as S  # noqa: E402
from replay.k09 import LEGACY_KAIROS_OPTIONS, run_comparison  # noqa: E402


def pct(new, old):
    return "n/a" if not old else f"{(new - old) / old * 100:+.1f}%"


def fmt(x):
    return f"{x:,.2f}" if isinstance(x, float) else f"{x:,}" if x is not None else "-"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--borrowers", type=int, default=5000)
    p.add_argument("--days", type=int, default=S.CAMPAIGN_DAYS)
    p.add_argument("--seed", type=int, default=S.SEED)
    p.add_argument("--baseline", choices=("cn10", "k15", "both"), default="cn10")
    p.add_argument("--drift-day", type=int, default=7, help="1-based day WhatsApp response drops (E17)")
    p.add_argument("--no-drift", action="store_true")
    p.add_argument("--explore", dest="explore", action="store_true", default=False,
                   help="seeded Thompson sampling for K09 arm choice (SIMULATOR mode); default ranks by posterior mean")
    p.add_argument("--no-explore", dest="explore", action="store_false")
    p.add_argument("--no-context", action="store_true", help="K09 without the borrower-context layer")
    p.add_argument("--dataset", default=None, help="K03-format CSV to use instead of generating one")
    p.add_argument("--check-determinism", action="store_true")
    p.add_argument("--kairos-opts", default=None,
                   help="JSON of replay.k09.KairosOptions overrides for the K09 arm, e.g. '{\"decay\": 0.8}'")
    p.add_argument("--legacy-k09", action="store_true",
                   help="K09 as it was before k09/optimize: no evidence decay, no value floor, no STOP penalty")
    p.add_argument("--policy", choices=("default", "rl2"), default="default",
                   help="K09 arm: default (KairosOptions ranker) or rl2 (K09 RL v2, kairos_backend/rl)")
    p.add_argument("--rl2-opts", default=None, help="JSON of kairos_backend.rl.policy.RL2Options overrides")
    p.add_argument("--report-levels", action="store_true",
                   help="rl2: print learned effects per level, layers, per-segment daily table, a borrower trace")
    p.add_argument("--budget-per-case-paise", type=int, default=None,
                   help="fixed paise per loan for every arm (default: match the baseline's spend)")
    a = p.parse_args()
    drift = None if a.no_drift else a.drift_day - 1
    baselines = ("cn10", "k15") if a.baseline == "both" else (a.baseline,)
    kwargs = dict(baselines=baselines, context=not a.no_context, explore=a.explore, dataset=a.dataset,
                  kairos_opts={**(LEGACY_KAIROS_OPTIONS if a.legacy_k09 else {}),
                               **(json.loads(a.kairos_opts) if a.kairos_opts else {})},
                  policy=a.policy, rl2_opts=json.loads(a.rl2_opts) if a.rl2_opts else None)

    t0 = time.perf_counter()
    r = run_comparison(a.borrowers, a.days, a.seed, drift, a.budget_per_case_paise, **kwargs)
    k, m = r["kairos"], r["matched"]
    print(f"\n{r['provenance']}")
    print(f"Baseline: {r['baseline_note']}")
    print(f"Matched: seed {m['seed']}, {m['borrowers']:,} borrowers, {m['days']} days from {m['start']}, "
          f"budget ₹{m['budget_pool_paise'] / 100:,.2f} ({m['budget_basis']}), caps {m['caps']}")
    print(f"Dataset: {m['dataset']}")
    print(f"Simulator: {k['simulator']}")
    print(f"K09 options: {m['kairos_options']}\n")
    arms = [(b, r["baselines"][b]) for b in baselines] + [("K09", k)]
    rows = [
        ("Attempted (unique)", lambda x: x["funnel_unique_borrowers"]["attempted"]),
        ("Reached (unique)", lambda x: x["funnel_unique_borrowers"]["reached"]),
        ("Meaningful reply (unique)", lambda x: x["funnel_unique_borrowers"]["meaningful_reply"]),
        ("Verified payment (unique)", lambda x: x["funnel_unique_borrowers"]["verified_payment"]),
        ("Contacts sent", lambda x: x["contacts_sent"]),
        ("Spend ₹", lambda x: x["spend_inr"]),
        ("Meaningful replies", lambda x: x["meaningful_replies"]),
        ("Replies per ₹1,000", lambda x: x["replies_per_1000_inr"]),
        ("Verified payments", lambda x: x["verified_payments"]),
        ("₹ recovered (verified EMIs)", lambda x: x["recovered_paise"] / 100),
        ("₹ recovered per ₹ spent", lambda x: round(x["recovered_paise"] / x["spend_paise"], 2) if x["spend_paise"] else 0.0),
        ("Opt-outs (STOP)", lambda x: x["opt_outs"]),
        ("K01 cap breaches", lambda x: x["cap_breaches_k01"]),
        ("K15 safety violations", lambda x: sum(x["k15_safety_violations"].values())),
        ("Decisions / second", lambda x: x["decisions_per_second"]),
    ]
    print(f"{'':28}" + "".join(f"{name:>12}" for name, _ in arms) + f"{'K09 vs ' + baselines[0]:>14}")
    for title, get in rows:
        vals = [get(x) for _, x in arms]
        print(f"{title:28}" + "".join(f"{fmt(v):>12}" for v in vals) + f"{pct(vals[-1] or 0, vals[0] or 0):>14}")

    for name, x in arms:
        print(f"\nSupervisor decisions {name:6}: {x['decisions']}   top WAIT/CANCEL rules: {x['wait_rules']}")
    if "cn10" in r["baselines"]:
        c = r["baselines"]["cn10"]["cn10"]
        print(f"CN10 cohorts {c['cohorts']}  scheduled steps {c['scheduled_steps']}  "
              f"NACH notices sent {c['nach_notices_sent']}  repeat after day 10: {c['repeat_after_day_10']}")
        print(f"Note: {c['ivr_note']}")
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
    for name, x in arms:
        thr = x["decisions_per_second"]
        print(f"Throughput {name:6}: {thr} decisions/s → 50,000 decisions in {50000 / thr / 60:.1f} min "
              f"(limit {S.PLANNING_TIME_LIMIT_SECONDS // 60} min)" if thr else f"Throughput {name}: n/a")

    if r.get("rl2"):
        from kairos_backend.rl.report import print_levels
        print_levels(r["rl2"], full=a.report_levels)

    if a.check_determinism:
        r2 = run_comparison(a.borrowers, a.days, a.seed, drift, a.budget_per_case_paise, **kwargs)
        keys = ("envelope_hash", "meaningful_replies", "spend_paise", "verified_payments", "daily", "decisions")
        same = all(r2[arm][key] == r[arm][key] for arm in ("baseline", "kairos") for key in keys) and \
            all(r2["baselines"][b][key] == r["baselines"][b][key] for b in baselines for key in keys)
        r["determinism"] = {"identical": same,
                            "envelope_hash": {arm: (r[arm]["envelope_hash"], r2[arm]["envelope_hash"])
                                              for arm in ("baseline", "kairos")}}
        print(f"\nDeterministic replay: {'IDENTICAL' if same else 'DIFFERENT'} "
              f"(CRM envelope hash K09 {r['kairos']['envelope_hash']} / {r2['kairos']['envelope_hash']})")

    os.makedirs("reports", exist_ok=True)
    path = os.path.join("reports", "k09_replay_report.json")
    atomic_write_text(path, json.dumps(r, indent=2, default=str))  # temp file + rename: Ctrl-C safe
    print(f"\nReport: {path}   ledger DBs: {r['dbs']}   ({time.perf_counter() - t0:.0f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(interruptible(main, lambda: "Replay arms still running were discarded with their temp DBs."))
