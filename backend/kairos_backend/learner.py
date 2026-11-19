"""K09 learner: Beta-Bernoulli reply model per arm (DPD band x channel x local-time slot).

What it does
    rank()           scores the Supervisor's ELIGIBLE candidates and picks the arm with the most
                     expected replies per rupee (costs arrive in integer paise), or WAIT when
                     nothing clears the reply floor.
    apply_outcome()  counts one FINAL outcome per touch (REPLY -> alpha+1, NO_REPLY -> beta+1),
                     idempotent on instruction_id.
    estimates()      the current posterior per arm, for before/after reports.

State lives in the ledger's SQLite DB (the one file that also holds the K03 spine; same
connection helper, writes through Ledger.tx()). K09 tables:
    learner_arms      one row per (band, channel, slot) that has seen at least one outcome;
                      alpha/beta are prior + observed. Arms without a row sit at their prior.
    learner_updates   one row per applied instruction_id (the idempotency key).
    decision_scores   one row per persisted ranking (what was scored, what was chosen, why).

Sparse segments: a (band, channel, slot) with fewer than `min_segment_obs` finalized outcomes is
scored from the pooled channel x slot posterior (prior + every band's observed outcomes) and is
labeled source="pooled"; with no observations anywhere it is source="prior".

Options (all off by default, so the default model_version / config hash is unchanged):
    decay=γ              discounted evidence: an outcome executed d days before the decision weighs γ**d
                         (E17-style drift: old evidence fades instead of anchoring the posterior).
    hier_strength=k      hierarchical slots: every band x channel x slot posterior is centred on the
                         pooled channel x slot mean with k pseudo-observations, instead of the hard
                         min_segment_obs switch between segment and pooled.
    min_replies_per_inr  a value floor next to the probability floor: an arm whose expected replies
                         per rupee is below it never qualifies (-> WAIT, the money stays in the pool).

Exploration: seeded Thompson sampling only when mode.allows_exploration (SANDBOX / SIMULATOR) AND
a random.Random is supplied. PRODUCTION always uses the posterior mean and never touches the rng.

Propensity logging (for off-policy evaluation, scripts/ope_k09.py): a greedy ranking records
propensity 1.0 (kind "greedy"). A Thompson ranking with propensity_draws > 0 records P(chosen arm)
estimated from propensity_draws posterior draws on a SEPARATE rng seeded from the posterior table
(the decision's own rng stream, and therefore every replay hash, is untouched). Off by default
(propensity_draws=0, kind "thompson_unlogged"), so default runs cost nothing extra.

SIMULATOR_PRIORS are SYNTHETIC (hand-set for the simulator), not learned from real outcomes.
"""
from __future__ import annotations

import hashlib
import json
import random
from dataclasses import asdict
from datetime import datetime
from typing import Any, Optional, Sequence

from .contracts import (CHANNELS, DEFAULT_MIN_REPLY_PROB, MODEL_FAMILY, SLOTS, ArmScore, Candidate, Mode,
                        Outcome, Ranking, replies_per_rupee)
from .ledger import Ledger

SCHEMA = """
CREATE TABLE IF NOT EXISTS learner_arms (
  band TEXT NOT NULL, channel TEXT NOT NULL, slot TEXT NOT NULL,
  alpha REAL NOT NULL, beta REAL NOT NULL, n_obs INTEGER NOT NULL DEFAULT 0, updated_at TEXT,
  PRIMARY KEY (band, channel, slot));
CREATE TABLE IF NOT EXISTS learner_updates (
  instruction_id TEXT PRIMARY KEY, band TEXT NOT NULL, channel TEXT NOT NULL, slot TEXT NOT NULL,
  reward INTEGER NOT NULL, applied_at TEXT);
CREATE INDEX IF NOT EXISTS ix_learner_updates_arm ON learner_updates (channel, slot);
CREATE TABLE IF NOT EXISTS decision_scores (
  decision_id TEXT PRIMARY KEY, at TEXT, borrower_id TEXT, band TEXT, mode TEXT, model_version TEXT,
  action TEXT, chosen_channel TEXT, chosen_slot TEXT, reason TEXT, scores_json TEXT);
"""

