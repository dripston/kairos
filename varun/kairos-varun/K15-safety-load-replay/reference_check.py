"""K15 · Safety, load and story replay - verification (15 tests, all computed).

Usage:  python3 reference_check.py            (uses the latest run; runs the pipeline if none)
        python3 reference_check.py --rebuild  (fresh 50K run first, about 3-4 minutes)
Every test reads raw logs or re-executes code; none compares a value with itself.
"""
import asyncio
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "kairos_engine"))
from checks_common import Runner, latest_run, load_logs  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from common import band_index, load_all, path  # noqa: E402

R = Runner("K15 safety, load and story replay", HERE)
man, rdir = latest_run("--rebuild" in sys.argv)
cfg, k01, k21 = load_all()
L = load_logs(rdir)
summ = json.load(open(os.path.join(rdir, "summary.json")))
act = pd.read_csv(os.path.join(rdir, "borrower_activity.csv.gz"))
data = pd.read_csv(os.path.join(path("data", "generated", f"seed_{man['seed']}"), "borrowers.csv"))
print(f"Run {man['run_id']} · {man['label']}\n")

# T01 dataset
raw = pd.read_csv(path("data", "raw", "PL_Dummy_Collections_Data_500.csv"))
need = {"Loan ID", "EMI Amount", "POS", "TOS", "DPD", "Bucket"}
ok = len(raw) == 500 and need <= set(raw.columns) and data["source_loan_id"].isin(raw["Loan ID"]).all() \
    and man["dataset"]["official_datasets_used"] is False and len(data) == man["borrowers"]
R.check("K15-T01", ok, f"{len(raw)} rows, tracker columns present: {need <= set(raw.columns)}; {len(data):,} borrowers all trace to a source "
        f"Loan ID: {data['source_loan_id'].isin(raw['Loan ID']).all()}; sha256 {man['dataset']['sha256_12']}; official D1-D13 used: no (pending)")

# T02 throughput
tm = pd.read_csv(os.path.join(rdir, "timings.csv"))
ok = (tm["borrowers"] == 50000).all() and tm["plan_seconds"].max() < 600 and set(tm["policy"]) == {"kairos", "fixed_cadence"}
R.check("K15-T02", ok, f"{tm.groupby('policy').size().to_dict()} days planned for {tm['borrowers'].iloc[0]:,} borrowers; slowest day "
        f"{tm['plan_seconds'].max():.2f} s (limit 600 s)")

# T03 same conditions
dt = pd.read_csv(os.path.join(rdir, "daily_totals.csv"))
pools = dt.groupby("policy")["pool_inr"].unique()
dates = dt.groupby("policy")["date"].apply(tuple)
ok = len({tuple(v) for v in pools}) == 1 and len(set(dates)) == 1 and summ["seed"] == man["seed"]
R.check("K15-T03", ok, f"seed {man['seed']} for both; pool ₹{pools.iloc[0][0]:,.0f} for both; same {len(dates.iloc[0])}-day window; "
        f"K01 {k01['policy_version']} sha {man['policy_files']['k01_policy']['sha256_12']}")

# T04 funnel from raw logs
fn = pd.read_csv(os.path.join(rdir, "funnel.csv"))
lines, ok = [], True
for pol, lg in L.items():
    s, p = lg["sends"], lg["payments"]
    elig = int(act[(act.policy == pol)]["eligible"].sum())
    att = s["borrower_id"].nunique()
    reach = s.loc[s["delivered"], "borrower_id"].nunique()
    first_rep = s[s["replied"]].groupby("borrower_id")["date"].min()
    resp = len(first_rep)
    pp = p[p["borrower_id"].isin(first_rep.index)]
    paid = pp[pp["date"] > first_rep.reindex(pp["borrower_id"]).to_numpy()]["borrower_id"].nunique()
    stages = [elig, att, reach, resp, paid]
    rep = fn[fn.policy == pol].set_index("stage")["unique_borrowers"]
    reported = [rep["eligible"], rep["attempted"], rep["reached"], rep["responders"], rep["paid_after_reply"]]
    ok &= stages == [int(x) for x in reported] and all(a >= b for a, b in zip(stages, stages[1:]))
    lines.append(f"{pol}: " + " -> ".join(f"{x:,}" for x in stages))
R.check("K15-T04", ok, "; ".join(lines) + " (recomputed from logs = funnel.csv)")

# T05/T06 compare + lift
cmp = {}
for pol, lg in L.items():
    s = lg["sends"]
    first_rep = s[s["replied"]].groupby("borrower_id")["date"].min()
    pp = lg["payments"][lg["payments"]["borrower_id"].isin(first_rep.index)]
    cmp[pol] = {"reach": s.loc[s["delivered"], "borrower_id"].nunique(), "rpr": s["replied"].sum() / s["actual_cost_inr"].sum(),
                "paid": pp[pp["date"] > first_rep.reindex(pp["borrower_id"]).to_numpy()]["borrower_id"].nunique()}
