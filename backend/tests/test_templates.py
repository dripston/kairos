"""K22 acceptance: return an approved template ID, or WAIT when no approved match exists."""
from __future__ import annotations

import copy
import json
import shutil
from datetime import date, datetime, timedelta

import pytest

from tests.spine_helpers import add_loan

from kairos_backend.ledger import Instruction, Ledger
from kairos_backend.policy import policy_dir
from kairos_backend.templates import (
    INTENT_TO_PURPOSE, CatalogError, TemplateCatalog, default_catalog_path, purpose_for_intent,
)

CHANNELS = ["sms", "whatsapp", "email", "ivr", "voicebot"]
ALL_BANDS = ["PRE_DUE", "B1", "B2", "B3", "B4"]


@pytest.fixture(scope="module")
def catalog(policy):
    return TemplateCatalog.load(policy=policy)


def tpl(id, channel="sms", lang="en", purpose="REMINDER", intensity="LOW", bands=None, approved=True, **kw):
    return {"id": id, "channel": channel, "lang": lang, "purpose": purpose, "intensity": intensity,
            "dpd_bands": bands or ALL_BANDS, "approved": approved, "version": 1,
            "provider_template_name": f"plh_{id.lower()}", "variables": ["borrower_first_name"], **kw}


def mini(policy, *templates):
    return TemplateCatalog.from_dict({"catalog_version": "test", "templates": list(templates)}, policy=policy)


# ---------- the shipped placeholder catalog ----------
def test_shipped_catalog_loads_and_is_marked_placeholder(catalog):
    assert catalog.is_placeholder and catalog.version.endswith("placeholder")
    assert catalog.source == str(default_catalog_path())
    assert any(not t.approved for t in catalog), "catalog must carry an unapproved template to prove the filter"


@pytest.mark.parametrize("channel", CHANNELS)
@pytest.mark.parametrize("lang", ["en", "hi"])
@pytest.mark.parametrize("intensity", ["LOW", "STANDARD"])
def test_reminder_covered_on_every_channel_language_intensity(catalog, channel, lang, intensity):
    c = catalog.select(channel, lang, "REMINDER", intensity, "B2")
    t = catalog.get(c.template_id)
    assert t.approved and (t.channel, t.lang, t.purpose) == (channel, lang, "REMINDER")
    assert c.catalog_version == catalog.version
    if channel == "voicebot" and intensity == "STANDARD":
        # Stated gap: the Sarvam reminder agent (K17) only ever states late charges (T2-level),
        # so there is no STANDARD voicebot reminder; a STANDARD request de-escalates.
        assert (c.decision, c.intensity, c.reason) == ("SEND", "LOW", "DE_ESCALATED")
    else:
        assert c.send and c.intensity == intensity and c.reason == "MATCH"


K13_PURPOSES = ["ASK_PAYMENT_DATE", "PTP_CONFIRM", "PAYMENT_RECEIVED", "CALLBACK_ACK", "HARDSHIP_ACK", "STOP_CONFIRM"]


@pytest.mark.parametrize("purpose", K13_PURPOSES)
@pytest.mark.parametrize("channel", ["sms", "whatsapp", "email"])
@pytest.mark.parametrize("lang", ["en", "hi"])
@pytest.mark.parametrize("band", ALL_BANDS)
def test_k13_purposes_covered_on_whatsapp_sms_email(catalog, purpose, channel, lang, band):
    c = catalog.select(channel, lang, purpose, "LOW", band, payment_verified=True)
    assert c.send and catalog.get(c.template_id).purpose == purpose


def test_reuses_frontend_template_ids(catalog):
    assert catalog.select("whatsapp", "hi", "REMINDER", "LOW", "B1").template_id == "WA_REMIND_HI_SOFT_02"
    assert catalog.select("sms", "hi", "REMINDER", "LOW", "B1").template_id == "SMS_REMIND_HI_01"


# ---------- approved only ----------
def test_unapproved_never_selected_even_when_it_wins_tie_break(catalog):
    assert not catalog.get("WA_DUE_EN_00").approved  # sorts before WA_PAYLINK_EN_01
    c = catalog.select("whatsapp", "en", "REMINDER", "STANDARD", "B1")
    assert c.template_id == "WA_PAYLINK_EN_01"


