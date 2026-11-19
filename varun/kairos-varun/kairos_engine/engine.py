"""Runs one campaign (one policy, one seed) day by day and writes the logs.

Each contact day, for all 50,000 borrowers:
  1. morning: DPD +1, verified payments clear obligations, broken PTPs tagged
  2. Supervisor gates (K01) per borrower x channel -> reason codes
  3. policy plans touches; budget is RESERVED in priority order (daily pacing)
  4. slot by slot: re-check gates at dispatch (a reply, STOP or answered call
     earlier today cancels later touches and releases their reservation),
     send, get outcomes from the simulator, reconcile the actual charge
  5. evening: reply windows close, the policy learns
"""
import os
import time

import numpy as np
import pandas as pd

from common import CHANNELS, SEGMENTS, SMS, WA, IVR, VOICEBOT, INTENTS, band_index, campaign_dates, is_quiet_day, slot_minutes, uniform
from guardrails import REASONS, R, Caps, check
from world import World, REPLY_TYPES, PTP_KEEP, WILLING_PAYS


def load_portfolio(gen_dir):
    """The ONLY place that reads input files. Point it at official D1/D2/D4/D10/D11/D13 later."""
    b = pd.read_csv(os.path.join(gen_dir, "borrowers.csv"), keep_default_na=False, na_values=[""])
    h = pd.read_csv(os.path.join(gen_dir, "history_summary.csv"))
    t = pd.read_csv(os.path.join(gen_dir, "templates.csv"))
    b = b.merge(h, on="borrower_id", how="left")
    for col in ("nach_bounce_reason", "aa_net_cash_flow"):
        b[col] = b[col].fillna("").astype(str)
    return b, t


class State:
    """Everything that changes during the campaign, one array slot per borrower."""

    def __init__(self, b, templates, k01):
        n = self.n = len(b)
        self.k01 = k01
        self.dpd = b["dpd_at_start"].to_numpy().astype(np.int32) - 1   # +1 on day 0 morning
        self.band = band_index(k01, self.dpd)
        self.lang = b["language"].to_numpy()
        self.consent = np.stack([b[f"consent_{c}"].to_numpy().astype(bool) for c in CHANNELS], 1)
        self.opted_out = b["opted_out_at_start"].to_numpy().astype(bool)
        self.email_blocked = np.zeros(n, bool)
        self.cleared = np.zeros(n, bool)
        self.amount_due = b["amount_due_inr"].to_numpy().astype(float)
        self.hardship_until = np.full(n, -1, np.int64)
        self.hardship_sup = np.zeros(n, bool)
        self.ptp_day = np.full(n, -1, np.int32)
        self.pay_day = np.full(n, -1, np.int32)
        self.pay_src = np.full(n, "", dtype=object)
        self.broken_ptp = np.zeros(n, np.int32)
        self.no_hold_ptp = np.zeros(n, np.int32)
        self.intent = np.full(n, -1, np.int8)
        far = -10**6
        self.last_reply_day = np.full(n, far)
        self.reply_day_ch = np.full((n, 5), far)
        self.reach_evidence_day = np.full((n, 5), far)
        self.last_read_day = np.full(n, far)
        self.last_click_day = np.full(n, far)
        self.last_answer_day = np.full(n, far)
        self.last_sms_deliv_day = np.full(n, far)
        self.last_reach_day = np.zeros(n, np.int64)   # history shows they were reachable
        self.attempts_since_reach = np.zeros(n, np.int32)
        self.last_sent_min = np.full((n, 5), -10**9, np.int64)
        self.sent_today = np.zeros((n, 5), np.int16)
        self.sent_today_all = np.zeros(n, np.int16)
        self.sent_week = np.zeros((n, 5), np.int16)
        self.sent_week_all = np.zeros(n, np.int16)
        self.touch_ring = np.zeros((3, n), np.int16)    # touches on each of the last 3 days
        self.stop_for_day = np.zeros(n, bool)
        self.dpd_stale = np.zeros(n, bool)
        self.ontime = b["ontime_emis_6m"].to_numpy().astype(float)
        self.bounce = b["nach_bounce_reason"].to_numpy().astype(str)
        self.aa_ncf = b["aa_net_cash_flow"].to_numpy().astype(str)
        self.dti = b["aa_dti"].to_numpy().astype(float)
        self.aa_flags = b["aa_risk_flags"].to_numpy().astype(int)
        # template registry lookup (approved only) per channel x purpose x language
        ok = templates[templates["status"] == "APPROVED"]
        self.tmpl = {(r.channel, r.purpose, r.language): r.template_id for r in ok.itertuples()}
        self.tmpl_ids = sorted(set(ok["template_id"]))
        self.template_idx = np.full((n, 5), -1, np.int16)

    def recent_touches(self):
        return self.touch_ring.sum(0) + self.sent_today_all

    def override_mask(self, day):
        """K21 overrides -> WAIT (holds, STOP, cleared)."""
        return (self.opted_out | self.cleared | (self.hardship_until > day * 1440) | (self.ptp_day >= day))

    def refresh_templates(self):
        purpose = np.where(self.band == 0, "reminder", "collection")
        for c, ch in enumerate(CHANNELS):
            for p in ("reminder", "collection"):
                for lang in ("en", "hi"):
                    m = (purpose == p) & (self.lang == lang)
                    tid = self.tmpl.get((ch, p, lang))
                    self.template_idx[m, c] = self.tmpl_ids.index(tid) if tid else -1


