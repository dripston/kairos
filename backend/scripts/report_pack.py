"""K26 report pack: run a small seeded SIMULATED campaign through the real ledger and write the
lender's five sheets + the CM-02 daily per-segment report as CSVs.

    python scripts/report_pack.py                       # 60 borrowers, 3 contact days, seed 42
    python scripts/report_pack.py --borrowers 200 --days 5 --seed 7 --hash-ids

Output: backend/reports/report_pack/*.csv (gitignored — never commit report output).
Borrowers come from K03's synthetic dataset generator (phones start with 0, names are fake); every
send goes through Ledger.reserve -> Executor.dispatch with FakeProviders, and borrower behaviour is
drawn with the K15 dev simulator's delivery rates. Nothing leaves this machine.
"""
from __future__ import annotations

import argparse
import random
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from kairos_backend import reports as R  # noqa: E402
from kairos_backend.executor import Executor, FakeProvider  # noqa: E402
from kairos_backend.ledger import Instruction, Ledger  # noqa: E402
from kairos_backend.policy import load_policy  # noqa: E402
from replay.simulator import DELIVERY_RATE  # noqa: E402
from spine.config import DATASET_AS_OF_DATE  # noqa: E402  2026-08-07, a Friday
from spine.synthetic_dataset import write_dataset  # noqa: E402

TEMPLATES = {"sms": ("SMS_EN", "en"), "whatsapp": ("WA_HI", "hi"), "email": ("EM_EN", "en"),
             "ivr": ("IVR_HI", "hi"), "voicebot": ("VB_HI", "hi")}
EST_COST = {"sms": 25, "whatsapp": 40, "email": 5, "ivr": 150, "voicebot": 120}
ACTUAL_COST = {"sms": 22, "whatsapp": None, "email": 5, "ivr": 140, "voicebot": None}  # None = not reconciled
REPLY_RATE = {"whatsapp": 0.20, "sms": 0.08, "email": 0.03, "ivr": 0.10, "voicebot": 0.12}
CAMPAIGN = "K26-DEMO"


def run(n: int, days: int, seed: int, out: Path, hash_ids: bool) -> None:
    rng = random.Random(seed)
    work = Path(tempfile.mkdtemp(prefix="k26-"))
    write_dataset(work / "syn.csv", n, seed=seed)
    policy = load_policy()
    lg = Ledger(policy, work / "k26.db")
    lg.import_dataset(work / "syn.csv")
    for ch, (tpl, lang) in TEMPLATES.items():
        lg.upsert_template(tpl, ch, lang)
    contact_days = policy.contact_days(DATASET_AS_OF_DATE, DATASET_AS_OF_DATE + timedelta(days=30))[:days]
    lg.upsert_campaign(CAMPAIGN, budget_per_case_paise=2_000, start_date=contact_days[0],
                       end_date=contact_days[0] + timedelta(days=13), name="K26 report pack (SIMULATED)")
    c = lg._conn()
    loans = c.execute("SELECT o.obligation_id, l.loan_id, l.borrower_id FROM obligations o"
                      " JOIN loans l ON l.loan_id = o.loan_id ORDER BY l.loan_id").fetchall()
    lg.enroll(CAMPAIGN, [r["loan_id"] for r in loans])
    ex = Executor(lg, {ch: FakeProvider(ch, cost_paise=ACTUAL_COST[ch]) for ch in TEMPLATES})

    decisions = {}
    for day in contact_days:
        lg.start_day(day)
        for r in loans:
            bid = r["borrower_id"]
            chans = [x["channel"] for x in c.execute("SELECT channel FROM consents WHERE borrower_id=? AND consented=1"
                                                     " AND opted_out=0 ORDER BY channel", (bid,))]
            if not chans:
                continue
            for k in range(rng.choice((1, 1, 2))):
                ch = rng.choice(chans)
                when = datetime(day.year, day.month, day.day, 9 + 4 * k + rng.randint(0, 3), rng.choice((0, 15, 30, 45)))
                res = lg.reserve(Instruction(bid, r["obligation_id"], CAMPAIGN, ch, TEMPLATES[ch][0], when, EST_COST[ch]))
                decisions[res.decision.reason] = decisions.get(res.decision.reason, 0) + 1
                if not res.reserved or ex.dispatch(res.instruction_id, when).outcome != "SENT":
                    continue
                reached = rng.random() < DELIVERY_RATE[ch]
                if ch in R.CALL_CHANNELS:
                    lg.record_call(bid, when + timedelta(minutes=1), rng.choice((35, 50, 90)) if reached else rng.choice((0, 4)), ch)
                elif reached:
                    lg.record_provider_status(res.instruction_id, "DELIVERED")
                if reached and rng.random() < REPLY_RATE[ch]:
                    lg.record_response(bid, when + timedelta(minutes=rng.randint(5, 90)), channel=ch,
                                       details=rng.choice(("will pay friday", "call me later", "paid already?")))
                    if rng.random() < 0.4:
                        ob = c.execute("SELECT amount_due_paise - amount_paid_paise AS left FROM obligations"
                                       " WHERE obligation_id=?", (r["obligation_id"],)).fetchone()
                        if ob["left"] > 0:
                            lg.record_payment_verified(r["obligation_id"], ob["left"], when + timedelta(hours=2),
                                                       f"PAY-{r['loan_id']}-{day}", f"UTR-{r['loan_id']}-{day}")
                elif reached and rng.random() < 0.02:
                    lg.record_stop(bid, when + timedelta(minutes=10), channel=ch)

    start, end = contact_days[0], contact_days[-1]
    sheets = R.report_pack(lg, start, end, hash_ids=hash_ids)
    for day in contact_days:
        rep = R.daily_segment_report(lg, CAMPAIGN, day)
        for name, rows in R.daily_segment_sheets(rep).items():
            sheets[f"{name}_{day.isoformat()}"] = rows
    paths = R.export_pack(sheets, out)

    print(f"Kairos K26 report pack — {R.PROVENANCE} (seed {seed}, {n} borrowers, {start} .. {end})")
    print("decisions:", ", ".join(f"{k} {v}" for k, v in sorted(decisions.items(), key=lambda kv: -kv[1])))
    for name in R.SHEETS:
        print(f"  {R.SHEET_TITLES[name]:<14} {len(sheets[name]):>4} rows")
    for day in contact_days:
        t = R.daily_segment_report(lg, CAMPAIGN, day)["totals"]
        rp = t["replies_per_1000_inr"]
        print(f"  {day}  attempts {t['attempts']:>3}  reach {t['reach']:>3}  replies {t['meaningful_replies']:>2}"
              f"  payments {t['verified_payments']:>2} (Rs.{t['verified_inr']:,.2f})"
              f"  spend Rs.{t['spend_committed_inr']:.2f}  replies/Rs.1000 {rp if rp is not None else '-'}"
              f"  cap breaches {t['cap_breaches']}")
    print(f"wrote {len(paths)} CSVs to {out}")
    lg.close()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--borrowers", type=int, default=60)
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", type=Path, default=BACKEND / "reports" / "report_pack")
    ap.add_argument("--hash-ids", action="store_true", help="replace borrower ids with a keyed hash (KAIROS_REPORT_SALT)")
    a = ap.parse_args(argv)
    run(a.borrowers, a.days, a.seed, a.out, a.hash_ids)


if __name__ == "__main__":
    main()
