"""K23 - cohort close and policy review, computed only from the run's event logs.

1. Finds the mid-campaign switch from Kairos's own behaviour (largest 3-day shift in
   channel mix). The simulator's drift day is used only to validate the detection.
2. Reviews which channels and which send times improved unique reach, meaningful
   replies and verified payments, before vs after the switch, for both policies.
3. Writes proposals with their evidence. Anything touching caps, consent, contact
   window, hardship or PTP holds is marked PENDING Ojas's approval and never applied.
4. Writes rollback snapshots (beliefs before and after the cohort) with file hashes.
"""
import json
import os

import numpy as np
import pandas as pd

from common import CHANNELS, SEGMENTS

# K01 areas that only the policy owner may change (tracker: caps, consent, hardship)
GOVERNED_PREFIXES = ("caps.", "consent", "contact_window.", "holds.", "dpd_rules.", "channels_allowed_by_band",
                     "templates.", "budget.", "collection_target")
POLICY_OWNER = "Ojas (Product)"


def needs_owner_approval(target):
    return target.startswith(GOVERNED_PREFIXES)


def detect_switch(sends, contact_days):
    """Single change-point in Kairos's channel mix: the contact day that best splits the
    campaign into a 'before' and 'after' with different channel shares (weighted so that
    tiny periods at either end cannot win). Needs >= 4 contact days on each side."""
    s = sends.assign(emp=np.where(sends["segment_idx"] % 2 == 0, "Salaried", "Self-employed"))
    share = s.groupby(["emp", "day", "channel"]).size().unstack(fill_value=0)
    share = share.div(share.sum(1), axis=0)
    n = len(contact_days)
    best = (None, -1.0, None, None)
    for emp in ("Salaried", "Self-employed"):
        sh = share.loc[emp].reindex(contact_days).fillna(0)
        for i in range(4, n - 3):
            before, after = sh.iloc[:i].mean(), sh.iloc[i:].mean()
            diff = after - before
            ch = diff.abs().idxmax()
            score = abs(diff[ch]) * np.sqrt(i * (n - i) / n)
            if score > best[1]:
                best = (contact_days[i], score, emp, f"{ch} {before[ch]:.0%} -> {after[ch]:.0%}")
    return {"switch_day": int(best[0]), "score": round(best[1], 4), "group": best[2], "change": best[3]}