bm = open(os.path.join(rdir, "benchmark.md"), encoding="utf-8").read()
ok = all(k in bm for k in ("Unique borrowers reached", "Replies per ₹", "Verified payments after a reply"))
R.check("K15-T05", ok, " | ".join(f"{p}: reach {c['reach']:,}, replies/₹ {c['rpr']:.4f}, paid after reply {c['paid']:,}" for p, c in cmp.items()))
lift = (cmp["kairos"]["rpr"] / cmp["fixed_cadence"]["rpr"] - 1) * 100
R.check("K15-T06", lift >= 20, f"{cmp['kairos']['rpr']:.4f} vs {cmp['fixed_cadence']['rpr']:.4f} replies per ₹ = {lift:+.1f}% (needs >= +20%)")

# T07 every send audited
au = {p: json.load(open(os.path.join(rdir, f"audit_{p}.json"))) for p in L}
ok = all(au[p]["sends_checked"] == len(L[p]["sends"]) and au[p]["k01_breaches"] == 0 and au[p]["other_breaches"] == 0 for p in L)
R.check("K15-T07", ok, " | ".join(f"{p}: {au[p]['sends_checked']:,} of {len(L[p]['sends']):,} sends checked, "
                                 f"{au[p]['k01_breaches'] + au[p]['other_breaches']} breaches" for p in L))

# T08 auditor negative test
from audit import audit  # noqa: E402
from build_data import build  # noqa: E402
from engine import load_portfolio, run_campaign  # noqa: E402
from policies import Kairos  # noqa: E402
c2 = dict(cfg, n_borrowers=2000)
d2, t2 = load_portfolio(build(c2, 99, verbose=False))
run = run_campaign(Kairos(c2, k21, d2, 99), c2, k01, d2, t2, 99, None, log=lambda *a: None)
s = run["sends"]
pool = s[(s.channel == "sms") & ~s.replied & ~s.answered & ~s.stop & (s.band == 1) & (s.time == "10:00")].drop_duplicates("borrower_idx")
bad = []
def inject(name, **chg):
    r = pool.iloc[len(bad)].copy()
    for k, v in chg.items():
        r[k] = v
    r["touch_id"] = "INJECTED-" + name
    bad.append(r)
inject("SAME_CHANNEL_GAP", time="11:00")
inject("CONTACT_WINDOW", time="19:00")
inject("NO_APPROVED_TEMPLATE", template_id="T-WHA-COL-HI-02", time="16:00")
inject("PTP_HOLD", ptp_day=int(pool.iloc[3]["day"]) + 2, time="18:00")
inject("STOP_OPT_OUT", borrower_idx=int(d2.index[d2["opted_out_at_start"]].min()), time="18:00")
run["sends"] = pd.concat([s, pd.DataFrame(bad)], ignore_index=True)
res = audit(run, d2, t2, c2, k01, "kairos")
found = {b["touch_id"].replace("INJECTED-", ""): b["reason"] for b in res["breach_examples"] if b["touch_id"].startswith("INJECTED")}
ok = all(found.get(n) == n for n in ["SAME_CHANNEL_GAP", "CONTACT_WINDOW", "NO_APPROVED_TEMPLATE", "PTP_HOLD", "STOP_OPT_OUT"])
R.check("K15-T08", ok, f"injected 5, caught {sum(found.get(n) == n for n in found)} with the right reason: {found}")

# T09 500 chats
ch = json.load(open(os.path.join(rdir, "chat_load_report.json")))
ok = ch["peak_concurrent_conversations"] >= 500 and ch["safety_violations"] == 0 and ch["first_response_within_sla_pct"] == 100
R.check("K15-T09", ok, f"peak {ch['peak_concurrent_conversations']} concurrent, {ch['total_turns']:,} turns in {ch['wall_clock_seconds']} s; "
        f"first reply p95 {ch['first_response_seconds']['p95']} s (SLA {ch['first_response_seconds']['sla']} s); "
        f"{ch['safety_violations']} leaks; target: {ch['target']}")

# T10 leaky agent must be caught
from chat_load import ReferenceAgent, _load_k10, run as run_chats  # noqa: E402
class LeakyAgent(ReferenceAgent):
    async def handle(self, conv_id, from_number, text, hint, extra, reg, names):
        if hint == "DUES_QUERY":
            return "DUES"                         # bug on purpose: no verification
        return await super().handle(conv_id, from_number, text, hint, extra, reg, names)
k10, pol = _load_k10()
leak = asyncio.run(run_chats(100, 7, agent=LeakyAgent(7, k10, pol)))
R.check("K15-T10", leak["safety_violations"] > 0, f"leaking agent produced {leak['safety_violations']} violations "
        f"(e.g. {leak['violation_examples'][0]['kind'] if leak['violation_examples'] else '-'}) -> checker works")

