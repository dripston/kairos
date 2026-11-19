"""The two policies being compared. Both see the same data, budget and K01 gates.

FixedCadence: each channel on its own timetable (the "old way" the brief wants beaten).
Kairos: Thompson-sampling bandit that learns reply chances per segment x channel x
slot, personalises them per borrower, applies K21 intensity, ranks every candidate
touch by expected replies per rupee and WAITs below a threshold (K09 + K21 + E17).

Interface used by engine.py (Rehan's K09 can replace Kairos by implementing it):
  plan_day(S, day, eligible_reason[N,5]) -> (idx, ch, slot, priority, day_reason)
  end_of_day(day, sends)                 -> learn from today's finalised touches
  learning_rows(day)                     -> per-segment learning snapshot for reports
"""
import numpy as np

from common import CHANNELS, SEGMENTS, IVR, VOICEBOT, VOICE
from guardrails import R
import k21


class FixedCadence:
    name = "fixed_cadence"

    def __init__(self, cfg, data):
        self.cad = [(CHANNELS.index(c["channel"]), cfg["slots"].index(c["slot"]), c["every_n_contact_days"])
                    for c in cfg["baseline"]["cadence"]]
        self.cad.sort(key=lambda t: t[1])
        self.dpd_order = None
        self.contact_day = 0
        self.last_plan = {}

    def plan_day(self, S, day, reason):
        order = np.lexsort((np.arange(S.n), -S.dpd))          # highest DPD first, then id
        idx_l, ch_l, sl_l = [], [], []
        scheduled = np.zeros(S.n, bool)
        for ch, slot, every in self.cad:
            if self.contact_day % every:
                continue
            ok = order[reason[order, ch] == 0]
            idx_l.append(ok); ch_l.append(np.full(len(ok), ch)); sl_l.append(np.full(len(ok), slot))
            scheduled[ok] = True
        self.contact_day += 1
        day_reason = _borrower_reason(reason)
        day_reason[(day_reason == 0) & ~scheduled] = R["NOT_SCHEDULED"]
        idx = np.concatenate(idx_l) if idx_l else np.array([], int)
        self.last_plan = {}
        return (idx, np.concatenate(ch_l) if ch_l else idx, np.concatenate(sl_l) if sl_l else idx,
                np.arange(len(idx))[::-1].astype(float), day_reason)

    def end_of_day(self, day, sends):
        pass

    def learning_rows(self, day):
        return []

    def explain(self, rows):
        n = len(rows[0])
        return {"score": np.full(n, -1), "intensity": np.full(n, ""), "top_reasons": np.full(n, ""),
                "expected_reply": np.full(n, np.nan)}


