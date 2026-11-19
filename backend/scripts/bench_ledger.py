"""Throughput check for the CM-02 requirement: 50,000 daily decisions in under 10 minutes.

Builds K03's synthetic dataset, loads it into the spine, enrolls every loan, then runs one
full atomic reserve() (spine facts + Gate + write) per borrower for one campaign day.
Usage:  python scripts/bench_ledger.py [N]
"""
import random
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

from kairos_backend.ledger import Instruction, Ledger
from kairos_backend.policy import load_policy
from spine.config import DATASET_AS_OF_DATE as DAY  # 2026-08-07, a Friday
from spine.synthetic_dataset import write_dataset

N = int(sys.argv[1]) if len(sys.argv) > 1 else 50_000
CH = {"sms": ("SMS_EN", 25), "whatsapp": ("WA_HI", 40), "email": ("EM_EN", 5), "ivr": ("IVR_HI", 150)}
rnd = random.Random(42)

work = Path(tempfile.mkdtemp())
t0 = time.perf_counter()
write_dataset(work / "syn.csv", N, seed=42)
lg = Ledger(load_policy(), work / "bench.db")
report = lg.import_dataset(work / "syn.csv")
for t, (ch, lang) in {"SMS_EN": ("sms", "en"), "WA_HI": ("whatsapp", "hi"), "EM_EN": ("email", "en"),
                      "IVR_HI": ("ivr", "hi")}.items():
    lg.upsert_template(t, ch, lang)
lg.upsert_campaign("C1", budget_per_case_paise=500, start_date=DAY, end_date=DAY + timedelta(days=13))
rows = lg._conn().execute("SELECT o.obligation_id, l.loan_id, l.borrower_id FROM obligations o"
                          " JOIN loans l ON l.loan_id = o.loan_id ORDER BY l.loan_id").fetchall()
lg.enroll("C1", [r["loan_id"] for r in rows])
setup = time.perf_counter() - t0

t0 = time.perf_counter()
reasons = {}
for r in rows:
    ch = rnd.choice(list(CH))
    tpl, cost = CH[ch]
    at = datetime(DAY.year, DAY.month, DAY.day, rnd.randint(8, 18), rnd.choice((0, 30)))
    d = lg.reserve(Instruction(r["borrower_id"], r["obligation_id"], "C1", ch, tpl, at, cost)).decision
    reasons[d.reason] = reasons.get(d.reason, 0) + 1
elapsed = time.perf_counter() - t0

camp = lg.campaign_state("C1")
print(f"setup (generate + import {report['loaded']:,} loans + enroll): {setup:.1f}s")
print(f"{len(rows):,} atomic decisions in {elapsed:.1f}s ({len(rows) / elapsed:,.0f}/s)")
print("decisions:", ", ".join(f"{k} {v:,}" for k, v in sorted(reasons.items(), key=lambda kv: -kv[1])))
print(f"budget: committed {camp['committed_paise']:,} of today's allowance "
      f"{camp['days'][0]['allowance_paise']:,} paise | cap breaches: {len(lg.cap_breaches())}")
print("target: 50,000 in < 600s ->", "PASS" if elapsed * 50_000 / len(rows) < 600 else "FAIL")
