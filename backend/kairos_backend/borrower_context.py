"""K09 borrower-context layer: who to contact, learned from each borrower's own history.

CM-02 brief: "The best channel and time differ by borrower and change over a campaign" and
"Over-contact lowers future response." The band × channel × slot learner can't see either: it
spends the same on a borrower who has ignored three messages as on one never contacted.

This layer learns a reply-rate multiplier per observable history bucket and applies it on top of
the arm estimate (a factorised model: p(arm, borrower) ≈ p(arm) × m(bucket)):

    engagement  new        no executed touch yet in this campaign
                miss1      one finalized no-reply, never replied
                miss2+     two or more finalized no-replies, never replied
                replied    at least one meaningful reply
    recency     r0         no executed touch in the previous 7 days
                r1         one executed touch in the previous 7 days
                r2+        two or more

With recency="gap" the recency part is instead the gap since the borrower's last executed touch
(a finer fatigue / spacing feature): g- (none yet), g2 (<= 2 days), g3, g4-6, g7+.

Each bucket is a Beta–Bernoulli posterior shrunk toward the pooled observed reply rate (so an
unseen bucket is exactly 1.0× and day 1 behaves like the plain learner). m(bucket) = bucket
posterior mean ÷ pooled mean. Updates are idempotent per instruction_id and use the
bucket the borrower was in when the touch was executed. Everything comes from the ledger's own
touches (K06) and touch_outcomes (K09) tables, keyed by the K03 spine borrower_id — no simulator
internals, no PII.
"""
from __future__ import annotations

import random
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any, Optional, Sequence

from .contracts import CHANNELS, SLOTS, ArmScore, Candidate, Mode, Outcome, Ranking, replies_per_rupee
from .learner import BetaBernoulliLearner
from .outcomes import SCHEMA as OUTCOMES_SCHEMA

ENGAGEMENT = ("new", "miss1", "miss2+", "replied")
RECENCY = ("r0", "r1", "r2+")
BUCKETS = tuple(f"{e}|{r}" for e in ENGAGEMENT for r in RECENCY)
GAPS = ("g-", "g2", "g3", "g4-6", "g7+")
GAP_BUCKETS = tuple(f"{e}|{g}" for e in ENGAGEMENT for g in GAPS)
RECENCY_MODES = ("count", "gap")
CONTEXT_VERSION = "k09-ctx-v1"

SCHEMA = """
CREATE TABLE IF NOT EXISTS learner_context (
  bucket TEXT PRIMARY KEY, alpha REAL NOT NULL, beta REAL NOT NULL, n_obs INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT);
CREATE TABLE IF NOT EXISTS learner_context_updates (
  instruction_id TEXT PRIMARY KEY, bucket TEXT NOT NULL, reward INTEGER NOT NULL, applied_at TEXT);
CREATE INDEX IF NOT EXISTS ix_touch_outcomes_borrower ON touch_outcomes (borrower_id, executed_at);
"""

_EXECUTED = ("SENT", "DELIVERED", "UNCERTAIN")


