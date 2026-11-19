"""K21 scorecard (kairos_backend/scorecard.py): focused behaviour beyond Ojas's 20 CSV rows."""
import pytest

from kairos_backend.scorecard import ScoreInputs, Scorecard


@pytest.fixture(scope="module")
def sc():
    return Scorecard()


def test_reads_every_number_from_scorecard_json(sc):
    assert sc.version == sc.data["policy_version"] and sc.data["base_score"] == 50
    assert sc.score(ScoreInputs(band="B1")).horizon_days == sc.data["horizon_days"]


def test_technical_bounce_persuadable_has_top_reasons(sc):
    r = sc.score(ScoreInputs(band="B2", ontime_emis_6m=2, broken_ptp=1, link_clicked_7d=True, intent="SILENT",
                             nach_bounce_reason="TECHNICAL", aa_net_cash_flow="POSITIVE", aa_dti=0.30, aa_risk_flags=0))
    assert (r.score, r.intensity) == (59, "HIGH")
    assert set(r.top_reasons[:2]) == {"bounce:TECHNICAL +12", "broken_ptp -12"}
    assert r.evidence_strength == 1.0


def test_low_evidence_caps_high_to_standard(sc):
    r = sc.score(ScoreInputs(band="B1", ontime_emis_6m=3, intent="SILENT"))
    assert r.band_intensity == "HIGH" and r.intensity == "STANDARD" and r.evidence_strength == 0.5


def test_override_forces_wait_but_score_is_kept(sc):
    r = sc.score(ScoreInputs(band="B1", ontime_emis_6m=3, intent="SILENT", override="PTP_HOLD"))
    assert r.intensity == "WAIT" and r.score == 55 and r.override == "PTP_HOLD"
    with pytest.raises(ValueError):
        sc.score(ScoreInputs(band="B1", override="MADE_UP"))


def test_sms_delivery_is_worth_zero_and_is_not_evidence(sc):
    a = sc.score(ScoreInputs(band="B1", ontime_emis_6m=3))
    b = sc.score(ScoreInputs(band="B1", ontime_emis_6m=3, sms_delivered_7d=True))
    assert (a.score, a.evidence_strength) == (b.score, b.evidence_strength)


def test_missing_aa_is_neutral(sc):
    a = sc.score(ScoreInputs(band="B2", ontime_emis_6m=3))
    b = sc.score(ScoreInputs(band="B2", ontime_emis_6m=3, aa_risk_flags=0))
    assert a.score == b.score


def test_allowance_is_strictest_of_intensity_and_caps(sc):
    assert sc.intensity_allowance("STANDARD", 3) == 2 and sc.intensity_allowance("HIGH", 6) == 6
    assert sc.final_allowance("HIGH", k01_cap=6, lender_cap=4) == 4
    assert sc.final_allowance("STANDARD", k01_cap=9, official_cap=3) == 3
    assert sc.final_allowance("WAIT", k01_cap=12) == 0 and sc.final_allowance("LOW", k01_cap=12) == 1


def test_channels_per_intensity():
    cost = {"email": 0.05, "sms": 0.20, "whatsapp": 0.40, "ivr": 1.50, "voicebot": 3.00}
    cons = ["sms", "whatsapp", "email", "ivr", "voicebot"]
    assert Scorecard.channels("LOW", cons, cost, reach_evidence=["whatsapp", "ivr"]) == ["whatsapp"]
    assert Scorecard.channels("LOW", cons, cost) == ["email"]
    assert "voicebot" not in Scorecard.channels("STANDARD", cons, cost) and "ivr" in Scorecard.channels("STANDARD", cons, cost)
    assert Scorecard.channels("HIGH", cons, cost)[-1] == "voicebot"
    assert Scorecard.channels("WAIT", cons, cost) == []
