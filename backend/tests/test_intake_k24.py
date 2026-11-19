"""K24 portfolio intake (Ojas's policy/K24-portfolio-intake) against our spine/intake.py, plus Ledger.unenroll."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from kairos_backend.ledger import Ledger
from spine import intake as K

from .policy_conformance.k24_adapter import UPLOAD_DATE, _csv, _v, run_k24
from .test_ledger import CH_TPL, D, add_loan, ins


@pytest.fixture(scope="module")
def v1():
    return _v("sample_upload_v1.csv")


def test_v1_rows_match_expected_v1(v1):
    got = v1.status()
    for e in _csv("expected_v1.csv"):
        assert got[e["loan_id"]] == (e["expected_status"], e["expected_reason"]), e["loan_id"]


def test_readme_preview_numbers(v1):
    camp = K.Campaign(date(2026, 11, 22), date(2026, 12, 21), budget_per_case_paise=1000)
    camp.budgeted_loan_ids = [r.loan_id for r in K.budget_scope(v1.accepted)[0]]
    pv = K.preview(v1, camp)
    assert (pv["rows_received"], pv["accepted"]) == (14, 6)
    assert pv["quarantined_by_reason"] == {"INVALID_MOBILE": 1, "MISSING_FIELD": 1, "INVALID_AMOUNT": 2,
                                           "DUPLICATE_LOAN_ID": 2, "INVALID_DATE": 1, "INVALID_DPD": 1}
    assert sum(pv["warnings_by_type"].values()) == 3
    assert (pv["customers"], pv["customers_with_2plus_loans"], pv["eligible_loans"]) == (5, 1, 5)
    assert (pv["campaign_pool_inr"], pv["customers_covered"], pv["can_arm"]) == (50, 4, True)
    assert pv["first_contact"] == "2026-11-23 08:00"          # Sun 22 Nov skipped
    assert pv["last_contact"] == "2026-12-21 before 19:00"
    assert pv["contact_days"] == 25


def test_arming_blocked_until_budget_set_is_chosen(v1):
    pv = K.preview(v1, K.Campaign(date(2026, 11, 22), date(2026, 12, 21), 1000))
    assert not pv["can_arm"] and pv["arm_blocked_reason"] == "BUDGET_SET_NOT_SPECIFIED"
    assert pv["budgeted_loans"] == 0 and pv["campaign_pool_paise"] == 0
    assert len(pv["proposed_budget_set"]) == 5 and "L1011" not in pv["proposed_budget_set"]
    empty = K.preview(v1, K.Campaign(date(2026, 11, 22), date(2026, 12, 21), 1000, []))
    assert not empty["can_arm"] and empty["arm_blocked_reason"] == "NO_BUDGETED_LOANS"


def test_column_aliases_and_mobile_normalisation():
    assert K.map_headers(["Loan No", "CIF", "EMI Due Date", "Days-Past.Due", "Mobile"]) == {
        "Loan No": "loan_id", "CIF": "customer_id", "EMI Due Date": "due_date", "Days-Past.Due": "dpd",
        "Mobile": "registered_mobile"}
    assert K.normalise_mobile("+91 98765 43210") == K.normalise_mobile("09876543210") == "9876543210"
    assert K.normalise_mobile("12345") is None and K.normalise_mobile("5876543210") is None


def test_merge_v2_matches_expected_and_is_idempotent(v1):
    first = K.merge(None, v1, budgeted=["L1001", "L1002", "L1003", "L1010", "L1012"])
    first.state.loans["L1001"]["holds"] = ["PTP_HOLD"]
    v2 = _v("sample_upload_v2.csv")
    second = K.merge(first.state, v2)
    for e in _csv("expected_merge_v2.csv"):
        assert second.changes[e["loan_id"]] == e["expected_change"], e["loan_id"]
    assert second.state.loans["L1001"]["holds"] == ["PTP_HOLD"]           # holds kept on UPDATED
    assert second.state.loans["L1003"]["status"] == K.REMOVED
    # the budgeted set never changes silently: L1003 leaves it, L1014 is only proposed
    assert second.left_budget == ["L1003"] and second.proposed_for_budget == ["L1014"]
    assert second.state.budgeted == ["L1001", "L1002", "L1010", "L1012"]
    assert second.state.pool_paise(1000) == 4000
    assert (second.version, second.upload_id is not None) == (2, True)
    third = K.merge(second.state, _v("sample_upload_v2.csv"))
    assert third.duplicate_upload and third.version == 2
    assert set(third.changes.values()) == {K.UNCHANGED}


def test_unenroll_recomputes_pool_and_cancels_only_that_loans_queued_touches(policy, tmp_path):
    lg = Ledger(policy, tmp_path / "k24.db")
    try:
        lg.upsert_template(CH_TPL["sms"], "sms", "en")
        lg.upsert_campaign("C1", budget_per_case_paise=10_000, start_date=D, end_date=D + timedelta(days=10))
        assert lg.enroll("C1", [add_loan(lg, "B1"), add_loan(lg, "B2")]) == 2
        lg.start_day(D)
        assert lg.campaign_state("C1")["pool_paise"] == 20_000
        r1, r2 = lg.reserve(ins("B1")), lg.reserve(ins("B2"))
        assert r1.reserved and r2.reserved
        committed = lg.campaign_state("C1")
        assert lg.unenroll("C1", ["B1-A", "NOT-ENROLLED"]) == 1
        st = lg.campaign_state("C1")
        assert st["pool_paise"] == 10_000
        statuses = dict(lg._conn().execute("SELECT borrower_id, status FROM touches").fetchall())
        assert statuses == {"B1": "CANCELED", "B2": "RESERVED"}
        assert lg.unenroll("C1", ["B1-A"]) == 0                                # idempotent
        assert committed["pool_paise"] == 20_000
    finally:
        lg.close()


def test_k24_conformance_all_reference_checks_pass():
    rows = run_k24()
    assert len(rows) == 32
    assert [r for r in rows if r["status"] != "PASS"] == []
