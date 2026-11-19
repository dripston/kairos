"""Throughput check for the CM-02 requirement: 50,000 daily decisions in under 10 minutes.

Runs N full atomic reserve() calls (facts + Gate + write) against a fresh SQLite file.
Usage:  python scripts/bench_ledger.py [N]
"""
import random
import sys
import tempfile
import time
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kairos_backend.ledger import Instruction, Ledger  # noqa: E402
from kairos_backend.policy import load_policy  # noqa: E402

N = int(sys.argv[1]) if len(sys.argv) > 1 else 50_000
D = date(2026, 11, 20)
rnd = random.Random(42)
CH = {"sms": ("SMS_EN", 0.20), "whatsapp": ("WA_HI", 0.40), "email": ("EM_EN", 0.05), "ivr": ("IVR_HI", 1.20)}

policy = load_policy()
db = Path(tempfile.mkdtemp()) / "bench.db"
lg = Ledger(policy, db)
lg.upsert_campaign("C1", budget_per_case_inr=5, allocated_cases=N, start_date=D, end_date=D + timedelta(days=30))
for t, (ch, lang) in {"SMS_EN": ("sms", "en"), "WA_HI": ("whatsapp", "hi"), "EM_EN": ("email", "en"), "IVR_HI": ("ivr", "hi")}.items():
    lg.upsert_template(t, ch, lang)

t0 = time.perf_counter()
with lg.tx() as c:  # bulk-load borrowers in one transaction
    for i in range(N):
        c.execute("INSERT INTO borrowers (id, dpd, dpd_updated_at, consent) VALUES (?,?,?,?)",
                  (f"B{i}", rnd.randint(-6, 120), datetime(2026, 11, 20, 7).isoformat() + "+05:30",
                   '{"sms": true, "whatsapp": true, "email": true, "ivr": true}'))
        c.execute("INSERT INTO obligations (id, borrower_id, campaign_id) VALUES (?,?,?)", (f"B{i}-A", f"B{i}", "C1"))
load = time.perf_counter() - t0

t0 = time.perf_counter()
sends = 0
for i in range(N):
    ch = rnd.choice(list(CH))
    tpl, cost = CH[ch]
    at = datetime(2026, 11, 20, rnd.randint(8, 18), rnd.choice((0, 30)))
    sends += lg.reserve(Instruction(f"B{i}", f"B{i}-A", "C1", ch, tpl, at, cost)).reserved
elapsed = time.perf_counter() - t0

print(f"loaded {N:,} borrowers in {load:.1f}s")
print(f"{N:,} atomic decisions in {elapsed:.1f}s  ({N / elapsed:,.0f}/s)  |  {sends:,} SEND  |  "
      f"budget {'OK' if lg.campaign_state('C1')['committed_inr'] <= 5 * N else 'EXCEEDED'}  |  "
      f"cap breaches: {len(lg.cap_breaches())}")
print("target: 50,000 in < 600s ->", "PASS" if elapsed * 50_000 / N < 600 else "FAIL")
