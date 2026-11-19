"""Kairos crazy journey: one deterministic, SIMULATED story through every component.

    python3 scripts/run_journey.py                 # full story + red-team summary + small K09 vs CN10 replay
    python3 scripts/run_journey.py --quick         # the story only
    python3 scripts/run_journey.py --seed 7 --out reports/journey

Writes <out>/timeline.json (events + CRM envelopes), <out>/summary.json and <out>/journey.html.
Ctrl-C prints a partial summary and exits 130; report files are written to a temp file and renamed,
so an interrupted run never leaves a half-written report. See replay/journey.py for the story.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from replay.journey import Journey  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--quick", action="store_true", help="skip the red-team suite and the K09 vs CN10 replay")
    ap.add_argument("--out", type=Path, default=BACKEND / "reports" / "journey")
    ap.add_argument("--replay-borrowers", type=int, default=150)
    ap.add_argument("--replay-days", type=int, default=4)
    args = ap.parse_args(argv)
    j = Journey(seed=args.seed, quick=args.quick, out_dir=args.out, replay_borrowers=args.replay_borrowers,
                replay_days=args.replay_days)
    try:
        j.run()
    except KeyboardInterrupt:
        j.interrupted()
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
