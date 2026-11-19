"""CN10: CreditNirvana's own "Personal Loan — 10-Day Post-Due Digital Collections Strategy"
(HDFC project), as a fixed-cadence baseline for the K09 replay.

STAND-IN until the official fixed-cadence policy file (K02) arrives. It is CN's strategy doc, not
the agentathon's baseline file; every result that uses it is SIMULATED.

Rules taken from the doc
  - Day 0 = EMI bounce detection (in the spine: the EMI due date, so cycle day = live DPD).
  - The cohort is fixed at Day 0 and never changes; the only early exit is a verified payment.
  - Cohorts: the dataset's "Risk Tagging" column when present ("C3 - Medium Risk" -> C3), else a
    delinquency score = bounces / cycles from the 6-month bounce string:
        C1 first-time bounce (no prior bounce)     C2 low risk     0-14 %
        C3 medium risk 14-42 %                      C4 high risk    > 42 % or 3+ consecutive bounces
        C5 high-ticket override (POS above HIGH_TICKET_POS_PAISE, default the portfolio's 90th
           percentile POS) -> uses C4's schedule.
    The score counts the current (Day 0) bounce; with the dataset's 6-month string the smallest
    score after a prior bounce is 2/6 = 33 %, so a computed C2 needs a longer history — the
    dataset's own tag supplies C2. "3+ consecutive" includes the current bounce.
  - Calendar (cycle day -> channels, message tier) in CALENDAR below.
  - After day 10: REPEAT_AFTER_DAY_10 = True repeats the cycle from day 1 (our choice: the dataset's
    loans are mostly well past day 10, and stopping would leave almost nobody to contact);
    False stops contacting after day 10.

Notes
  - IVR in CN's doc is a one-way voice blast (no response capture). The K15 DEV simulator models
    "ivr" like any other channel (delivery 50 %, replies possible), and the replay follows the
    simulator, so CN10's IVR replies count as meaningful replies here. Flagged in the report.
  - Templates: tier T1..T4 per channel and language, CN10_{WA|SMS|IVR}_T{n}_{EN|HI}; the day-8
    WhatsApp for C4/C5 carries the NACH re-presentment notice (..._T3_NACH_..) when the loan is
    re-presentment eligible. All are registered with the K06 ledger so the K01 template check passes.
  - Same-day multi-channel steps (e.g. "WA+SMS") are planned one channel at a time through the same
    K06 stack; the matched caps (K01 + K15 lender override: 1 contact/day) block the extras exactly
    as they would block K09.
"""
from __future__ import annotations

from typing import Iterable, Optional

# cycle day -> list of (channel, tier)
_C4 = {1: [("whatsapp", 1), ("sms", 1)], 2: [("ivr", 1)], 3: [("whatsapp", 2)], 4: [("sms", 2)],
       5: [("ivr", 2)], 6: [("whatsapp", 3), ("sms", 3)], 7: [("ivr", 3)], 8: [("whatsapp", 3)],
       9: [("sms", 4), ("ivr", 4)], 10: [("whatsapp", 4), ("sms", 4), ("ivr", 4)]}
CALENDAR: dict[str, dict[int, list[tuple[str, int]]]] = {
    "C1": {1: [("whatsapp", 1)], 3: [("sms", 1)], 5: [("whatsapp", 2)], 7: [("sms", 2)], 9: [("whatsapp", 3)],
           10: [("whatsapp", 4)]},
    "C2": {1: [("whatsapp", 1)], 4: [("sms", 1)], 7: [("whatsapp", 2)], 10: [("whatsapp", 3), ("sms", 3)]},
    "C3": {1: [("whatsapp", 1)], 3: [("sms", 1)], 5: [("whatsapp", 2)], 6: [("ivr", 2)], 8: [("whatsapp", 3)],
           9: [("sms", 3)], 10: [("whatsapp", 4), ("sms", 4)]},
    "C4": _C4,
    "C5": _C4,
}
NACH_NOTICE = {"cohorts": ("C4", "C5"), "day": 8, "channel": "whatsapp"}
CHANNEL_CODE = {"whatsapp": "WA", "sms": "SMS", "ivr": "IVR"}
LANGS = ("en", "hi")

