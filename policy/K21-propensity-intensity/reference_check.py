"""
Kairos K21 - reference propensity scorecard + intensity (stdlib only, portable).

score(profile) -> (score 0-100, intensity, top_reasons, evidence_strength)
Deterministic and fast (pure arithmetic) so it runs on 50,000 accounts well
under the 10-minute budget. Usage:  python3 reference_check.py
"""
import csv, json, math, os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
Y = lambda v: (v or "").strip().upper() == "Y"


def score(sc, p):
    pt, contrib = sc["points"], []
    add = lambda name, v: v and contrib.append((name, v))

    add("dpd_band:" + p["band"], pt["dpd_band"][p["band"]])
    if p["ontime_6m"].strip():
        add("ontime_emis_6m", max(-12, min(12, (int(p["ontime_6m"]) - 3) * 4)))
    add("broken_ptp", max(pt["broken_ptp_in_period"]["max_total"],
                          int(p["broken_ptp"] or 0) * pt["broken_ptp_in_period"]["per_item"]))
    if Y(p["paid_30d"]): add("paid_last_30d", pt["paid_in_last_30_days"])
    if Y(p["partial_cycle"]): add("partial_payment", pt["partial_payment_this_cycle"])
    if Y(p["responded_7d"]): add("responded_7d", pt["borrower_responded_last_7d"])
    if int(p["response_channels"] or 0) >= 2: add("responded_2plus_channels", pt["responded_on_2plus_channels"])
    if Y(p["wa_read_7d"]): add("whatsapp_read", pt["whatsapp_read_last_7d"])
    if Y(p["clicked_7d"]): add("link_clicked", pt["link_clicked_last_7d"])
    if Y(p["call_answered_7d"]): add("call_answered", pt["call_answered_over_10s_last_7d"])
    if Y(p["sms_delivered_7d"]): add("sms_delivered", pt["sms_delivered_last_7d"])  # 0 by design
    if Y(p["unreachable_14d"]): add("unreachable_14d", pt["unreachable_all_channels_14d"])
    if p["intent"].strip(): add("intent:" + p["intent"], pt["intent"][p["intent"]])
    if p["bounce_reason"].strip(): add("bounce:" + p["bounce_reason"], pt["nach_bounce_reason"][p["bounce_reason"]])
    if p["aa_ncf"].strip(): add("aa_cash_flow:" + p["aa_ncf"], pt["aa_net_cash_flow_last_month"][p["aa_ncf"]])
    if p["dti"].strip():
        d = float(p["dti"])
        if d > 0.65: add("aa_dti_high", pt["aa_dti"]["above_0_65"])
        elif d < 0.40: add("aa_dti_low", pt["aa_dti"]["below_0_40"])
    if int(p["aa_flags"] or 0) >= 3: add("aa_risk_flags", pt["aa_risk_flags_3plus"])

    lo, hi = sc["clamp"]
    s = max(lo, min(hi, sc["base_score"] + sum(v for _, v in contrib)))
    intensity = next(b["intensity"] for b in sc["intensity_by_score"] if b["min"] <= s <= b["max"])
    if p["override"].strip():
        intensity = "WAIT"
    groups = [p["ontime_6m"].strip() or p["broken_ptp"].strip(),
              any(Y(p[k]) for k in ("responded_7d", "wa_read_7d", "clicked_7d", "call_answered_7d", "unreachable_14d")),
              p["intent"].strip(), p["bounce_reason"].strip() or p["aa_ncf"].strip() or p["dti"].strip()]
    strength = sum(bool(g) for g in groups) / len(groups)
    if intensity == "HIGH" and strength <= 0.5:   # low-evidence cap (decided 19 Nov)
        intensity = "STANDARD"
    top = sorted(contrib, key=lambda kv: -abs(kv[1]))[:3]
    return s, intensity, top, strength


def allowance(sc, intensity, band_cap):
    """Per-channel daily allowance before K01/lender/official caps are applied."""
    return {"WAIT": 0, "LOW": 1, "STANDARD": math.ceil(band_cap / 2), "HIGH": band_cap}[intensity]


def main():
    sc = json.load(open(os.path.join(HERE, "scorecard.json")))
    rows = list(csv.DictReader(open(os.path.join(HERE, "test_cases.csv"))))
    fails = 0
    for r in rows:
        s, inten, top, ev = score(sc, r)
        ok = (s == int(r["expected_score"]) and inten == r["expected_intensity"])
        fails += not ok
        reasons = ", ".join(f"{k} {v:+d}" for k, v in top)
        print(f'{"PASS" if ok else "FAIL"}  {r["case_id"]:8} score={s:3d} {inten:8} ev={ev:.2f}  {r["description"]}  [{reasons}]'
              + ("" if ok else f'   <-- expected {r["expected_score"]} {r["expected_intensity"]}'))
    # allowance sanity: STANDARD is half the K01 cap (rounded up), HIGH the full cap
    assert allowance(sc, "STANDARD", 3) == 2 and allowance(sc, "HIGH", 6) == 6 and allowance(sc, "LOW", 9) == 1
    # throughput: 50,000 scores must be fast
    t = time.time()
    for _ in range(50000):
        score(sc, rows[2])
    secs = time.time() - t
    print(f"\n{len(rows) - fails}/{len(rows)} passed  (scorecard {sc['policy_version']})  |  50,000 scores in {secs:.2f}s")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
