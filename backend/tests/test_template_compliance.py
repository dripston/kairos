"""K22 collections-compliance checklist: every shipped template is checked; bad templates fail."""
from __future__ import annotations

import copy
import json

import pytest

from kairos_backend import template_compliance as tc
from kairos_backend.templates import TemplateCatalog, default_catalog_path


@pytest.fixture(scope="module")
def raw():
    return json.loads(default_catalog_path().read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def results(raw):
    return {r.template_id: r for r in tc.check_catalog(raw)}


def good_sms(**kw):
    row = {
        "id": "SMS_T_EN_01", "channel": "sms", "type": "sms", "lang": "en", "purpose": "REMINDER", "tier": 1,
        "intensity": "LOW", "dpd_bands": ["B1", "B2", "B3", "B4"], "family": "sms.test",
        "identity_stage": "outbound_registered_contact",
        "body": "{{lender_name}}: Your PL EMI of Rs.{{amount}} (A/c XX{{loan_last4}}) is pending since {{due_date}}. "
                "Pay: {{payment_link}} Ignore if already paid. Help: {{helpline}}",
        "dlt_template_id": "PENDING_DLT", "sender_id": "PENDING_HEADER",
    }
    row.update(kw)
    row.setdefault("variables", sorted(set(tc.placeholders(row.get("body", "") + (row.get("subject") or "")))))
    return row


def failed(row, rows=None):
    return tc.check_template(row, rows).failed_rules


# ---------- the shipped catalog ----------
def test_every_approved_template_passes_the_checklist(raw, results):
    approved = [r for r in raw["templates"] if r["approval"]["status"] == tc.APPROVED_STATUS]
    assert len(approved) >= 80
    for r in approved:
        assert results[r["id"]].passed, (r["id"], results[r["id"]].reasons)
        assert r["approved"] is True


def test_stored_approval_blocks_match_the_live_checklist(raw, results):
    """catalog.json must be re-stamped (python -m kairos_backend.template_compliance --stamp) after edits."""
    for r in raw["templates"]:
        live = results[r["id"]]
        a = r["approval"]
        assert a["checklist"] == live.checklist, r["id"]
        assert a["failures"] == live.reasons, r["id"]
        assert a["status"] == (tc.APPROVED_STATUS if live.passed else tc.FAILED_STATUS)
        assert r["approved"] is live.passed
        assert a["reviewed_by"] == "Kairos collections compliance review (automated checklist)"
        assert a["reviewed_on"] == "2026-11-19" and a["notes"]
        assert set(a["checklist"]) == set(tc.RULES)


def test_only_the_negative_control_fails(results):
    failing = {tid: r.failed_rules for tid, r in results.items() if not r.passed}
    assert set(failing) == {"WA_DUE_EN_00"}
    assert {"NO_THIRD_PARTY_DISCLOSURE", "CONSEQUENCES_PERMITTED", "ACCOUNT_LAST4_ONLY", "PLACEHOLDERS_ALLOWED",
            "PAY_AND_HELP", "LENDER_IDENTIFIED"} <= set(failing["WA_DUE_EN_00"])


def test_stamp_is_idempotent(raw):
    assert tc.stamp(copy.deepcopy(raw)) == raw


def test_catalog_loads_with_approval_blocks(policy):
    cat = TemplateCatalog.load(policy=policy)
    assert all((t.approval_status == tc.APPROVED_STATUS) == t.approved for t in cat)


def test_english_sms_fit_one_gsm7_segment_and_hindi_length_is_known(raw):
    for r in raw["templates"]:
        if r["channel"] != "sms":
            continue
        rendered = tc.render_max(r["body"])
        if r["lang"] == "en":
            assert tc.is_gsm7(rendered) and tc.gsm7_length(rendered) <= 160, r["id"]
        else:
            assert tc.sms_segments(rendered, unicode=True) <= tc.SMS_HI_MAX_SEGMENTS, r["id"]


def test_en_and_hi_carry_the_same_placeholders(raw):
    fams = {}
    for r in raw["templates"]:
        fams.setdefault(r["family"], {})[r["lang"]] = set(tc.placeholders(tc._all_text(r)))
    for fam, by_lang in fams.items():
        if fam.endswith("legacy_draft"):
            continue
        assert set(by_lang) == {"en", "hi"}, fam
        assert by_lang["en"] == by_lang["hi"], fam


def test_good_fixture_passes():
    r = tc.check_template(good_sms())
    assert r.passed, r.reasons
    assert r.checklist["IGNORE_IF_PAID"] == tc.PASS and r.checklist["LANGUAGE_PARITY"] == tc.PASS


# ---------- deliberately bad templates ----------
@pytest.mark.parametrize("body,rule", [
    ("{{lender_name}}: Pay EMI Rs.{{amount}} (A/c XX{{loan_last4}}) or police will arrest you. {{payment_link}} "
     "Ignore if already paid. Help: {{helpline}}", "NO_THREATS_OR_ABUSE"),
    ("{{lender_name}}: Defaulters like you should be ashamed. Pay Rs.{{amount}} {{payment_link}} Ignore if already "
     "paid. Help: {{helpline}}", "NO_THREATS_OR_ABUSE"),
    ("{{lender_name}}: Pay Rs.{{amount}} today or legal action will follow. {{payment_link}} Ignore if already paid. "
     "Help: {{helpline}}", "CONSEQUENCES_PERMITTED"),
    ("{{lender_name}}: Pay Rs.{{amount}} or our agent will visit your home. {{payment_link}} Ignore if already paid. "
     "Help: {{helpline}}", "CONSEQUENCES_PERMITTED"),
    ("{{lender_name}}: Pay Rs.{{amount}} or we will inform your employer. {{payment_link}} Ignore if already paid. "
     "Help: {{helpline}}", "NO_THIRD_PARTY_DISCLOSURE"),
    ("{{lender_name}}: Your PL EMI of Rs.{{amount}} is pending. Pay: {{payment_link}} Ignore if already paid.",
     "PAY_AND_HELP"),
    ("{{lender_name}}: Your PL EMI of Rs.{{amount}} is pending. Ignore if already paid. Help: {{helpline}}",
     "PAY_AND_HELP"),
    ("{{lender_name}}: EMI for loan {{loan_number}} is pending. {{payment_link}} Ignore if already paid. Help: "
     "{{helpline}}", "ACCOUNT_LAST4_ONLY"),
    ("{{lender_name}}: EMI for loan 4567812345 is pending. {{payment_link}} Ignore if already paid. Help: "
     "{{helpline}}", "ACCOUNT_LAST4_ONLY"),
    ("{{lender_name}}: EMI for loan {{loan_last4}} is pending. {{payment_link}} Ignore if already paid. Help: "
     "{{helpline}}", "ACCOUNT_LAST4_ONLY"),
    ("{{lender_name}}: Dear {{customer}}, EMI pending. {{payment_link}} Ignore if already paid. Help: {{helpline}}",
     "PLACEHOLDERS_ALLOWED"),
    ("{{lender_name}}: Dear [Name], EMI pending. {{payment_link}} Ignore if already paid. Help: {{helpline}}",
     "PLACEHOLDERS_ALLOWED"),
    ("{{lender_name}}: Your PL EMI of Rs.{{amount}} is pending. Pay: {{payment_link}} Help: {{helpline}}",
     "IGNORE_IF_PAID"),
    ("{{lender_name}}: EMI Rs.{{amount}} pending. Late fee may apply. {{payment_link}} Ignore if already paid. "
     "Help: {{helpline}}", "CONSEQUENCES_PERMITTED"),
    ("{{lender_name}}: Your PL EMI of Rs.{{amount}} (A/c XX{{loan_last4}}) is pending since {{due_date}}. Kindly pay "
     "it at the earliest using {{payment_link}} Ignore if already paid. For help call {{helpline}}",
     "LENGTH_LIMITS"),
    ("{{lender_name}}: Your EMI of ₹{{amount}} is pending. {{payment_link}} Ignore if already paid. Help: {{helpline}}",
     "LENGTH_LIMITS"),
])
def test_bad_sms_fails_with_reason(body, rule):
    row = good_sms(body=body)
    row.pop("variables")
    row["variables"] = sorted(set(tc.placeholders(body)))
    res = tc.check_template(row)
    assert rule in res.failed_rules, res.reasons
    assert any(x.startswith(rule + ":") for x in res.reasons)


def test_tier_2_may_not_mention_bureau_and_penal_interest_is_misleading():
    t2 = good_sms(tier=2, body="{{lender_name}}: EMI Rs.{{amount}} (A/c XX{{loan_last4}}) overdue. Late fee and credit "
                               "score impact. {{payment_link}} Help: {{helpline}}")
    assert "CONSEQUENCES_PERMITTED" in failed(t2)
    t3 = good_sms(tier=3, intensity="STANDARD", body="{{lender_name}}: EMI Rs.{{amount}} (A/c XX{{loan_last4}}) overdue "
                  "{{days_overdue}} days. Penal interest applies. {{payment_link}} Help: {{helpline}}")
    res = tc.check_template(t3)
    assert "CONSEQUENCES_PERMITTED" in res.failed_rules and any("penal interest" in x for x in res.reasons)


def test_hindi_threat_and_third_party_words_fail():
    row = good_sms(lang="hi", body="{{lender_name}}: भुगतान करें वरना पुलिस आएगी और आपके परिवार को बताया जाएगा। "
                                   "{{payment_link}} भुगतान कर दिया हो तो अनदेखा करें। सहायता: {{helpline}}")
    f = failed(row)
    assert "NO_THREATS_OR_ABUSE" in f and "NO_THIRD_PARTY_DISCLOSURE" in f


def test_language_parity_detects_placeholder_mismatch_and_script():
    en = good_sms()
    hi = good_sms(id="SMS_T_HI_01", lang="hi", body="{{lender_name}}: आपकी EMI बकाया है। भुगतान करें: {{payment_link}} "
                  "भुगतान कर दिया हो तो अनदेखा करें। सहायता: {{helpline}}")
    rows = [en, hi]
    assert "LANGUAGE_PARITY" in failed(en, rows) and "LANGUAGE_PARITY" in failed(hi, rows)
    latin_hi = good_sms(id="SMS_T_HI_01", lang="hi")
    assert "LANGUAGE_PARITY" in failed(latin_hi, [en, latin_hi])
    assert "LANGUAGE_PARITY" in failed(en, [en])          # no hi version at all


def test_pre_verification_reply_may_not_carry_account_details():
    row = good_sms(purpose="CALLBACK_ACK", tier=None, identity_stage="pre_verification",
                   body="{{lender_name}}: Callback noted for your EMI of Rs.{{amount}}. Help: {{helpline}}")
    row.pop("tier")
    res = tc.check_template(row)
    assert "IDENTITY_STAGE" in res.failed_rules and any("amount" in x for x in res.reasons)


def test_identity_stage_must_match_purpose_and_receipt_must_say_verified():
    row = good_sms(purpose="PAYMENT_RECEIVED", identity_stage="pre_verification",
                   body="{{lender_name}}: We received Rs.{{amount}} for A/c XX{{loan_last4}}. Help: {{helpline}}")
    row.pop("tier")
    assert "IDENTITY_STAGE" in failed(row)
    row["identity_stage"] = "post_verification"
    assert "IDENTITY_STAGE" in failed(row)                 # no send_condition
    row["send_condition"] = "only after D7/LMS verification"
    assert "IDENTITY_STAGE" not in failed(row)


@pytest.mark.parametrize("purpose,body,why", [
    ("HARDSHIP_ACK", "{{lender_name}}: Sorry to hear that. Please still pay: {{payment_link}} Help: {{helpline}}",
     "must not ask for payment"),
    ("PTP_CONFIRM", "{{lender_name}}: Noted your date {{promise_date}}. Help: {{helpline}}", "reminders pause"),
    ("STOP_CONFIRM", "{{lender_name}}: Noted. Help: {{helpline}}", "no further reminders"),
])
def test_purpose_content_rules(purpose, body, why):
    row = good_sms(purpose=purpose, identity_stage="pre_verification", body=body, dpd_bands=["B1"])
    row.pop("tier")
    res = tc.check_template(row)
    assert "PURPOSE_CONTENT" in res.failed_rules and any(why in x for x in res.reasons)


def test_t4_needs_final_date_and_t4_not_in_b1():
    row = good_sms(tier=4, intensity="STANDARD", dpd_bands=["B1", "B2"],
                   body="{{lender_name}}: Final notice. Pay EMI Rs.{{amount}} (A/c XX{{loan_last4}}). "
                        "{{payment_link}} Help: {{helpline}}")
    f = failed(row)
    assert "PURPOSE_CONTENT" in f and "TIER_BANDS" in f


def test_sms_needs_dlt_fields_and_whatsapp_needs_utility():
    assert "CHANNEL_REGISTRATION" in failed(good_sms(dlt_template_id=""))
    assert "LENDER_IDENTIFIED" in failed(good_sms(sender_id=""))
    wa = good_sms(channel="whatsapp", type="whatsapp", wa_category="MARKETING", wa_template_name="plh_x")
    assert "CHANNEL_REGISTRATION" in failed(wa)


def test_voice_opening_may_not_mention_the_loan_before_identity():
    row = {"id": "VB_X_EN_01", "channel": "voicebot", "type": "voicebot_script", "lang": "en", "purpose": "CALLBACK_ACK",
           "intensity": "LOW", "dpd_bands": ["B1"], "family": "voicebot.x", "identity_stage": "in_call_identity_check",
           "opening_line": "Hello, this is {{lender_name}}, calling about your loan EMI. Am I speaking with {{name}}?",
           "variables": ["lender_name", "name"],
           "sarvam_agent": {"name": "a", "app_id": "b", "prompt_sha": "c"}, "agent_controls": sorted(tc.REQUIRED_AGENT_CONTROLS)}
    res = tc.check_template(row)
    assert "IDENTITY_STAGE" in res.failed_rules
    row["opening_line"] = "Hello, this is {{lender_name}}. Am I speaking with {{name}}?"
    assert tc.check_template(row).passed
    row["agent_controls"] = []
    assert "CHANNEL_REGISTRATION" in failed(row)


def test_ivr_blast_must_name_the_recipient_first(raw):
    ivr = next(r for r in raw["templates"] if r["id"] == "IVR_DUE_EN_01")
    bad = copy.deepcopy(ivr)
    bad["body"] = bad["body"].replace("This message is for {{name}}. If you are not {{name}}, please disconnect this call. ", "")
    bad["variables"] = sorted(set(tc.placeholders(bad["body"])))
    assert "IDENTITY_STAGE" in failed(bad)


def test_cli_reports(capsys):
    assert tc.main([]) == 0
    out = capsys.readouterr().out
    assert "templates pass the collections-compliance checklist" in out and "WA_DUE_EN_00" in out
