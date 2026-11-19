"""Every row of Ojas's K13 test_cases.csv must produce the expected action, hold and queue."""
import pytest

from kairos_backend.exceptions import ExceptionEvent, handle

from .conftest import K13_DIR, read_csv

ROWS = read_csv(K13_DIR / "test_cases.csv")


def opt_int(v):
    return int(v) if (v or "").strip() else None


def event_from_row(r) -> ExceptionEvent:
    return ExceptionEvent(
        event=r["event"],
        band=r["band"],
        in_window=r["in_window"] == "Y",
        rebuttals_used=opt_int(r["rebuttals_used"]) or 0,
        date_offset_days=opt_int(r["date_offset_days"]),
        broken_ptp_count=opt_int(r["broken_ptp_count"]) or 0,
        paid_now=r["paid_now"] == "Y",
        callback_attempts=opt_int(r["callback_attempts"]) or 0,
        callback_answered=r["callback_answered"] == "Y",
    )


@pytest.mark.parametrize("row", ROWS, ids=[r["case_id"] for r in ROWS])
def test_k13_case(policy, row):
    got = handle(policy, event_from_row(row)).as_tuple()
    assert got == (row["expected_action"], row["expected_hold"], row["expected_queue"]), row["description"]


def test_all_25_cases_present():
    assert len(ROWS) == 25