PRIORS_PROVENANCE = "SYNTHETIC"

# SYNTHETIC simulator priors: (mean reply probability, strength in pseudo-observations) per
# (channel, slot). Hand-set for the simulator / demo until real outcomes exist. Label results SIMULATED.
_SIM_BASE = {"whatsapp": 0.18, "sms": 0.08, "email": 0.02, "ivr": 0.10, "voicebot": 0.12}
_SIM_SLOT_OVERRIDES = {("whatsapp", "17-19"): 0.24}
SIMULATOR_PRIOR_STRENGTH = 20.0
SIMULATOR_PRIORS: dict[tuple[str, str], tuple[float, float]] = {
    (ch, slot): (_SIM_SLOT_OVERRIDES.get((ch, slot), mean), SIMULATOR_PRIOR_STRENGTH)
    for ch, mean in _SIM_BASE.items() for slot in SLOTS
}

# Weak prior for an arm the priors dict does not mention.
DEFAULT_PRIOR: tuple[float, float] = (0.05, 2.0)

_CH_ORDER = {c: i for i, c in enumerate(CHANNELS)}
WAIT_ACTION = "WAIT"


def arm_key(s: Any) -> str:
    """Action id used in propensity logs: "channel|slot"."""
    return f"{s.channel}|{s.slot}"

_SLOT_ORDER = {s: i for i, s in enumerate(SLOTS)}


def _validate_prior(key: Any, prior: tuple[float, float]) -> tuple[float, float]:
    mean, strength = float(prior[0]), float(prior[1])
    if not (0.0 < mean < 1.0) or strength <= 0:
        raise ValueError(f"prior for {key} must have 0 < mean < 1 and strength > 0, got {prior}")
    return mean, strength


