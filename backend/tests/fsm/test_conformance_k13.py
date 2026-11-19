"""k13.exception conformance: exceptions.handle() is exactly the declared decision table."""
from __future__ import annotations

import random

import pytest

from kairos_backend.exceptions import EVENTS, ExceptionEvent, handle
from kairos_backend.machines import k13_exception_machine
from tests.fsm.world import Coverage

SUPERVISOR_VARIANTS = {"CAPTURE_PTP_SUPERVISOR": "CAPTURE_PTP", "HOLD_UNTIL_DATE_SUPERVISOR": "HOLD_UNTIL_DATE"}


def random_event(rng: random.Random, policy) -> ExceptionEvent:
    return ExceptionEvent(event=rng.choice(sorted(EVENTS)), band=rng.choice(list(policy.exceptions["needs_time"]["max_rebuttals_by_band"])),
                          in_window=rng.random() < 0.7, rebuttals_used=rng.randrange(0, 9),
                          date_offset_days=None if rng.random() < 0.3 else rng.randrange(-1, 14),
                          broken_ptp_count=rng.choice([0, 0, 1, 2, 3]), paid_now=rng.random() < 0.4,
                          callback_attempts=rng.randrange(0, 4), callback_answered=rng.random() < 0.4)


def check(m, policy, e: ExceptionEvent):
    state, t = m.step("RECEIVED", e.event, vars(e))
    out = handle(policy, e)
    assert SUPERVISOR_VARIANTS.get(state, state) == out.action, (e, state, out)
    assert t.action == f"hold={out.hold}; queue={out.queue or '-'}", (e, t, out)
    return t


@pytest.mark.parametrize("seed", range(10))
def test_handle_matches_the_declared_table(policy, seed):
    m = k13_exception_machine(policy)
    rng = random.Random(seed)
    for _ in range(500):
        check(m, policy, random_event(rng, policy))


def test_every_k13_row_is_exercised_and_events_are_closed(policy):
    m = k13_exception_machine(policy)
    assert set(m.events) == set(EVENTS)  # the machine's alphabet IS the K13 event list
    cov = Coverage()
    for seed in range(10):
        rng = random.Random(seed)
        for _ in range(500):
            cov.add(check(m, policy, random_event(rng, policy)))
    assert cov.missing(m) == []