def test_unapproved_never_selected_anywhere(catalog):
    unapproved = {t.id for t in catalog if not t.approved}
    for ch in CHANNELS:
        for lang in ("en", "hi"):
            for p in INTENT_TO_PURPOSE.values():
                for level in ("LOW", "STANDARD"):
                    for band in ALL_BANDS:
                        assert catalog.select(ch, lang, p, level, band).template_id not in unapproved


def test_only_unapproved_match_waits(policy):
    cat = mini(policy, tpl("EM_ASKDATE_EN_01", channel="email", purpose="ASK_PAYMENT_DATE", approved=False))
    c = cat.select("email", "en", "ASK_PAYMENT_DATE", "LOW", "B1")
    assert (c.decision, c.template_id, c.reason) == ("WAIT", None, "NO_APPROVED_TEMPLATE")


# ---------- language ----------
@pytest.mark.parametrize("lang", ["mr", "ta", "", None])
def test_unsupported_language_waits(catalog, lang):
    c = catalog.select("whatsapp", lang, "REMINDER", "LOW", "B1")
    assert (c.decision, c.reason, c.template_id) == ("WAIT", "LANGUAGE_NOT_SUPPORTED", None)


def test_no_fallback_to_other_language(policy):
    cat = mini(policy, tpl("SMS_REMIND_EN_01", lang="en"))
    assert cat.select("sms", "en", "REMINDER", "LOW", "B1").send
    c = cat.select("sms", "hi", "REMINDER", "LOW", "B1")
    assert (c.decision, c.reason) == ("WAIT", "NO_APPROVED_TEMPLATE")


def test_language_code_is_case_insensitive(catalog):
    assert catalog.select("SMS", "HI", "REMINDER", "low", "B1").template_id == "SMS_REMIND_HI_01"


# ---------- intensity ----------
def test_intensity_wait_waits(catalog):
    c = catalog.select("whatsapp", "hi", "REMINDER", "WAIT", "B1")
    assert (c.decision, c.reason, c.template_id) == ("WAIT", "INTENSITY_WAIT", None)


def test_low_never_escalates_to_standard(policy):
    cat = mini(policy, tpl("SMS_DUE_EN_01", intensity="STANDARD"))
    c = cat.select("sms", "en", "REMINDER", "LOW", "B1")
    assert (c.decision, c.reason) == ("WAIT", "NO_APPROVED_TEMPLATE")


def test_low_request_picks_low_even_when_standard_exists(catalog):
    c = catalog.select("sms", "en", "REMINDER", "LOW", "B3")
    assert (c.template_id, c.intensity) == ("SMS_REMIND_EN_01", "LOW")


def test_standard_prefers_exact_intensity(catalog):
    c = catalog.select("sms", "hi", "REMINDER", "STANDARD", "B3")
    assert (c.template_id, c.intensity, c.reason) == ("SMS_DUE_HI_02", "STANDARD", "MATCH")


def test_standard_de_escalates_to_low_when_no_standard(catalog):
    # no STANDARD hardship ack exists (K13: neutral, no collection ask)
    c = catalog.select("whatsapp", "en", "HARDSHIP_ACK", "STANDARD", "B2")
    assert c.send and (c.intensity, c.requested_intensity, c.reason) == ("LOW", "STANDARD", "DE_ESCALATED")


# ---------- band ----------
def test_band_filter_pre_due_has_no_standard_reminder(catalog):
    """Pre-due only has LOW PRE_DUE_REMINDER templates: a STANDARD request de-escalates to LOW,
    and channels without a pre-due template (ivr blast, voicebot) WAIT."""
    c = catalog.select("sms", "hi", "REMINDER", "STANDARD", "PRE_DUE")
    assert (c.template_id, c.purpose, c.intensity, c.reason) == ("SMS_PREDUE_HI_01", "PRE_DUE_REMINDER", "LOW", "DE_ESCALATED")
    c = catalog.select("ivr", "hi", "REMINDER", "STANDARD", "PRE_DUE")
    assert (c.decision, c.reason) == ("WAIT", "NO_APPROVED_TEMPLATE")


