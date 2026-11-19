"""K01 hard gates (Supervisor), vectorised: "may this touch be sent right now?"

Rules run in Ojas's evaluation_order; the first rule hit is the reason. Every number
comes from k01_policy.json (plus optional lender caps), never from this file.
Independent proof: audit.py re-checks every logged send with Ojas's own
reference_check.evaluate(), so a bug here would show up as a breach there.
"""
import numpy as np

from common import CHANNELS, EMAIL

REASONS = ["ELIGIBLE", "STOP_OPT_OUT", "NO_CONSENT", "DPD_INVALID", "NOT_IN_SCOPE",
           "HARDSHIP_HOLD", "PTP_HOLD", "OBLIGATION_CLEARED", "QUIET_DAY", "CONTACT_WINDOW",
           "STOP_FOR_DAY", "CHANNEL_CAP", "PRE_DUE_TOTAL_CAP", "ALL_CHANNEL_DAY_CAP",
           "WEEKLY_CAP", "SAME_CHANNEL_GAP", "NO_APPROVED_TEMPLATE", "BUDGET_EXHAUSTED",
           "CAMPAIGN_TARGET_MET",
           # not K01 gates - policy choices, logged so every borrower-day has a reason
           "INTENSITY_LIMIT", "BELOW_VALUE_THRESHOLD", "NOT_SCHEDULED"]
R = {name: i for i, name in enumerate(REASONS)}
DECISION_OF = {"STOP_OPT_OUT": "CANCEL", "OBLIGATION_CLEARED": "CANCEL"}


def _tight(*vals):
    v = [x for x in vals if x is not None]
    return min(v) if v else np.inf


class Caps:
    """Effective caps = strictest of policy, lender and official (official not received yet)."""

    def __init__(self, k01, official=None):
        caps = k01["caps"]
        lo = caps.get("lender_overrides") or {}
        official = official or {}
        bands = [b["id"] for b in k01["dpd_bands"]]
        self.per_ch_day = np.full((len(bands), 5), np.inf)
        self.all_day = np.full(len(bands), np.inf)
        self.per_ch_week = np.full((len(bands), 5), np.inf)
        self.all_week = np.full(len(bands), np.inf)
        for bi, b in enumerate(bands):
            for ci, ch in enumerate(CHANNELS):
                ovr = (lo.get("per_channel_overrides") or {}).get(ch, {})
                self.per_ch_day[bi, ci] = _tight(caps["per_channel_per_day_by_band"].get(b),
                                                 (lo.get("per_channel_per_day_by_band") or {}).get(b),
                                                 ovr.get("per_day"), official.get(b))
                self.per_ch_week[bi, ci] = _tight((lo.get("per_channel_per_week_by_band") or {}).get(b),
                                                  ovr.get("per_week"))
            self.all_day[bi] = _tight(caps["all_channels_per_day_by_band"].get(b),
                                      (lo.get("all_channels_per_day_by_band") or {}).get(b))
            self.all_week[bi] = _tight((lo.get("all_channels_per_week_by_band") or {}).get(b))
        self.pre_due_policy = np.array([caps["all_channels_per_day_by_band"].get(b) is not None for b in bands])
        self.gap = max(x for x in (caps["min_gap_minutes_same_channel"], lo.get("min_gap_minutes_same_channel")) if x is not None)
        w = k01["contact_window"]
        self.win = (int(w["start"][:2]) * 60 + int(w["start"][3:]), int(w["end_exclusive"][:2]) * 60 + int(w["end_exclusive"][3:]))


def check(S, idx, ch, day, minute_of_day, caps, quiet, target_met):
    """Reason code per borrower in idx for sending channel ch now (0 = eligible).

    Budget is checked separately at reservation time (it depends on queue order).
    """
    r = np.zeros(len(idx), dtype=np.int8)

    def hit(mask, name):
        r[(r == 0) & mask] = R[name]

    now = day * 1440 + minute_of_day
    band = S.band[idx]
    hit(S.opted_out[idx], "STOP_OPT_OUT")
    no_consent = ~S.consent[idx, ch]
    if ch == EMAIL:
        no_consent |= S.email_blocked[idx]
    hit(no_consent, "NO_CONSENT")
    hit(S.dpd_stale[idx], "DPD_INVALID")
    hit(band < 0, "NOT_IN_SCOPE")
    hit(S.hardship_until[idx] > now, "HARDSHIP_HOLD")
    hit(S.ptp_day[idx] >= day, "PTP_HOLD")
    hit(S.cleared[idx], "OBLIGATION_CLEARED")
    if quiet:
        hit(np.ones(len(idx), bool), "QUIET_DAY")
    if not (caps.win[0] <= minute_of_day < caps.win[1]):
        hit(np.ones(len(idx), bool), "CONTACT_WINDOW")
    hit(S.stop_for_day[idx], "STOP_FOR_DAY")
    b = np.maximum(band, 0)
    hit(S.sent_today[idx, ch] >= caps.per_ch_day[b, ch], "CHANNEL_CAP")
    all_hit = S.sent_today_all[idx] >= caps.all_day[b]
    hit(all_hit & caps.pre_due_policy[b], "PRE_DUE_TOTAL_CAP")
    hit(all_hit, "ALL_CHANNEL_DAY_CAP")
    hit((S.sent_week[idx, ch] >= caps.per_ch_week[b, ch]) | (S.sent_week_all[idx] >= caps.all_week[b]), "WEEKLY_CAP")
    hit(now - S.last_sent_min[idx, ch] < caps.gap, "SAME_CHANNEL_GAP")
    hit(S.template_idx[idx, ch] < 0, "NO_APPROVED_TEMPLATE")
    if target_met:
        hit(np.ones(len(idx), bool), "CAMPAIGN_TARGET_MET")
    return r
