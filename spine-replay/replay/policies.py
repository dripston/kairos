"""Policies: decide who to contact, on which channel, when, with which template.

Every policy has the same two methods, so K09's real policy can plug in later:
    plan_day(day, portfolio, log) -> list of Decision
    observe(day, outcomes, portfolio) -> None   (learn from today's results)
"""
from collections import defaultdict
from dataclasses import dataclass

from replay import settings as S
from replay.portfolio import is_eligible


@dataclass(frozen=True)
class Decision:
    borrower_index: int
    channel: str
    slot: str
    template_id: str


class FixedCadenceBaseline:
    """PLACEHOLDER until the official baseline file (K02).
    Each channel runs its own fixed schedule, like the problem statement describes:
    SMS every 3 days, WhatsApp every 5 days, always in the morning."""

    name = "baseline"
    label = "Fixed-cadence baseline (placeholder until official file)"

    def plan_day(self, day, portfolio, log):
        decisions = []
        for b in portfolio:
            if not is_eligible(b):
                continue
            if "sms" in b.consents and (day + b.index) % 3 == 0:
                decisions.append(Decision(b.index, "sms", "morning", S.TEMPLATES["sms"][0]))
            if "whatsapp" in b.consents and (day + b.index) % 5 == 0:
                decisions.append(Decision(b.index, "whatsapp", "morning", S.TEMPLATES["whatsapp"][0]))
        return decisions

    def observe(self, day, outcomes, portfolio):
        pass  # a fixed schedule never learns


class ReferencePolicy:
    """STAND-IN until K09 plugs in the real Kairos policy.
    Respects caps and spacing, learns reply rates per (DPD band, channel, slot),
    and spends the budget on the contacts with the best expected replies per rupee."""

    name = "kairos"
    label = "Learning policy (reference stand-in until K09)"

    def __init__(self):
        # [replies + 1, non-replies + 1]: starting at 1/1 means "no evidence yet, assume 50%"
        self.stats = defaultdict(lambda: [1.0, 1.0])

    def plan_day(self, day, portfolio, log):
        candidates = []
        for b in portfolio:
            if not is_eligible(b):
                continue
            if log.previous_7_days(b.index, day) + 1 > S.MAX_CONTACTS_PER_7_DAYS:
                continue
            last = log.last_day(b.index)
            if last is not None and day - last < S.MIN_DAYS_BETWEEN_CONTACTS:
                continue
            best = None
            for channel in sorted(b.consents):
                for slot in S.SLOTS:
                    wins, losses = self.stats[(b.dpd_band, channel, slot)]
                    value = (wins / (wins + losses)) / S.COST_PAISE[channel]
                    if best is None or value > best[0]:
                        best = (value, channel, slot)
            candidates.append((best[0], b.dpd, b.index, best[1], best[2]))

        candidates.sort(key=lambda c: (-c[0], -c[1], c[2]))  # best value first, then most overdue
        decisions, spent = [], 0
        for _, _, index, channel, slot in candidates:
            cost = S.COST_PAISE[channel]
            if spent + cost > S.DAILY_BUDGET_PAISE:
                continue
            spent += cost
            decisions.append(Decision(index, channel, slot, S.TEMPLATES[channel][0]))
        return decisions

    def observe(self, day, outcomes, portfolio):
        for o in outcomes:
            key = (portfolio[o.borrower_index].dpd_band, o.channel, o.slot)
            self.stats[key][0 if o.meaningful_reply else 1] += 1


POLICIES = {"baseline": FixedCadenceBaseline, "kairos": ReferencePolicy}
