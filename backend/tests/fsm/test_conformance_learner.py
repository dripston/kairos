"""k09.learner.arm conformance: where each arm's estimate comes from moves only along declared transitions.

Seeded random FINAL / non-final outcomes (with duplicates) fed to the real BetaBernoulliLearner; after each
one, every arm's `source` (prior / pooled / segment) must equal the machine state the harness tracks.
"""
from __future__ import annotations

import itertools
import random
from datetime import timedelta

import pytest

from kairos_backend.contracts import Outcome
from kairos_backend.learner import BetaBernoulliLearner
from kairos_backend.machines import LEARNER_ARM
from tests.fsm.world import Coverage, at, new_ledger

BANDS, CHS, SLOTS = ("B1", "B2"), ("sms", "whatsapp"), ("08-11", "11-14")
ARMS = list(itertools.product(BANDS, CHS, SLOTS))
MIN_SEG = 3


def run_seed(policy, path, seed):
    rng = random.Random(seed)
    lg = new_ledger(policy, path)
    lr = BetaBernoulliLearner(lg, min_segment_obs=MIN_SEG)
    state = {a: "PRIOR" for a in ARMS}
    seg_n = {a: 0 for a in ARMS}
    seen, cov = [], Coverage()
    for i in range(120):
        if seen and rng.random() < 0.15:
            iid, arm, status = rng.choice(seen)  # the same instruction again
            dup = status in ("REPLY", "NO_REPLY")
        else:
            iid, arm, status = f"L{i}", rng.choice(ARMS), rng.choice(["REPLY", "NO_REPLY", "NO_REPLY", "UNRESOLVED", "PENDING"])
            seen.append((iid, arm, status))
            dup = False
        b, ch, sl = arm
        t = at(10) + timedelta(hours=i)
        applied = lr.apply_outcome(Outcome(iid, "X", b, ch, sl, t, t + timedelta(hours=24), status))
        learnable = status in ("REPLY", "NO_REPLY") and not dup
        assert applied == learnable
        for a in ARMS:
            if not learnable:
                ev, ctx = ("DUPLICATE_OUTCOME" if dup else "NOT_LEARNABLE"), {}
            elif a == arm:
                ev, ctx = "OUTCOME_THIS_BAND", {"segment_full": seg_n[a] + 1 >= MIN_SEG}
            elif a[1:] == arm[1:]:
                ev, ctx = "OUTCOME_OTHER_BAND", {}
            else:
                continue
            new, tr = LEARNER_ARM.step(state[a], ev, ctx)
            cov.add(tr)
            state[a] = new
        if learnable:
            seg_n[arm] += 1
        for a in ARMS:
            src = lr._arm(lr._posteriors(a[0]), a[1], a[2])[3]
            assert src.upper() == state[a], (a, src, state[a])
    lg.close()
    return cov


@pytest.mark.parametrize("seed", range(15))
def test_learner_arm_sources_follow_the_declared_lifecycle(policy, tmp_path, seed):
    run_seed(policy, tmp_path / "l.db", seed)


def test_every_learner_arm_transition_is_exercised(policy, tmp_path):
    cov = Coverage()
    for seed in range(15):
        cov.fired.update(run_seed(policy, tmp_path / f"l{seed}.db", seed).fired)
    # with MIN_SEG > 1 a fresh arm can never fill its segment in one outcome
    assert cov.missing(LEARNER_ARM, exempt=[("PRIOR", "OUTCOME_THIS_BAND", "SEGMENT", "segment_full")]) == []