def run_campaign(policy, cfg, k01, data, templates, seed, out_dir, log=print):

    S = State(data, templates, k01)
    world = World(data, seed)
    caps = Caps(k01)
    dates = campaign_dates(cfg)
    quiet = [is_quiet_day(k01, d) for d in dates]
    slot_min = slot_minutes(cfg)
    cost = np.array([cfg["channel_cost_inr"][c] for c in CHANNELS])
    seg = data["seg_idx"].to_numpy()
    pool = cfg["budget_per_case_inr"] * S.n
    spent = 0.0
    due_at_start = S.amount_due.sum()
    collected, cured = 0.0, 0
    tgt = cfg["collection_target"]
    target_met = False
    sends_all, payments, decisions, daily, learning, timings = [], [], [], [], [], []
    touch_no = 0
    ever_eligible = np.zeros(S.n, bool)

    for d, day_date in enumerate(dates):
        # ---------------- 1. morning ----------------
        S.dpd[~S.cleared] += 1
        S.band = band_index(k01, S.dpd)
        S.refresh_templates()
        S.touch_ring = np.roll(S.touch_ring, 1, axis=0)
        S.touch_ring[0] = S.sent_today_all
        S.sent_today[:] = 0
        S.sent_today_all[:] = 0
        S.stop_for_day[:] = False
        if day_date.weekday() == 0:
            S.sent_week[:] = 0
            S.sent_week_all[:] = 0
        S.dpd_stale = uniform(seed, np.arange(S.n), d, 0, 0, 11) < 0.002        # E01: stale feed
        active = ~S.cleared
        pay_now = active & (S.pay_day >= 0) & (S.pay_day <= d)
        broken = active & (S.ptp_day >= 0) & (S.ptp_day < d) & ~pay_now
        S.broken_ptp[broken] += 1
        S.ptp_day[broken] = -1
        S.ptp_day[pay_now] = -1
        self_cure = active & ~pay_now & world.self_cure(np.arange(S.n), d, np.maximum(S.band, 0))
        for mask, src in ((pay_now, None), (self_cure, "self_cure")):
            ids = np.flatnonzero(mask)
            if len(ids):
                payments.append(pd.DataFrame({"borrower_idx": ids, "day": d, "date": day_date.isoformat(),
                                              "amount_inr": S.amount_due[ids],
                                              "source": S.pay_src[ids] if src is None else src}))
                S.cleared[ids] = True
                collected += S.amount_due[ids].sum()
                cured += len(ids)
        if collected / due_at_start >= tgt["amount_pct"] or cured / S.n >= tgt["count_pct"]:
            target_met = True

        contact_days_left = sum(1 for q in quiet[d:] if not q)
        day_budget = 0.0 if quiet[d] else (pool - spent) / max(contact_days_left, 1)

        # ---------------- 2-3. gates + plan + reserve ----------------
        t0 = time.perf_counter()
        all_idx = np.arange(S.n)
        reason = np.stack([check(S, all_idx, c, d, slot_min[0], caps, quiet[d], target_met) for c in range(5)], 1)
        ever_eligible |= (reason == 0).any(1)
        idx, ch, sl, prio, day_reason = policy.plan_day(S, d, reason)
        est = cost[ch]
        reserved_before = np.concatenate([[0.0], np.cumsum(est)[:-1]])
        fits = reserved_before + est <= day_budget + 1e-9
        # stop at the first touch that does not fit (strict priority order)
        first_miss = np.argmin(fits) if not fits.all() else len(fits)
        keep = np.arange(len(fits)) < first_miss
        cut = idx[~keep]
        cut_only = np.setdiff1d(cut, idx[keep])
        day_reason[cut_only] = R["BUDGET_EXHAUSTED"]
        idx, ch, sl, est, reserved_before = idx[keep], ch[keep], sl[keep], est[keep], reserved_before[keep]
        plan_secs = time.perf_counter() - t0
        timings.append({"day": d, "date": day_date.isoformat(), "plan_seconds": round(plan_secs, 3),
                        "borrowers": S.n, "quiet_day": quiet[d]})

        # ---------------- 4. dispatch slot by slot ----------------
        sent_any = np.zeros(S.n, bool)
        day_spend = 0.0
        day_sends = {"idx": [], "ch": [], "slot": [], "replied": []}
        cancelled = {}
        for k in range(len(slot_min)):
            now = d * 1440 + slot_min[k]
            for c in range(5):
                m = (sl == k) & (ch == c)
                if not m.any():
                    continue
                ids, rb = idx[m], reserved_before[m]
                r = check(S, ids, c, d, slot_min[k], caps, quiet[d], target_met)
                for code, cnt in zip(*np.unique(r[r > 0], return_counts=True)):
                    cancelled[REASONS[code]] = cancelled.get(REASONS[code], 0) + int(cnt)
                go = r == 0
                ids, rb = ids[go], rb[go]
                if not len(ids):
                    continue
                # snapshot of state at decision time (audited later)
                snap = {"dpd": S.dpd[ids].copy(), "band": S.band[ids].copy(),
                        "hardship_until": S.hardship_until[ids].copy(), "hardship_sup": S.hardship_sup[ids].copy(),
                        "ptp_day": S.ptp_day[ids].copy(), "stop_for_day": S.stop_for_day[ids].copy(),
                        "template": np.array(S.tmpl_ids)[S.template_idx[ids, c]],
                        "recent": S.recent_touches()[ids].copy()}
                o = world.touch_outcomes(ids, c, k, d, S.band[ids], snap["recent"])
                # counters
                S.sent_today[ids, c] += 1
                S.sent_today_all[ids] += 1
                S.sent_week[ids, c] += 1
                S.sent_week_all[ids] += 1
                S.last_sent_min[ids, c] = now
                sent_any[ids] = True
                reach = o["answered"] if c in (IVR, VOICEBOT) else o["delivered"]
                S.last_reach_day[ids[o["delivered"]]] = d
                S.attempts_since_reach[ids] = np.where(o["delivered"], 0, S.attempts_since_reach[ids] + 1)
                if c == SMS:
                    S.last_sms_deliv_day[ids[o["delivered"]]] = d
                S.email_blocked[ids[o["hard_bounce"]]] = True
                S.last_read_day[ids[o["read"]]] = d
                S.last_click_day[ids[o["clicked"]]] = d
                S.last_answer_day[ids[o["answered"]]] = d
                ev = o["read"] | o["clicked"] | o["answered"] | o["replied"]
                S.reach_evidence_day[ids[ev], c] = d
                S.stop_for_day[ids[o["answered"]]] = True                  # call answered > 10 s
                S.opted_out[ids[o["stop"]]] = True                         # all channels, next cycle
                S.stop_for_day[ids[o["stop"]]] = True
                rep = ids[o["replied"]]
                S.stop_for_day[rep] = True
                S.last_reply_day[rep] = d
                S.reply_day_ch[rep, c] = d
                rt = o["reply_type"][o["replied"]]
                uf, uf2 = o["u_follow"][o["replied"]], o["u_follow2"][o["replied"]]
                _apply_replies(S, rep, rt, uf, uf2, d, world)
                actual = np.where((c in (IVR, VOICEBOT)) & ~o["delivered"], cost[c] * cfg["unconnected_call_charge_share"], cost[c])
                day_spend += actual.sum()
                ex = policy.explain((ids, np.full(len(ids), c), np.full(len(ids), k)))
                n_ = len(ids)
                sends_all.append(pd.DataFrame({
                    "touch_id": [f"{policy.name[:2].upper()}-{seed}-{touch_no + j:08d}" for j in range(n_)],
                    "policy": policy.name, "borrower_idx": ids, "segment_idx": seg[ids], "day": d,
                    "date": day_date.isoformat(), "time": cfg["slots"][k], "slot": k, "channel": CHANNELS[c],
                    "template_id": snap["template"], "dpd": snap["dpd"], "band": snap["band"],
                    "intensity": ex["intensity"], "score": ex["score"], "top_reasons": ex["top_reasons"],
                    "expected_reply": np.round(ex["expected_reply"], 4),
                    "est_cost_inr": cost[c], "actual_cost_inr": actual,
                    "budget_remaining_inr": np.round(day_budget - rb, 4),
                    "hardship_until_min": snap["hardship_until"], "hardship_supervisor": snap["hardship_sup"],
                    "ptp_day": snap["ptp_day"], "stop_for_day_at_send": snap["stop_for_day"],
                    "dpd_age_hours": 2, "campaign_target_met": target_met,
                    "delivered": o["delivered"], "read": o["read"], "clicked": o["clicked"],
                    "answered": o["answered"], "replied": o["replied"],
                    "reply_type": np.where(o["replied"], np.array(REPLY_TYPES + [""])[o["reply_type"]], ""),
                    "stop": o["stop"], "hard_bounce": o["hard_bounce"],
                    "policy_version": k01["policy_version"]}))
                touch_no += n_
                day_sends["idx"].append(ids); day_sends["ch"].append(np.full(n_, c))
                day_sends["slot"].append(np.full(n_, k)); day_sends["replied"].append(o["replied"])
        spent += day_spend

        # ---------------- 5. evening: learn + report ----------------
        ds = {k_: (np.concatenate(v) if v else np.array([], int)) for k_, v in day_sends.items()}
        policy.end_of_day(d, ds)
        for row in policy.learning_rows(d):
            row["date"] = day_date.isoformat()
            learning.append(row)
        final = np.where(sent_any, 0, day_reason)
        final = np.where(S.cleared & ~sent_any, R["OBLIGATION_CLEARED"], final)
        codes, counts = np.unique(final, return_counts=True)
        for code, cnt in zip(codes, counts):
            name = REASONS[code] if code else "ELIGIBLE"
            dec = "SEND" if code == 0 else ("CANCEL" if name in ("STOP_OPT_OUT", "OBLIGATION_CLEARED") else "WAIT")
            decisions.append({"policy": policy.name, "day": d, "date": day_date.isoformat(), "decision": dec,
                              "reason": "TOUCHED" if code == 0 else name, "borrowers": int(cnt)})
        n_handoff = int((S.hardship_sup & (S.hardship_until > d * 1440)).sum())
        if n_handoff:
            decisions.append({"policy": policy.name, "day": d, "date": day_date.isoformat(), "decision": "HANDOFF",
                              "reason": "HARDSHIP_BEYOND_4_DAYS (subset of HARDSHIP_HOLD)", "borrowers": n_handoff})
        for name, cnt in cancelled.items():
            decisions.append({"policy": policy.name, "day": d, "date": day_date.isoformat(),
                              "decision": "CANCELLED_AT_DISPATCH", "reason": name, "borrowers": cnt})
        daily.append({"day": d, "date": day_date.isoformat(), "quiet_day": quiet[d], "day_budget_inr": round(day_budget, 2),
                      "spend_inr": round(day_spend, 2), "cum_spend_inr": round(spent, 2), "pool_inr": pool,
                      "touches": int(len(ds["idx"])), "replies": int(ds["replied"].sum()) if len(ds["idx"]) else 0,
                      "cleared_total": int(S.cleared.sum()), "collected_inr": round(collected, 0),
                      "target_met": target_met, "plan_seconds": round(plan_secs, 3)})
        log(f"  [{policy.name:13}] day {d:2d} {day_date} {'QUIET' if quiet[d] else '     '} "
            f"touches {daily[-1]['touches']:6,}  replies {daily[-1]['replies']:5,}  spend ₹{day_spend:9,.0f}  "
            f"plan {plan_secs:5.2f}s")

    sends = pd.concat(sends_all, ignore_index=True) if sends_all else pd.DataFrame()
    pays = pd.concat(payments, ignore_index=True) if payments else pd.DataFrame(columns=["borrower_idx", "day", "date", "amount_inr", "source"])
    out = {"sends": sends, "payments": pays, "decisions": pd.DataFrame(decisions), "daily": pd.DataFrame(daily),
           "learning": pd.DataFrame(learning), "timings": pd.DataFrame(timings), "ever_eligible": ever_eligible,
           "totals": {"pool_inr": pool, "spent_inr": spent, "collected_inr": collected, "cured": cured,
                      "amount_due_at_start_inr": due_at_start, "target_met": target_met,
                      "opted_out_end": int(S.opted_out.sum()), "broken_ptp": int(S.broken_ptp.sum()),
                      "no_hold_ptp": int(S.no_hold_ptp.sum()), "drift_day": world.drift_day}}
    return out


