"""Turns two campaign runs into the evidence the tracker asks for.

K15  benchmark.md, funnel.csv, segment_daily.csv, timings.csv, audit_*.json
K23  cohort_review.md   (what improved, proposed changes, rollback, approvals)
K26  kpis.csv           (every KPI with numerator, denominator, definition, label)
plus decision logs sends_<policy>.csv.gz for K25/K27 (same touch IDs everywhere).
"""
import json
import os

import numpy as np
import pandas as pd

from common import CHANNELS, SEGMENTS



def _stats(run, data):
    s, p = run["sends"], run["payments"]
    t = run["totals"]
    first_touch = s.groupby("borrower_idx")["day"].min()
    paid_after = p[p["borrower_idx"].isin(first_touch.index)]
    paid_after = paid_after[paid_after["day"] > first_touch.reindex(paid_after["borrower_idx"]).to_numpy()]
    responders = s.loc[s["replied"], ["borrower_idx", "day"]].groupby("borrower_idx")["day"].min()
    paid_after_reply = p[p["borrower_idx"].isin(responders.index)]
    paid_after_reply = paid_after_reply[paid_after_reply["day"] > responders.reindex(paid_after_reply["borrower_idx"]).to_numpy()]
    st = {"touches": len(s), "spend_inr": round(t["spent_inr"], 2), "replies": int(s["replied"].sum()),
          "allocated": len(data), "eligible": int(run["ever_eligible"].sum()),
          "attempted": int(s["borrower_idx"].nunique()),
          "reached": int(s.loc[s["delivered"], "borrower_idx"].nunique()),
          "responders": int(s.loc[s["replied"], "borrower_idx"].nunique()),
          "paid_after_contact": int(paid_after["borrower_idx"].nunique()),
          "paid_after_reply": int(paid_after_reply["borrower_idx"].nunique()),
          "paid_total": int(t["cured"]), "collected_inr": round(t["collected_inr"], 0),
          "opt_outs_during": int(s["stop"].sum()), "max_plan_seconds": float(run["timings"]["plan_seconds"].max())}
    st["replies_per_inr"] = st["replies"] / st["spend_inr"] if st["spend_inr"] else 0.0
    st["inr_per_reply"] = st["spend_inr"] / st["replies"] if st["replies"] else None
    return st