def test_band_not_listed_waits(policy):
    cat = mini(policy, tpl("SMS_LATE_EN_01", bands=["B3", "B4"]))
    assert cat.select("sms", "en", "REMINDER", "LOW", "B4").send
    c = cat.select("sms", "en", "REMINDER", "LOW", "B1")
    assert (c.decision, c.reason) == ("WAIT", "NO_APPROVED_TEMPLATE")


def test_not_in_scope_band_waits(catalog):
    c = catalog.select("sms", "en", "REMINDER", "LOW", None)
    assert (c.decision, c.reason) == ("WAIT", "NOT_IN_SCOPE")


def test_channel_not_allowed_in_band_waits(policy):
    p = copy.deepcopy(policy)
    p.raw["channels_allowed_by_band"]["B1"] = ["sms"]
    cat = TemplateCatalog.load(policy=p)
    assert cat.select("sms", "en", "REMINDER", "LOW", "B1").send
    assert cat.select("ivr", "en", "REMINDER", "LOW", "B1").reason == "CHANNEL_NOT_ALLOWED"


# ---------- determinism ----------
def test_tie_break_by_lowest_id_regardless_of_file_order(policy):
    a = tpl("SMS_REMIND_EN_02")
    b = tpl("SMS_REMIND_EN_01")
    c = tpl("SMS_REMIND_EN_03")
    for order in ([a, b, c], [c, a, b], [b, c, a]):
        assert mini(policy, *order).select("sms", "en", "REMINDER", "LOW", "B1").template_id == "SMS_REMIND_EN_01"


def test_same_input_same_output(catalog):
    first = catalog.select("whatsapp", "hi", "PTP_CONFIRM", "STANDARD", "B2").as_dict()
    assert all(catalog.select("whatsapp", "hi", "PTP_CONFIRM", "STANDARD", "B2").as_dict() == first for _ in range(20))


# ---------- intent -> purpose ----------
@pytest.mark.parametrize("intent,purpose", [
    ("NEEDS_TIME", "ASK_PAYMENT_DATE"), ("PTP", "PTP_CONFIRM"), ("PTP_OFFERED", "PTP_CONFIRM"),
    ("CALLBACK", "CALLBACK_ACK"), ("CALLBACK_REQUEST", "CALLBACK_ACK"), ("HARDSHIP", "HARDSHIP_ACK"),
    ("PAID", None), ("payment_verified", "PAYMENT_RECEIVED"),
    (None, "REMINDER"), ("", "REMINDER"), ("NO_REPLY", "REMINDER"), ("DISPUTE", "REMINDER"),
])
def test_intent_to_purpose(intent, purpose):
    assert purpose_for_intent(intent) == purpose


@pytest.mark.parametrize("intent", ["ABUSIVE", "LEGAL_THREAT", "FRAUD_CLAIM", "STOP"])
def test_routed_stop_intents_get_no_template(catalog, intent):
    assert purpose_for_intent(intent) is None
    c = catalog.select("whatsapp", "en", None, "LOW", "B1", intent=intent)
    assert (c.decision, c.reason) == ("WAIT", "INTENT_NOT_AUTOMATED")


def test_paid_claim_never_gets_a_receipt_or_reminder(catalog):
    """A borrower saying "already paid" is not a verified payment (D7/LMS checks first)."""
    for intent in ("PAID", "CLAIMS_PAID", "ALREADY_PAID"):
        c = catalog.select("whatsapp", "en", None, "LOW", "B1", intent=intent)
        assert (c.decision, c.reason) == ("WAIT", "PAYMENT_NOT_VERIFIED")
    c = catalog.select("whatsapp", "en", "PAYMENT_RECEIVED", "LOW", "B1")
    assert (c.decision, c.reason) == ("WAIT", "PAYMENT_NOT_VERIFIED")
    assert catalog.select("whatsapp", "en", None, "LOW", "B1", intent="PAYMENT_VERIFIED").template_id == "WA_PAIDACK_EN_01"
    assert catalog.select("sms", "hi", "PAYMENT_RECEIVED", "LOW", "B3", payment_verified=True).template_id == "SMS_PAIDACK_HI_01"


