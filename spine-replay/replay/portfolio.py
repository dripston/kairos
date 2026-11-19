"""Synthetic 50K portfolio and contact history. Same seed = exactly the same portfolio."""
import random
from collections import defaultdict
from dataclasses import dataclass

from spine.policy import dpd_band  # K01 policy.json bands, the same ones K03 uses


@dataclass
class Borrower:
    index: int
    borrower_id: str
    dpd: int
    dpd_band: str
    consents: frozenset
    language: str
    opted_out: bool = False
    paid: bool = False


CONSENT_RATES = {"whatsapp": 0.75, "sms": 0.95, "email": 0.50, "ivr": 0.60, "voicebot": 0.40}


def generate_portfolio(n, seed):
    rng = random.Random(seed)
    borrowers = []
    for i in range(n):
        dpd = rng.choice((rng.randint(1, 29), rng.randint(1, 29), rng.randint(30, 59),
                          rng.randint(30, 59), rng.randint(60, 89), rng.randint(90, 180)))
        consents = frozenset(ch for ch, rate in CONSENT_RATES.items() if rng.random() < rate)
        borrowers.append(Borrower(
            index=i,
            borrower_id=f"SYN-{i:06d}",
            dpd=dpd,
            dpd_band=dpd_band(dpd),
            consents=consents,
            language=rng.choice(("en", "hi", "kn", "ta", "te")),
            opted_out=rng.random() < 0.02,  # some borrowers start the campaign already opted out
        ))
    return borrowers


def is_eligible(b):
    """Can this borrower be contacted at all today?"""
    # K01: missing DPD -> HOLD (never contacted).
    return b.dpd is not None and b.dpd > 0 and not b.paid and not b.opted_out and bool(b.consents)


class ContactLog:
    """Which days each borrower was contacted. Shared, read-only, with every policy."""

    def __init__(self):
        self._days = defaultdict(list)

    def record(self, index, day):
        self._days[index].append(day)

    def count_between(self, index, first_day, last_day):
        return sum(1 for d in self._days.get(index, ()) if first_day <= d <= last_day)

    def previous_7_days(self, index, day):
        """Contacts in the 6 days before `day` (today's contacts are added on top for the 7-day cap)."""
        return self.count_between(index, day - 6, day - 1)

    def last_day(self, index):
        days = self._days.get(index)
        return days[-1] if days else None


def portfolio_from_csv(path):
    """Build the replay portfolio from a dataset file (same format the spine loader reads),
    so K03 and K15 use exactly the same borrowers."""
    from spine.importer import parse_row, read_rows

    borrowers = []
    for _, row in read_rows(path):
        r = parse_row(row)
        borrowers.append(Borrower(
            index=len(borrowers),
            borrower_id=r["borrower_id"],
            dpd=r["file_dpd"],
            dpd_band=dpd_band(r["file_dpd"]),
            consents=frozenset(ch for ch, flag in r["consents"].items() if flag),
            language="en",
            opted_out=bool(r["opted_out"]),
        ))
    return borrowers
