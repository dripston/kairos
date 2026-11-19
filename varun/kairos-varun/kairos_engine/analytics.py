"""K26 - join agent activity with eligible borrowers, reach, replies, cost and payments.

borrower_activity.csv.gz : one row per borrower per policy (the joined table)
kpis.csv                 : every KPI with numerator, denominator, their source events,
                           the value, and the run label (LIVE / REPLAY / SIMULATED)
The K26 reference_check recomputes every KPI straight from the raw event logs
(sends_*.csv.gz, payments_*.csv) and the portfolio file, and must match exactly.
"""
import os

import numpy as np
import pandas as pd

RUN_MODES = ("LIVE", "REPLAY", "SIMULATED")


def build(runs, data, run_id, run_mode, seed, baseline_source, out_dir):
    assert run_mode in RUN_MODES
    acts = []
    for name, r in runs.items():
        s, p = r["sends"], r["payments"]
        g = s.groupby("borrower_idx").agg(touches=("replied", "size"), spend_inr=("actual_cost_inr", "sum"),
                                          replies=("replied", "sum"), reached=("delivered", "any"),
                                          first_touch_day=("day", "min"))
        first_reply = s[s["replied"]].groupby("borrower_idx")["day"].min()
        a = pd.DataFrame(index=pd.RangeIndex(len(data), name="borrower_idx"))
        a = a.join(g).join(first_reply.rename("first_reply_day"))
        pay = p.groupby("borrower_idx").agg(payment_day=("day", "min"), amount_inr=("amount_inr", "sum"))
        a = a.join(pay)
        a["touches"] = a["touches"].fillna(0).astype(int)
        a["replies"] = a["replies"].fillna(0).astype(int)
        a["spend_inr"] = a["spend_inr"].fillna(0.0).round(2)
        a["attempted"] = a["touches"] > 0
        a["reached"] = a["reached"].fillna(False).astype(bool)
        a["responded"] = a["replies"] > 0
        a["paid_any"] = a["payment_day"].notna()
        a["paid_after_reply"] = a["paid_any"] & a["first_reply_day"].notna() & (a["payment_day"] > a["first_reply_day"])
        a["eligible"] = r["ever_eligible"]
        a["policy"] = name
        acts.append(a.reset_index())
    act = pd.concat(acts, ignore_index=True)
    meta = data[["borrower_id", "loan_id", "obligation_id", "segment", "dpd_at_start", "language"]].reset_index(names="borrower_idx")
    act = act.merge(meta, on="borrower_idx", how="left")
    act["run_id"], act["run_label"], act["seed"], act["baseline"] = run_id, run_mode, seed, baseline_source
    cols = ["run_id", "run_label", "seed", "baseline", "policy", "borrower_id", "loan_id", "obligation_id", "segment",
            "dpd_at_start", "language", "eligible", "attempted", "touches", "reached", "replies", "responded",
            "spend_inr", "paid_any", "paid_after_reply", "first_touch_day", "first_reply_day", "payment_day", "amount_inr"]
    act = act[cols]
    os.makedirs(out_dir, exist_ok=True)
    act.to_csv(os.path.join(out_dir, "borrower_activity.csv.gz"), index=False, compression={"method": "gzip", "compresslevel": 1})

    rows = []
    def kpi(policy, name, num, num_src, den, den_src, definition):
        rows.append({"run_id": run_id, "run_label": run_mode, "seed": seed, "baseline": baseline_source, "policy": policy,
                     "kpi": name, "numerator": num, "numerator_source": num_src, "denominator": den,
                     "denominator_source": den_src, "value": round(num / den, 6) if den else None, "definition": definition})
    for name, a in act.groupby("policy"):
        s = runs[name]["sends"]
        n_alloc, n_elig = len(a), int(a.eligible.sum())
        n_att, n_reach, n_resp = int(a.attempted.sum()), int(a.reached.sum()), int(a.responded.sum())
        n_paid_rep, n_paid = int(a.paid_after_reply.sum()), int(a.paid_any.sum())
        replies, touches, spend = int(s.replied.sum()), len(s), round(float(s.actual_cost_inr.sum()), 2)
        kpi(name, "eligible_rate", n_elig, "borrowers passing all K01 gates on >=1 contact day (decision log)", n_alloc, "allocated borrowers (portfolio file)", "eligible / allocated")
        kpi(name, "attempt_rate", n_att, "distinct borrower_id in send events", n_elig, "eligible borrowers", "attempted / eligible")
        kpi(name, "unique_reach_rate", n_reach, "distinct borrower_id with delivered=True send events", n_att, "attempted borrowers", "reached / attempted")
        kpi(name, "response_rate", n_resp, "distinct borrower_id with replied=True send events", n_reach, "reached borrowers", "responders / reached")
        kpi(name, "payment_after_reply_rate", n_paid_rep, "distinct borrower_id with a payment event after their first reply", n_resp, "responders", "paid after reply / responders")
        kpi(name, "replies_per_inr", replies, "send events with replied=True (one reward per touch)", spend, "sum of actual_cost_inr over send events", "CM-02 score")
        kpi(name, "inr_per_reply", spend, "sum of actual_cost_inr over send events", replies, "send events with replied=True", "cost per meaningful reply")
        kpi(name, "reply_per_touch", replies, "send events with replied=True", touches, "send events", "replies / touches")
        kpi(name, "verified_payment_rate_all", n_paid, "distinct borrower_id with a payment event (incl. self-cure)", n_alloc, "allocated borrowers", "paid / allocated")
        kpi(name, "budget_utilisation", spend, "sum of actual_cost_inr over send events", runs[name]["totals"]["pool_inr"], "campaign pool = budget per case x allocated", "spend / pool")
        kpi(name, "opt_out_rate", int(s.stop.sum()), "send events with stop=True", n_reach, "reached borrowers", "STOPs / reached")
    k = pd.DataFrame(rows)
    k.to_csv(os.path.join(out_dir, "kpis.csv"), index=False)
    seg = act.groupby(["policy", "segment"]).agg(allocated=("borrower_id", "size"), eligible=("eligible", "sum"),
                                                 reached=("reached", "sum"), responders=("responded", "sum"),
                                                 replies=("replies", "sum"), spend_inr=("spend_inr", "sum"),
                                                 paid_after_reply=("paid_after_reply", "sum")).reset_index()
    seg["replies_per_inr"] = (seg.replies / seg.spend_inr).round(5)
    seg["run_label"] = run_mode
    seg.to_csv(os.path.join(out_dir, "kpis_by_segment.csv"), index=False)
    return act, k