def write_all(out, runs, audits, data, templates, cfg, k01, k21, seed, run_id, elapsed, determinism=None):
    os.makedirs(out, exist_ok=True)
    seg_names = np.array(SEGMENTS)
    emp_of = np.array(["Salaried" if s.endswith("SAL") else "Self-employed" for s in SEGMENTS])
    stats = {name: _stats(r, data) for name, r in runs.items()}
    b, k = stats["fixed_cadence"], stats["kairos"]
    lift = (k["replies_per_inr"] / b["replies_per_inr"] - 1) * 100
    breaches = sum(a["k01_breaches"] + a["other_breaches"] for a in audits.values())
    drift_day = runs["kairos"]["totals"]["drift_day"]
    dates = runs["kairos"]["daily"]["date"].tolist()

    # ---------- decision logs (K25 shape) ----------
    ids = data[["borrower_id", "loan_id", "obligation_id", "segment"]]
    for name, r in runs.items():
        s = r["sends"].join(ids, on="borrower_idx")
        s["band"] = np.array([b_["id"] for b_ in k01["dpd_bands"]])[s["band"]]
        s["run_id"], s["data_mode"] = run_id, "SIMULATED"
        keep = ["run_id", "data_mode", "touch_id", "policy", "borrower_id", "loan_id", "obligation_id", "segment",
                "date", "time", "channel", "template_id", "band", "dpd", "intensity", "score", "top_reasons",
                "expected_reply", "est_cost_inr", "actual_cost_inr", "budget_remaining_inr", "delivered", "read",
                "clicked", "answered", "replied", "reply_type", "stop", "hard_bounce", "policy_version",
                "dpd_age_hours", "hardship_until_min", "hardship_supervisor", "ptp_day", "stop_for_day_at_send", "campaign_target_met"]
        s[keep].to_csv(os.path.join(out, f"sends_{name}.csv.gz"), index=False,
                       compression={"method": "gzip", "compresslevel": 1})
        p = r["payments"].join(ids, on="borrower_idx")
        p["policy"] = name
        p.drop(columns=["borrower_idx"]).to_csv(os.path.join(out, f"payments_{name}.csv"), index=False)

    # ---------- funnel ----------
    rows = []
    stages = [("allocated", "Allocated borrowers"), ("eligible", "Passed K01 gates at least once"),
              ("attempted", "Attempted (>=1 touch sent)"), ("reached", "Delivered or connected"),
              ("responders", "Meaningful reply (K01 definition)"), ("paid_after_reply", "Verified payment after replying")]
    for name, st in stats.items():
        prev = None
        for key, label in stages:
            rows.append({"policy": name, "stage": key, "definition": label, "unique_borrowers": st[key],
                         "pct_of_previous": round(100 * st[key] / prev, 1) if prev else None,
                         "pct_of_eligible": round(100 * st[key] / st["eligible"], 1) if st["eligible"] else None})
            prev = st[key]
    funnel = pd.DataFrame(rows)
    funnel.to_csv(os.path.join(out, "funnel.csv"), index=False)

    # ---------- per segment per day (spend, response, learning) ----------
    seg_rows = []
    for name, r in runs.items():
        s = r["sends"]
        g = s.groupby(["date", "segment_idx"]).agg(touches=("replied", "size"), spend_inr=("actual_cost_inr", "sum"),
                                                    replies=("replied", "sum")).reset_index()
        p = r["payments"].copy()
        p["segment_idx"] = data["seg_idx"].to_numpy()[p["borrower_idx"]]
        pg = p.groupby(["date", "segment_idx"]).agg(payments=("amount_inr", "size"), collected_inr=("amount_inr", "sum")).reset_index()
        g = g.merge(pg, on=["date", "segment_idx"], how="outer").fillna(0)
        g["policy"], g["segment"] = name, seg_names[g["segment_idx"].astype(int)]
        g["replies_per_inr"] = np.where(g["spend_inr"] > 0, g["replies"] / g["spend_inr"].replace(0, np.nan), 0).round(4)
        seg_rows.append(g.drop(columns=["segment_idx"]))
    segd = pd.concat(seg_rows)
    learn = runs["kairos"]["learning"]
    if len(learn):
        segd = segd.merge(learn.drop(columns=["day"]).assign(policy="kairos"), on=["policy", "date", "segment"], how="left")
    segd.sort_values(["policy", "date", "segment"]).to_csv(os.path.join(out, "segment_daily.csv"), index=False)
    learn.to_csv(os.path.join(out, "learning_daily.csv"), index=False)

    # ---------- channel mix (mid-campaign switch evidence) ----------
    mix_rows = []
    for name, r in runs.items():
        s = r["sends"].assign(employment=emp_of[r["sends"]["segment_idx"]],
                              period=np.where(r["sends"]["day"] >= drift_day, "after_switch_point", "before_switch_point"))
        m = s.groupby(["period", "employment", "channel"]).agg(touches=("replied", "size"), replies=("replied", "sum"),
                                                              spend_inr=("actual_cost_inr", "sum")).reset_index()
        m["share"] = (m["touches"] / m.groupby(["period", "employment"])["touches"].transform("sum")).round(3)
        m["reply_rate"] = (m["replies"] / m["touches"]).round(4)
        m["replies_per_inr"] = (m["replies"] / m["spend_inr"]).round(4)
        m["policy"] = name
        mix_rows.append(m)
    mix = pd.concat(mix_rows)
    mix.to_csv(os.path.join(out, "channel_mix_by_period.csv"), index=False)
    daily_mix = []
    for name, r in runs.items():
        s = r["sends"].assign(employment=emp_of[r["sends"]["segment_idx"]])
        dm = s.groupby(["date", "employment", "channel"]).size().rename("touches").reset_index()
        dm["share"] = (dm["touches"] / dm.groupby(["date", "employment"])["touches"].transform("sum")).round(3)
        dm["policy"] = name
        daily_mix.append(dm)
    pd.concat(daily_mix).to_csv(os.path.join(out, "channel_mix_daily.csv"), index=False)

    pd.concat([r["decisions"] for r in runs.values()]).to_csv(os.path.join(out, "decisions_daily.csv"), index=False)
    pd.concat([r["daily"].assign(policy=n) for n, r in runs.items()]).to_csv(os.path.join(out, "daily_totals.csv"), index=False)
    pd.concat([r["timings"].assign(policy=n) for n, r in runs.items()]).to_csv(os.path.join(out, "timings.csv"), index=False)
    for name, a in audits.items():
        with open(os.path.join(out, f"audit_{name}.json"), "w") as f:
            json.dump(a, f, indent=2)

    # ---------- K26 KPI table ----------
    kp = []
    def add(metric, pol, num, den, definition):
        kp.append({"run_id": run_id, "data_mode": "SIMULATED", "policy": pol, "metric": metric, "numerator": num,
                   "denominator": den, "value": round(num / den, 4) if den else None, "definition": definition})
    for name, st in stats.items():
        add("replies_per_inr", name, st["replies"], st["spend_inr"], "meaningful replies / actual ₹ spent (CM-02 score)")
        add("unique_reach_rate", name, st["reached"], st["eligible"], "borrowers delivered/connected / borrowers eligible")
        add("response_rate", name, st["responders"], st["reached"], "unique responders / unique reached")
        add("reply_per_touch", name, st["replies"], st["touches"], "meaningful replies / touches sent")
        add("verified_payment_rate", name, st["paid_after_contact"], st["attempted"], "paid after first touch / attempted")
        add("verified_payment_after_reply", name, st["paid_after_reply"], st["responders"], "paid after replying / unique responders")
        add("budget_utilisation", name, st["spend_inr"], runs[name]["totals"]["pool_inr"], "actual spend / campaign pool")
        add("opt_out_rate", name, st["opt_outs_during"], st["reached"], "STOPs during campaign / reached")
        add("cap_breaches", name, audits[name]["k01_breaches"] + audits[name]["other_breaches"], st["touches"],
            "sends failing Ojas's K01 reference or budget/intensity checks / touches")
    pd.DataFrame(kp).to_csv(os.path.join(out, "kpis.csv"), index=False)

    checks = {
        "lift_at_least_20pct": bool(lift >= 20),
        "zero_cap_breaches": bool(breaches == 0),
        "decisions_under_10_min_per_day": bool(max(k["max_plan_seconds"], b["max_plan_seconds"]) < 600),
        "deterministic_replay": determinism["identical"] if determinism else None,
    }
    summary = {"run_id": run_id, "label": cfg["run_label"], "seed": seed, "borrowers": len(data),
               "campaign": {"start": dates[0], "end": dates[-1], "contact_days": int((~runs["kairos"]["daily"]["quiet_day"]).sum())},
               "budget_pool_inr": runs["kairos"]["totals"]["pool_inr"],
               "policy_versions": {"k01": k01["policy_version"], "k21": k21["policy_version"], "k21_model": k21["model_version"]},
               "simulator_drift_day_hidden_from_policy": drift_day, "lift_replies_per_inr_pct": round(lift, 1),
               "acceptance_checks": checks, "policies": stats, "elapsed_seconds": round(elapsed, 1),
               "determinism": determinism,
               "audit": {"sends_checked": int(sum(a["sends_checked"] for a in audits.values())),
                         "breaches": int(breaches)}}
    with open(os.path.join(out, "summary.json"), "w") as f:
        json.dump(summary, f, indent=2, default=lambda o: o.item() if hasattr(o, "item") else str(o))

    _benchmark_md(out, summary, stats, funnel, audits, mix, cfg)
    _cohort_md(out, runs, data, cfg, k01, mix, drift_day, dates, stats, learn, run_id)
    return summary