def test_select_by_intent(catalog):
    c = catalog.select("whatsapp", "hi", None, "LOW", "B1", intent="NEEDS_TIME")
    assert (c.purpose, c.template_id) == ("ASK_PAYMENT_DATE", "WA_ASKDATE_HI_01")


# ---------- per channel ----------
def test_wait_is_per_channel(catalog):
    # the IVR blast is one-way, so there is no IVR date-ask template
    out = catalog.select_for_channels(["whatsapp", "ivr", "sms"], "en", "ASK_PAYMENT_DATE", "LOW", "B1")
    assert list(out) == ["whatsapp", "ivr", "sms"]
    assert out["whatsapp"].template_id == "WA_ASKDATE_EN_01"
    assert out["sms"].template_id == "SMS_ASKDATE_EN_01"
    assert (out["ivr"].decision, out["ivr"].reason) == ("WAIT", "NO_APPROVED_TEMPLATE")


def test_per_channel_intensity_mapping(catalog):
    out = catalog.select_for_channels(["sms", "ivr", "email"], "hi", "REMINDER", {"sms": "STANDARD", "ivr": "WAIT"}, "B2")
    assert (out["sms"].template_id, out["sms"].intensity) == ("SMS_DUE_HI_02", "STANDARD")
    assert out["ivr"].reason == "INTENSITY_WAIT"
    assert out["email"].reason == "INTENSITY_WAIT"  # not permitted -> WAIT


# ---------- caller bugs fail loudly ----------
@pytest.mark.parametrize("kwargs", [
    {"channel": "fax"}, {"intensity": "HIGH"}, {"purpose": "COLLECT_NOW"}, {"band": "B9"},
])
def test_unknown_vocabulary_raises(catalog, kwargs):
    args = {"channel": "sms", "language": "en", "purpose": "REMINDER", "intensity": "LOW", "band": "B1", **kwargs}
    with pytest.raises(ValueError):
        catalog.select(**args)


# ---------- ledger + K01 gate ----------
D = date(2026, 11, 20)  # Friday, a contact day


@pytest.fixture
def ledger(policy, tmp_path):
    # K03 spine: borrower + loan B1-A + EMI B1-A-1 (12 DPD), every channel consented, enrolled in C1.
    lg = Ledger(policy, tmp_path / "k22.db")
    lg.upsert_campaign("C1", budget_per_case_paise=10_000, start_date=D, end_date=D + timedelta(days=10))
    add_loan(lg, "B1", D, dpd=12, consents={ch: 1 for ch in CHANNELS}, campaign="C1")
    lg.start_day(D)  # daily LMS refresh -> DPD is fresh
    yield lg
    lg.close()


def _decide(ledger, channel, template_id):
    return ledger.preview(Instruction("B1", "B1-A-1", "C1", channel, template_id, datetime(2026, 11, 20, 10), 20))


def test_sync_makes_gate_pass_for_selected_template(ledger, catalog, policy):
    band = policy.band_for(12)
    choice = catalog.select("whatsapp", "en", "REMINDER", "STANDARD", band)
    assert _decide(ledger, "whatsapp", choice.template_id).reason == "NO_APPROVED_TEMPLATE"  # not synced yet
    res = catalog.sync_to_ledger(ledger)
    assert res["upserted"] == len(catalog)
    d = _decide(ledger, "whatsapp", choice.template_id)
    assert (d.decision, d.reason) == ("SEND", "ELIGIBLE")


def test_sync_keeps_unapproved_blocked_at_gate(ledger, catalog):
    catalog.sync_to_ledger(ledger)
    d = _decide(ledger, "whatsapp", "WA_DUE_EN_00")
    assert (d.decision, d.reason, d.rule) == ("WAIT", "NO_APPROVED_TEMPLATE", "TEMPLATE_APPROVED")


