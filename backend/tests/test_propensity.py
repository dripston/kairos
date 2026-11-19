from __future__ import annotations

from datetime import datetime, timedelta
from itertools import product

import pytest

from kairos_backend.propensity import (
    MODEL, P_MAX, P_MIN, InMemoryShadowLogger, NoopShadowLogger, propensity_features, rule_propensity,
)


def band_of(dpd):
    return "PRE_DUE" if dpd <= 0 else "B1" if dpd <= 30 else "B2" if dpd <= 60 else "B3" if dpd <= 90 else "B4"


INTENTS = [None, "PTP", "CLAIMS_PAID", "NEEDS_TIME", "HARDSHIP", "STOP", "CALLBACK", "DISPUTE", "WRONG_PERSON", "OTHER"]


@pytest.mark.parametrize("broken,intent,replied,nach,horizon", list(product(
    [0, 1, 3, 5], INTENTS, [False, True], [None, True, False], [1, 7, 30])))
def test_monotonic_non_increasing_in_dpd_and_bounded(broken, intent, replied, nach, horizon):
    prev = None
    for dpd in range(-6, 400):
        r = rule_propensity(dpd, band_of(dpd), broken, intent, replied, nach, horizon)
        assert P_MIN <= r["p"] <= P_MAX
        if prev is not None:
            assert r["p"] <= prev, (dpd, r["p"], prev)
        prev = r["p"]


def test_shape_and_label():
    r = rule_propensity(12, "B1")
    assert set(r) == {"p", "horizon_days", "intensity", "reasons", "model"}
    assert r["model"] == MODEL == "rule-based-demo" and r["horizon_days"] == 7
    assert "demo rule" in r["reasons"][0]
    assert rule_propensity(12, "B1") == r  # deterministic


@pytest.mark.parametrize("intent", ["NEEDS_TIME", "HARDSHIP", "STOP", "CALLBACK"])
def test_pause_intents_override_to_wait(intent):
    for dpd in (-3, 5, 45, 120):
        r = rule_propensity(dpd, band_of(dpd), last_intent=intent, replied_recently=True, nach_mandate=True)
        assert r["intensity"] == "WAIT"


def test_intensity_low_vs_standard():
    assert rule_propensity(-2, "PRE_DUE", nach_mandate=True)["intensity"] == "LOW"
    assert rule_propensity(75, "B3", broken_ptp=2)["intensity"] == "STANDARD"


def test_signals_move_p_in_the_documented_direction():
    base = rule_propensity(20, "B1")["p"]
    assert rule_propensity(20, "B1", broken_ptp=2)["p"] < base
    assert rule_propensity(20, "B1", last_intent="PTP")["p"] > base
    assert rule_propensity(20, "B1", replied_recently=True)["p"] > base
    assert rule_propensity(20, "B1", nach_mandate=True)["p"] > base
    assert rule_propensity(20, "B1", nach_mandate=False)["p"] < base
    assert rule_propensity(20, "B1", horizon_days=30)["p"] > base > rule_propensity(20, "B1", horizon_days=1)["p"]


def test_bounds_at_extremes():
    assert rule_propensity(-6, "PRE_DUE", 0, "CLAIMS_PAID", True, True, 90)["p"] == P_MAX
    assert rule_propensity(900, "B4", 9, "WRONG_PERSON", False, False, 1)["p"] == P_MIN


def test_bad_inputs():
    with pytest.raises(ValueError):
        rule_propensity(10, "B9")
    with pytest.raises(ValueError):
        rule_propensity(10, "B1", horizon_days=0)


def test_shadow_logger_labels_only_verified_and_matured():
    log = InMemoryShadowLogger()
    t0 = datetime(2026, 11, 19, 10)
    f = propensity_features(12, "B1", horizon_days=7)
    log.log_score("d1", "b1", t0, f, rule_propensity(**f)["p"])
    log.log_score("d1", "b1", t0, f, 0.99)  # idempotent
    log.log_score("d2", "b2", t0, f, 0.4)
    log.log_score("d3", "b3", t0, f, 0.4)
    assert log.matured_examples() == []
    assert log.record_verified_payment("b1", t0 + timedelta(days=3)) == 1
    assert log.record_verified_payment("b3", t0 + timedelta(days=8)) == 0  # outside horizon
    assert log.mature(t0 + timedelta(days=6)) == 0
    assert log.mature(t0 + timedelta(days=7)) == 2
    ex = sorted(log.matured_examples(), key=lambda e: e[1])
    assert [lbl for _, lbl in ex] == [0, 0, 1] and ex[0][0]["dpd"] == 12
    assert log.rows["d1"].p_rule != 0.99


def test_noop_logger():
    n = NoopShadowLogger()
    n.log_score("d", "b", datetime(2026, 1, 1), {}, 0.5)
    assert n.record_verified_payment("b", datetime(2026, 1, 2)) == 0 and n.mature(datetime(2027, 1, 1)) == 0
    assert n.matured_examples() == []