class BorrowerContextModel:
    def __init__(self, ledger, prior_mean: float = 0.03, prior_strength: float = 30.0, recency: str = "count"):
        if not (0 < prior_mean < 1 and prior_strength > 0):
            raise ValueError("prior_mean must be in (0,1) and prior_strength > 0")
        if recency not in RECENCY_MODES:
            raise ValueError(f"recency must be one of {RECENCY_MODES}")
        self.recency = recency
        self.buckets = BUCKETS if recency == "count" else GAP_BUCKETS
        self.ledger = ledger
        self.prior = (prior_mean * prior_strength, (1 - prior_mean) * prior_strength)
        self.ledger._conn().executescript(OUTCOMES_SCHEMA)  # touch_outcomes must exist for the index
        self.ledger._conn().executescript(SCHEMA)

    # ---------- history → bucket ----------
    def bucket_for(self, borrower_id: str, at: datetime) -> str:
        c = self.ledger._conn()
        at_iso = self.ledger._iso(at)
        week_iso = self.ledger._iso(at - timedelta(days=7))
        recent = c.execute(
            f"SELECT COUNT(*) FROM touches WHERE borrower_id=? AND status IN ({','.join('?' * len(_EXECUTED))})"
            " AND scheduled_at < ? AND scheduled_at >= ?", (borrower_id, *_EXECUTED, at_iso, week_iso)).fetchone()[0]
        row = c.execute(
            "SELECT SUM(CASE WHEN status='REPLY' AND reply_at < ? THEN 1 ELSE 0 END) AS replies,"
            " SUM(CASE WHEN status='NO_REPLY' AND finalized_at <= ? THEN 1 ELSE 0 END) AS misses,"
            " COUNT(*) AS executed FROM touch_outcomes WHERE borrower_id=? AND executed_at < ?",
            (at_iso, at_iso, borrower_id, at_iso)).fetchone()
        replies, misses, executed = (row[0] or 0), (row[1] or 0), (row[2] or 0)
        if replies:
            eng = "replied"
        elif executed == 0:
            eng = "new"
        elif misses >= 2:
            eng = "miss2+"
        elif misses == 1:
            eng = "miss1"
        else:  # executed but not finalized yet: treat as new until evidence arrives
            eng = "new"
        if self.recency == "gap":
            return f"{eng}|{self._gap_bucket(borrower_id, at)}"
        rec = "r0" if recent == 0 else ("r1" if recent == 1 else "r2+")
        return f"{eng}|{rec}"

    def _gap_bucket(self, borrower_id: str, at: datetime) -> str:
        last = self.ledger._conn().execute(
            f"SELECT MAX(scheduled_at) FROM touches WHERE borrower_id=? AND status IN ({','.join('?' * len(_EXECUTED))})"
            " AND scheduled_at < ?", (borrower_id, *_EXECUTED, self.ledger._iso(at))).fetchone()[0]
        if last is None:
            return "g-"
        gap = (self.ledger.policy.local(at).date() - datetime.fromisoformat(last).date()).days
        return "g2" if gap <= 2 else "g3" if gap == 3 else "g4-6" if gap <= 6 else "g7+"

    # ---------- posteriors ----------
    def _counts(self) -> dict[str, tuple[int, int]]:
        """(replies, no-replies) observed per bucket; stored as alpha/beta increments over the prior."""
        a0, b0 = self.prior
        rows = {r["bucket"]: (r["alpha"] - a0, r["beta"] - b0)
                for r in self.ledger._conn().execute("SELECT * FROM learner_context")}
        return {b: rows.get(b, (0.0, 0.0)) for b in self.buckets}

    def pooled_rate(self) -> float:
        """Pooled observed reply rate across buckets (prior-smoothed)."""
        a0, b0 = self.prior
        counts = self._counts().values()
        succ, fail = sum(s for s, _ in counts), sum(f for _, f in counts)
        return (a0 + succ) / (a0 + b0 + succ + fail)

    def multipliers(self) -> dict[str, float]:
        """Hierarchical shrinkage: each bucket's Beta posterior is centred on the POOLED observed reply
        rate with strength `prior_strength`, so an unseen bucket is exactly 1.0× and a bucket only
        moves away from the pool as its own evidence accumulates."""
        counts = self._counts()
        a0, b0 = self.prior
        succ = sum(s for s, _ in counts.values())
        fail = sum(f for _, f in counts.values())
        pooled = (a0 + succ) / (a0 + b0 + succ + fail)
        strength = a0 + b0
        out = {}
        for b, (s, f) in counts.items():
            mean = (pooled * strength + s) / (strength + s + f)
            out[b] = mean / pooled
        return out

    def multiplier(self, bucket: str) -> float:
        return self.multipliers()[bucket]

    def apply_outcome(self, outcome: Outcome) -> bool:
        reward = outcome.reward
        if reward is None:
            return False
        bucket = self.bucket_for(outcome.borrower_id, outcome.executed_at)
        now = self.ledger._iso(self.ledger.policy.now())
        a0, b0 = self.prior
        with self.ledger.tx() as c:
            cur = c.execute("INSERT OR IGNORE INTO learner_context_updates VALUES (?,?,?,?)",
                            (outcome.instruction_id, bucket, int(reward), now))
            if cur.rowcount == 0:
                return False
            c.execute("INSERT INTO learner_context (bucket, alpha, beta, n_obs, updated_at) VALUES (?,?,?,0,?)"
                      " ON CONFLICT(bucket) DO NOTHING", (bucket, a0, b0, now))
            c.execute("UPDATE learner_context SET alpha = alpha + ?, beta = beta + ?, n_obs = n_obs + 1,"
                      " updated_at = ? WHERE bucket = ?", (reward, 1 - reward, now, bucket))
        return True

    def estimates(self) -> list[dict[str, Any]]:
        m = self.multipliers()
        return [{"bucket": b, "replies": int(s), "no_replies": int(f), "n_obs": int(s + f),
                 "observed_rate": round(s / (s + f), 4) if s + f else None, "multiplier": round(m[b], 3)}
                for b, (s, f) in self._counts().items()]


