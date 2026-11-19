"""Zero-breach proof. Re-checks EVERY logged send with Ojas's own K01 reference.

Independence: counters (sends today/this week, minutes since last same-channel send,
stop-for-day, opt-out, cleared obligation, email bounce) are recomputed from the
event logs, not copied from the engine. Consent comes from the borrower file.
Only DPD, holds and budget figures are taken from the decision snapshot.
"""
import importlib.util
import json
from datetime import date, timedelta

import numpy as np
import pandas as pd

from common import ojas_file, path


def _ref():
    spec = importlib.util.spec_from_file_location("k01ref", ojas_file("k01_reference")[0])
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _prior_any(flag, keys):
    """1 if the flag was set on an EARLIER row of the same group (rows sorted in time)."""
    cm = flag.groupby(keys, sort=False).cummax()
    return cm.groupby(keys, sort=False).shift(1, fill_value=0)


def audit(run, data, templates, cfg, k01, policy_name):
    ref = _ref()
    lender = ""
    if cfg.get("lender_profile"):
        ref.LENDER_PROFILES["CFG"] = json.load(open(path(cfg["lender_profile"])))
        lender = "CFG"
    s = run["sends"].sort_values(["borrower_idx", "day", "slot"]).reset_index(drop=True)
    if s.empty:
        return {"policy": policy_name, "sends_checked": 0, "k01_breaches": 0, "other_breaches": 0}
    start = date.fromisoformat(cfg["campaign_start"])
    g = s.groupby("borrower_idx", sort=False)
    gd = s.groupby(["borrower_idx", "day"], sort=False)
    s["n_today_all"] = gd.cumcount()
    s["n_today_ch"] = s.groupby(["borrower_idx", "day", "channel"], sort=False).cumcount()
    week = pd.to_datetime(s["date"]).dt.isocalendar().week.astype(int)
    s["n_week_ch"] = s.groupby([s["borrower_idx"], week, s["channel"]], sort=False).cumcount()
    s["n_week_all"] = s.groupby([s["borrower_idx"], week], sort=False).cumcount()
    minute = s["day"] * 1440 + s["time"].str[:2].astype(int) * 60 + s["time"].str[3:].astype(int)
    prev = minute.groupby([s["borrower_idx"], s["channel"]]).shift(1)
    s["mins_gap"] = (minute - prev)
    trig = (s["replied"] | s["answered"] | s["stop"]).astype(int)
    s["stop_today"] = _prior_any(trig, [s["borrower_idx"], s["day"]])
    stop_before = _prior_any(s["stop"].astype(int), [s["borrower_idx"]])
    start_opt = data["opted_out_at_start"].to_numpy().astype(bool)[s["borrower_idx"]]
    s["opted"] = start_opt | (stop_before > 0)
    is_email = s["channel"] == "email"
    bounce_before = _prior_any((s["hard_bounce"] & is_email).astype(int), [s["borrower_idx"]])
    consent = np.zeros(len(s), bool)
    for ch in s["channel"].unique():
        m = (s["channel"] == ch).to_numpy()
        consent[m] = data[f"consent_{ch}"].to_numpy().astype(bool)[s.loc[m, "borrower_idx"]]
    s["consent_ok"] = consent & ~(is_email & (bounce_before > 0)).to_numpy()
    pays = run["payments"]
    pay_day = np.full(len(data), 10**6)
    if len(pays):
        pay_day[pays["borrower_idx"].to_numpy()] = pays["day"].to_numpy()
    s["cleared"] = pay_day[s["borrower_idx"]] <= s["day"]
    approved = set(templates.loc[templates["status"] == "APPROVED", "template_id"])

    def iso_min(m):
        if m < 0:
            return ""
        dd, mm = divmod(int(m), 1440)
        return f"{(start + timedelta(days=dd)).isoformat()}T{mm // 60:02d}:{mm % 60:02d}"

    yn = lambda b: "Y" if b else "N"
    breaches = []
    cols = ["date", "time", "dpd", "dpd_age_hours", "channel", "consent_ok", "opted", "hardship_until_min",
            "hardship_supervisor", "ptp_day", "cleared", "stop_today", "n_today_ch", "n_today_all", "mins_gap",
            "n_week_ch", "n_week_all", "template_id", "budget_remaining_inr", "est_cost_inr",
            "campaign_target_met", "touch_id"]
    for row in s[cols].itertuples(index=False, name=None):
        (dt, tm, dpd, age, ch, cons, opt, hu, hsup, pday, clr, stop, ntc, nta, gap, nwc, nwa, tid, bud, est, tgt, touch) = row
        c = {"date": dt, "time": tm, "dpd": str(dpd), "dpd_age_hours": str(age), "channel": ch,
             "consent": yn(cons), "opted_out": yn(opt), "hardship_until": iso_min(hu), "hardship_supervisor": yn(hsup),
             "ptp_date": (start + timedelta(days=int(pday))).isoformat() if pday >= 0 else "",
             "obligation_cleared": yn(clr), "stop_for_day": yn(stop), "sends_today_channel": str(ntc),
             "sends_today_all": str(nta), "mins_since_last_same_channel": "" if gap != gap else str(int(gap)),
             "official_cap": "", "lender": lender, "sends_week_channel": str(nwc), "sends_week_all": str(nwa),
             "template_approved": yn(tid in approved), "budget_remaining_inr": str(bud),
             "est_cost_inr": str(est), "campaign_target_met": yn(tgt)}
        got = ref.evaluate(k01, c)
        if got[0] != "SEND":
            breaches.append({"touch_id": touch, "decision": got[0], "reason": got[1]})

    other = []
    d = run["daily"]
    over = d[d["spend_inr"] > d["day_budget_inr"] + 0.01]
    if len(over):
        other.append(f"daily budget exceeded on {len(over)} days")
    if run["totals"]["spent_inr"] > run["totals"]["pool_inr"] + 0.01:
        other.append("campaign pool exceeded")
    if policy_name == "kairos":
        low = s[s["intensity"] == "LOW"]
        if (low.groupby(["borrower_idx", "day"]).size() > 1).any():
            other.append("LOW intensity sent more than 1 touch in a day")
        if low["channel"].isin(["ivr", "voicebot"]).any():
            other.append("LOW intensity used a voice channel")
        if (s["intensity"] == "WAIT").any():
            other.append("touch sent while K21 said WAIT")
        if ((s["intensity"] == "STANDARD") & (s["channel"] == "voicebot")).any():
            other.append("STANDARD intensity used voicebot")
    res = {"policy": policy_name, "sends_checked": int(len(s)), "k01_breaches": len(breaches),
           "breach_examples": breaches[:10], "other_breaches": len(other), "other_breach_detail": other}
    return res
