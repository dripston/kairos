"""K09 RL v2: cost-optimal collections policy (hierarchical value + dual budget pacing + lookahead + stopping).

Plugs into the K09 replay as `policy="rl2"` and into the Supervisor as a contracts.Learner (rank /
apply_outcome / estimates / model_version). Per contact day:

    1. refit the hierarchy (rl.value) on every finalized outcome so far (it is also updated the moment an
       outcome is finalized: apply_outcome);
    2. for every contactable borrower, score each consented channel x slot:
           p = P(reply), q = P(pay | reply), s = P(STOP), V = p (1 + alpha q EMI/mean EMI) - mu s
       and pick the borrower's arm (best V / cost, or best V - lambda cost with arm_rule="lagrange");
    3. price money (rl.pacing): lambda = max(dual price, phi x realized replies per paisa);
    4. keep borrowers with V > lambda x cost, unless waiting a day is worth more (rl.lookahead) or the
       borrower is paused for good (optimal stopping, rl.lookahead);
    5. hand them to the Supervisor in order of V / cost with their top arms as candidates, so K01's daily
       allowance funds the best value per rupee first; the K01 gate re-checks every one.

Only ledger / spine information is used: finalized touch outcomes, the payment noted on a touch, STOP
events, the borrower's state (spine borrowers.state), cohort (dataset Risk Tagging / bounce string), EMI
amount (spine row), costs. Deterministic: no rng, every loop in a fixed order.
"""
from __future__ import annotations

import hashlib
import json
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field, fields
from datetime import date, datetime, timedelta
from typing import Any, Callable, Optional, Sequence

from ..contracts import CHANNELS, DEFAULT_MIN_REPLY_PROB, SLOTS, ArmScore, Candidate, Mode, Ranking
from ..learner import SCHEMA as LEARNER_SCHEMA
from spine.policy import dpd_band  # K01 bands, the same ones the spine uses

from .lookahead import contacts_left, continuation_value, wait_is_better
from .pacing import DualPacer
from .value import CH_LAYER, Rec, ValueModel, gap_bucket, rec_bucket

RL2_VERSION = "k09-rl2-v1"
BANDS = ("B1", "B2", "B3", "B4")


@dataclass(frozen=True)
class RL2Options:
    alpha: float = 0.0              # weight of P(pay | reply) x EMI / mean EMI in the value
    mu: float = 0.0                 # STOP penalty in reply units
    pacing: str = "floor"           # "floor" (previous relative floor), "dual" (BwK price), "none"
    phi: float = 0.5                # ratio guard: lambda >= phi x realized replies per paisa
    eta: float = 0.5                # dual step size
    rho: float = 1.0                # pacing target as a share of K01's daily allowance
    spend_down_days: int = 1        # last contact days without the dual price
    arm_rule: str = "ratio"         # borrower's arm: "ratio" (V / cost) or "lagrange" (V - lambda cost)
    lookahead: bool = False         # contact-vs-wait one-step lookahead on learned fatigue
    stopping: bool = False          # optimal stopping (pause borrower for the campaign)
    segments: bool = True           # campaign -> cohort -> region levels
    individual: bool = True         # borrower, borrower x channel, borrower x slot levels
    sequencing: bool = True         # channel-transition levels
    arm_decay: float = 0.7
    pop_decay: float = 0.85
    ind_decay: Optional[float] = None
    min_reply_prob: float = DEFAULT_MIN_REPLY_PROB
    top_k: int = 3                  # candidates handed to the Supervisor per borrower (fallbacks for the gate)
    trace_every: int = 50           # keep a decision trace for every n-th borrower (explanations)

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "RL2Options":
        d = dict(d or {})
        unknown = set(d) - {f.name for f in fields(cls)}
        if unknown:
            raise ValueError(f"unknown rl2 options {sorted(unknown)}")
        return cls(**d)


@dataclass
class BorrowerMeta:
    bid: str
    index: int
    cohort: str
    region: str
    emi_paise: int


