"""The Gate: K01's hard rules as one pure function.

"The engine decides what is best. This policy decides what is allowed."
evaluate() answers one question: may this touch be sent at this moment, and if
not, why. It reads the rule order from policy.json (evaluation_order), first hit
wins, and every decision carries policy_version.

It is pure — all state (counts, last-send times, holds, budget) arrives in Facts.
The Ledger computes Facts atomically; tests feed Ojas's test_cases.csv directly.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Callable, Optional

from .policy import Policy

SEND, WAIT, CANCEL, HANDOFF = "SEND", "WAIT", "CANCEL", "HANDOFF"

# K13 adds two holds that K01's evaluation_order does not list yet. Until Ojas
# places them, they run right after HARDSHIP_HOLD (pending confirmation).
EXTRA_RULES_AFTER = {"HARDSHIP_HOLD": ["CALLBACK_PENDING", "SUPERVISOR_HOLD"]}


@dataclass
class Facts:
    """Everything the Gate needs about one candidate touch, at one moment.

    `at` may carry any UTC offset: the Gate judges day, contact window and quiet days in the policy
    timezone (IST). A naive `at` is taken to be IST already, as everywhere in the Ledger (RT-F04)."""
    at: datetime
    channel: str
    dpd: Optional[int]
    dpd_age_hours: float = 0.0
    consent: bool = True
    opted_out: bool = False
    hardship_until: Optional[datetime] = None
    hardship_supervisor: bool = False
    callback_pending: bool = False
    supervisor_hold: bool = False
    ptp_date: Optional[date] = None
    obligation_cleared: bool = False
    stop_for_day: bool = False
    sends_today_channel: int = 0
    sends_today_all: int = 0
    sends_week_channel: int = 0
    sends_week_all: int = 0
    mins_since_last_same_channel: Optional[float] = None
    official_cap: Optional[int] = None
    lender_id: Optional[str] = None
    template_approved: bool = True
    budget_remaining_inr: float = float("inf")
    est_cost_inr: float = 0.0
    campaign_target_met: bool = False
    purpose: Optional[str] = None          # "reminder" | "collection"; None skips the purpose check
    verified_overdue: bool = True


@dataclass
class Decision:
    decision: str
    reason: str
    rule: str
    policy_version: str
    band: Optional[str] = None
    detail: dict = field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        return self.decision == SEND

    def as_dict(self) -> dict:
        return asdict(self)


Hit = Optional[tuple[str, str, dict]]  # (decision, reason, detail) or None to pass


class _Ctx:
    def __init__(self, policy: Policy, f: Facts):
        self.p, self.f = policy, f
        self.at = policy.local(f.at)  # IST, whatever clock the caller used (RT-F04)
        self.today = self.at.date()
        self.hhmm = self.at.strftime("%H:%M")
        self.band: Optional[str] = policy.band_for(f.dpd) if f.dpd is not None else None


# ---------- rules (one per evaluation_order id) ----------
def _stop_opt_out(c: _Ctx) -> Hit:
    return (CANCEL, "STOP_OPT_OUT", {}) if c.f.opted_out else None


def _consent(c: _Ctx) -> Hit:
    if c.p.raw["consent_required_per_channel"] and not c.f.consent:
        return WAIT, "NO_CONSENT", {}
    return None


def _dpd_valid(c: _Ctx) -> Hit:
    if c.f.dpd is None or c.f.dpd_age_hours > c.p.stale_after_hours:
        return WAIT, "DPD_INVALID", {"dpd_age_hours": c.f.dpd_age_hours}
    return None


def _in_scope(c: _Ctx) -> Hit:
    if c.band is None:
        return WAIT, "NOT_IN_SCOPE", {"dpd": c.f.dpd}
    if not c.p.channel_allowed(c.band, c.f.channel):
        return WAIT, "CHANNEL_NOT_ALLOWED", {}
    if c.f.purpose:
        if c.f.purpose not in c.p.band(c.band)["message_purpose"]:
            return WAIT, "PURPOSE_NOT_ALLOWED", {"purpose": c.f.purpose}
        if (c.f.purpose == "collection" and c.p.raw["dpd_rules"]["collection_purpose_requires_verified_overdue"]
                and not c.f.verified_overdue):
            return WAIT, "OVERDUE_NOT_VERIFIED", {}
    return None


def _hardship(c: _Ctx) -> Hit:
    if c.f.hardship_until and c.p.local(c.f.hardship_until) > c.at:
        return (HANDOFF if c.f.hardship_supervisor else WAIT), "HARDSHIP_HOLD", {"until": c.f.hardship_until.isoformat()}
    return None


def _callback_pending(c: _Ctx) -> Hit:
    return (WAIT, "CALLBACK_PENDING", {}) if c.f.callback_pending else None


def _supervisor_hold(c: _Ctx) -> Hit:
    return (WAIT, "SUPERVISOR_HOLD", {}) if c.f.supervisor_hold else None


def _ptp(c: _Ctx) -> Hit:
    # K01: no activity on any channel through the end of the PTP date
    if c.f.ptp_date and c.f.ptp_date >= c.today:
        return WAIT, "PTP_HOLD", {"ptp_date": c.f.ptp_date.isoformat()}
    return None


def _cleared(c: _Ctx) -> Hit:
    return (CANCEL, "OBLIGATION_CLEARED", {}) if c.f.obligation_cleared else None


def _quiet_day(c: _Ctx) -> Hit:
    return (WAIT, "QUIET_DAY", {}) if c.p.is_quiet_day(c.today) else None


def _window(c: _Ctx) -> Hit:
    return None if c.p.in_contact_window(c.hhmm) else (WAIT, "CONTACT_WINDOW", {"time": c.hhmm})


def _stop_for_day(c: _Ctx) -> Hit:
    return (WAIT, "STOP_FOR_DAY", {}) if c.f.stop_for_day else None


def _channel_cap(c: _Ctx) -> Hit:
    cap = c.p.channel_day_cap(c.band, c.f.channel, c.f.lender_id, c.f.official_cap)
    if cap is not None and c.f.sends_today_channel >= cap:
        return WAIT, "CHANNEL_CAP", {"cap": cap, "sent": c.f.sends_today_channel}
    return None


def _all_channel_cap(c: _Ctx) -> Hit:
    cap, pol = c.p.all_channel_day_cap(c.band, c.f.lender_id)
    if cap is not None and c.f.sends_today_all >= cap:
        reason = "PRE_DUE_TOTAL_CAP" if pol is not None and cap == pol else "ALL_CHANNEL_DAY_CAP"
        return WAIT, reason, {"cap": cap, "sent": c.f.sends_today_all}
    return None


def _weekly(c: _Ctx) -> Hit:
    per_ch, per_all = c.p.weekly_caps(c.band, c.f.channel, c.f.lender_id)
    if per_ch is not None and c.f.sends_week_channel >= per_ch:
        return WAIT, "WEEKLY_CAP", {"cap": per_ch, "scope": "channel"}
    if per_all is not None and c.f.sends_week_all >= per_all:
        return WAIT, "WEEKLY_CAP", {"cap": per_all, "scope": "all"}
    return None


def _gap(c: _Ctx) -> Hit:
    gap = c.p.min_gap_minutes(c.f.lender_id)
    m = c.f.mins_since_last_same_channel
    if m is not None and m < gap:
        return WAIT, "SAME_CHANNEL_GAP", {"gap_min": gap, "since_min": round(m, 1)}
    return None


def _template(c: _Ctx) -> Hit:
    return None if c.f.template_approved else (WAIT, "NO_APPROVED_TEMPLATE", {})


def _budget(c: _Ctx) -> Hit:
    if c.f.budget_remaining_inr < c.f.est_cost_inr:
        return WAIT, "BUDGET_EXHAUSTED", {"remaining": c.f.budget_remaining_inr, "cost": c.f.est_cost_inr}
    return None


def _campaign_stop(c: _Ctx) -> Hit:
    return (WAIT, "CAMPAIGN_TARGET_MET", {}) if c.f.campaign_target_met else None


RULES: dict[str, Callable[[_Ctx], Hit]] = {
    "STOP_OPT_OUT": _stop_opt_out,
    "CONSENT": _consent,
    "DPD_VALID": _dpd_valid,
    "IN_SCOPE": _in_scope,
    "HARDSHIP_HOLD": _hardship,
    "CALLBACK_PENDING": _callback_pending,
    "SUPERVISOR_HOLD": _supervisor_hold,
    "PTP_HOLD": _ptp,
    "OBLIGATION_CLEARED": _cleared,
    "QUIET_DAY": _quiet_day,
    "CONTACT_WINDOW": _window,
    "STOP_FOR_DAY": _stop_for_day,
    "CHANNEL_CAP": _channel_cap,
    "PRE_DUE_TOTAL_CAP": _all_channel_cap,
    "WEEKLY_CAPS": _weekly,
    "SAME_CHANNEL_GAP": _gap,
    "TEMPLATE_APPROVED": _template,
    "BUDGET_AVAILABLE": _budget,
    "CAMPAIGN_STOP_CONDITION": _campaign_stop,
}


def rule_order(policy: Policy) -> list[str]:
    """policy.evaluation_order, plus K13 holds slotted in where Ojas hasn't placed them."""
    order = policy.evaluation_order
    out: list[str] = []
    for rid in order:
        if rid not in RULES:
            raise ValueError(f"policy.json evaluation_order has unknown rule '{rid}' — Gate must implement it")
        out.append(rid)
        for extra in EXTRA_RULES_AFTER.get(rid, []):
            if extra not in order:
                out.append(extra)
    return out


def evaluate(policy: Policy, facts: Facts) -> Decision:
    c = _Ctx(policy, facts)
    for rid in rule_order(policy):
        hit = RULES[rid](c)
        if hit:
            decision, reason, detail = hit
            return Decision(decision, reason, rid, policy.version, c.band, detail)
    return Decision(SEND, "ELIGIBLE", "ALL_PASSED", policy.version, c.band, {})