class Kairos:
    name = "kairos"

    def __init__(self, cfg, k21_sc, data, seed):
        kc = cfg["kairos"]
        self.cfg, self.sc, self.seed = cfg, k21_sc, seed
        self.cost = np.array([cfg["channel_cost_inr"][c] for c in CHANNELS])
        self.gamma, self.lam = kc["forgetting_per_day"], kc["min_replies_per_rupee"]
        self.pen, self.k_pers = kc["fatigue_penalty_assumed"], kc["personal_shrinkage"]
        self.max_touches = kc["max_touches_per_day"]
        nseg, nslot = len(SEGMENTS), len(cfg["slots"])
        self.seg = data["seg_idx"].to_numpy()
        ht = data[[f"hist_touches_{c}" for c in CHANNELS]].to_numpy().astype(float)
        hr = data[[f"hist_replies_{c}" for c in CHANNELS]].to_numpy().astype(float)
        # E17 pooled prior: channel-level reply rate from 12-month history, same for every
        # segment and slot, so sparse segments and never-tried slots start sensibly
        r_ch = (hr.sum(0) + 1) / (ht.sum(0) + 50)
        m = kc["pooled_prior_strength"]
        self.a0 = np.broadcast_to((m * r_ch)[None, :, None], (nseg, 5, nslot)).copy()
        self.b0 = np.broadcast_to((m * (1 - r_ch))[None, :, None], (nseg, 5, nslot)).copy()
        # segment history at the old cadence slot (down-weighted; forgotten over time)
        self.S = np.zeros((nseg, 5, nslot))
        self.F = np.zeros((nseg, 5, nslot))
        for c, ch in enumerate(CHANNELS):
            hs = cfg["slots"].index(data[f"hist_slot_{ch}"].iloc[0])
            tot_t = np.bincount(self.seg, ht[:, c], nseg)
            tot_r = np.bincount(self.seg, hr[:, c], nseg)
            w = np.minimum(kc["history_weight"], 200 / np.maximum(tot_t, 1))
            self.S[:, c, hs] += w * tot_r
            self.F[:, c, hs] += w * (tot_t - tot_r)
        # personal evidence per borrower x channel; it fades like segment evidence so old
        # history cannot mask a mid-campaign drift
        self.pn, self.ps = kc["history_weight"] * ht, kc["history_weight"] * hr
        self.today = None

    def snapshot(self):
        """Segment-level beliefs (what carries to the next cohort) - used for K23 rollback."""
        return {"segments": SEGMENTS, "channels": CHANNELS, "slots": self.cfg["slots"],
                "prior_alpha": np.round(self.a0, 6).tolist(), "prior_beta": np.round(self.b0, 6).tolist(),
                "evidence_success": np.round(self.S, 6).tolist(), "evidence_failure": np.round(self.F, 6).tolist()}

    def _posterior_mean(self):
        a, b = self.a0 + self.S, self.b0 + self.F
        return a / (a + b)

    def features(self, S, day):
        d7 = day - 7
        return {"band": np.maximum(S.band, 0), "ontime": S.ontime, "broken_ptp": S.broken_ptp,
                "broken_ptp_present": np.ones(S.n, bool), "paid_30d": np.zeros(S.n, bool),
                "partial": np.zeros(S.n, bool), "responded_7d": S.last_reply_day >= d7,
                "response_channels": (S.reply_day_ch >= d7).sum(1), "wa_read_7d": S.last_read_day >= d7,
                "clicked_7d": S.last_click_day >= d7, "call_answered_7d": S.last_answer_day >= d7,
                "sms_delivered_7d": S.last_sms_deliv_day >= d7,
                "unreachable_14d": (S.last_reach_day < day - 14) & (S.attempts_since_reach >= 3),
                "intent": S.intent, "bounce": S.bounce, "aa_ncf": S.aa_ncf, "dti": S.dti,
                "aa_flags": S.aa_flags, "override": S.override_mask(day)}

    def plan_day(self, S, day, reason):
        n = S.n
        score, inten, strength, C = k21.score(self.sc, self.features(S, day))
        allowed = reason == 0
        # K21 intensity -> which channels and how many touches today
        allowed[inten == 0] = False
        allowed[inten == 2, VOICEBOT] = False                         # STANDARD: no voicebot
        low = inten == 1
        evidence = np.stack([S.reach_evidence_day[:, c] >= day - 7 for c in range(5)], 1)
        nonvoice_cost = np.where(np.isin(np.arange(5), list(VOICE)), np.inf, self.cost)
        cand = allowed & np.isfinite(nonvoice_cost)[None, :]
        pref = np.where(cand & evidence, nonvoice_cost, np.inf)
        pref_any = np.where(cand, nonvoice_cost, np.inf)
        low_ch = np.where(np.isfinite(pref.min(1)), pref.argmin(1), pref_any.argmin(1))
        low_has = np.isfinite(pref_any.min(1))
        low_mask = np.zeros_like(allowed)
        low_mask[np.arange(n), low_ch] = low_has
        allowed[low] = low_mask[low]
        touches = np.where(inten == 0, 0, np.where(low, 1, self.max_touches))

        # Thompson sampling: one draw per borrower per arm from the segment posterior
        rng = np.random.default_rng([self.seed, day, 4242])
        a, b = (self.a0 + self.S)[self.seg], (self.b0 + self.F)[self.seg]
        theta = rng.beta(a, b)                                         # (N, 5, slots)
        seg_rate = self._posterior_mean().mean(2)[self.seg]            # (N, 5)
        personal = np.clip(((self.ps + self.k_pers * seg_rate) / (self.pn + self.k_pers)) / seg_rate, 0.25, 4)
        fatigue = self.pen ** S.recent_touches()
        exp_reply = np.minimum(0.5, theta * personal[:, :, None] * fatigue[:, None, None])  # a probability
        value = exp_reply / self.cost[None, :, None]
        value[~allowed] = -1
        nslot = value.shape[2]
        flat = value.reshape(n, -1)
        best = flat.argmax(1)
        v1 = flat[np.arange(n), best]
        ch1, sl1 = best // nslot, best % nslot
        flat2 = flat.copy()
        flat2[np.arange(n)[:, None], (np.arange(5) * nslot)[None, :] + sl1[:, None]] = -1   # other slot
        flat2[np.arange(n)[:, None], ch1[:, None] * nslot + np.arange(nslot)[None, :]] = -1  # other channel
        second = flat2.argmax(1)
        v2 = flat2[np.arange(n), second] * self.pen
        t1 = (touches >= 1) & (v1 >= self.lam)
        t2 = (touches >= 2) & t1 & (v2 >= self.lam)
        idx = np.concatenate([np.flatnonzero(t1), np.flatnonzero(t2)])
        ch = np.concatenate([ch1[t1], (second // nslot)[t2]])
        sl = np.concatenate([sl1[t1], (second % nslot)[t2]])
        prio = np.concatenate([v1[t1], v2[t2]])
        er = np.concatenate([exp_reply.reshape(n, -1)[t1, best[t1]], exp_reply.reshape(n, -1)[t2, second[t2]] * self.pen])
        order = np.lexsort((idx, -prio))                               # best value per rupee first
        day_reason = _borrower_reason(reason)
        day_reason[(day_reason == 0) & (inten == 0)] = R["INTENSITY_LIMIT"]
        day_reason[(day_reason == 0) & ~t1] = R["BELOW_VALUE_THRESHOLD"]
        self.today = {"score": score, "inten": inten, "C": C, "intent": S.intent.copy(),
                      "band": np.maximum(S.band, 0), "er": dict(zip(zip(idx.tolist(), ch.tolist(), sl.tolist()), er.tolist()))}
        return idx[order], ch[order], sl[order], prio[order], day_reason

    def explain(self, rows):
        """Score, intensity, top reasons and expected reply for logged sends."""
        t = self.today
        idx, ch, sl = rows
        return {"score": t["score"][idx], "intensity": np.array(k21.INTENSITIES)[t["inten"][idx]],
                "top_reasons": np.array(k21.top_reasons(t["C"], idx, t["intent"], t["band"]), dtype=object),
                "expected_reply": np.array([t["er"].get((i, c, s), np.nan) for i, c, s in zip(idx.tolist(), ch.tolist(), sl.tolist())])}

    def end_of_day(self, day, sends):
        """Reply windows for today's touches are closed -> update beliefs (one reward per touch)."""
        self.S *= self.gamma
        self.F *= self.gamma
        self.pn *= self.gamma
        self.ps *= self.gamma
        if len(sends["idx"]):
            seg = self.seg[sends["idx"]]
            rew = sends["replied"].astype(float)
            np.add.at(self.S, (seg, sends["ch"], sends["slot"]), rew)
            np.add.at(self.F, (seg, sends["ch"], sends["slot"]), 1 - rew)
            np.add.at(self.pn, (sends["idx"], sends["ch"]), 1)
            np.add.at(self.ps, (sends["idx"], sends["ch"]), rew)

    def learning_rows(self, day):
        pm = self._posterior_mean()
        a = self.a0 + self.S
        n_eff = (self.S + self.F)
        vpr = pm / self.cost[None, :, None]
        rows = []
        for g, seg in enumerate(SEGMENTS):
            k = vpr[g].argmax()
            c, s = divmod(k, vpr.shape[2])
            rows.append({"day": day, "segment": seg, "best_arm": f"{CHANNELS[c]}@{self.cfg['slots'][s]}",
                         "best_arm_reply_rate": round(float(pm[g, c, s]), 4),
                         "best_arm_replies_per_inr": round(float(vpr[g, c, s]), 4),
                         "evidence_touches": round(float(n_eff[g].sum()), 1),
                         **{f"rate_{CHANNELS[c]}": round(float(pm[g, c].mean()), 4) for c in range(5)}})
        return rows


def _borrower_reason(reason):
    """One reason per borrower: eligible if any channel is, else the furthest-reaching block."""
    no_consent = reason == R["NO_CONSENT"]
    r = np.where(no_consent, -1, reason.astype(int))
    out = r.max(1)
    out[out < 0] = R["NO_CONSENT"]
    out[(reason == 0).any(1)] = 0
    return out.astype(np.int8)