def test_sync_withdraws_templates_no_longer_in_catalog(ledger, catalog, policy):
    ledger.upsert_template("OLD_SMS_EN_01", "sms", "en", approved=True)
    assert _decide(ledger, "sms", "OLD_SMS_EN_01").decision == "SEND"
    res = catalog.sync_to_ledger(ledger)
    assert res["withdrawn"] == ["OLD_SMS_EN_01"]
    assert _decide(ledger, "sms", "OLD_SMS_EN_01").reason == "NO_APPROVED_TEMPLATE"


def test_sync_reflects_approval_withdrawn_in_catalog(ledger, policy):
    mini(policy, tpl("SMS_REMIND_EN_01")).sync_to_ledger(ledger)
    assert _decide(ledger, "sms", "SMS_REMIND_EN_01").decision == "SEND"
    mini(policy, tpl("SMS_REMIND_EN_01", approved=False)).sync_to_ledger(ledger)
    assert _decide(ledger, "sms", "SMS_REMIND_EN_01").reason == "NO_APPROVED_TEMPLATE"


# ---------- catalog validation ----------
@pytest.mark.parametrize("bad,msg", [
    ({"channel": "fax"}, "not a K01 channel"),
    ({"lang": "Hindi"}, "lowercase language code"),
    ({"purpose": "THREAT"}, "purpose 'THREAT'"),
    ({"intensity": "HIGH"}, "intensity 'HIGH'"),
    ({"dpd_bands": ["B9"]}, "not K01 bands"),
    ({"dpd_bands": []}, "non-empty list"),
    ({"approved": "yes"}, "'approved' must be true or false"),
    ({"version": 0}, "positive integer"),
    ({"provider_template_name": ""}, "provider_template_name"),
    ({"variables": "name"}, "'variables'"),
])
def test_validation_errors_are_clear(policy, bad, msg):
    with pytest.raises(CatalogError) as e:
        mini(policy, tpl("SMS_X_EN_01", **bad))
    assert "SMS_X_EN_01" in str(e.value) and msg in str(e.value)


def test_validation_reports_missing_fields_and_duplicates(policy):
    broken = tpl("SMS_A_EN_01")
    del broken["approved"]
    with pytest.raises(CatalogError) as e:
        mini(policy, broken, tpl("SMS_B_EN_01"), tpl("SMS_B_EN_01"))
    text = str(e.value)
    assert "missing field(s) approved" in text and "duplicate id" in text


def test_validation_top_level(policy):
    with pytest.raises(CatalogError, match="catalog_version"):
        TemplateCatalog.from_dict({"templates": [tpl("SMS_A_EN_01")]}, policy=policy)
    with pytest.raises(CatalogError, match="'templates' must be a non-empty list"):
        TemplateCatalog.from_dict({"catalog_version": "x", "templates": []}, policy=policy)


def test_missing_and_malformed_files(policy, tmp_path):
    with pytest.raises(CatalogError, match="not found"):
        TemplateCatalog.load(tmp_path / "nope.json", policy=policy)
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(CatalogError, match="not valid JSON"):
        TemplateCatalog.load(bad, policy=policy)


def test_respects_kairos_policy_dir(tmp_path, monkeypatch):
    base = tmp_path / "policy"
    shutil.copytree(policy_dir() / "K01-decision-policy", base / "K01-decision-policy")
    (base / "K22-template-catalog").mkdir()
    (base / "K22-template-catalog" / "catalog.json").write_text(
        json.dumps({"catalog_version": "d13-test", "templates": [tpl("SMS_ONLY_EN_01")]}), encoding="utf-8")
    monkeypatch.setenv("KAIROS_POLICY_DIR", str(base))
    cat = TemplateCatalog.load()
    assert cat.version == "d13-test" and not cat.is_placeholder
    assert cat.select("sms", "en", "REMINDER", "LOW", "B1").template_id == "SMS_ONLY_EN_01"


# ---------- CN tone tiers (T1-T4) ----------
TIER_CHANNELS = ["sms", "whatsapp", "email", "ivr"]


