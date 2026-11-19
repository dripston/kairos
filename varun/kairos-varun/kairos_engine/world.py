"""Dev campaign simulator - the "world" that borrowers live in.

Stand-in for the official CM-02 campaign simulator (pending, K02). It answers one
question: "if we send this touch, what happens?" Policies never read anything in
here directly; they only see the outcomes, exactly like the official simulator API.

What makes it realistic (the brief's "what makes it hard"):
  * reply chances differ by segment, channel, time slot and borrower
  * they DRIFT mid-campaign (a different seed moves the drift day and sizes)
  * over-contact lowers future replies (fatigue) and raises STOP risk
To plug in the official simulator later, keep the same method names
(`reply_prob`, `touch_outcomes`, `self_cure`) and call its API inside them.
"""
import numpy as np

from common import SMS, WA, EMAIL, IVR, VOICEBOT, uniform

# Reply chance once a message is delivered (or a call is answered). Pre-jitter.
BASE_REPLY = np.array([0.022, 0.070, 0.016, 0.100, 0.120])
DELIVER = np.array([0.95, 0.90, 0.80, 0.70, 0.70])     # delivered / call connected
EMAIL_HARD_BOUNCE = 0.04                                 # on top of non-delivery
ANSWER_GIVEN_CONNECTED = 0.60                            # call answered > 10 s
WA_READ, CLICK = 0.65, 0.08
SLOT_MULT = {0: [0.7, 0.6, 1.3, 0.7, 0.9, 1.6],          # salaried: lunch + evening
             1: [1.3, 1.4, 0.9, 1.0, 0.8, 0.7]}          # self-employed: mornings
RISK_MULT = np.array([1.40, 1.25, 1.00, 0.70, 0.90])     # C1..C5
BAND_MULT = np.array([1.30, 1.00, 0.80, 0.60, 0.45])     # PRE_DUE..B4
FATIGUE = 0.80                                           # per touch in the last 3 days
STOP_BASE = 0.0008
SELF_CURE = np.array([0.020, 0.015, 0.010, 0.005, 0.008])
# meaningful reply -> what the borrower said
REPLY_TYPES = ["WILLING", "PTP", "NEEDS_TIME", "HARDSHIP", "DISPUTE", "REFUSED"]
REPLY_CUM = np.cumsum([0.24, 0.34, 0.22, 0.05, 0.05, 0.10])
PTP_KEEP = np.array([0.75, 0.68, 0.58, 0.40, 0.50])
WILLING_PAYS = 0.65


class World:
    def __init__(self, borrowers, seed):
        self.seed = seed
        n = len(borrowers)
        self.emp = borrowers["emp_idx"].to_numpy()
        self.risk = borrowers["risk_idx"].to_numpy()
        age = borrowers["age"].to_numpy()
        u = lambda *k: uniform(seed, 999, *k)
        # seed-specific jitter so a hidden seed is a genuinely different world
        self.base = BASE_REPLY * (0.75 + 0.5 * u(np.arange(5), 1))
        self.slot = np.array([SLOT_MULT[e] for e in (0, 1)]) * (0.85 + 0.3 * u(np.arange(12).reshape(2, 6), 2))
        self.drift_day = int(9 + u(0, 3) * 6)               # day 9..14
        self.drift = np.ones((2, 5))
        self.drift[0, WA] = 0.45 + 0.2 * u(0, 4)            # salaried tire of WhatsApp
        self.drift[0, SMS] = 1.35 + 0.3 * u(0, 5)
        self.drift[0, EMAIL] = 1.2
        self.drift[1, IVR] = 0.6
        self.drift[1, WA] = 1.3
        self.drift_slot = np.ones((2, 6))
        self.drift_slot[0, 5], self.drift_slot[0, 2] = 0.7, 1.3
        age_mult = np.ones((n, 5))
        young, old = age < 30, age > 45
        age_mult[young, WA], age_mult[young, EMAIL], age_mult[young, IVR] = 1.3, 1.2, 0.8
        age_mult[old, IVR], age_mult[old, WA] = 1.3, 0.8
        # hidden personal affinity per borrower x channel (mean 1)
        z = np.sqrt(-2 * np.log(u(np.arange(n)[:, None], np.arange(5)[None, :], 6) + 1e-12)) * \
            np.cos(2 * np.pi * u(np.arange(n)[:, None], np.arange(5)[None, :], 7))
        self.person = age_mult * np.exp(0.6 * z - 0.18) * RISK_MULT[self.risk][:, None]

    # ---- the response curve ----
    def reply_prob(self, idx, ch, slot, day, band, recent_touches, drifted=True):
        """P(meaningful reply | delivered/answered) for borrowers idx on channel ch."""
        e = self.emp[idx]
        p = self.base[ch] * self.slot[e, slot] * self.person[idx, ch] * BAND_MULT[np.maximum(band, 0)]
        if drifted and day >= self.drift_day:
            p = p * self.drift[e, ch] * self.drift_slot[e, slot]
        return np.minimum(0.5, p * FATIGUE ** recent_touches)

    def touch_outcomes(self, idx, ch, slot, day, band, recent_touches):
        """Outcome of sending channel ch at slot to borrowers idx on campaign day."""
        u = lambda stream: uniform(self.seed, idx, day, slot, ch, stream)
        r = {}
        d = u(1)
        r["delivered"] = d < DELIVER[ch]
        r["hard_bounce"] = (ch == EMAIL) & (d >= DELIVER[ch]) & (d < DELIVER[ch] + EMAIL_HARD_BOUNCE)
        r["read"] = r["delivered"] & (ch == WA) & (u(2) < WA_READ)
        r["clicked"] = r["delivered"] & (ch in (SMS, WA, EMAIL)) & (u(3) < CLICK)
        r["answered"] = r["delivered"] & (ch in (IVR, VOICEBOT)) & (u(2) < ANSWER_GIVEN_CONNECTED)
        reachable = r["answered"] if ch in (IVR, VOICEBOT) else r["delivered"]
        p = self.reply_prob(idx, ch, slot, day, band, recent_touches)
        stop = reachable & (u(6) < STOP_BASE * (1 + recent_touches))
        r["stop"] = stop
        r["replied"] = reachable & ~stop & (u(4) < p)
        r["reply_type"] = np.where(r["replied"], np.searchsorted(REPLY_CUM, u(5) * REPLY_CUM[-1]), -1)
        r["u_follow"] = u(7)       # used for PTP date / keep / hardship date
        r["u_follow2"] = u(8)
        return r

    def self_cure(self, idx, day, band):
        """Borrowers who pay today with no contact at all (same draw for both policies)."""
        h = SELF_CURE[self.risk[idx]] * np.where(band == 0, 1.5, np.where(band >= 3, 0.6, 1.0))
        return uniform(self.seed, idx, day, 0, 0, 9) < h

    def history(self, idx, ch, slot, touches):
        """12-month history replies for `touches` past sends at a fixed slot (pre-drift)."""
        p = self.reply_prob(idx, ch, slot, 0, np.ones(len(idx), dtype=np.int8), 0.6, drifted=False)
        p = p * DELIVER[ch] * (ANSWER_GIVEN_CONNECTED if ch in (IVR, VOICEBOT) else 1.0)
        rng = np.random.default_rng([self.seed, ch, 77])
        return rng.binomial(touches, p)