class RL2Policy:
    """contracts.Learner + daily planner. `run` is the replay StackRun (portfolio, loans, candidates(),
    _dpd(), start, days); production would pass the same facts from the spine."""

    def __init__(self, ledger, run, opts: Optional[dict] = None, campaign_id: str = "CMP",
                 warm_start: Optional[dict] = None, journey: Optional[Callable[[str, int], Optional[set]]] = None,
                 costs: Optional[dict] = None, has_template: Optional[Callable[[str, str], bool]] = None):
        self.ledger, self.run = ledger, run
        self.opts = RL2Options.from_dict(opts)
        o = self.opts
        self.campaign_id = campaign_id
        self.journey = journey
        self.has_template = has_template or (lambda ch, lang: True)
        self.costs = dict(costs or {})
        self.min_reply_prob = o.min_reply_prob
        self.model = ValueModel(arm_decay=o.arm_decay, pop_decay=o.pop_decay, ind_decay=o.ind_decay,
                                warm=warm_start, segments=o.segments, individual=o.individual,
                                sequencing=o.sequencing)
        self.warm_started = warm_start is not None
        self.pacer = DualPacer(mode=o.pacing, eta=o.eta, rho=o.rho, phi=o.phi, spend_down_days=o.spend_down_days)
        conn = ledger._conn()
        conn.executescript(LEARNER_SCHEMA)
        self.meta: dict[str, BorrowerMeta] = {}
        self.mean_emi = 1.0
        self.history: dict[str, list[tuple[int, str, str]]] = defaultdict(list)  # bid -> (day ord, ch, R/D/U)
        self.applied: set[str] = set()
        self.paused: dict[str, str] = {}
        self.today: dict[str, list[tuple]] = {}
        self.stats = Counter()
        self.eval_seconds = 0.0
        self.traces: dict[str, list[dict]] = defaultdict(list)
        self.daily: list[dict] = []
        self.snapshots: list[dict] = []
        self._stop_events: Optional[dict] = None
        self._stop_key = None
        self.config_hash = hashlib.sha1(json.dumps(asdict(o), sort_keys=True, default=str).encode()).hexdigest()[:8]
        self.realized = [0, 0]   # replies, paise over finalized touches

    def _load_meta(self) -> None:
        """Cohort, region (spine borrowers.state) and EMI per borrower; read once the spine is loaded."""
        if self.meta:
            return
        state = {r[0]: (r[1] or "UNKNOWN") for r in self.ledger._conn().execute("SELECT borrower_id, state FROM borrowers")}
        for b in self.run.portfolio:
            loan = self.run.loans[b.index]
            self.meta[b.borrower_id] = BorrowerMeta(b.borrower_id, b.index, loan.cohort or "C?",
                                                    state.get(b.borrower_id, "UNKNOWN"), int(loan.emi_paise or 0))
        emis = [m.emi_paise for m in self.meta.values() if m.emi_paise > 0]
        self.mean_emi = (sum(emis) / len(emis)) if emis else 1.0

    # ---------- contracts.Learner ----------
    @property
    def model_version(self) -> str:
        return f"{RL2_VERSION}/{self.config_hash}{'+warm' if self.warm_started else ''}@{len(self.model.records)}"

    @property
    def n_updates(self) -> int:
        return len(self.model.records)

    def rank(self, band: str, candidates: Sequence[Candidate], mode: Mode, rng=None, borrower_id: Optional[str] = None,
             persist: bool = True, at: Optional[datetime] = None) -> Ranking:
        prefs = self.today.get(borrower_id or "", [])
        by_arm = {(c.channel, c.slot): c for c in candidates}
        scores = []
        for ch, slot, p, v, cost, reason in prefs:
            ok = (ch, slot) in by_arm
            scores.append(ArmScore(band=band, channel=ch, slot=slot, alpha=0.0, beta=0.0, n_obs=len(self.model.records),
                                   source="rl2-hier", posterior_mean=p, sampled_p=None, p_used=p,
                                   est_cost_paise=int(cost), replies_per_inr=v / (cost / 100) if cost else 0.0,
                                   qualifies=ok, reason=reason if ok else f"{reason}; not eligible at the gate"))
        best = next((s for s in scores if s.qualifies), None)
        mode = Mode(mode)
        if best is None:
            r = Ranking("WAIT", None, scores, self.model_version, mode, "WAIT: no planned rl2 arm is eligible")
        else:
            r = Ranking("SEND", best, scores, self.model_version, mode,
                        f"SEND {best.channel} {best.slot}: {best.reason}")
        if persist:
            r.decision_id = self._persist(r, band, borrower_id, at, candidates)
        return r

    def _persist(self, ranking, band, borrower_id, at, candidates) -> str:
        at = at or min((c.at for c in candidates), default=None) or self.ledger.policy.now()
        at_iso = self.ledger._iso(at)
        key = f"{borrower_id or ''}|{band}|{at_iso}|{ranking.model_version}|{ranking.mode.value}"
        did = "DEC-" + hashlib.sha1(key.encode()).hexdigest()[:16]
        ch = ranking.chosen
        with self.ledger.tx() as c:
            c.execute("INSERT OR IGNORE INTO decision_scores (decision_id, at, borrower_id, band, mode, model_version,"
                      " action, chosen_channel, chosen_slot, reason, scores_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                      (did, at_iso, borrower_id, band, ranking.mode.value, ranking.model_version, ranking.action,
                       ch.channel if ch else None, ch.slot if ch else None, ranking.reason,
                       json.dumps([[s.channel, s.slot, round(s.p_used, 5), s.est_cost_paise] for s in ranking.scores])))
        return did

    def _stop_at(self, bid: str) -> list[str]:
        conn = self.ledger._conn()
        n = conn.execute("SELECT COUNT(*) FROM events WHERE event_type='consent_changed'").fetchone()[0]
        if self._stop_events is None or n != self._stop_key:
            ev: dict = defaultdict(list)
            for r in conn.execute("SELECT borrower_id, occurred_at FROM events WHERE event_type='consent_changed'"
                                  " AND details LIKE 'STOP%' ORDER BY occurred_at"):
                ev[r[0]].append(r[1])
            self._stop_events, self._stop_key = ev, n
        return self._stop_events.get(bid, [])

    def _context(self, bid: str, day_ord: int):
        """(recency count, previous touch tuple) from the borrower's finalized history before `day_ord`."""
        hist = self.history.get(bid, ())
        n = sum(1 for d, _, _ in hist if day_ord - 6 <= d < day_ord)
        prev = None
        for d, ch, out in reversed(hist):
            if d < day_ord:
                prev = (ch, out, gap_bucket(day_ord - d))
                break
        return rec_bucket(n), prev

    def apply_outcome(self, outcome) -> bool:
        reward = outcome.reward
        if reward is None or outcome.instruction_id in self.applied:
            return False
        self.applied.add(outcome.instruction_id)
        self._load_meta()
        m = self.meta.get(outcome.borrower_id)
        ex = self.ledger.policy.local(outcome.executed_at)
        day_ord = ex.date().toordinal()
        rec, prev = self._context(outcome.borrower_id, day_ord)
        row = self.ledger._conn().execute("SELECT payment_verified_at, window_end FROM touch_outcomes"
                                          " WHERE instruction_id=?", (outcome.instruction_id,)).fetchone()
        paid = int(bool(row and row[0]))
        ex_iso, end_iso = self.ledger._iso(ex), (row[1] if row else self.ledger._iso(outcome.window_end))
        stop = int(any(ex_iso <= t <= end_iso for t in self._stop_at(outcome.borrower_id)))
        r = Rec(outcome.instruction_id, outcome.borrower_id, day_ord, outcome.band, outcome.channel, outcome.slot,
                m.cohort if m else "C?", m.region if m else "UNKNOWN", self.campaign_id, rec, prev, int(reward),
                paid, stop, 0.0)
        r.p_arm = self.model.p_arm(r.band, r.ch, r.slot)
        r.p_pred = self.model.p_reply(r)
        self.model.add(r)
        out = "R" if reward else ("D" if outcome.reached or outcome.reached is None else "U")
        self.history[outcome.borrower_id].append((day_ord, outcome.channel, out))
        self.history[outcome.borrower_id].sort()
        self.realized[0] += int(reward)
        self.realized[1] += self.costs.get(outcome.channel, 0)
        if reward and outcome.borrower_id in self.paused:   # a reply is a trigger: resume
            self.paused.pop(outcome.borrower_id)
        return True

    def estimates(self, band: Optional[str] = None) -> list[dict[str, Any]]:
        out = []
        n_by = Counter((r.band, r.ch, r.slot) for r in self.model.records)
        for b in ([band] if band is not None else list(BANDS)):
            for ch in CHANNELS:
                for slot in SLOTS:
                    mean = self.model.p_arm(b, ch, slot)
                    a0, b0 = self.model.prior.get((ch, slot), (0.1, 1.9))
                    out.append({"band": b, "channel": ch, "slot": slot, "alpha": mean, "beta": 1 - mean,
                                "mean": mean, "n_obs": n_by.get((b, ch, slot), 0), "source": "rl2-hier",
                                "segment_n_obs": n_by.get((b, ch, slot), 0), "prior_mean": a0 / (a0 + b0),
                                "prior_provenance": "SYNTHETIC"})
        return out

    # ---------- daily planner ----------
    def _budget(self, d: date) -> tuple[int, int, Optional[tuple[int, int]]]:
        """(pool left, contact days left incl. today, (yesterday's committed, allowance) or None)."""
        c = self.ledger._conn()
        camp = c.execute("SELECT * FROM campaigns WHERE id=?", (self.campaign_id,)).fetchone()
        pool_left = camp["budget_per_case_paise"] * camp["allocated_cases"] - camp["committed_paise"]
        days_left = len(self.ledger.policy.contact_days(d, date.fromisoformat(camp["end_date"])))
        prev = c.execute("SELECT committed_paise, allowance_paise FROM budget_days WHERE campaign_id=? AND day<?"
                         " ORDER BY day DESC LIMIT 1", (self.campaign_id, d.isoformat())).fetchone()
        return pool_left, days_left, (tuple(prev) if prev else None)

    def plan_queue(self, run, day: int, d: date, plan_at: datetime, too_soon) -> list[tuple]:
        t0 = time.perf_counter()
        o, mdl = self.opts, self.model
        self._load_meta()
        day_ord = d.toordinal()
        mdl.refresh(day_ord)
        pool_left, days_left, prev = self._budget(d)
        if prev is not None and day > 0:
            self.pacer.update(prev[0], prev[1])
        allowance = pool_left // days_left if days_left else 0
        realized = (self.realized[0] / self.realized[1]) if self.realized[1] else None
        n_final = len(mdl.records)
        k_left = contacts_left(days_left)
        fat = {r: mdl.fatigue.get(r) for r in (0, 1, 2)}
        sfat = {r: mdl.stop_fatigue.get(r) for r in (0, 1, 2)}
        camp_m = mdl.campaign.get(self.campaign_id) if o.segments else 1.0
        rows = []
        for b in run.portfolio:
            if b.paid or b.opted_out or not b.consents or too_soon(b):
                continue
            bid = b.borrower_id
            if bid in self.paused:
                self.stats["paused_skip"] += 1
                continue
            m = self.meta[bid]
            dpd = run._dpd(b.index, day)
            if dpd is None:
                continue
            band = dpd_band(dpd)
            rec, prev = self._context(bid, day_ord)
            allowed = self.journey(bid, day) if self.journey else None
            seg = fat[rec] * camp_m
            if o.segments:
                seg *= mdl.cohort.get(m.cohort) * mdl.region.get((m.cohort, m.region))
            if o.individual:
                seg *= mdl.borrower.get(bid)
            q = mdl.q_global
            for lv in mdl.pay_levels:
                q *= lv.get(lv.keyfn(_Probe(m, band, self.campaign_id)))
            q = min(q, 1.0)
            emi_rel = m.emi_paise / self.mean_emi
            s_mult = mdl.sigma * sfat[rec] * mdl.stop_cohort.get(m.cohort) * mdl.stop_region.get((m.cohort, m.region))
            arms = []
            for ch in sorted(b.consents):
                if allowed is not None and ch not in allowed:
                    continue
                if not self.has_template(ch, b.language):
                    continue
                cost = self.costs.get(ch, 0)
                if cost <= 0:
                    continue
                cm = 1.0
                if o.sequencing:
                    tk = _trans(prev, ch)
                    cm *= mdl.trans.get(tk) * mdl.trans_cohort.get((m.cohort,) + tk)
                if o.individual:
                    cm *= mdl.borrower_ch.get((bid, ch))
                for slot in SLOTS:
                    if slot == "11-14":
                        continue
                    p = mdl.p_arm(band, ch, slot) * seg * cm
                    if o.individual:
                        p *= mdl.borrower_slot.get((bid, slot))
                    p = min(p, 0.95)
                    if p < o.min_reply_prob:
                        continue
                    s = p * s_mult
                    v = p * (1.0 + o.alpha * q * emi_rel) - o.mu * s
                    if v <= 0:
                        continue
                    arms.append((v / cost, v, p, s, ch, slot, cost))
            if not arms:
                self.stats["no_arm"] += 1
                continue
            arms.sort(key=lambda a: (-a[0], CHANNELS.index(a[4]), a[5]))
            rows.append((b, m, band, rec, prev, arms, q, emi_rel, dpd))
        lam = self.pacer.price(day, [r[5][0][0] for r in rows], realized, n_final, days_left, allowance,
                               [r[5][0][6] for r in rows])
        lam_floor = self.pacer.guard(realized, n_final)
        order = []
        new_today: dict = {}
        for b, m, band, rec, prev, arms, q, emi_rel, dpd in rows:
            bid = b.borrower_id
            if o.arm_rule == "lagrange":
                arms = sorted(arms, key=lambda a: (-(a[1] - lam * a[6]), CHANNELS.index(a[4]), a[5]))
            best = arms[0]
            ratio, v, p, s, ch, slot, cost = best
            decision, why = "SEND", ""
            if v - lam * cost <= 0:
                decision, why = "WAIT", f"V/cost {ratio:.5f} below price {lam:.5f}"
                if o.stopping and n_final >= self.pacer.warmup and self.history.get(bid):
                    ob, eb, kb = mdl.borrower_evidence(bid) if o.individual else (0.0, 0.0, 1e6)
                    mb = (ob + kb) / (eb + kb)
                    cv = continuation_value(v / mb, p / mb, ob, eb, kb, cost, lam_floor, k_left)
                    if cv <= 0:
                        self.paused[bid] = (f"day {day + 1}: continuation value {cv:.4f} <= 0 at guard price "
                                            f"{lam_floor:.5f} (replies {ob:.0f} vs expected {eb:.2f}, kappa {kb:.2f},"
                                            f" {k_left} contacts left)")
                        decision, why = "PAUSE", self.paused[bid]
            elif o.lookahead and rec > 0:
                hist = self.history.get(bid, ())
                n_tom = sum(1 for dd, _, _ in hist if day_ord - 5 <= dd <= day_ord)
                r_tom = rec_bucket(n_tom)
                if r_tom < rec:
                    f = fat[r_tom] / fat[rec]
                    sf = f * sfat[r_tom] / sfat[rec]
                    v_tom = p * f * (1.0 + o.alpha * q * emi_rel) - o.mu * s * sf
                    if wait_is_better(v, v_tom, cost, lam, days_left):
                        decision, why = "WAIT", (f"lookahead: tomorrow fatigue x{f:.2f} beats today "
                                                 f"(V {v:.4f} -> {v_tom:.4f}, {days_left} days left)")
            self.stats[decision] += 1
            if decision == "SEND":
                reason = (f"p={p:.4f} q={q:.3f} s={s:.5f} V={v:.4f} / {cost}p = {ratio:.5f} > lambda {lam:.5f}")
                new_today[bid] = [(a[4], a[5], a[2], a[1], a[6], reason) for a in arms[:o.top_k]]
                order.append((-ratio, -dpd, b.index))
            if o.trace_every and b.index % o.trace_every == 0:
                self.traces[bid].append({
                    "day": day + 1, "date": d.isoformat(), "band": band, "recent_touches": rec,
                    "previous": prev, "lambda": round(lam, 6),
                    "factors": self.explain(bid, band, rec, prev, ch, slot),
                    "best_arm": {"channel": ch, "slot": slot, "p_reply": round(p, 4), "p_pay": round(q, 3),
                                 "p_stop": round(s, 5), "value": round(v, 4), "cost_paise": cost,
                                 "value_per_paisa": round(ratio, 6)},
                    "decision": decision, "why": why or "value per paisa above the price"})
        self.today = new_today
        order.sort()
        self.daily.append({"day": day + 1, "lambda": lam, "guard": lam_floor, "evaluated": len(rows),
                           "queued": len(order), "paused_total": len(self.paused), "allowance_paise": allowance,
                           "pool_left_paise": pool_left})
        self._snapshot(day + 1)
        self.eval_seconds += time.perf_counter() - t0
        self.stats["evaluated"] += len(rows)
        out = []
        for _, _, i in order:
            b = run.portfolio[i]
            keep = {(a[0], a[1]) for a in new_today[b.borrower_id]}
            cands = [c for c in run.candidates(b, d) if (c.channel, c.slot) in keep]
            out.append((b, cands, None))
        return out

    def explain(self, bid, band, rec, prev, ch, slot) -> list[tuple[str, float]]:
        m = self.meta[bid]
        r = Rec("?", bid, 0, band, ch, slot, m.cohort, m.region, self.campaign_id, rec, prev, 0, 0, 0, 0.0)
        return ([("arm " + f"{band} {ch} {slot}", round(self.model.p_arm(band, ch, slot), 4))]
                + [(n, round(x, 3)) for n, x in self.model.factors(r)])

    def _snapshot(self, day: int) -> None:
        """Per-segment learning table for the day (CM-02: report learning per segment)."""
        mdl = self.model
        best = {}
        for band in BANDS:
            arms = sorted(((mdl.p_arm(band, ch, s) / (self.costs.get(ch, 1) / 100), ch, s)
                           for ch in ("whatsapp", "sms", "ivr", "email", "voicebot") for s in ("08-11", "14-17", "17-19")),
                          key=lambda t: (-t[0], t[1], t[2]))
            best[band] = f"{arms[0][1]} {arms[0][2]} ({arms[0][0]:.3f} replies/₹)"
        self.snapshots.append({
            "day": day, "finalized_touches": len(mdl.records),
            "best_arm_by_band": best,
            "cohort_multiplier": {k: round(v, 3) for k, v in sorted(mdl.cohort.m.items())},
            "fatigue": {k: round(mdl.fatigue.get(k), 3) for k in (0, 1, 2)},
            "kappa": {lv.name: round(lv.kappa, 2) for lv in [mdl.band] + mdl.reply_levels},
            "p_pay_given_reply": round(mdl.q_global, 3), "stop_share": round(mdl.sigma, 4)})

    def report(self) -> dict:
        from .report import levels_report
        return {"version": self.model_version, "options": asdict(self.opts), "stats": dict(self.stats),
                "eval_seconds": round(self.eval_seconds, 2),
                "borrowers_evaluated_per_second": (round(self.stats["evaluated"] / self.eval_seconds)
                                                   if self.eval_seconds else None),
                "paused": len(self.paused), "pacing": self.daily, "levels": levels_report(self)}


class _Probe:
    """Minimal record for the pay levels' key functions."""
    __slots__ = ("campaign", "cohort", "region", "band")

    def __init__(self, m: BorrowerMeta, band: str, campaign: str):
        self.campaign, self.cohort, self.region, self.band = campaign, m.cohort, m.region, band


def _trans(prev, ch):
    from .value import trans_key
    return trans_key(prev, ch)