class BetaBernoulliLearner:
    """contracts.Learner backed by the ledger's SQLite DB."""

    def __init__(self, ledger: Ledger, priors: Optional[dict[tuple[str, str], tuple[float, float]]] = None, *,
                 min_segment_obs: int = 30, min_reply_prob: float = DEFAULT_MIN_REPLY_PROB,
                 default_prior: tuple[float, float] = DEFAULT_PRIOR, decay: Optional[float] = None,
                 hier_strength: Optional[float] = None, min_replies_per_inr: float = 0.0,
                 propensity_draws: int = 0):
        self.ledger = ledger
        self.propensity_draws = max(0, int(propensity_draws))   # not part of config_hash: logging only
        self._prop_cache: dict = {}
        self.synthetic_priors = priors is None or priors is SIMULATOR_PRIORS
        raw = SIMULATOR_PRIORS if priors is None else priors
        self.priors: dict[tuple[str, str], tuple[float, float]] = {
            (ch, slot): _validate_prior((ch, slot), p) for (ch, slot), p in raw.items()}
        self.default_prior = _validate_prior("default_prior", default_prior)
        self.min_segment_obs = int(min_segment_obs)
        self.min_reply_prob = float(min_reply_prob)
        if decay is not None and not (0.0 < float(decay) <= 1.0):
            raise ValueError(f"decay must be in (0, 1], got {decay}")
        if hier_strength is not None and float(hier_strength) <= 0:
            raise ValueError(f"hier_strength must be > 0, got {hier_strength}")
        self.decay = None if decay is None else float(decay)
        self.hier_strength = None if hier_strength is None else float(hier_strength)
        self.min_replies_per_inr = float(min_replies_per_inr)
        self._post_cache: dict = {}
        config = {
            "priors": sorted([ch, slot, m, s] for (ch, slot), (m, s) in self.priors.items()),
            "default_prior": list(self.default_prior),
            "min_segment_obs": self.min_segment_obs,
            "min_reply_prob": self.min_reply_prob,
        }
        # options join the hash only when set, so the default model keeps its version string
        for k, v in (("decay", self.decay), ("hier_strength", self.hier_strength),
                     ("min_replies_per_inr", self.min_replies_per_inr or None)):
            if v is not None:
                config[k] = v
        self.config_hash = hashlib.sha1(json.dumps(config, sort_keys=True).encode()).hexdigest()[:8]
        conn = self.ledger._conn()
        conn.executescript(SCHEMA)
        # executed_at (decision-time clock of the touch) is what `decay` discounts by; older DBs lack it
        if "executed_at" not in {r[1] for r in conn.execute("PRAGMA table_info(learner_updates)")}:
            conn.execute("ALTER TABLE learner_updates ADD COLUMN executed_at TEXT")
        have = {r[1] for r in conn.execute("PRAGMA table_info(decision_scores)")}
        for col, typ in (("propensity", "REAL"), ("propensity_kind", "TEXT"), ("arm_probs_json", "TEXT")):
            if col not in have:
                conn.execute(f"ALTER TABLE decision_scores ADD COLUMN {col} {typ}")

    # ---------- helpers ----------
    def _now_iso(self) -> str:
        return self.ledger.policy.now().isoformat(timespec="seconds")

    def _iso(self, dt: datetime) -> str:
        return self.ledger.policy.local(dt).isoformat(timespec="seconds")

    def prior(self, channel: str, slot: str) -> tuple[float, float]:
        """(alpha, beta) of the prior for a channel x slot."""
        mean, strength = self.priors.get((channel, slot), self.default_prior)
        return mean * strength, (1.0 - mean) * strength

    def _bands(self) -> list[str]:
        return [b["id"] for b in self.ledger.policy.raw["dpd_bands"]]

    @property
    def n_updates(self) -> int:
        return self.ledger._conn().execute("SELECT COUNT(*) FROM learner_updates").fetchone()[0]

    @property
    def model_version(self) -> str:
        return f"{MODEL_FAMILY}/{self.config_hash}@{self.n_updates}"

    def _posteriors(self, band: str, ref: Optional[datetime] = None) -> dict[tuple[str, str], tuple[float, float, int, str]]:
        """(channel, slot) -> (alpha, beta, n_obs, source) for every arm with any observation.
        Arms missing from the result are at their prior. `ref` is the decision time (for `decay`)."""
        if self.decay is not None or self.hier_strength is not None:
            return self._posteriors_weighted(band, ref)
        c = self.ledger._conn()
        seg = {(r["channel"], r["slot"]): (r["alpha"], r["beta"], r["n_obs"])
               for r in c.execute("SELECT channel, slot, alpha, beta, n_obs FROM learner_arms WHERE band=?", (band,))}
        pooled = {(r["channel"], r["slot"]): (r["s"], r["n"])
                  for r in c.execute("SELECT channel, slot, SUM(reward) s, COUNT(*) n FROM learner_updates"
                                     " GROUP BY channel, slot")}
        out = {}
        for key in set(seg) | set(pooled):
            a0, b0 = self.prior(*key)
            if key in seg and seg[key][2] >= self.min_segment_obs:
                out[key] = (seg[key][0], seg[key][1], int(seg[key][2]), "segment")
            elif key in pooled and pooled[key][1] > 0:
                s, n = pooled[key]
                out[key] = (a0 + s, b0 + (n - s), int(n), "pooled")
        return out

    def _weighted_counts(self, ref_day: Optional[str]) -> tuple[dict, dict]:
        """(band, channel, slot) -> [succ, fail] and (channel, slot) -> [succ, fail], each outcome
        weighted decay**(days from its execution day to ref_day). Cached per (updates, ref day)."""
        key = (self.n_updates, ref_day)
        hit = self._post_cache.get(key)
        if hit is not None:
            return hit
        seg: dict = {}
        pooled: dict = {}
        ref_d = datetime.fromisoformat(ref_day).date() if ref_day else None
        for r in self.ledger._conn().execute(
                "SELECT band, channel, slot, substr(executed_at, 1, 10) d, SUM(reward) s, COUNT(*) n"
                " FROM learner_updates GROUP BY band, channel, slot, d"):
            w = 1.0
            if self.decay is not None and ref_d is not None and r["d"]:
                age = (ref_d - datetime.fromisoformat(r["d"]).date()).days
                w = self.decay ** max(0, age)
            s, f = r["s"] * w, (r["n"] - r["s"]) * w
            for table, k in ((seg, (r["band"], r["channel"], r["slot"])), (pooled, (r["channel"], r["slot"]))):
                acc = table.setdefault(k, [0.0, 0.0])
                acc[0] += s
                acc[1] += f
        self._post_cache = {key: (seg, pooled)}   # one entry: decisions arrive in time order
        return seg, pooled

    def _posteriors_weighted(self, band: str, ref: Optional[datetime]) -> dict:
        ref_day = self._iso(ref)[:10] if ref is not None else None
        seg, pooled = self._weighted_counts(ref_day)
        out = {}
        for (ch, slot), (ps, pf) in pooled.items():
            a0, b0 = self.prior(ch, slot)
            pa, pb = a0 + ps, b0 + pf
            ss, sf = seg.get((band, ch, slot), (0.0, 0.0))
            n_seg = ss + sf
            if self.hier_strength is not None:
                mu, k = pa / (pa + pb), self.hier_strength
                out[(ch, slot)] = (k * mu + ss, k * (1 - mu) + sf, int(round(n_seg)), "hier")
            elif n_seg >= self.min_segment_obs:
                out[(ch, slot)] = (a0 + ss, b0 + sf, int(round(n_seg)), "segment")
            else:
                out[(ch, slot)] = (pa, pb, int(round(ps + pf)), "pooled")
        return out

    def _arm(self, post: dict, channel: str, slot: str) -> tuple[float, float, int, str]:
        if (channel, slot) in post:
            return post[(channel, slot)]
        a0, b0 = self.prior(channel, slot)
        return a0, b0, 0, "prior"

    # ---------- Learner protocol ----------
    def rank(self, band: str, candidates: Sequence[Candidate], mode: Mode,
             rng: Optional[random.Random] = None, borrower_id: Optional[str] = None,
             persist: bool = True, at: Optional[datetime] = None) -> Ranking:
        """Score eligible candidates; SEND the best replies-per-rupee arm or WAIT.
        `at` (optional, beyond the protocol) is the decision time used for the decision_id;
        it defaults to the earliest candidate time, else now."""
        mode = Mode(mode)
        model_version = self.model_version
        explore = mode.allows_exploration and rng is not None  # PRODUCTION never samples
        ref = at or min((c.at for c in candidates), default=None)
        post = self._posteriors(band, ref)
        # Score in a canonical order so a seeded rng gives the same draws whatever order the
        # Supervisor listed the candidates in.
        ordered = sorted(candidates, key=lambda k: (_CH_ORDER.get(k.channel, len(_CH_ORDER)),
                                                    _SLOT_ORDER.get(k.slot, len(_SLOT_ORDER)),
                                                    k.est_cost_paise, k.template_id or "", k.at.isoformat()))
        scores: list[ArmScore] = []
        for cand in ordered:
            alpha, beta, n_obs, source = self._arm(post, cand.channel, cand.slot)
            mean = alpha / (alpha + beta)
            sampled = rng.betavariate(alpha, beta) if explore else None
            p_used = sampled if sampled is not None else mean
            cost = int(cand.est_cost_paise)
            rpi = replies_per_rupee(p_used, cost)
            known = cand.channel in _CH_ORDER and cand.slot in _SLOT_ORDER
            qualifies = (known and cost > 0 and p_used >= self.min_reply_prob
                         and rpi >= self.min_replies_per_inr)
            basis = f"p={p_used:.4f} ({'thompson' if sampled is not None else 'mean'}, {source} n={n_obs})"
            if not known:
                reason = f"unknown arm {cand.channel}/{cand.slot}"
            elif cost <= 0:
                reason = f"{basis}; no positive cost estimate (est_cost_paise={cost})"
            elif p_used < self.min_reply_prob:
                reason = f"{basis} below min_reply_prob {self.min_reply_prob}"
            elif rpi < self.min_replies_per_inr:
                reason = f"{basis}: {rpi:.4f} replies per INR below min_replies_per_inr {self.min_replies_per_inr}"
            else:
                reason = f"{basis} / INR {cost / 100:.2f} ({cost} paise) = {rpi:.4f} replies per INR"
            scores.append(ArmScore(band=band, channel=cand.channel, slot=cand.slot, alpha=alpha, beta=beta,
                                   n_obs=n_obs, source=source, posterior_mean=mean, sampled_p=sampled,
                                   p_used=p_used, est_cost_paise=cost, replies_per_inr=rpi,
                                   qualifies=qualifies, reason=reason))

        qualifying = [(i, s) for i, s in enumerate(scores) if s.qualifies]
        if not candidates:
            ranking = Ranking("WAIT", None, scores, model_version, mode, "WAIT: no eligible candidates")
        elif not qualifying:
            ranking = Ranking("WAIT", None, scores, model_version, mode,
                              f"WAIT: no candidate qualifies (all below min_reply_prob {self.min_reply_prob},"
                              " without a positive cost estimate, or unknown arms)")
        else:
            # highest replies per rupee; ties -> channel order, then slot order (scores are in that order)
            _, best = min(qualifying, key=lambda t: (-t[1].replies_per_inr, t[0]))
            ranking = Ranking("SEND", best, scores, model_version, mode,
                              f"SEND {best.channel} {best.slot}: best replies per INR "
                              f"({best.replies_per_inr:.4f}; {best.reason})")
        if not explore:
            ranking.propensity, ranking.propensity_kind = 1.0, "greedy"
        elif self.propensity_draws > 0 and scores:
            probs = self.thompson_propensities(scores, self.propensity_draws)
            key = arm_key(ranking.chosen) if ranking.chosen is not None else WAIT_ACTION
            ranking.propensity, ranking.propensity_kind, ranking.arm_probs = probs[key], "thompson_mc", probs
        else:
            ranking.propensity_kind = "thompson_unlogged"
        if persist:
            ranking.decision_id = self._persist(ranking, band, borrower_id, at, candidates)
        return ranking

    def thompson_propensities(self, scores: Sequence[ArmScore], draws: int) -> dict[str, float]:
        """P(each arm, or WAIT, is chosen) under this learner's Thompson rule, by Monte Carlo over `draws`
        posterior draws: the same qualification (known arm, positive cost, reply floor, value floor) and
        the same tie-break (canonical order) as rank(). Duplicate candidates of one arm draw independently,
        as in rank(). Smoothed (count + 0.5) / (draws + 0.5 K) over the K possible actions, so every
        probability is in (0, 1] and they sum to 1. Seeded from the posterior table: deterministic, and
        cached (identical tables within a day share one estimate)."""
        cand = [(arm_key(s), s.alpha, s.beta, int(s.est_cost_paise)) for s in scores
                if s.channel in _CH_ORDER and s.slot in _SLOT_ORDER and s.est_cost_paise > 0]
        material = json.dumps([[k, round(a, 9), round(b, 9), c] for k, a, b, c in cand]
                              + [self.min_reply_prob, self.min_replies_per_inr, int(draws)])
        hit = self._prop_cache.get(material)
        if hit is not None:
            return dict(hit)
        r = random.Random(int(hashlib.sha1(material.encode()).hexdigest()[:16], 16))
        wins: dict[str, int] = {}
        for _ in range(int(draws)):
            best, best_v = WAIT_ACTION, None
            for k, a, b, c in cand:
                p = r.betavariate(a, b)
                if p < self.min_reply_prob:
                    continue
                v = replies_per_rupee(p, c)
                if v < self.min_replies_per_inr:
                    continue
                if best_v is None or v > best_v:
                    best, best_v = k, v
            wins[best] = wins.get(best, 0) + 1
        actions = list(dict.fromkeys([k for k, _, _, _ in cand] + [WAIT_ACTION]))
        z = draws + 0.5 * len(actions)
        probs = {k: (wins.get(k, 0) + 0.5) / z for k in actions}
        if len(self._prop_cache) > 4096:
            self._prop_cache.clear()
        self._prop_cache[material] = probs
        return dict(probs)

    def _persist(self, ranking: Ranking, band: str, borrower_id: Optional[str], at: Optional[datetime],
                 candidates: Sequence[Candidate]) -> str:
        if at is None:
            at = min((c.at for c in candidates), default=None) or self.ledger.policy.now()
        at_iso = self._iso(at)
        key = f"{borrower_id or ''}|{band}|{at_iso}|{ranking.model_version}|{ranking.mode.value}"
        decision_id = "DEC-" + hashlib.sha1(key.encode()).hexdigest()[:16]
        chosen = ranking.chosen
        with self.ledger.tx() as c:
            c.execute(
                "INSERT OR IGNORE INTO decision_scores (decision_id, at, borrower_id, band, mode, model_version,"
                " action, chosen_channel, chosen_slot, reason, scores_json, propensity, propensity_kind, arm_probs_json)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (decision_id, at_iso, borrower_id, band, ranking.mode.value, ranking.model_version, ranking.action,
                 chosen.channel if chosen else None, chosen.slot if chosen else None, ranking.reason,
                 json.dumps([asdict(s) for s in ranking.scores]), ranking.propensity, ranking.propensity_kind,
                 json.dumps(ranking.arm_probs, sort_keys=True) if ranking.arm_probs is not None else None))
        return decision_id

    def apply_outcome(self, outcome: Outcome) -> bool:
        """REPLY -> alpha+1, NO_REPLY -> beta+1, once per instruction_id. PENDING / UNRESOLVED -> False."""
        reward = outcome.reward
        if reward is None:
            return False
        a0, b0 = self.prior(outcome.channel, outcome.slot)
        da, db = (1.0, 0.0) if reward == 1 else (0.0, 1.0)
        now = self._now_iso()
        with self.ledger.tx() as c:
            cur = c.execute("INSERT OR IGNORE INTO learner_updates (instruction_id, band, channel, slot, reward,"
                            " applied_at, executed_at) VALUES (?,?,?,?,?,?,?)",
                            (outcome.instruction_id, outcome.band, outcome.channel, outcome.slot, reward, now,
                             self._iso(outcome.executed_at) if outcome.executed_at else None))
            if cur.rowcount == 0:
                return False
            c.execute("INSERT INTO learner_arms (band, channel, slot, alpha, beta, n_obs, updated_at)"
                      " VALUES (?,?,?,?,?,1,?) ON CONFLICT(band, channel, slot) DO UPDATE SET"
                      " alpha = alpha + ?, beta = beta + ?, n_obs = n_obs + 1, updated_at = excluded.updated_at",
                      (outcome.band, outcome.channel, outcome.slot, a0 + da, b0 + db, now, da, db))
        return True

    def estimates(self, band: Optional[str] = None) -> list[dict[str, Any]]:
        """Effective posterior per arm (what rank() would use as its mean), for every band x channel x
        slot, plus the raw segment count. `prior_provenance` is SYNTHETIC for the simulator priors."""
        out = []
        for b in ([band] if band is not None else self._bands()):
            post = self._posteriors(b)
            seg_n = {(r["channel"], r["slot"]): r["n_obs"] for r in self.ledger._conn().execute(
                "SELECT channel, slot, n_obs FROM learner_arms WHERE band=?", (b,))}
            for ch in CHANNELS:
                for slot in SLOTS:
                    alpha, beta, n_obs, source = self._arm(post, ch, slot)
                    a0, b0 = self.prior(ch, slot)
                    out.append({"band": b, "channel": ch, "slot": slot, "alpha": alpha, "beta": beta,
                                "mean": alpha / (alpha + beta), "n_obs": n_obs, "source": source,
                                "segment_n_obs": int(seg_n.get((ch, slot), 0)), "prior_mean": a0 / (a0 + b0),
                                "prior_provenance": PRIORS_PROVENANCE if self.synthetic_priors else "CONFIGURED"})
        return out
