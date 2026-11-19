"""K15: safety, load and story replay.

Run:          python3 run_replay.py
Quick run:    python3 run_replay.py --borrowers 5000 --days 7
Other seed:   python3 run_replay.py --seed 7
On dataset:   python3 run_replay.py --dataset data/synthetic_50k.csv

Saves the full results to reports/replay_report.json (used by K16 demo and K23 review).
"""
import argparse
import json
import os
import time

from kairos_backend.safeio import atomic_write_text, interruptible  # noqa: E402
from replay import settings as S
from replay.campaign import run_campaign
from replay.chat_load import PlaceholderChatHandler, run_load_test, run_safety_scenarios
from replay.policies import FixedCadenceBaseline, ReferencePolicy
from replay.portfolio import portfolio_from_csv

FUNNEL_STEPS = [
    ("eligible", "Eligible"),
    ("attempted", "Attempted (unique reach)"),
    ("delivered_or_connected", "Delivered / connected"),
    ("meaningful_reply", "Meaningful reply"),
    ("verified_payment", "Verified payment"),
]


def rs(paise):
    return f"Rs.{paise / 100:,.0f}"


def change(new, old):
    if not old:
        return "n/a"
    return f"{(new - old) / old * 100:+.1f}%"


def print_report(base, kai, load, safety_chat):
    print("\nNOTE: " + base["label"] + "; " + kai["label"] + "; " + kai["simulator"] + ".")
    print(f"Run: {kai['borrowers']:,} borrowers, {kai['days']} days, seed {kai['seed']}\n")

    print(f"{'FUNNEL (unique borrowers)':<30}{'Baseline':>12}{'Kairos':>12}{'Change':>10}")
    for key, title in FUNNEL_STEPS:
        b, k = base["funnel"][key], kai["funnel"][key]
        print(f"{title:<30}{b:>12,}{k:>12,}{change(k, b):>10}")

    print(f"\n{'CAMPAIGN TOTALS':<30}{'Baseline':>12}{'Kairos':>12}{'Change':>10}")
    rows = [
        ("Contacts sent", base["contacts"], kai["contacts"], "{:,}"),
        ("Spend", base["spend_paise"], kai["spend_paise"], None),
        ("Meaningful replies", base["meaningful_replies"], kai["meaningful_replies"], "{:,}"),
        ("Replies per Rs.1,000", base["replies_per_1000_rupees"], kai["replies_per_1000_rupees"], "{:,.2f}"),
        ("Verified payments", base["verified_payments"], kai["verified_payments"], "{:,}"),
        ("Opt-outs (STOP)", base["opt_outs"], kai["opt_outs"], "{:,}"),
    ]
    for title, b, k, fmt in rows:
        bs, ks = (rs(b), rs(k)) if fmt is None else (fmt.format(b), fmt.format(k))
        print(f"{title:<30}{bs:>12}{ks:>12}{change(k, b):>10}")

    print("\nSAFETY (checked independently of the policy)")
    for result in (base, kai):
        total = sum(result["violations"].values())
        detail = ", ".join(f"{rule}: {n:,}" for rule, n in sorted(result["violations"].items())) or "none"
        print(f"  {result['policy']:<9} {'PASS' if total == 0 else 'FAIL'}  violations: {detail}")

    print(f"\nPLANNING THROUGHPUT ({kai['borrowers']:,} borrowers per day, limit {S.PLANNING_TIME_LIMIT_SECONDS}s)")
    for result in (base, kai):
        ok = result["planning_seconds_max"] < S.PLANNING_TIME_LIMIT_SECONDS
        print(f"  {result['policy']:<9} {'PASS' if ok else 'FAIL'}  slowest day {result['planning_seconds_max']}s, "
              f"average {result['planning_seconds_avg']}s")

    print(f"\nCHAT LOAD ({load['concurrent_chats']} at once, first response limit {S.FIRST_RESPONSE_LIMIT_SECONDS}s)")
    tag = "  [PLACEHOLDER: proves the harness, not the agent]" if load["is_placeholder"] else ""
    print(f"  {'PASS' if load['meets_target'] else 'FAIL'}  answered {load['answered']}/{load['concurrent_chats']}, "
          f"p95 {load['first_response_p95_seconds']}s, max {load['first_response_max_seconds']}s{tag}")
    print(f"  Chat safety scenarios: {safety_chat['status']}"
          + (f" ({safety_chat['reason']})" if "reason" in safety_chat else ""))


def main():
    parser = argparse.ArgumentParser(description="K15 safety, load and story replay")
    parser.add_argument("--borrowers", type=int, default=S.N_BORROWERS)
    parser.add_argument("--days", type=int, default=S.CAMPAIGN_DAYS)
    parser.add_argument("--seed", type=int, default=S.SEED)
    parser.add_argument("--dataset", default=None, help="CSV file to use instead of generated borrowers")
    args = parser.parse_args()
    portfolio = portfolio_from_csv(args.dataset) if args.dataset else None
    if portfolio is not None:
        args.borrowers = len(portfolio)
        print(f"Using dataset {args.dataset}")

    started = time.perf_counter()
    print(f"Running baseline campaign ({args.borrowers:,} borrowers, {args.days} days)...")
    base = run_campaign(FixedCadenceBaseline(), args.borrowers, args.days, args.seed, portfolio)
    print("Running Kairos campaign...")
    kai = run_campaign(ReferencePolicy(), args.borrowers, args.days, args.seed, portfolio)
    print("Running 500-chat load test...")
    handler = PlaceholderChatHandler(args.seed)
    load = run_load_test(handler)
    safety_chat = run_safety_scenarios(handler)

    print_report(base, kai, load, safety_chat)

    os.makedirs("reports", exist_ok=True)
    path = os.path.join("reports", "replay_report.json")
    atomic_write_text(path, json.dumps({"baseline": base, "kairos": kai, "chat_load": load,
                                        "chat_safety": safety_chat}, indent=2))  # temp file + rename
    print(f"\nFull report saved to {path}  (total time {time.perf_counter() - started:.1f}s)")


if __name__ == "__main__":
    raise SystemExit(interruptible(main))