def _fmt_check(v):
    return "PASS" if v is True else ("FAIL" if v is False else "not run")


def _benchmark_md(out, sm, stats, funnel, audits, mix, cfg):
    b, k = stats["fixed_cadence"], stats["kairos"]
    L = [f"# K15 · Kairos vs fixed cadence — benchmark ({sm['run_id']})", "",
         f"> **{sm['label']}.** Seed {sm['seed']} · {sm['borrowers']:,} borrowers · {sm['campaign']['start']} → "
         f"{sm['campaign']['end']} ({sm['campaign']['contact_days']} contact days) · same budget pool "
         f"₹{sm['budget_pool_inr']:,.0f}, same K01 caps, same simulator draws for both policies.", "",
         "## Acceptance checks", "",
         "| Check | Result |", "|---|---|",
         f"| Replies per ₹ at least 20% above fixed cadence | **{_fmt_check(sm['acceptance_checks']['lift_at_least_20pct'])}** ({sm['lift_replies_per_inr_pct']:+.1f}%) |",
         f"| Zero cap breaches (every send re-checked with Ojas's K01 reference) | **{_fmt_check(sm['acceptance_checks']['zero_cap_breaches'])}** ({audits['kairos']['sends_checked'] + audits['fixed_cadence']['sends_checked']:,} sends checked) |",
         f"| Daily decisions for all borrowers under 10 minutes | **{_fmt_check(sm['acceptance_checks']['decisions_under_10_min_per_day'])}** (slowest day {max(b['max_plan_seconds'], k['max_plan_seconds']):.2f} s) |",
         f"| Same seed gives identical replay | **{_fmt_check(sm['acceptance_checks']['deterministic_replay'])}** |", "",
         "## Headline", "",
         "| Metric | Fixed cadence | Kairos |", "|---|---:|---:|"]
    for key, label, f in [("touches", "Touches sent", "{:,}"), ("spend_inr", "Spend (₹)", "{:,.0f}"),
                          ("replies", "Meaningful replies", "{:,}"), ("replies_per_inr", "Replies per ₹", "{:.4f}"),
                          ("inr_per_reply", "₹ per reply", "{:.2f}"), ("reached", "Unique borrowers reached", "{:,}"),
                          ("responders", "Unique responders", "{:,}"), ("paid_after_contact", "Verified payments after contact (incl. self-cure)", "{:,}"),
                          ("paid_after_reply", "Verified payments after a reply", "{:,}"),
                          ("collected_inr", "₹ collected (all verified payments)", "{:,.0f}"), ("opt_outs_during", "Opt-outs during campaign", "{:,}")]:
        L.append(f"| {label} | {f.format(b[key])} | {f.format(k[key])} |")
    L += ["", "## Funnel (unique borrowers, each counted once per stage)", "",
          "| Stage | Fixed cadence | Kairos |", "|---|---:|---:|"]
    for stage in funnel["stage"].unique():
        fb = funnel[(funnel.policy == "fixed_cadence") & (funnel.stage == stage)].iloc[0]
        fk = funnel[(funnel.policy == "kairos") & (funnel.stage == stage)].iloc[0]
        L.append(f"| {fb['definition']} | {fb['unique_borrowers']:,} | {fk['unique_borrowers']:,} |")
    L += ["", "## Where the lift comes from", ""]
    km = mix[mix.policy == "kairos"]
    for emp in ("Salaried", "Self-employed"):
        bef = km[(km.employment == emp) & (km.period == "before_switch_point")].set_index("channel")
        aft = km[(km.employment == emp) & (km.period == "after_switch_point")].set_index("channel")
        parts = [f"{c} {bef['share'].get(c, 0):.0%}→{aft['share'].get(c, 0):.0%}" for c in CHANNELS if c in bef.index or c in aft.index]
        L.append(f"- **{emp}** channel mix before → after the mid-campaign drift: " + ", ".join(parts) + ".")
    L += ["- Kairos waits when no touch clears the value threshold "
          f"({cfg['kairos']['min_replies_per_rupee']} replies per ₹), so it spends less on borrowers who are unlikely to respond today.",
          "- K21 intensity: LOW borrowers get one cheap touch, voice only for STANDARD/HIGH; holds (PTP, hardship, STOP) always win.",
          "", "## Caveat", "",
          "This is the **dev simulator** with synthetic data. The judged number comes from the organisers' simulator on the hidden "
          "seed. The harness is built so that simulator replaces `src/world.py` and the official fixed-cadence policy file replaces "
          "`baseline.cadence` in `config.json` (blocked on K02).", ""]
    open(os.path.join(out, "benchmark.md"), "w").write("\n".join(L))