def review(runs, data, cfg, k01, pre_snapshot, post_snapshot, run_id, out_dir, drift_day):
    k = runs["kairos"]["sends"].copy()
    dates = runs["kairos"]["daily"]["date"].tolist()
    quiet = runs["kairos"]["daily"]["quiet_day"].tolist()
    contact_days = [d for d, q in enumerate(quiet) if not q]
    sw = detect_switch(k, contact_days)
    cut = sw["switch_day"]
    seg = np.array(SEGMENTS)
    emp_of = lambda df: np.where(df["segment_idx"] % 2 == 0, "Salaried", "Self-employed")

    def period(df):
        return np.where(df["day"] >= cut, "after_switch", "before_switch")

    # verified payments attributed to the channel of the borrower's last reply before paying
    def paid_after_reply_by_channel(sends, pays):
        rep = sends[sends["replied"]][["borrower_idx", "day", "slot", "channel", "segment_idx"]]
        if rep.empty or pays.empty:
            return pd.DataFrame(columns=["borrower_idx", "channel", "segment_idx", "reply_day"])
        last = rep.sort_values(["borrower_idx", "day", "slot"]).merge(pays[["borrower_idx", "day"]].rename(columns={"day": "pay_day"}), on="borrower_idx")
        last = last[last["day"] < last["pay_day"]].groupby("borrower_idx").tail(1)
        return last.rename(columns={"day": "reply_day"})

    channel_rows, time_rows, policy_rows = [], [], []
    for name, r in runs.items():
        s = r["sends"].assign(emp=emp_of(r["sends"]), period=period(r["sends"]))
        paid = paid_after_reply_by_channel(r["sends"], r["payments"])
        paid = paid.assign(emp=emp_of(paid), period=np.where(paid["reply_day"] >= cut, "after_switch", "before_switch")) if len(paid) else paid
        for (emp, per, ch), g in s.groupby(["emp", "period", "channel"]):
            pg = paid[(paid["emp"] == emp) & (paid["period"] == per) & (paid["channel"] == ch)] if len(paid) else []
            channel_rows.append({"policy": name, "employment": emp, "period": per, "channel": ch, "touches": len(g),
                                 "unique_reach": int(g.loc[g["delivered"], "borrower_idx"].nunique()),
                                 "meaningful_replies": int(g["replied"].sum()), "verified_payments_after_reply": len(pg),
                                 "spend_inr": round(float(g["actual_cost_inr"].sum()), 2),
                                 "reply_rate": round(float(g["replied"].mean()), 5),
                                 "replies_per_inr": round(float(g["replied"].sum() / g["actual_cost_inr"].sum()), 5)})
        for (emp, per, tm), g in s.groupby(["emp", "period", "time"]):
            time_rows.append({"policy": name, "employment": emp, "period": per, "time": tm, "touches": len(g),
                              "meaningful_replies": int(g["replied"].sum()), "reply_rate": round(float(g["replied"].mean()), 5),
                              "replies_per_inr": round(float(g["replied"].sum() / g["actual_cost_inr"].sum()), 5)})
        p = r["payments"]
        first_touch = r["sends"].groupby("borrower_idx")["day"].min()
        policy_rows.append({"policy": name,
                            "unique_reach": int(r["sends"].loc[r["sends"]["delivered"], "borrower_idx"].nunique()),
                            "meaningful_replies": int(r["sends"]["replied"].sum()),
                            "verified_payments_after_reply": int(len(paid)),
                            "verified_payments_after_contact": int((p["day"].to_numpy() > first_touch.reindex(p["borrower_idx"]).fillna(1e9).to_numpy()).sum()),
                            "spend_inr": round(float(r["sends"]["actual_cost_inr"].sum()), 2)})
    ch_df, tm_df = pd.DataFrame(channel_rows), pd.DataFrame(time_rows)

    # ---------- proposals, each with evidence ----------
    props = []
    kc = ch_df[ch_df.policy == "kairos"]
    for emp in ("Salaried", "Self-employed"):
        for ch in CHANNELS:
            b = kc[(kc.employment == emp) & (kc.period == "before_switch") & (kc.channel == ch)]
            a = kc[(kc.employment == emp) & (kc.period == "after_switch") & (kc.channel == ch)]
            if b.empty or a.empty or min(b.touches.iloc[0], a.touches.iloc[0]) < 300:
                continue
            rb, ra = b.reply_rate.iloc[0], a.reply_rate.iloc[0]
            if ra < 0.7 * rb or ra > 1.3 * rb:
                props.append({"category": "LEARNING_PRIOR", "target": f"kairos.prior.{emp}.{ch}",
                              "change": f"{'Lower' if ra < rb else 'Raise'} the starting weight of {ch} for {emp} borrowers in the next cohort",
                              "evidence": {"reply_rate_before": rb, "reply_rate_after": ra,
                                           "touches_before": int(b.touches.iloc[0]), "touches_after": int(a.touches.iloc[0]),
                                           "share_before": round(b.touches.iloc[0] / kc[(kc.employment == emp) & (kc.period == 'before_switch')].touches.sum(), 3),
                                           "share_after": round(a.touches.iloc[0] / kc[(kc.employment == emp) & (kc.period == 'after_switch')].touches.sum(), 3)}})
    kt = tm_df[(tm_df.policy == "kairos") & (tm_df.period == "after_switch") & (tm_df.touches >= 300)]
    old_slots = {c["channel"]: c["slot"] for c in cfg["baseline"]["cadence"]}
    for emp in ("Salaried", "Self-employed"):
        g = kt[kt.employment == emp]
        if len(g):
            best = g.loc[g.replies_per_inr.idxmax()]
            props.append({"category": "SEND_TIME_DEFAULT", "target": f"kairos.default_slot.{emp}",
                          "change": f"Start {emp} borrowers at {best.time} in the next cohort (best replies per ₹ after the switch)",
                          "evidence": {"best_time": best.time, "replies_per_inr": best.replies_per_inr, "touches": int(best.touches),
                                       "old_fixed_cadence_slots": old_slots}})
    ivr = runs["kairos"]["sends"][runs["kairos"]["sends"].channel == "ivr"]
    if len(ivr) >= 300:
        rpr = float(ivr.replied.sum() / ivr.actual_cost_inr.sum())
        if rpr < cfg["kairos"]["min_replies_per_rupee"]:
            props.append({"category": "CHANNEL_SCOPE", "target": "channels_allowed_by_band",
                          "change": "Allow IVR only for B2+ bands (today it is allowed in every band)",
                          "evidence": {"ivr_replies_per_inr": round(rpr, 4), "threshold": cfg["kairos"]["min_replies_per_rupee"], "ivr_touches": len(ivr)}})
    # opt-out risk vs contact pressure (fixed cadence has the most data on over-contact)
    f = runs["fixed_cadence"]["sends"].sort_values(["borrower_idx", "day", "slot"])
    per_day = f.groupby(["borrower_idx", "day"]).size().rename("n").reset_index()
    per_day["prev3"] = per_day.groupby("borrower_idx")["n"].transform(lambda x: x.shift(1, fill_value=0).rolling(3, min_periods=1).sum())
    f = f.merge(per_day[["borrower_idx", "day", "prev3"]], on=["borrower_idx", "day"])
    lo, hi = f[f.prev3 <= 3], f[f.prev3 >= 5]
    if len(lo) > 1000 and len(hi) > 1000:
        stop_lo, stop_hi = float(lo.stop.mean()), float(hi.stop.mean())
        if stop_hi > 1.5 * stop_lo:
            props.append({"category": "FREQUENCY_CAPS", "target": "caps.cross_channel_daily_ceiling",
                          "change": "Consider a cross-channel daily ceiling (K01 sets none today)",
                          "evidence": {"stop_rate_after_<=3_touches_in_3_days": round(stop_lo, 5),
                                       "stop_rate_after_>=5_touches_in_3_days": round(stop_hi, 5),
                                       "touches_compared": [len(lo), len(hi)], "source": "fixed-cadence run"}})
    props.append({"category": "TEMPLATE_REGISTRY", "target": "k22.template_catalogue.voicebot.hi",
                  "change": "Register an approved Hindi voicebot template (Hindi borrowers' voicebot touches WAIT today)",
                  "evidence": {"reason_code": "NO_APPROVED_TEMPLATE", "owner_of_catalogue": "K22 (Yuval)"}})
    for i, pr in enumerate(props, 1):
        pr["proposal_id"] = f"PROP-{i:02d}"
        gov = needs_owner_approval(pr["target"])
        pr["requires_policy_owner_approval"] = gov
        pr["approver"] = (POLICY_OWNER if gov else "Yuval (K22 template catalogue)" if pr["target"].startswith("k22.")
                          else "Varun (data owner) - learning/config only")
        pr["status"] = "PENDING_POLICY_OWNER_APPROVAL" if gov else "PROPOSED"
        pr["applied"] = False

    # ---------- rollback snapshots ----------
    os.makedirs(out_dir, exist_ok=True)
    pre_p, post_p = os.path.join(out_dir, "priors_pre_cohort.json"), os.path.join(out_dir, "priors_post_cohort.json")
    json.dump(pre_snapshot, open(pre_p, "w"))
    json.dump(post_snapshot, open(post_p, "w"))
    import hashlib
    h = lambda p: hashlib.sha256(open(p, "rb").read()).hexdigest()[:12]
    rollback = {"run_id": run_id, "pre_cohort_snapshot": "priors_pre_cohort.json", "pre_cohort_sha256_12": h(pre_p),
                "post_cohort_snapshot": "priors_post_cohort.json", "post_cohort_sha256_12": h(post_p),
                "how": "Learned beliefs are applied only as next-cohort starting weights. To roll back, start the next "
                       "cohort from priors_pre_cohort.json (history-only prior). K01/K21 policy files are never edited by this review.",
                "policy_files_unchanged": True}

    rep = {"task": "K23", "run_id": run_id, "run_label": "SIMULATED", "cohort": f"{len(data):,} borrowers, {dates[0]} to {dates[-1]}",
           "switch_detected": {**sw, "date": dates[sw["switch_day"]]},
           "validation_only_simulator_drift_day": drift_day,
           "policy_comparison": policy_rows, "channels": channel_rows, "send_times": time_rows,
           "proposals": props, "governance_rule": f"Changes to caps, consent, contact window, hardship or PTP holds require {POLICY_OWNER} approval.",
           "rollback": rollback}
    json.dump(rep, open(os.path.join(out_dir, "cohort_review.json"), "w"), indent=2, default=lambda o: o.item() if hasattr(o, "item") else str(o))
    _md(rep, out_dir)
    return rep


