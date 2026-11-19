"""Every row of Ojas's K01 test_cases.csv must produce the expected decision and reason."""
from datetime import date, datetime

import pytest

from kairos_backend.gate import Facts, evaluate, rule_order

from .conftest import K01_DIR, read_csv

ROWS = read_csv(K01_DIR / "test_cases.csv")


def yes(v: str) -> bool:
    return (v or "").strip().upper() == "Y"


def opt_int(v: str):
    return int(v) if (v or "").strip() else None


def facts_from_row(r: dict) -> Facts:
    return Facts(
        at=datetime.fromisoformat(f"{r['date']}T{r['time']}"),
        channel=r["channel"],
        dpd=opt_int(r["dpd"]),
        dpd_age_hours=float(r["dpd_age_hours"] or 0),
        consent=yes(r["consent"]),
        opted_out=yes(r["opted_out"]),
        hardship_until=datetime.fromisoformat(r["hardship_until"]) if r["hardship_until"].strip() else None,
        hardship_supervisor=yes(r["hardship_supervisor"]),
        ptp_date=date.fromisoformat(r["ptp_date"]) if r["ptp_date"].strip() else None,
        obligation_cleared=yes(r["obligation_cleared"]),
        stop_for_day=yes(r["stop_for_day"]),
        sends_today_channel=int(r["sends_today_channel"]),
        sends_today_all=int(r["sends_today_all"]),
        sends_week_channel=int(r["sends_week_channel"] or 0),
        sends_week_all=int(r["sends_week_all"] or 0),
        mins_since_last_same_channel=opt_int(r["mins_since_last_same_channel"]),
        official_cap=opt_int(r["official_cap"]),
        lender_id=r["lender"].strip() or None,
        template_approved=yes(r["template_approved"]),
        budget_remaining_inr=float(r["budget_remaining_inr"]),
        est_cost_inr=float(r["est_cost_inr"]),
        campaign_target_met=yes(r["campaign_target_met"]),
    )


@pytest.mark.parametrize("row", ROWS, ids=[r["case_id"] for r in ROWS])
def test_k01_case(policy, row):
    d = evaluate(policy, facts_from_row(row))
    assert (d.decision, d.reason) == (row["expected_decision"], row["expected_reason"]), row["description"]
    assert d.policy_version == policy.version


def test_all_38_cases_present():
    assert len(ROWS) == 38


def test_order_follows_policy_and_slots_k13_holds(policy):
    order = rule_order(policy)
    assert order[: len(policy.evaluation_order)] != []  # sanity
    assert [r for r in order if r in policy.evaluation_order] == policy.evaluation_order
    h = order.index("HARDSHIP_HOLD")
    assert order[h + 1 : h + 3] == ["CALLBACK_PENDING", "SUPERVISOR_HOLD"]


def test_collection_purpose_needs_verified_overdue(policy):
    base = dict(at=datetime(2026, 11, 20, 11, 0), channel="sms", dpd=12)
    assert evaluate(policy, Facts(**base, purpose="collection", verified_overdue=False)).reason == "OVERDUE_NOT_VERIFIED"
    assert evaluate(policy, Facts(**base, purpose="collection", verified_overdue=True)).decision == "SEND"


def test_pre_due_is_reminder_only(policy):
    f = Facts(at=datetime(2026, 11, 20, 11, 0), channel="sms", dpd=-2, purpose="collection")
    assert evaluate(policy, f).reason == "PURPOSE_NOT_ALLOWED"


def test_gate_judges_time_in_ist_whatever_the_callers_offset(policy):
    """RT-F04: day, window and quiet day come from IST; a naive `at` is IST."""
    from datetime import timedelta, timezone

    def reason(at, **kw):
        d = evaluate(policy, Facts(at=at, channel="sms", dpd=12, **kw))
        return d.decision, d.reason

    utc = timezone.utc
    assert reason(datetime(2026, 11, 20, 14, 0, tzinfo=utc)) == ("WAIT", "CONTACT_WINDOW")    # 19:30 IST
    assert reason(datetime(2026, 11, 20, 5, 0, tzinfo=utc)) == ("SEND", "ELIGIBLE")           # 10:30 IST
    assert reason(datetime(2026, 11, 20, 2, 29, tzinfo=utc)) == ("WAIT", "CONTACT_WINDOW")    # 07:59 IST
    hawaii = timezone(timedelta(hours=-10))
    assert reason(datetime(2026, 11, 21, 15, 0, tzinfo=hawaii)) == ("WAIT", "QUIET_DAY")      # Sun 06:30 IST
    assert reason(datetime(2026, 11, 20, 19, 30)) == ("WAIT", "CONTACT_WINDOW")               # naive = IST
    assert reason(datetime(2026, 11, 20, 10, 0)) == ("SEND", "ELIGIBLE")
    # PTP date and hardship are compared on the IST clock too (aware and naive mixed is not a TypeError)
    late = datetime(2026, 11, 20, 20, 0, tzinfo=utc)  # Sat 21 Nov 01:30 IST: a PTP for the 20th has ended
    assert reason(late, ptp_date=date(2026, 11, 20)) == ("WAIT", "CONTACT_WINDOW")
    assert reason(late, ptp_date=date(2026, 11, 21)) == ("WAIT", "PTP_HOLD")
    assert reason(datetime(2026, 11, 20, 5, 0, tzinfo=utc),
                  hardship_until=datetime(2026, 11, 20, 11, 0)) == ("WAIT", "HARDSHIP_HOLD")
    assert reason(datetime(2026, 11, 20, 6, 0, tzinfo=utc),
                  hardship_until=datetime(2026, 11, 20, 11, 0)) == ("SEND", "ELIGIBLE")