def _cohort_md(out, runs, data, cfg, k01, mix, drift_day, dates, stats, learn, run_id):
    k = runs["kairos"]["sends"]
    km = mix[mix.policy == "kairos"]
    L = [f"# K23 · Cohort close and policy review ({run_id})", "",
         f"> SIMULATED dev run. Cohort = {len(data):,} allocated borrowers, {dates[0]} → {dates[-1]}. "
         f"Review point = day {drift_day} ({dates[drift_day]}), where reply behaviour shifted in the simulator.", "",
         "## 1. What improved", "", "| Employment | Channel | Reply rate before | Reply rate after | Replies per ₹ before | after | Share before | after |",
         "|---|---|---:|---:|---:|---:|---:|---:|"]
    changes = []
    for emp in ("Salaried", "Self-employed"):
        bef = km[(km.employment == emp) & (km.period == "before_switch_point")].set_index("channel")
        aft = km[(km.employment == emp) & (km.period == "after_switch_point")].set_index("channel")
        for c in CHANNELS:
            if c not in bef.index or c not in aft.index:
                continue
            L.append(f"| {emp} | {c} | {bef.loc[c, 'reply_rate']:.2%} | {aft.loc[c, 'reply_rate']:.2%} | "
                     f"{bef.loc[c, 'replies_per_inr']:.3f} | {aft.loc[c, 'replies_per_inr']:.3f} | "
                     f"{bef.loc[c, 'share']:.0%} | {aft.loc[c, 'share']:.0%} |")
            rb, ra = bef.loc[c, "reply_rate"], aft.loc[c, "reply_rate"]
            if min(bef.loc[c, "touches"], aft.loc[c, "touches"]) < 300:      # too little evidence to propose
                continue
            sb, sa = bef.loc[c, "share"], aft.loc[c, "share"]
            moved = f"Kairos already moved its share {sb:.0%} → {sa:.0%}. " if abs(sa - sb) >= 0.03 else ""
            if ra < 0.7 * rb:
                changes.append(f"**{emp}: lower the starting weight of {c}.** Reply rate fell {rb:.2%} → {ra:.2%}. "
                               f"{moved}Carry the updated prior into the next cohort.")
            elif ra > 1.3 * rb:
                changes.append(f"**{emp}: raise the starting weight of {c}.** Reply rate rose {rb:.2%} → {ra:.2%}. {moved}")
    st_b, st_k = stats["fixed_cadence"], stats["kairos"]
    L += ["", f"Unique reach: fixed cadence {st_b['reached']:,} vs Kairos {st_k['reached']:,}. "
          f"Meaningful replies: {st_b['replies']:,} vs {st_k['replies']:,}. "
          f"Verified payments after a reply: {st_b['paid_after_reply']:,} vs {st_k['paid_after_reply']:,}. "
          f"Opt-outs: {st_b['opt_outs_during']:,} vs {st_k['opt_outs_during']:,}.", ""]

    # best time slot per segment at cohort close vs the old cadence slot
    L += ["## 2. Best channel and time per segment at cohort close", "",
          "| Segment | Best arm (channel@time) | Reply rate | Replies per ₹ |", "|---|---|---:|---:|"]
    if len(learn):
        last = learn[learn["day"] == learn["day"].max()]
        for r in last.itertuples():
            L.append(f"| {r.segment} | {r.best_arm} | {r.best_arm_reply_rate:.2%} | {r.best_arm_replies_per_inr:.3f} |")
        old = {c["channel"]: c["slot"] for c in cfg["baseline"]["cadence"]}
        moved = [r for r in last.itertuples() if old.get(r.best_arm.split("@")[0]) not in (None, r.best_arm.split("@")[1])]
        if moved:
            changes.append(f"**Send times:** in {len(moved)} of {len(last)} segments the best slot differs from the old fixed "
                           "cadence slot for that channel. Start the next cohort from the learned slots.")
    ivr = k[k.channel == "ivr"]
    if len(ivr) and ivr["replied"].sum() / ivr["actual_cost_inr"].sum() < cfg["kairos"]["min_replies_per_rupee"]:
        changes.append(f"**IVR:** {ivr['replied'].sum() / ivr['actual_cost_inr'].sum():.3f} replies per ₹ overall, below the "
                       f"{cfg['kairos']['min_replies_per_rupee']} threshold. Keep it for HIGH-intensity borrowers with answered-call evidence.")
    changes.append("**Templates (K22, Yuval):** Hindi voicebot has no approved template, so those touches WAIT. Register one if voicebot stays in scope.")
    L += ["", "## 3. Proposed changes for the next cohort", ""]
    L += [f"{i}. {c}" for i, c in enumerate(changes, 1)]
    L += ["", "## 4. Needs policy-owner approval (Ojas) — not applied automatically", "",
          "Any change to caps, consent rules, contact window, hardship or PTP holds. This review proposes none of those. "
          f"Observed for context: {st_k['opt_outs_during']} opt-outs under Kairos vs {st_b['opt_outs_during']} under fixed cadence; "
          f"{runs['kairos']['totals']['broken_ptp']} broken PTPs; {runs['kairos']['totals']['no_hold_ptp']} PTPs recorded NO_HOLD after 2 broken promises (K13).",
          "", "## 5. Rollback", "",
          f"Learned priors are only carried forward as starting weights. To roll back, start the next cohort from the history-only prior "
          f"(re-run with the same config, run_id `{run_id}`) and compare on the same seed before switching. Policy files stay at K01 "
          f"{k01['policy_version']}.", ""]
    open(os.path.join(out, "cohort_review.md"), "w").write("\n".join(L))
