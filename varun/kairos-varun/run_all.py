"""Varun's tasks K15 / K23 / K26 / K27 - one command.

  python3 run_all.py              fresh 50,000-borrower run + every test (about 4-6 minutes)
  python3 run_all.py --checks     tests only, on the latest run
  python3 run_all.py --borrowers 5000   quick development run (K15-T02 will fail: it needs 50,000)
"""
import argparse
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "kairos_engine"))
TASKS = ["K15-safety-load-replay", "K23-cohort-review", "K26-analytics-metrics", "K27-agent-dashboard"]

ap = argparse.ArgumentParser()
ap.add_argument("--checks", action="store_true")
ap.add_argument("--borrowers", type=int)
ap.add_argument("--seed", type=int)
a = ap.parse_args()
t0 = time.time()
if not a.checks:
    from pipeline import run_pipeline
    run_pipeline(seed=a.seed, borrowers=a.borrowers)
results = []
for t in TASKS:
    print(f"\n{'=' * 78}\n{t}\n{'=' * 78}")
    p = subprocess.run([sys.executable, "reference_check.py"], cwd=os.path.join(HERE, t), capture_output=True, text=True)
    print(p.stdout + p.stderr)
    last = [l for l in p.stdout.splitlines() if " passed " in l]
    results.append((t, p.returncode == 0, last[-1].split("(")[0].strip() if last else "did not finish"))
print(f"\n{'=' * 78}\nSUMMARY ({time.time() - t0:.0f}s)")
for t, ok, line in results:
    print(f"  {'PASS' if ok else 'FAIL'}  {t:26} {line}")
sys.exit(0 if all(ok for _, ok, _ in results) else 1)