def _md(rep, out_dir):
    L = [f"# K23 · Cohort close and policy review ({rep['run_id']})", "",
         f"> **SIMULATED** dev run. Cohort: {rep['cohort']}. Every number below is computed from the run's touch and payment logs.", "",
         "## 1. The mid-campaign switch", "",
         f"Kairos's channel mix changed most on **day {rep['switch_detected']['switch_day']} ({rep['switch_detected']['date']})** "
         f"for {rep['switch_detected']['group']} borrowers ({rep['switch_detected']['change']}). "
         f"For validation only: the simulator's hidden drift was on day {rep['validation_only_simulator_drift_day']} "
         f"(gap {rep['switch_detected']['switch_day'] - rep['validation_only_simulator_drift_day']:+d} days). "
         "The review below compares the whole period before the switch with the whole period after it.", "",
         "## 2. Both policies over the cohort", "", "| Policy | Unique reach | Meaningful replies | Verified payments after a reply | Spend (₹) |", "|---|---:|---:|---:|---:|"]
    for p in rep["policy_comparison"]:
        L.append(f"| {p['policy']} | {p['unique_reach']:,} | {p['meaningful_replies']:,} | {p['verified_payments_after_reply']:,} | {p['spend_inr']:,.0f} |")
    L += ["", "## 3. Channels before vs after the switch (Kairos)", "",
          "| Employment | Channel | Reply rate before | after | Replies per ₹ before | after | Payments after reply before | after |", "|---|---|---:|---:|---:|---:|---:|---:|"]
    ch = pd.DataFrame(rep["channels"])
    kc = ch[ch.policy == "kairos"]
    for emp in ("Salaried", "Self-employed"):
        for c in CHANNELS:
            b = kc[(kc.employment == emp) & (kc.period == "before_switch") & (kc.channel == c)]
            a = kc[(kc.employment == emp) & (kc.period == "after_switch") & (kc.channel == c)]
            if b.empty or a.empty:
                continue
            b, a = b.iloc[0], a.iloc[0]
            L.append(f"| {emp} | {c} | {b.reply_rate:.2%} | {a.reply_rate:.2%} | {b.replies_per_inr:.3f} | {a.replies_per_inr:.3f} | {b.verified_payments_after_reply:,} | {a.verified_payments_after_reply:,} |")
    L += ["", "## 4. Send times after the switch (Kairos, replies per ₹)", "", "| Employment | " + " | ".join(sorted(set(t['time'] for t in rep['send_times']))) + " |",
          "|---|" + "---:|" * len(set(t['time'] for t in rep['send_times']))]
    tm = pd.DataFrame(rep["send_times"])
    tk = tm[(tm.policy == "kairos") & (tm.period == "after_switch")]
    for emp in ("Salaried", "Self-employed"):
        row = [f"{tk[(tk.employment == emp) & (tk.time == t)].replies_per_inr.iloc[0]:.3f}" if len(tk[(tk.employment == emp) & (tk.time == t)]) else "—"
               for t in sorted(set(tm.time))]
        L.append(f"| {emp} | " + " | ".join(row) + " |")
    L += ["", "## 5. Proposed changes (none applied)", "", "| ID | Change | Needs Ojas? | Status |", "|---|---|---|---|"]
    for p in rep["proposals"]:
        L.append(f"| {p['proposal_id']} | {p['change']} | {'**Yes**' if p['requires_policy_owner_approval'] else 'No'} | {p['status']} |")
    L += ["", "Evidence for each proposal is in `cohort_review.json` → `proposals[].evidence`.", "",
          f"**Governance:** {rep['governance_rule']}", "", "## 6. Rollback", "", rep["rollback"]["how"],
          f"Snapshots: `{rep['rollback']['pre_cohort_snapshot']}` ({rep['rollback']['pre_cohort_sha256_12']}) and "
          f"`{rep['rollback']['post_cohort_snapshot']}` ({rep['rollback']['post_cohort_sha256_12']}).", ""]
    open(os.path.join(out_dir, "cohort_review.md"), "w").write("\n".join(L))