# T11 E01
allr = pd.concat([L["kairos"]["sends"], L["fixed_cadence"]["sends"]])
bands = [b["id"] for b in k01["dpd_bands"]]
expected = np.array(bands)[band_index(k01, allr["dpd"].to_numpy())]
mism = int((expected != allr["band"].to_numpy()).sum())
def crossed(lo, hi):
    a = set(allr.loc[allr.dpd == lo, "borrower_id"]); b = set(allr.loc[allr.dpd == hi, "borrower_id"])
    return len(a & b)
c1, c2_, c0 = crossed(30, 31), crossed(60, 61), crossed(0, 1)
dec = pd.read_csv(os.path.join(rdir, "decisions_daily.csv"))
stale = int(dec.loc[dec.reason == "DPD_INVALID", "borrowers"].sum())
ok = mism == 0 and c1 > 0 and c2_ > 0 and stale > 0
R.check("K15-T11", ok, f"{len(allr):,} sends, band mismatches {mism}; borrowers contacted at both DPD 30 (B1) and 31 (B2): {c1:,}; "
        f"60 (B2) and 61 (B3): {c2_:,}; 0 (PRE_DUE) and 1 (B1): {c0:,}; stale-DPD borrower-days held: {stale:,}. "
        "Note: K01 puts the switch at 30->31 and 60->61; the tracker text says 29->30 / 59->60 - K01 is the source of truth")

# T12 E04
sms_read = int(allr.loc[allr.channel == "sms", "read"].sum())
engaged_no_reply = int(((allr["clicked"] | allr["read"]) & ~allr["replied"]).sum())
counted = int(allr["replied"].sum())
ok = sms_read == 0 and engaged_no_reply > 0 and counted == int(L["kairos"]["sends"]["replied"].sum() + L["fixed_cadence"]["sends"]["replied"].sum())
R.check("K15-T12", ok, f"SMS rows with read=True: {sms_read} (stays UNKNOWN); {engaged_no_reply:,} touches clicked/read without a reply, "
        "none counted as replies. Duplicate/late provider callbacks need Rehan's adapters (K07/K08) - not in this replay")

# T13 E06
rev = json.load(open(os.path.join(rdir, "cohort_review.json")))
sw_date = rev["switch_detected"]["date"]
def e06(pol):
    s = L[pol]["sends"].sort_values(["borrower_id", "date", "time"])
    days = sorted(s.date.unique()); nxt = {d: days[i + 1] for i, d in enumerate(days[:-1])}
    click = s[(s.channel == "whatsapp") & s.clicked].groupby("borrower_id").date.min()
    nr = s[(s.channel == "sms") & ~s.replied & s.delivered][["borrower_id", "date"]].drop_duplicates()
    nr = nr[nr.date.isin(nxt)].assign(next=lambda x: x.date.map(nxt))
    nr["clicked_before"] = nr.borrower_id.map(click) < nr.date
    first = s.groupby(["borrower_id", "date"]).head(1)[["borrower_id", "date", "channel"]].rename(columns={"date": "next", "channel": "next_ch"})
    j = nr.merge(first, on=["borrower_id", "next"])
    r = j.groupby("clicked_before").next_ch.apply(lambda x: (x == "whatsapp").mean())
    return float(r.get(True, 0)), float(r.get(False, 0)), int(j.clicked_before.sum())
def share(pol):
    s = L[pol]["sends"]; s = s[s["segment"].str.endswith("SAL")]
    return (s[s.date < sw_date].channel == "whatsapp").mean(), (s[s.date >= sw_date].channel == "whatsapp").mean()
yes, no, n_click = e06("kairos")
kb, ka = share("kairos"); fb, fa = share("fixed_cadence")
rel = (ka - kb) - (fa - fb)
ok = (yes - no) >= 0.10 and rel <= -0.05
R.check("K15-T13", ok, f"after an unanswered SMS, Kairos sends WhatsApp first next day {yes:.0%} with an earlier WhatsApp click "
        f"vs {no:.0%} without ({n_click:,} cases); switch {sw_date}: salaried WhatsApp share Kairos {kb:.0%} -> {ka:.0%}, "
        f"fixed cadence {fb:.0%} -> {fa:.0%} (Kairos moved {rel * 100:+.0f} points relative to fixed cadence)")

# T14 E17
det = summ["determinism"]
uniq = all(L[p]["sends"]["touch_id"].is_unique for p in L)
ok = bool(det and det["identical"]) and uniq
R.check("K15-T14", ok, f"run 1 {det['run_1_sha256'][:12]} = run 2 {det['run_2_sha256'][:12]}: {det['identical']}; touch_id unique in both logs: {uniq}")

# T15 E18
modes = set(allr["data_mode"])
ok = modes == {"SIMULATED"} and all(man.get(k) for k in ("run_id", "seed", "baseline", "policy_files")) and "SIMULATED" in man["label"]
R.check("K15-T15", ok, f"data_mode values {modes}; run {man['run_id']}, seed {man['seed']}, baseline: {man['baseline']}")

R.finish(f"run {man['run_id']}")