@pytest.mark.parametrize("channel", TIER_CHANNELS)
@pytest.mark.parametrize("lang", ["en", "hi"])
@pytest.mark.parametrize("tier,intensity", [(1, "LOW"), (2, "LOW"), (3, "STANDARD"), (4, "STANDARD")])
def test_every_channel_language_has_all_four_tiers(catalog, channel, lang, tier, intensity):
    c = catalog.select(channel, lang, "REMINDER", intensity, "B2", tier=tier)
    assert c.send and c.tier == tier and c.intensity == intensity
    t = catalog.get(c.template_id)
    assert t.tier == tier and t.extra["approval"]["status"] == "APPROVED_FOR_DEMO"


def test_tier_intensity_mapping_is_enforced_on_every_tiered_template(catalog):
    from kairos_backend.templates import TIER_INTENSITY
    tiered = [t for t in catalog if t.tier is not None]
    assert tiered and all(t.purpose == "REMINDER" and t.intensity == TIER_INTENSITY[t.tier] for t in tiered)
    assert TIER_INTENSITY == {1: "LOW", 2: "LOW", 3: "STANDARD", 4: "STANDARD"}


@pytest.mark.parametrize("channel", TIER_CHANNELS)
def test_tier_band_rules(catalog, channel):
    for t in catalog:
        if t.channel == channel and t.purpose == "REMINDER" and t.tier:
            assert "PRE_DUE" not in t.dpd_bands
            if t.tier == 4:
                assert "B1" not in t.dpd_bands and {"B2", "B3", "B4"} <= set(t.dpd_bands)
            else:
                assert {"B1", "B2", "B3", "B4"} <= set(t.dpd_bands)


def test_default_picks_least_intense_tier(catalog):
    assert catalog.select("whatsapp", "en", "REMINDER", "LOW", "B2").tier == 1
    assert catalog.select("whatsapp", "en", "REMINDER", "STANDARD", "B2").tier == 3
    assert catalog.select("email", "hi", "REMINDER", "STANDARD", "B4").template_id == "EM_DUE_HI_01"


def test_t4_is_not_used_in_b1(catalog):
    c = catalog.select("sms", "en", "REMINDER", "STANDARD", "B1", tier=4)
    assert (c.decision, c.reason) == ("WAIT", "NO_APPROVED_TEMPLATE")
    assert catalog.select("sms", "en", "REMINDER", "STANDARD", "B2", tier=4).template_id == "SMS_FINAL_EN_01"
    assert catalog.select("sms", "en", "REMINDER", "STANDARD", "B1", tier=3).template_id == "SMS_DUE_EN_01"


def test_tier_never_exceeds_permitted_intensity(catalog):
    c = catalog.select("whatsapp", "hi", "REMINDER", "LOW", "B3", tier=3)
    assert (c.decision, c.reason) == ("WAIT", "TIER_EXCEEDS_INTENSITY")
    # STANDARD permits a LOW tier (de-escalation)
    c = catalog.select("whatsapp", "hi", "REMINDER", "STANDARD", "B3", tier=2)
    assert (c.template_id, c.reason) == ("WA_LATEFEE_HI_01", "DE_ESCALATED")


@pytest.mark.parametrize("bad", [0, 5, True, "2"])
def test_unknown_tier_raises(catalog, bad):
    with pytest.raises(ValueError):
        catalog.select("sms", "en", "REMINDER", "LOW", "B1", tier=bad)


def test_tier_only_for_reminders(catalog):
    with pytest.raises(ValueError):
        catalog.select("sms", "en", "HARDSHIP_ACK", "LOW", "B1", tier=1)


def test_tier_ladder(catalog):
    assert catalog.tier_ladder("whatsapp", "hi", "B2") == {
        1: "WA_REMIND_HI_SOFT_02", 2: "WA_LATEFEE_HI_01", 3: "WA_PAYLINK_HI_01", 4: "WA_FINAL_HI_01"}
    assert catalog.tier_ladder("ivr", "en", "B1")[4] is None
    assert catalog.tier_ladder("voicebot", "en", "B2") == {1: None, 2: None, 3: None, 4: None}


# ---------- band filtering: pre-due, NACH ----------
@pytest.mark.parametrize("channel,expected", [("sms", "SMS_PREDUE_EN_01"), ("whatsapp", "WA_PREDUE_EN_01"),
                                              ("email", "EM_PREDUE_EN_01")])
