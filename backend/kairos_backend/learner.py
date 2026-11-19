"""K09 learner: Beta-Bernoulli reply model per arm (DPD band x channel x local-time slot).

What it does
    rank()           scores the Supervisor's ELIGIBLE candidates and picks the arm with the most
                     expected replies per rupee, or WAIT when nothing clears the reply floor.
    apply_outcome()  counts one FINAL outcome per touch (REPLY -> alpha+1, NO_REPLY -> beta+1),
                     idempotent on instruction_id.
    estimates()      the current posterior per arm, for before/after reports.

State lives in the ledger's SQLite DB (same file, same connection helper, writes through
Ledger.tx()). Tables:
    learner_arms      one row per (band, channel, slot) that has seen at least one outcome;
                      alpha/beta are prior + observed. Arms without a row sit at their prior.
    learner_updates   one row per applied instruction_id (the idempotency key).
    decision_scores   one row per persisted ranking (what was scored, what was chosen, why).

Sparse segments: a (band, channel, slot) with fewer than `min_segment_obs` finalized outcomes is
scored from the pooled channel x slot posterior (prior + every band's observed outcomes) and is
labeled source="pooled"; with no observations anywhere it is source="prior".

Exploration: seeded Thompson sampling only when mode.allows_exploration (SANDBOX / SIMULATOR) AND
a random.Random is supplied. PRODUCTION always uses the posterior mean and never touches the rng.

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
                        Outcome, Ranking)
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
                 default_prior: tuple[float, float] = DEFAULT_PRIOR):
        self.ledger = ledger
        self.synthetic_priors = priors is None or priors is SIMULATOR_PRIORS
        raw = SIMULATOR_PRIORS if priors is None else priors
        self.priors: dict[tuple[str, str], tuple[float, float]] = {
            (ch, slot): _validate_prior((ch, slot), p) for (ch, slot), p in raw.items()}
        self.default_prior = _validate_prior("default_prior", default_prior)
        self.min_segment_obs = int(min_segment_obs)
        self.min_reply_prob = float(min_reply_prob)
        config = {
            "priors": sorted([ch, slot, m, s] for (ch, slot), (m, s) in self.priors.items()),
            "default_prior": list(self.default_prior),
            "min_segment_obs": self.min_segment_obs,
            "min_reply_prob": self.min_reply_prob,
        }
        self.config_hash = hashlib.sha1(json.dumps(config, sort_keys=True).encode()).hexdigest()[:8]
        self.ledger._conn().executescript(SCHEMA)

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

    def _posteriors(self, band: str) -> dict[tuple[str, str], tuple[float, float, int, str]]:
        """(channel, slot) -> (alpha, beta, n_obs, source) for every arm with any observation.
        Arms missing from the result are at their prior."""
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
        post = self._posteriors(band)
        # Score in a canonical order so a seeded rng gives the same draws whatever order the
        # Supervisor listed the candidates in.
        ordered = sorted(candidates, key=lambda k: (_CH_ORDER.get(k.channel, len(_CH_ORDER)),
                                                    _SLOT_ORDER.get(k.slot, len(_SLOT_ORDER)),
                                                    k.est_cost_inr, k.template_id or "", k.at.isoformat()))
        scores: list[ArmScore] = []
        for cand in ordered:
            alpha, beta, n_obs, source = self._arm(post, cand.channel, cand.slot)
            mean = alpha / (alpha + beta)
            sampled = rng.betavariate(alpha, beta) if explore else None
            p_used = sampled if sampled is not None else mean
            cost = float(cand.est_cost_inr)
            rpi = p_used / cost if cost > 0 else 0.0
            known = cand.channel in _CH_ORDER and cand.slot in _SLOT_ORDER
            qualifies = known and cost > 0 and p_used >= self.min_reply_prob
            basis = f"p={p_used:.4f} ({'thompson' if sampled is not None else 'mean'}, {source} n={n_obs})"
            if not known:
                reason = f"unknown arm {cand.channel}/{cand.slot}"
            elif cost <= 0:
                reason = f"{basis}; no positive cost estimate (est_cost_inr={cost})"
            elif p_used < self.min_reply_prob:
                reason = f"{basis} below min_reply_prob {self.min_reply_prob}"
            else:
                reason = f"{basis} / INR {cost:.2f} = {rpi:.4f} replies per INR"
            scores.append(ArmScore(band=band, channel=cand.channel, slot=cand.slot, alpha=alpha, beta=beta,
                                   n_obs=n_obs, source=source, posterior_mean=mean, sampled_p=sampled,
                                   p_used=p_used, est_cost_inr=cost, replies_per_inr=rpi,
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
        if persist:
            ranking.decision_id = self._persist(ranking, band, borrower_id, at, candidates)
        return ranking

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
                " action, chosen_channel, chosen_slot, reason, scores_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (decision_id, at_iso, borrower_id, band, ranking.mode.value, ranking.model_version, ranking.action,
                 chosen.channel if chosen else None, chosen.slot if chosen else None, ranking.reason,
                 json.dumps([asdict(s) for s in ranking.scores])))
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
                            " applied_at) VALUES (?,?,?,?,?,?)",
                            (outcome.instruction_id, outcome.band, outcome.channel, outcome.slot, reward, now))
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