def _apply_replies(S, rep, rt, uf, uf2, d, world):
    """What the borrower said -> holds, intent, payments (K01 holds + K13 exceptions)."""
    name = np.array(REPLY_TYPES)[rt]
    intent_of = {"WILLING": "WILLING", "PTP": "WILLING", "NEEDS_TIME": "NEEDS_TIME",
                 "HARDSHIP": "HARDSHIP", "DISPUTE": "DISPUTE", "REFUSED": "REFUSED"}
    S.intent[rep] = [INTENTS.index(intent_of[x]) for x in name]
    w = rep[(name == "WILLING") & (uf < WILLING_PAYS)]
    S.pay_day[w] = d + 1 + (uf2[(name == "WILLING") & (uf < WILLING_PAYS)] < 0.5)
    S.pay_src[w] = "reply_willing"
    ptp = name == "PTP"
    nohold = ptp & (S.broken_ptp[rep] >= 2)                     # K13: 2 broken PTPs -> record NO_HOLD
    S.no_hold_ptp[rep[nohold]] += 1
    hold = ptp & ~nohold
    p_ids = rep[hold]
    S.ptp_day[p_ids] = d + 1 + (uf[hold] * 7).astype(int)        # date within 7 days -> auto-accepted
    kept = uf2[hold] < PTP_KEEP[world.risk[p_ids]]
    S.pay_day[p_ids[kept]] = S.ptp_day[p_ids[kept]]
    S.pay_src[p_ids[kept]] = "ptp_kept"
    hs = name == "HARDSHIP"
    h_ids, hu = rep[hs], uf[hs]
    until = np.where(hu < 0.5, (d + 3) * 1440,                                  # no date: pause 2 days
                     np.where(hu < 0.8, (d + 1 + (uf2[hs] * 4).astype(int)) * 1440 + 600,   # date within 4 days
                              (d + 8) * 1440))                                  # beyond 4 days
    S.hardship_until[h_ids] = until
    S.hardship_sup[h_ids] = hu >= 0.8                                           # -> supervisor (HANDOFF)