# Settings (PLACEHOLDER until Product confirms)
REPEAT_AFTER_DAY_10 = True
HIGH_TICKET_POS_PAISE: Optional[int] = None   # None -> 90th percentile of the portfolio's POS
HIGH_TICKET_PERCENTILE = 0.90
LOW_RISK_MAX, MEDIUM_RISK_MAX = 0.14, 0.42     # delinquency score bands (fractions)
LABEL = ("CN10: CreditNirvana 10-day post-due digital collections strategy (CN's own doc, HDFC "
         "project), stand-in until the official fixed-cadence file (K02)")


def template_id(channel: str, tier: int, language: str, nach: bool = False) -> str:
    return f"CN10_{CHANNEL_CODE[channel]}_T{tier}{'_NACH' if nach else ''}_{language.upper()}"


def all_templates() -> Iterable[tuple[str, str, str]]:
    """(template_id, channel, language) for every CN10 template, to register with the ledger."""
    for ch in CHANNEL_CODE:
        for tier in (1, 2, 3, 4):
            for lang in LANGS:
                yield template_id(ch, tier, lang), ch, lang
    for lang in LANGS:
        yield template_id("whatsapp", 3, lang, nach=True), "whatsapp", lang


def delinquency_score(bounce_string: str) -> tuple[Optional[float], int, int]:
    """(bounces / cycles over the months with an EMI, prior bounces, longest run of consecutive
    bounces). The last month is the current bounce (Day 0)."""
    months = [m for m in (bounce_string or "").split(",") if m and m != "NA"]
    if not months:
        return None, 0, 0
    run = longest = 0
    for m in months:
        run = run + 1 if m == "1" else 0
        longest = max(longest, run)
    return months.count("1") / len(months), months[:-1].count("1"), longest


def computed_cohort(bounce_string: str) -> str:
    score, prior, longest = delinquency_score(bounce_string)
    if score is None or prior == 0:
        return "C1"
    if longest >= 3 or score > MEDIUM_RISK_MAX:
        return "C4"
    if score > LOW_RISK_MAX:
        return "C3"
    return "C2"


def cohort_of(risk_tag: Optional[str], bounce_string: str, pos_paise: Optional[int],
              high_ticket_pos_paise: Optional[int]) -> str:
    """Cohort at Day 0. The dataset tag wins when present; high ticket (POS above the threshold)
    overrides to C5 either way."""
    tag = (risk_tag or "").strip().split(" ")[0].upper()
    cohort = tag if tag in CALENDAR else computed_cohort(bounce_string)
    if high_ticket_pos_paise is not None and pos_paise is not None and pos_paise > high_ticket_pos_paise:
        cohort = "C5"
    return cohort


def high_ticket_threshold(pos_values: list[int], setting: Optional[int] = HIGH_TICKET_POS_PAISE,
                          percentile: float = HIGH_TICKET_PERCENTILE) -> Optional[int]:
    if setting is not None:
        return setting
    vals = sorted(v for v in pos_values if v is not None)
    if not vals:
        return None
    return vals[min(len(vals) - 1, int(percentile * len(vals)))]


def cycle_day(dpd: Optional[int], repeat: bool = REPEAT_AFTER_DAY_10) -> Optional[int]:
    """Day in CN's 10-day calendar for a loan `dpd` days after its bounce (Day 0 = DPD 0)."""
    if dpd is None or dpd < 1:
        return None
    if dpd > 10 and not repeat:
        return None
    return (dpd - 1) % 10 + 1


def steps_for(cohort: str, dpd: Optional[int], language: str, represent_eligible: bool = False,
              repeat: bool = REPEAT_AFTER_DAY_10) -> list[tuple[str, int, str]]:
    """Today's scheduled (channel, tier, template_id) for a loan, in calendar order."""
    day = cycle_day(dpd, repeat)
    if day is None:
        return []
    out = []
    for ch, tier in CALENDAR[cohort].get(day, []):
        nach = (represent_eligible and cohort in NACH_NOTICE["cohorts"] and day == NACH_NOTICE["day"]
                and ch == NACH_NOTICE["channel"])
        out.append((ch, tier, template_id(ch, tier, language, nach)))
    return out
