"""DEV SIMULATOR: a labeled stand-in for the official CM-02 campaign simulator (blocked on K02).

Each borrower has hidden traits the policies cannot see (how responsive they are,
which channel and time they prefer). Random draws are keyed by (seed, day, borrower,
purpose), so both policies face the same "luck" and every run is repeatable.
"""
import random
from dataclasses import dataclass

from replay import settings as S

DELIVERY_RATE = {"whatsapp": 0.93, "sms": 0.96, "email": 0.80, "ivr": 0.50, "voicebot": 0.40}
# Keys are the K01 band IDs from policy.json.
BAND_EFFECT = {"PRE_DUE": 1.0, "B1": 1.0, "B2": 0.8, "B3": 0.6, "B4": 0.4,
               "CLEARED": 1.0, "NOT_IN_SCOPE": 1.0, "DPD_INVALID": 1.0}
_MASK = (1 << 64) - 1
_DELIVER, _REPLY, _STOP, _CLAIM, _VERIFY = 1, 2, 3, 4, 5


def _mix(x):
    x = (x + 0x9E3779B97F4A7C15) & _MASK
    x = ((x ^ (x >> 30)) * 0xBF58476D1CE4E5B9) & _MASK
    x = ((x ^ (x >> 27)) * 0x94D049BB133111EB) & _MASK
    return x ^ (x >> 31)


def _uniform(seed, day, index, purpose):
    x = _mix(_mix(_mix(_mix(seed) ^ day) ^ index) ^ purpose)
    return x / 2 ** 64


@dataclass
class Outcome:
    borrower_index: int
    channel: str
    slot: str
    cost_paise: int
    delivered: bool
    meaningful_reply: bool
    opted_out: bool
    payment_claimed: bool
    payment_verified: bool


class DevSimulator:
    label = "DEV simulator (replace with official CM-02 simulator)"

    def __init__(self, portfolio, seed):
        self.seed = seed
        rng = random.Random(seed + 1)
        self.responsiveness, self.affinity, self.best_slot = [], [], []
        for b in portfolio:
            self.responsiveness.append(0.01 + 0.15 * rng.random() ** 2)
            self.affinity.append({
                "whatsapp": 1.6 * rng.uniform(0.5, 1.5),
                "sms": 0.7 * rng.uniform(0.5, 1.5),
                "email": 0.3 * rng.uniform(0.5, 1.5),
                "ivr": 0.9 * rng.uniform(0.5, 1.5),
                "voicebot": 1.8 * rng.uniform(0.5, 1.5),
            })
            self.best_slot.append(rng.choice(S.SLOTS))

    def run_day(self, day, decisions, portfolio, log):
        outcomes, today = [], {}
        for d in decisions:
            i, ch = d.borrower_index, d.channel
            today[i] = today.get(i, 0) + 1
            recent = log.previous_7_days(i, day)
            fatigue = 0.8 ** recent * (0.5 if today[i] > 1 else 1.0)  # over-contact lowers response

            delivered = _uniform(self.seed, day, i, _DELIVER * 10 + today[i]) < DELIVERY_RATE[ch]
            reply = stop = claimed = verified = False
            if delivered:
                p = (self.responsiveness[i] * self.affinity[i][ch] * BAND_EFFECT[portfolio[i].dpd_band]
                     * (1.3 if d.slot == self.best_slot[i] else 0.8) * fatigue)
                if _uniform(self.seed, day, i, _REPLY * 10 + today[i]) < min(p, 0.95):
                    if _uniform(self.seed, day, i, _STOP) < 0.04 * (1 + recent):
                        stop = True        # "STOP" is a reply, but not a meaningful one
                    else:
                        reply = True
                        claimed = _uniform(self.seed, day, i, _CLAIM) < 0.30
                        verified = claimed and _uniform(self.seed, day, i, _VERIFY) < 0.90  # ~10% fake/unmatched
            outcomes.append(Outcome(i, ch, d.slot, S.COST_PAISE[ch], delivered, reply, stop, claimed, verified))
        return outcomes