class ContextualLearner:
    """contracts.Learner: BetaBernoulliLearner (which arm) × BorrowerContextModel (whether / how much).

    The chosen arm and its replies-per-rupee are rescored with the borrower's multiplier, so the
    reply floor (min_reply_prob) turns into a per-borrower WAIT, and the score used to allocate a
    binding budget ranks likelier repliers first. Thompson sampling stays where it was: on the arm
    estimate, only in SANDBOX/SIMULATOR.

    affinity_strength=k (optional) adds a per-borrower x channel factor, a hierarchical estimate shrunk
    to the borrower's own overall rate: a(b, c) = [(r_bc + k*rho) / (n_bc + k)] / [(R_b + k*rho) / (N_b + k)]
    from the borrower's finalized outcomes (r replies of n touches on channel c; R of N on any channel;
    rho = pooled reply rate). It only moves channels relative to each other, since the engagement
    bucket already carries the borrower-level level; with no history it is exactly 1.0.

    value_floor_rel=phi (optional) is a marginal-value spending rule: once `value_floor_warmup` touches
    are finalized, a candidate whose borrower-adjusted replies per rupee is below phi x the campaign's
    realized replies per rupee so far (finalized replies / their touches' cost, from the ledger) WAITs,
    and its money stays in the pool (K01 pacing carries it forward). Scale-free: no absolute
    threshold to tune per portfolio."""

    def __init__(self, base: BetaBernoulliLearner, context: BorrowerContextModel,
                 affinity_strength: Optional[float] = None, value_floor_rel: Optional[float] = None,
                 value_floor_warmup: int = 200):
        if affinity_strength is not None and affinity_strength <= 0:
            raise ValueError("affinity_strength must be > 0")
        if value_floor_rel is not None and value_floor_rel < 0:
            raise ValueError("value_floor_rel must be >= 0")
        self.value_floor_rel = value_floor_rel
        self.value_floor_warmup = int(value_floor_warmup)
        self._realized_cache: dict = {}
        self.base = base
        self.context = context
        self.affinity_strength = affinity_strength
        self.min_reply_prob = base.min_reply_prob
        self.ledger = base.ledger

    @property
    def model_version(self) -> str:
        n = self.ledger._conn().execute("SELECT COUNT(*) FROM learner_context_updates").fetchone()[0]
        return f"{self.base.model_version}+{CONTEXT_VERSION}@{n}"

    @property
    def n_updates(self) -> int:
        return self.base.n_updates

    def channel_affinity(self, borrower_id: str, at: datetime) -> dict[str, float]:
        """channel -> a(b, c) for channels with finalized history before `at` (others are 1.0)."""
        if self.affinity_strength is None or not borrower_id:
            return {}
        at_iso = self.ledger._iso(at)
        rows = self.ledger._conn().execute(
            "SELECT channel, SUM(CASE WHEN status='REPLY' AND reply_at < ? THEN 1 ELSE 0 END) r,"
            " SUM(CASE WHEN (status='REPLY' AND reply_at < ?) OR (status='NO_REPLY' AND finalized_at <= ?)"
            " THEN 1 ELSE 0 END) n FROM touch_outcomes WHERE borrower_id=? AND executed_at < ? GROUP BY channel",
            (at_iso, at_iso, at_iso, borrower_id, at_iso)).fetchall()
        hist = {r["channel"]: (r["r"] or 0, r["n"] or 0) for r in rows if (r["n"] or 0) > 0}
        if not hist:
            return {}
        k, rho = self.affinity_strength, self.context.pooled_rate()
        big_r, big_n = sum(r for r, _ in hist.values()), sum(n for _, n in hist.values())
        overall = (big_r + k * rho) / (big_n + k)
        return {ch: ((r + k * rho) / (n + k)) / overall for ch, (r, n) in hist.items()}

    def realized_replies_per_inr(self, at: datetime) -> tuple[Optional[float], int]:
        """(finalized replies / INR spent on those touches, n finalized) before `at`, from the ledger."""
        at_iso = self.ledger._iso(at)
        hit = self._realized_cache.get(at_iso)
        if hit is not None:
            return hit
        row = self.ledger._conn().execute(
            "SELECT COUNT(*) n, SUM(CASE WHEN o.status='REPLY' THEN 1 ELSE 0 END) r, SUM(t.est_cost_paise) cost"
            " FROM touch_outcomes o JOIN touches t ON t.instruction_id = o.instruction_id"
            " WHERE o.status IN ('REPLY', 'NO_REPLY') AND o.finalized_at <= ?", (at_iso,)).fetchone()
        n, r, cost = row["n"] or 0, row["r"] or 0, row["cost"] or 0
        out = ((r / (cost / 100)) if cost > 0 else None, n)
        self._realized_cache = {at_iso: out}
        return out

    def value_floor(self, at: datetime) -> float:
        floor = getattr(self.base, "min_replies_per_inr", 0.0)
        if self.value_floor_rel is not None:
            realized, n = self.realized_replies_per_inr(at)
            if realized is not None and n >= self.value_floor_warmup:
                floor = max(floor, self.value_floor_rel * realized)
        return floor

    def rank(self, band: str, candidates: Sequence[Candidate], mode: Mode, rng: Optional[random.Random] = None,
             borrower_id: Optional[str] = None, persist: bool = True, at: Optional[datetime] = None) -> Ranking:
        base = self.base.rank(band, candidates, mode, rng=rng, borrower_id=borrower_id, persist=False, at=at)
        when = at or min((c.at for c in candidates), default=None) or self.ledger.policy.now()
        bucket = self.context.bucket_for(borrower_id, when) if borrower_id else "new|r0"
        m0 = self.context.multiplier(bucket)
        aff = self.channel_affinity(borrower_id, when)
        floor_rpi = self.value_floor(when)
        scores = []
        for s in base.scores:
            a = aff.get(s.channel, 1.0)
            m = m0 * a
            p = s.p_used * m
            rpi = replies_per_rupee(p, s.est_cost_paise)
            # the base arm must clear the reply floor on its own (as before); the borrower-adjusted p
            # must clear it too, and the value floor applies to the borrower-adjusted replies per rupee
            q = (s.channel in CHANNELS and s.slot in SLOTS and s.est_cost_paise > 0
                 and s.p_used >= self.min_reply_prob and p >= self.min_reply_prob and rpi >= floor_rpi)
            note = f"; × borrower {bucket} {m0:.2f}" + (f" × {s.channel} affinity {a:.2f}" if s.channel in aff else "")
            scores.append(replace(s, p_used=p, posterior_mean=s.posterior_mean * m,
                                  sampled_p=None if s.sampled_p is None else s.sampled_p * m,
                                  replies_per_inr=rpi, qualifies=bool(q), reason=f"{s.reason}{note}"))
        version = self.model_version
        qual = [(i, s) for i, s in enumerate(scores) if s.qualifies]
        if not qual:
            ranking = Ranking("WAIT", None, scores, version, Mode(mode),
                              f"WAIT: borrower {bucket} (×{m0:.2f}) leaves no arm above {self.min_reply_prob}")
        else:
            _, best = min(qual, key=lambda t: (-t[1].replies_per_inr, t[0]))
            ranking = Ranking("SEND", best, scores, version, Mode(mode),
                              f"SEND {best.channel} {best.slot}: {best.replies_per_inr:.4f} replies/INR "
                              f"(arm × borrower {bucket} {m0:.2f})")
        if persist:
            ranking.decision_id = self.base._persist(ranking, band, borrower_id, at, candidates)
        return ranking

    def apply_outcome(self, outcome: Outcome) -> bool:
        ctx = self.context.apply_outcome(outcome)   # bucket computed before the base update
        arm = self.base.apply_outcome(outcome)
        return arm or ctx

    def estimates(self, band: Optional[str] = None) -> list[dict[str, Any]]:
        return self.base.estimates(band)

    @property
    def min_segment_obs(self):
        return getattr(self.base, "min_segment_obs", 30)
