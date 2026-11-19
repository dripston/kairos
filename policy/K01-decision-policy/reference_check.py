"""
Kairos K01 - reference eligibility check (stdlib only, portable).

Purpose: a minimal, readable implementation of policy.json's hard rules so the
team can (a) verify test_cases.csv is self-consistent and (b) port the same
rule order into the real Supervisor/guardrail service. This is NOT the ranking
engine - it only answers "may this touch be sent right now, and if not, why".

Usage:  python3 reference_check.py [policy.json] [test_cases.csv]
"""
import csv, json, sys, os
from datetime import datetime, date

HERE = os.path.dirname(os.path.abspath(__file__))


LENDER_PROFILES = {}  # name -> lender cap dict; "EXAMPLE" loaded from lender_caps.example.json


def tightest(*vals):
    """Smallest non-null cap; None if no source sets one."""
    vals = [v for v in vals if v is not None]
    return min(vals) if vals else None


def band_for(policy, dpd):
    for b in policy["dpd_bands"]:
        lo, hi = b["dpd_min"], b["dpd_max"]
        if (lo is None or dpd >= lo) and (hi is None or dpd <= hi):
            return b["id"]
    return None


def evaluate(policy, c):
    """Return (decision, reason) for one candidate touch. Rules run in policy order."""
    yes = lambda k: (c.get(k) or "").strip().upper() == "Y"
    today = date.fromisoformat(c["date"])
    now = datetime.fromisoformat(f'{c["date"]}T{c["time"]}')
    ch = c["channel"]

    # 1 STOP / opt-out: suppress all channels, cancel queued work
    if yes("opted_out"):
        return "CANCEL", "STOP_OPT_OUT"
    # 2 consent per channel
    if policy["consent_required_per_channel"] and not yes("consent"):
        return "WAIT", "NO_CONSENT"
    # 3 DPD must be present and fresh
    if not c["dpd"].strip():
        return "WAIT", "DPD_INVALID"
    if float(c["dpd_age_hours"] or 0) > policy["dpd_rules"]["stale_after_hours"]:
        return "WAIT", "DPD_INVALID"
    dpd = int(c["dpd"])
    band = band_for(policy, dpd)
    if band is None:  # e.g. DPD earlier than the pre-due start (-6)
        return "WAIT", "NOT_IN_SCOPE"
    lender = LENDER_PROFILES.get((c.get("lender") or "").strip(), {})
    if ch not in policy["channels_allowed_by_band"][band]:
        return "WAIT", "CHANNEL_NOT_ALLOWED"
    # 4 hardship hold
    if c["hardship_until"].strip() and datetime.fromisoformat(c["hardship_until"]) > now:
        return ("HANDOFF" if yes("hardship_supervisor") else "WAIT"), "HARDSHIP_HOLD"
    # 5 promise-to-pay hold (through end of PTP date)
    if c["ptp_date"].strip() and date.fromisoformat(c["ptp_date"]) >= today:
        return "WAIT", "PTP_HOLD"
    # 6 obligation already cleared by verified payment
    if yes("obligation_cleared"):
        return "CANCEL", "OBLIGATION_CLEARED"
    # 7 quiet days
    qd = policy["contact_window"]["quiet_days"]
    if today.strftime("%a").upper()[:3] in qd["weekdays"] or c["date"] in qd["national_holidays"]:
        return "WAIT", "QUIET_DAY"
    # 8 contact window (end exclusive)
    w = policy["contact_window"]
    if not (w["start"] <= c["time"] < w["end_exclusive"]):
        return "WAIT", "CONTACT_WINDOW"
    # 9 stop-for-day (pickup >10s or any response captured today)
    if yes("stop_for_day"):
        return "WAIT", "STOP_FOR_DAY"
    # 10 per-channel daily cap = min(policy, lender, official); lender can only tighten
    caps = policy["caps"]
    ch_override = lender.get("per_channel_overrides", {}).get(ch, {})
    day_cap = tightest(caps["per_channel_per_day_by_band"].get(band),
                       lender.get("per_channel_per_day_by_band", {}).get(band),
                       ch_override.get("per_day"),
                       int(c["official_cap"]) if c["official_cap"].strip() else None)
    if day_cap is not None and int(c["sends_today_channel"]) >= day_cap:
        return "WAIT", "CHANNEL_CAP"
    # 11 all-channel daily cap (policy sets it for pre-due; lender may set it for any band)
    pol_all = caps["all_channels_per_day_by_band"].get(band)
    all_cap = tightest(pol_all, lender.get("all_channels_per_day_by_band", {}).get(band))
    if all_cap is not None and int(c["sends_today_all"]) >= all_cap:
        return "WAIT", ("PRE_DUE_TOTAL_CAP" if pol_all is not None and all_cap == pol_all else "ALL_CHANNEL_DAY_CAP")
    # 11b weekly caps (only lender-supplied today)
    wk_ch = tightest(lender.get("per_channel_per_week_by_band", {}).get(band), ch_override.get("per_week"))
    wk_all = lender.get("all_channels_per_week_by_band", {}).get(band)
    if wk_ch is not None and int(c.get("sends_week_channel") or 0) >= wk_ch:
        return "WAIT", "WEEKLY_CAP"
    if wk_all is not None and int(c.get("sends_week_all") or 0) >= wk_all:
        return "WAIT", "WEEKLY_CAP"
    # 12 same-channel minimum gap = max(policy, lender)
    gap = max(x for x in (caps["min_gap_minutes_same_channel"], lender.get("min_gap_minutes_same_channel")) if x is not None)
    if c["mins_since_last_same_channel"].strip() and int(c["mins_since_last_same_channel"]) < gap:
        return "WAIT", "SAME_CHANNEL_GAP"
    # 13 approved template
    if not yes("template_approved"):
        return "WAIT", "NO_APPROVED_TEMPLATE"
    # 14 budget reservation
    if float(c["budget_remaining_inr"]) < float(c["est_cost_inr"]):
        return "WAIT", "BUDGET_EXHAUSTED"
    # 15 campaign stop condition
    if yes("campaign_target_met"):
        return "WAIT", "CAMPAIGN_TARGET_MET"
    return "SEND", "ELIGIBLE"


def main():
    pol_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "policy.json")
    csv_path = sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, "test_cases.csv")
    policy = json.load(open(pol_path))
    ex = os.path.join(HERE, "lender_caps.example.json")
    if os.path.exists(ex):
        LENDER_PROFILES["EXAMPLE"] = json.load(open(ex))
    fails = 0
    rows = list(csv.DictReader(open(csv_path)))
    for r in rows:
        got = evaluate(policy, r)
        exp = (r["expected_decision"], r["expected_reason"])
        ok = got == exp
        fails += not ok
        print(f'{"PASS" if ok else "FAIL"}  {r["case_id"]:9} {got[0]:8} {got[1]:22} {r["description"]}'
              + ("" if ok else f'   <-- expected {exp}'))
    print(f"\n{len(rows) - fails}/{len(rows)} passed  (policy {policy['policy_version']})")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
