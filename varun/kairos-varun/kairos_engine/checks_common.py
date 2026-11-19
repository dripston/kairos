"""Shared plumbing for the K15/K23/K26/K27 reference_check.py runners.

Each runner reads the latest run (runs/latest.json). If there is none, it runs the
pipeline first (about 3-4 minutes at 50,000 borrowers). Tests read the RAW event logs
in runs/<run_id>/ so a number in a report cannot pass unless the logs agree.
"""
import csv
import json
import os
import sys

import pandas as pd

ENGINE = os.path.dirname(os.path.abspath(__file__))
PACKAGE = os.path.dirname(ENGINE)
if ENGINE not in sys.path:
    sys.path.insert(0, ENGINE)


def latest_run(rebuild=False):
    lp = os.path.join(PACKAGE, "runs", "latest.json")
    m = json.load(open(lp)) if os.path.exists(lp) else None
    if rebuild or m is None or not os.path.exists(os.path.join(PACKAGE, m["run_dir"], "sends_kairos.csv.gz")):
        print("No complete run found - running the pipeline first (about 3-4 minutes)...\n")
        from pipeline import run_pipeline
        m = run_pipeline()
        print()
    return m, os.path.join(PACKAGE, m["run_dir"])


def load_logs(run_dir, policies=("kairos", "fixed_cadence")):
    out = {}
    for p in policies:
        out[p] = {"sends": pd.read_csv(os.path.join(run_dir, f"sends_{p}.csv.gz"), keep_default_na=False,
                                       na_values=[""], dtype={"reply_type": str, "top_reasons": str, "intensity": str}),
                  "payments": pd.read_csv(os.path.join(run_dir, f"payments_{p}.csv"))}
    return out


class Runner:
    def __init__(self, task, here):
        self.task, self.here = task, here
        self.cases = {r["case_id"]: r for r in csv.DictReader(open(os.path.join(here, "test_cases.csv"), encoding="utf-8"))}
        self.results = []

    def check(self, case_id, passed, detail):
        desc = self.cases[case_id]["description"]
        self.results.append((case_id, bool(passed), detail))
        print(f"{'PASS' if passed else 'FAIL'}  {case_id:8} {desc}\n        -> {detail}")

    def finish(self, extra=""):
        missing = set(self.cases) - {c for c, _, _ in self.results}
        for m in sorted(missing):
            self.check(m, False, "test not executed")
        n_pass = sum(p for _, p, _ in self.results)
        print(f"\n{n_pass}/{len(self.results)} passed  ({self.task}){('  |  ' + extra) if extra else ''}")
        with open(os.path.join(self.here, "outputs", "test_results.json"), "w") as f:
            json.dump([{"case_id": c, "passed": p, "detail": d} for c, p, d in self.results], f, indent=2)
        sys.exit(0 if n_pass == len(self.results) else 1)