def test_pre_due_reminder_served_at_pre_due(catalog, channel, expected):
    c = catalog.select(channel, "en", "REMINDER", "LOW", "PRE_DUE")
    assert (c.template_id, c.purpose) == (expected, "PRE_DUE_REMINDER")


@pytest.mark.parametrize("channel", ["ivr", "voicebot"])
def test_no_pre_due_voice_template(catalog, channel):
    assert catalog.select(channel, "hi", "REMINDER", "LOW", "PRE_DUE").reason == "NO_APPROVED_TEMPLATE"


def test_post_due_reminders_never_list_pre_due_and_pre_due_never_post(catalog):
    for t in catalog:
        if t.purpose == "REMINDER":
            assert "PRE_DUE" not in t.dpd_bands, t.id
        if t.purpose == "PRE_DUE_REMINDER":
            assert list(t.dpd_bands) == ["PRE_DUE"], t.id
    c = catalog.select("sms", "en", "PRE_DUE_REMINDER", "LOW", "B1")
    assert (c.decision, c.reason) == ("WAIT", "NO_APPROVED_TEMPLATE")


@pytest.mark.parametrize("channel", ["sms", "whatsapp"])
@pytest.mark.parametrize("lang", ["en", "hi"])
def test_nach_represent_notice(catalog, channel, lang):
    c = catalog.select(channel, lang, "NACH_REPRESENTMENT", "LOW", "B1")
    assert c.send and "represent_date" in catalog.get(c.template_id).variables
    assert catalog.select(channel, lang, "NACH_REPRESENTMENT", "LOW", "B3").reason == "NO_APPROVED_TEMPLATE"


def test_stop_confirm_is_selected_explicitly_never_from_intent(catalog):
    assert purpose_for_intent("STOP") is None
    c = catalog.select("whatsapp", "hi", "STOP_CONFIRM", "LOW", "B2")
    assert c.template_id == "WA_STOP_HI_01"


def test_voice_touches_have_sarvam_script_ids(catalog):
    k17 = catalog.select("voicebot", "hi", "REMINDER", "LOW", "B1")
    k18 = catalog.select("voicebot", "en", "CALLBACK_ACK", "LOW", "B1")
    assert catalog.get(k17.template_id).extra["sarvam_agent"]["name"] == "Kairos - EMI Reminder Call (K17 IVR)"
    assert catalog.get(k18.template_id).extra["sarvam_agent"]["name"] == "Kairos - Callback Voicebot (K18)"
    assert {catalog.get(k17.template_id).type, catalog.get(k18.template_id).type} == {"voicebot_script"}


def test_frontend_ids_still_exist_and_are_approved(catalog):
    for tid in ("SMS_REMIND_HI_01", "SMS_REMIND_EN_01", "SMS_DUE_HI_02", "WA_REMIND_HI_SOFT_02", "WA_PAYLINK_HI_01",
                "WA_PAYLINK_EN_01", "EM_STATEMENT_EN_01", "IVR_DUE_HI_01", "IVR_DUE_EN_01", "VB_CALLBACK_HI_01"):
        assert catalog.get(tid) is not None and catalog.get(tid).approved, tid


# ---------- validation of the new fields ----------
@pytest.mark.parametrize("bad,msg", [
    ({"tier": 5}, "'tier' must be 1, 2, 3 or 4"),
    ({"tier": 3}, "tier 3 must be intensity STANDARD"),
    ({"tier": 1, "purpose": "HARDSHIP_ACK"}, "only REMINDER templates carry a tier"),
    ({"type": "ivr_blast"}, "does not fit channel"),
    ({"approval": {"status": "FAILED_CHECKLIST"}}, "disagrees with approval.status"),
    ({"approval": "yes"}, "'approval' must be an object"),
])
def test_validation_of_tier_type_and_approval(policy, bad, msg):
    with pytest.raises(CatalogError) as e:
        mini(policy, tpl("SMS_X_EN_01", **bad))
    assert msg in str(e.value)
