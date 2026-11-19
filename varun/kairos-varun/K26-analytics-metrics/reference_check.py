"""K26 · Analytics data and metrics - verification (9 tests, rebuilt from raw events).

Usage:  python3 reference_check.py            (uses the latest run; runs the pipeline if none)
        python3 reference_check.py --rebuild
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "kairos_engine"))
from checks_common import Runner, latest_run, load_logs  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from common import path  # noqa: E402

R = Runner("K26 analytics data and metrics", HERE)
man, rdir = latest_run("--rebuild" in sys.argv)
L = load_logs(rdir)
k = pd.read_csv(os.path.join(rdir, "kpis.csv"))
seg = pd.read_csv(os.path.join(rdir, "kpis_by_segment.csv"))
act = pd.read_csv(os.path.join(rdir, "borrower_activity.csv.gz"))
port = pd.read_csv(os.path.join(path("data", "generated", f"seed_{man['seed']}"), "borrowers.csv"))
print(f"Run {man['run_id']} · {man['label']}\n")

# T01 joins
ids, obl = set(port.borrower_id), set(port.obligation_id)
orph = sum((~L[p]["sends"].borrower_id.isin(ids)).sum() + (~L[p]["sends"].obligation_id.isin(obl)).sum()
           + (~L[p]["payments"].borrower_id.isin(ids)).sum() for p in L)
uniq = not act.duplicated(["policy", "borrower_id"]).any() and len(act) == 2 * len(port)
R.check("K26-T01", orph == 0 and uniq, f"orphan events: {orph}; activity rows {len(act):,} = 2 x {len(port):,} borrowers, unique per policy: {uniq}")

# T02 fields
need = ["run_id", "run_label", "seed", "baseline", "policy", "kpi", "numerator", "numerator_source", "denominator", "denominator_source", "value", "definition"]
empty = int(k[need].isna().sum().sum())
R.check("K26-T02", empty == 0, f"{len(k)} KPI rows x {len(need)} fields, {empty} empty")

# T03 independent recomputation
diffs, n = [], 0
for pol, lg in L.items():
    s, p = lg["sends"], lg["payments"]
    elig = int(act[act.policy == pol].eligible.sum())
    att = s.borrower_id.nunique(); reach = s.loc[s.delivered, "borrower_id"].nunique()
    fr = s[s.replied].groupby("borrower_id").date.min(); resp = len(fr)
    pp = p[p.borrower_id.isin(fr.index)]
    paid_rep = pp[pp.date > fr.reindex(pp.borrower_id).to_numpy()].borrower_id.nunique()
    rep, spend, touches = int(s.replied.sum()), float(s.actual_cost_inr.sum()), len(s)
    pool = float(k[(k.policy == pol) & (k.kpi == "budget_utilisation")].denominator.iloc[0])
    expect = {"eligible_rate": (elig, len(port)), "attempt_rate": (att, elig), "unique_reach_rate": (reach, att),
              "response_rate": (resp, reach), "payment_after_reply_rate": (paid_rep, resp), "replies_per_inr": (rep, spend),
              "inr_per_reply": (spend, rep), "reply_per_touch": (rep, touches), "verified_payment_rate_all": (p.borrower_id.nunique(), len(port)),
              "budget_utilisation": (spend, man["budget_pool_inr"]), "opt_out_rate": (int(s.stop.sum()), reach)}
    for name, (num, den) in expect.items():
        n += 1
        row = k[(k.policy == pol) & (k.kpi == name)].iloc[0]
        if not (np.isclose(row.numerator, num, atol=0.01) and np.isclose(row.denominator, den, atol=0.01) and np.isclose(row.value, num / den, atol=1e-6)):
            diffs.append(f"{pol}.{name}")
R.check("K26-T03", not diffs, f"{n} KPIs rebuilt from raw send/payment events and the portfolio, differences: {diffs or 0}")

# T04 denominators chain
chain = [("attempt_rate", "eligible_rate"), ("unique_reach_rate", "attempt_rate"), ("response_rate", "unique_reach_rate"),
         ("payment_after_reply_rate", "response_rate")]
ok = True
for pol in L:
    kk = k[k.policy == pol].set_index("kpi")
    for cur, prev in chain:
        ok &= kk.loc[cur, "denominator"] == kk.loc[prev, "numerator"] and kk.loc[cur, "numerator"] <= kk.loc[cur, "denominator"]
R.check("K26-T04", ok, "each funnel rate's denominator equals the previous stage's numerator, and no rate exceeds 100% (both policies)")

# T05 unique counting
ok, parts = True, []
for pol, lg in L.items():
    a, s = act[act.policy == pol], lg["sends"]
    pairs = [("attempted", s.borrower_id.nunique()), ("reached", s.loc[s.delivered, "borrower_id"].nunique()),
             ("responded", s.loc[s.replied, "borrower_id"].nunique())]
    for col, raw in pairs:
        ok &= int(a[col].sum()) == raw
    parts.append(f"{pol}: " + ", ".join(f"{c} {int(a[c].sum()):,}={r:,}" for c, r in pairs))
R.check("K26-T05", ok, " | ".join(parts))

# T06 spend
dt = pd.read_csv(os.path.join(rdir, "daily_totals.csv"))
ok, parts = True, []
for pol, lg in L.items():
    vals = [lg["sends"].actual_cost_inr.sum(), dt[dt.policy == pol].spend_inr.sum(),
            float(k[(k.policy == pol) & (k.kpi == "replies_per_inr")].denominator.iloc[0]), act[act.policy == pol].spend_inr.sum()]
    ok &= max(vals) - min(vals) <= 0.05
    parts.append(f"{pol}: events ₹{vals[0]:,.2f} / daily ₹{vals[1]:,.2f} / KPI ₹{vals[2]:,.2f} / activity ₹{vals[3]:,.2f}")
R.check("K26-T06", ok, " | ".join(parts))

# T07 labels
from analytics import RUN_MODES, build  # noqa: E402
labels = set(k.run_label) | set(seg.run_label) | set(act.run_label)
try:
    build({}, port, "X", "TEST", 0, "x", "/tmp"); rejected = False
except AssertionError:
    rejected = True
ok = labels <= set(RUN_MODES) and labels == {man["run_mode"]} == {"SIMULATED"} and rejected
R.check("K26-T07", ok, f"labels found {labels} (allowed {RUN_MODES}); invalid label 'TEST' rejected: {rejected}")

# T08 segments add up
ok = True
for pol in L:
    a, sg = act[act.policy == pol], seg[seg.policy == pol]
    ok &= sg.allocated.sum() == len(a) and sg.reached.sum() == a.reached.sum() and sg.responders.sum() == a.responded.sum() \
        and sg.replies.sum() == a.replies.sum() and abs(sg.spend_inr.sum() - a.spend_inr.sum()) < 0.05
R.check("K26-T08", ok, f"{seg.segment.nunique()} segments x 2 policies add up to the totals")

# T09 consistency with K15
sm = json.load(open(os.path.join(rdir, "summary.json")))
fn = pd.read_csv(os.path.join(rdir, "funnel.csv"))
ok, parts = True, []
for pol in L:
    v = float(k[(k.policy == pol) & (k.kpi == "replies_per_inr")].value.iloc[0])
    ok &= np.isclose(v, sm["policies"][pol]["replies_per_inr"], atol=1e-6) and \
        int(k[(k.policy == pol) & (k.kpi == "payment_after_reply_rate")].numerator.iloc[0]) == int(fn[(fn.policy == pol) & (fn.stage == "paid_after_reply")].unique_borrowers.iloc[0])
    parts.append(f"{pol} replies/₹ {v:.4f}")
R.check("K26-T09", ok, "replies per ₹ " + ", ".join(parts) + " equal K15 summary; paid-after-reply equals the K15 funnel")

R.finish(f"run {man['run_id']}")
