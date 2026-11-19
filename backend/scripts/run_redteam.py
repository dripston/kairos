"""Run the Kairos guardrail red-team suite against fresh synthetic databases (environment SIMULATED).

    python scripts/run_redteam.py                       # everything; report -> reports/redteam_report.json
    python scripts/run_redteam.py --category caps --category api
    python scripts/run_redteam.py --id RT-CAP-01 --seed 7
    python scripts/run_redteam.py --plugin my_branch.redteam_attacks   # extra attack modules
    python scripts/run_redteam.py --list

Exit code: 0 when every FAIL is a registered finding (see redteam/findings.py), 1 on any unexpected
FAIL/ERROR (or any FAIL at all with --strict).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from redteam import (DEFAULT_SEED, FAIL, build_report, load_attacks, redteam_policy,  # noqa: E402
                     run_all, table, write_report)
from redteam.runner import utcnow  # noqa: E402

DEFAULT_REPORT = BACKEND / "reports" / "redteam_report.json"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--id", action="append", default=[], help="attack id (repeatable)")
    ap.add_argument("--category", action="append", default=[], help="attack category (repeatable)")
    ap.add_argument("--component", action="append", default=[], help="component prefix, e.g. k06.api (repeatable)")
    ap.add_argument("--plugin", action="append", default=[], help="extra module that registers attacks")
    ap.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    ap.add_argument("--strict", action="store_true", help="exit 1 on any FAIL, including known findings")
    ap.add_argument("--list", action="store_true", help="list attacks and exit")
    args = ap.parse_args(argv)

    reg = load_attacks(args.plugin)
    attacks = reg.select(args.id, args.category, args.component)
    if not attacks:
        print("no attacks selected", file=sys.stderr)
        return 2
    if args.list:
        for a in attacks:
            print(f"{a.id:12s} {a.category:10s} {a.component:14s} {a.title}  [{', '.join(a.covers)}]")
        return 0

    policy = redteam_policy()
    started = utcnow()
    outcomes = run_all(attacks, policy, args.seed)
    report = build_report(outcomes, policy, args.seed, started, utcnow())
    path = write_report(report, args.report)

    t = report["totals"]
    print(f"Kairos guardrail red-team · run {report['run_id']} · env {report['environment']} · "
          f"policy {report['policy_version']} (K13 {report['exceptions_policy_version']}) · seed {args.seed}\n")
    print(table(outcomes))
    print(f"\n{t['attacks']} attacks: {t['PASS']} PASS, {t['FAIL']} FAIL ({t['known_findings']} known findings), "
          f"{t['ERROR']} ERROR, {t['SKIP']} SKIP")
    for f in report["findings"]:
        if f["still_failing"]:
            print(f"  {f['id']} [{f['severity']}] {f['title']} -> {f['owner']}  ({', '.join(f['attacks'])})")
    if report["findings_apparently_fixed"]:
        print(f"  finding attacks now passing (remove the finding marker): {', '.join(report['findings_apparently_fixed'])}")
    if report["unexpected_failures"]:
        print(f"  UNEXPECTED: {', '.join(report['unexpected_failures'])}")
    print(f"\nreport: {path}")
    bad = report["unexpected_failures"] or (args.strict and any(o.status == FAIL for o in outcomes))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
