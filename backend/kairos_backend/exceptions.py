"""K13 operational exceptions as a pure decision table.

handle() maps one borrower event to (action, hold, queue), reading every number
from exceptions.json. The Ledger applies the resulting hold; the conversation
agent (CM-03) uses the action. Mirrors Ojas's reference_check.py.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .policy import Policy

ROUTED_CONTINUE = {"DISPUTE", "COMPLAINT", "WAIVER_REQUEST"}
ROUTED_STOP = {"ABUSIVE", "LEGAL_THREAT", "FRAUD_CLAIM"}
EVENTS = {"NEEDS_TIME", "PTP_OFFERED", "CALLBACK_REQUEST", "CALLBACK_DUE", "HARDSHIP"} | ROUTED_CONTINUE | ROUTED_STOP


@dataclass
class ExceptionEvent:
    event: str
    band: str
    in_window: bool = True
    rebuttals_used: int = 0
    date_offset_days: Optional[int] = None
    broken_ptp_count: int = 0
    paid_now: bool = False
    callback_attempts: int = 0
    callback_answered: bool = False


@dataclass
class ExceptionOutcome:
    action: str
    hold: str
    queue: str  # "" when no ticket/case is needed

    def as_tuple(self) -> tuple[str, str, str]:
        return self.action, self.hold, self.queue


def handle(policy: Policy, e: ExceptionEvent) -> ExceptionOutcome:
    x = policy.exceptions
    if not x:
        raise RuntimeError("K13 exceptions.json not loaded")
    if e.event not in EVENTS:
        raise ValueError(f"unknown exception event {e.event}")
    q = x["queues"]["default"]
    thr = x["promise_to_pay"]["broken_ptp"]["threshold"]
    broken = e.broken_ptp_count >= thr

    if e.event == "NEEDS_TIME":
        if broken:
            return ExceptionOutcome("PUSH_PAYMENT_NOW", "NONE", "")
        if not e.in_window:
            return ExceptionOutcome("ACK_ONLY", "STOP_FOR_DAY", "")
        if e.rebuttals_used < x["needs_time"]["max_rebuttals_by_band"][e.band]:
            return ExceptionOutcome("REBUTTAL", "NONE", "")
        return ExceptionOutcome("ACCEPT_NO_DATE", "STOP_FOR_DAY", "")

    if e.event == "PTP_OFFERED":
        if broken:
            if e.paid_now:
                return ExceptionOutcome("VERIFY_PAYMENT", "NONE", "")
            return ExceptionOutcome("RECORD_PTP_NO_HOLD", "STOP_FOR_DAY", "")
        far = (e.date_offset_days or 0) > x["promise_to_pay"]["auto_accept_max_days"]
        return ExceptionOutcome("CAPTURE_PTP", "PTP_HOLD", q if far else "")

    if e.event == "CALLBACK_REQUEST":
        return ExceptionOutcome("CREATE_CALLBACK_TASK", "CALLBACK_PENDING", q)
    if e.event == "CALLBACK_DUE":
        if e.callback_answered:
            return ExceptionOutcome("CLOSE_CALLBACK_TASK", "STOP_FOR_DAY", "")
        if e.callback_attempts < 2:  # one retry allowed
            return ExceptionOutcome("RETRY_CALLBACK_2H", "CALLBACK_PENDING", q)
        return ExceptionOutcome("SEND_MISSED_CALLBACK_MSG", "STOP_FOR_DAY", "")

    if e.event == "HARDSHIP":
        if e.date_offset_days is None:
            return ExceptionOutcome("HOLD_2_DAYS", "HARDSHIP_HOLD", "")
        within = policy.raw["holds"]["HARDSHIP"]["borrower_date_within_days"]
        return ExceptionOutcome("HOLD_UNTIL_DATE", "HARDSHIP_HOLD", q if e.date_offset_days > within else "")

    if e.event in ROUTED_CONTINUE:
        return ExceptionOutcome("CREATE_TICKET", "STOP_FOR_DAY", q)
    return ExceptionOutcome("END_CONVERSATION_AND_TICKET", "SUPERVISOR_HOLD", q)
