"""K23 · Cohort close and policy review - verification (9 tests, all recomputed from raw logs).

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

from cohort import detect_switch, needs_owner_approval, POLICY_OWNER  # noqa: E402
from common import SEGMENTS, file_sha, load_all, ojas_file  # noqa: E402

R = Runner("K23 cohort close and policy review", HERE)
man, rdir = latest_run("--rebuild" in sys.argv)
cfg, k01, k21 = load_all()
L = load_logs(rdir)
rev = json.load(open(os.path.join(rdir, "cohort_review.json")))
dt = pd.read_csv(os.path.join(rdir, "daily_totals.csv"))
dates = dt[dt.policy == "kairos"]["date"].tolist()
quiet = dt[dt.policy == "kairos"]["quiet_day"].tolist()
day_of = {d: i for i, d in enumerate(dates)}
contact = [i for i, q in enumerate(quiet) if not q]
print(f"Run {man['run_id']} · {man['label']}\n")

def internal(s):
    return s.assign(segment_idx=s["segment"].map({g: i for i, g in enumerate(SEGMENTS)}), day=s["date"].map(day_of))

# T01
sw = detect_switch(internal(L["kairos"]["sends"]), contact)
cut = sw["switch_day"]
n_before, n_after = sum(d < cut for d in contact), sum(d >= cut for d in contact)
ok = sw["switch_day"] == rev["switch_detected"]["switch_day"] and n_before >= 4 and n_after >= 4
R.check("K23-T01", ok, f"switch re-detected on {dates[cut]} ({sw['group']}: {sw['change']}) = review's {rev['switch_detected']['date']}; "
        f"{n_before} contact days before, {n_after} after")

# T02 / T03
def recompute(group_col):
    rows = []
    for pol, lg in L.items():
        s = internal(lg["sends"])
        s["employment"] = np.where(s["segment"].str.endswith("SAL"), "Salaried", "Self-employed")
        s["period"] = np.where(s["day"] >= cut, "after_switch", "before_switch")
        for key, g in s.groupby(["employment", "period", group_col]):
            r = {"policy": pol, "employment": key[0], "period": key[1], group_col: key[2], "touches": len(g),
                 "meaningful_replies": int(g.replied.sum()), "reply_rate": round(float(g.replied.mean()), 5),
                 "replies_per_inr": round(float(g.replied.sum() / g.actual_cost_inr.sum()), 5)}
            if group_col == "channel":
                r["unique_reach"] = int(g.loc[g.delivered, "borrower_id"].nunique())
                r["spend_inr"] = round(float(g.actual_cost_inr.sum()), 2)
            rows.append(r)
    return pd.DataFrame(rows)

def compare(mine, theirs, keys):
    t = pd.DataFrame(theirs)[mine.columns]
    m = mine.merge(t, on=keys, suffixes=("", "_rev"), how="outer", indicator=True)
    bad = (m["_merge"] != "both").sum()
    for c in mine.columns:
        if c in keys:
            continue
        bad += int((~np.isclose(m[c].astype(float), m[c + "_rev"].astype(float), atol=0.011)).sum())
    return len(mine), int(bad)

chk = recompute("channel")
n, bad = compare(chk, rev["channels"], ["policy", "employment", "period", "channel"])
R.check("K23-T02", bad == 0, f"{n} channel rows recomputed from raw logs, {bad} differences from cohort_review.json")
tmk = recompute("time")
n, bad = compare(tmk, rev["send_times"], ["policy", "employment", "period", "time"])
slots = set(tmk[(tmk.policy == "kairos") & (tmk.period == "after_switch")]["time"])
R.check("K23-T03", bad == 0 and slots == set(cfg["slots"]), f"{n} send-time rows recomputed, {bad} differences; Kairos slots reviewed after the switch: {sorted(slots)}")

# T04
fn = pd.read_csv(os.path.join(rdir, "funnel.csv"))
pc = {p["policy"]: p for p in rev["policy_comparison"]}
ch = pd.DataFrame(rev["channels"])
ok, parts = True, []
for pol in L:
    f = fn[fn.policy == pol].set_index("stage")["unique_borrowers"]
    attributed = int(ch[ch.policy == pol]["verified_payments_after_reply"].sum())
    reach = int(L[pol]["sends"].loc[L[pol]["sends"].delivered, "borrower_id"].nunique())
    replies = int(L[pol]["sends"]["replied"].sum())
    ok &= pc[pol]["unique_reach"] == reach == f["reached"] and pc[pol]["meaningful_replies"] == replies \
        and pc[pol]["verified_payments_after_reply"] == f["paid_after_reply"] == attributed
    parts.append(f"{pol}: reach {reach:,}, replies {replies:,}, paid after reply {pc[pol]['verified_payments_after_reply']:,} "
                 f"(K15 funnel {f['paid_after_reply']:,}, sum over channels {attributed:,})")
R.check("K23-T04", ok, " | ".join(parts))

# T05
props = rev["proposals"]
kc = chk[chk.policy == "kairos"].set_index(["employment", "period", "channel"])
ok, n_learn = all(p.get("evidence") for p in props), 0
for p in props:
    if p["category"] == "LEARNING_PRIOR":
        n_learn += 1
        _, _, emp, chn = p["target"].split(".")
        b, a = kc.loc[(emp, "before_switch", chn)], kc.loc[(emp, "after_switch", chn)]
        e = p["evidence"]
        ok &= np.isclose(e["reply_rate_before"], b.reply_rate) and np.isclose(e["reply_rate_after"], a.reply_rate) \
            and min(b.touches, a.touches) >= 300
R.check("K23-T05", ok, f"{len(props)} proposals all carry evidence; {n_learn} learning proposals match recomputed reply rates with >= 300 touches each side")

# T06
governed = ["caps.per_channel_per_day_by_band.B1", "consent_required_per_channel", "holds.HARDSHIP.default_pause_days",
            "contact_window.start", "holds.PROMISE_TO_PAY.effect", "caps.cross_channel_daily_ceiling"]
free = ["kairos.prior.Salaried.whatsapp", "kairos.default_slot.Salaried", "k22.template_catalogue.voicebot.hi"]
cls_ok = all(needs_owner_approval(t) for t in governed) and not any(needs_owner_approval(t) for t in free)
prop_ok = all(p["requires_policy_owner_approval"] == needs_owner_approval(p["target"]) and
              (not p["requires_policy_owner_approval"] or (p["approver"] == POLICY_OWNER and p["status"] == "PENDING_POLICY_OWNER_APPROVAL"))
              and p["applied"] is False for p in props)
pend = [p["proposal_id"] + " " + p["target"] for p in props if p["requires_policy_owner_approval"]]
R.check("K23-T06", cls_ok and prop_ok, f"classifier correct on {len(governed)} governed + {len(free)} free targets; "
        f"pending Ojas: {pend or 'none'}; applied: none")

# T07
from engine import load_portfolio  # noqa: E402
from policies import Kairos  # noqa: E402
from common import path  # noqa: E402
data, _ = load_portfolio(path("data", "generated", f"seed_{man['seed']}"))
fresh = Kairos(cfg, k21, data, man["seed"]).snapshot()
pre = json.load(open(os.path.join(rdir, "priors_pre_cohort.json")))
same = all(np.allclose(np.array(fresh[k]), np.array(pre[k])) for k in ("prior_alpha", "prior_beta", "evidence_success", "evidence_failure"))
hashes = file_sha(os.path.join(rdir, "priors_pre_cohort.json")) == rev["rollback"]["pre_cohort_sha256_12"] and \
    file_sha(os.path.join(rdir, "priors_post_cohort.json")) == rev["rollback"]["post_cohort_sha256_12"]
post = json.load(open(os.path.join(rdir, "priors_post_cohort.json")))
changed = not np.allclose(np.array(post["evidence_success"]), np.array(pre["evidence_success"]))
R.check("K23-T07", same and hashes and changed, f"fresh start == pre-cohort snapshot: {same}; snapshot hashes match: {hashes}; "
        f"post-cohort beliefs differ from pre (learning happened): {changed}")

# T08
now = {k: file_sha(ojas_file(k)[0]) for k in ("k01_policy", "k21_scorecard")}
rec = {k: man["policy_files"][k]["sha256_12"] for k in now}
R.check("K23-T08", now == rec, f"K01 {now['k01_policy']} / K21 {now['k21_scorecard']} unchanged since the run ({man['policy_files']['k01_policy']['source']})")

# T09
post_rpr = {}
for pol, lg in L.items():
    s = internal(lg["sends"]); s = s[s.day >= cut]
    post_rpr[pol] = s.replied.sum() / s.actual_cost_inr.sum()
R.check("K23-T09", post_rpr["kairos"] > post_rpr["fixed_cadence"],
        f"after the switch: Kairos {post_rpr['kairos']:.4f} vs fixed cadence {post_rpr['fixed_cadence']:.4f} replies per ₹ "
        f"({(post_rpr['kairos'] / post_rpr['fixed_cadence'] - 1) * 100:+.1f}%)")

R.finish(f"run {man['run_id']}")
