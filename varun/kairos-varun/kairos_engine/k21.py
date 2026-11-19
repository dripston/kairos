"""K21 propensity-to-pay + nudge intensity, vectorised for 50K borrowers.

Same rules as Ojas's policy/k21 reference_check.py, but on numpy arrays so one call
scores every borrower. All points and cut-offs are read from k21_scorecard.json.
tests/test_parity.py proves it returns the same score + intensity on all 20 of
Ojas's test cases.
"""
import numpy as np

from common import BANDS, INTENTS

INTENSITIES = ["WAIT", "LOW", "STANDARD", "HIGH"]
FEATURES = ["dpd_band", "ontime_emis_6m", "broken_ptp", "paid_last_30d", "partial_payment",
            "responded_7d", "responded_2plus_channels", "whatsapp_read", "link_clicked",
            "call_answered", "sms_delivered", "unreachable_14d", "intent", "bounce",
            "aa_cash_flow", "aa_dti", "aa_risk_flags"]


def score(sc, f):
    """f: dict of equal-length arrays. Missing numeric = NaN, missing code = -1.

    keys: band(int 0-4), ontime(float/NaN), broken_ptp, paid_30d, partial, responded_7d,
    response_channels, wa_read_7d, clicked_7d, call_answered_7d, sms_delivered_7d,
    unreachable_14d, intent(code into INTENTS or -1), bounce('TECHNICAL'/'INSUFFICIENT_FUNDS'/''),
    aa_ncf('POSITIVE'/'NEGATIVE'/''), dti(float/NaN), aa_flags, override(bool)
    Returns score, intensity code, evidence_strength, contributions matrix (N x features).
    """
    pt = sc["points"]
    n = len(f["band"])
    C = np.zeros((n, len(FEATURES)))
    C[:, 0] = np.array([pt["dpd_band"][b] for b in BANDS])[f["band"]]
    has_ontime = ~np.isnan(f["ontime"])
    C[:, 1] = np.where(has_ontime, np.clip((np.nan_to_num(f["ontime"]) - 3) * 4, -12, 12), 0)
    bp = pt["broken_ptp_in_period"]
    C[:, 2] = np.maximum(bp["max_total"], f["broken_ptp"] * bp["per_item"])
    C[:, 3] = f["paid_30d"] * pt["paid_in_last_30_days"]
    C[:, 4] = f["partial"] * pt["partial_payment_this_cycle"]
    C[:, 5] = f["responded_7d"] * pt["borrower_responded_last_7d"]
    C[:, 6] = (f["response_channels"] >= 2) * pt["responded_on_2plus_channels"]
    C[:, 7] = f["wa_read_7d"] * pt["whatsapp_read_last_7d"]
    C[:, 8] = f["clicked_7d"] * pt["link_clicked_last_7d"]
    C[:, 9] = f["call_answered_7d"] * pt["call_answered_over_10s_last_7d"]
    C[:, 10] = f["sms_delivered_7d"] * pt["sms_delivered_last_7d"]
    C[:, 11] = f["unreachable_14d"] * pt["unreachable_all_channels_14d"]
    ip = np.array([pt["intent"][k] for k in INTENTS] + [0])
    C[:, 12] = ip[np.where(f["intent"] < 0, len(INTENTS), f["intent"])]
    C[:, 13] = np.select([f["bounce"] == "TECHNICAL", f["bounce"] == "INSUFFICIENT_FUNDS"],
                         [pt["nach_bounce_reason"]["TECHNICAL"], pt["nach_bounce_reason"]["INSUFFICIENT_FUNDS"]], 0)
    C[:, 14] = np.select([f["aa_ncf"] == "POSITIVE", f["aa_ncf"] == "NEGATIVE"],
                         [pt["aa_net_cash_flow_last_month"]["POSITIVE"], pt["aa_net_cash_flow_last_month"]["NEGATIVE"]], 0)
    dti = f["dti"]
    C[:, 15] = np.select([dti > 0.65, dti < 0.40], [pt["aa_dti"]["above_0_65"], pt["aa_dti"]["below_0_40"]], 0)
    C[:, 16] = (f["aa_flags"] >= 3) * pt["aa_risk_flags_3plus"]

    lo, hi = sc["clamp"]
    s = np.clip(sc["base_score"] + C.sum(axis=1), lo, hi).astype(int)
    inten = np.zeros(n, dtype=np.int8)
    for band in sc["intensity_by_score"]:
        inten[(s >= band["min"]) & (s <= band["max"])] = INTENSITIES.index(band["intensity"])
    groups = np.stack([
        has_ontime | (f["broken_ptp_present"]),
        f["responded_7d"] | f["wa_read_7d"] | f["clicked_7d"] | f["call_answered_7d"] | f["unreachable_14d"],
        f["intent"] >= 0,
        (f["bounce"] != "") | (f["aa_ncf"] != "") | ~np.isnan(dti)])
    strength = groups.mean(axis=0)
    inten[(inten == 3) & (strength <= 0.5)] = 2      # low-evidence cap (decided 19 Nov)
    inten[f["override"]] = 0
    return s, inten, strength, C


def top_reasons(C, rows, intent_codes, bands, k=3):
    """Text of the 3 largest signed contributions for the given rows (for the decision log)."""
    out = []
    for r in rows:
        c = C[r]
        order = np.argsort(-np.abs(c), kind="stable")[:k]
        parts = []
        for j in order:
            if c[j] == 0:
                continue
            name = FEATURES[j]
            if name == "dpd_band":
                name += ":" + BANDS[bands[r]]
            elif name == "intent":
                name += ":" + INTENTS[intent_codes[r]]
            parts.append(f"{name} {int(c[j]):+d}")
        out.append("; ".join(parts))
    return out
