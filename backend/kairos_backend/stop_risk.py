"""K09 opt-out (STOP) risk: P(STOP | touch) per channel x contact-number segment, learned from the ledger.

Why: a STOP suppresses the borrower on every channel for the rest of the campaign (K01 holds.STOP_OPT_OUT),
and on the replay's tune seeds STOPs per reply were about 3x higher on a borrower's 2nd+ touch than on the
first (3-4 vs 11-15 per 100 replies), for CN10 and K09 alike. Ranking by replies alone ignores that cost.

Attribution: a STOP event (`Ledger.record_stop` -> spine `events`, consent_changed "STOP: ...") is charged
to the borrower's executed touch whose reply window [executed_at, window_end] contains it. Only touches
whose window has closed by the decision time count, so the estimate never uses the future.

Segments: channel x n_prior, where n_prior is how many executed touches the borrower had before this one
(0, 1, 2, 3+). Hierarchical Beta shrinkage, each level centred on the one above:

    pooled   (stops + m0*s0) / (touches + s0)           prior mean m0, strength s0
    channel  (stops_c + k*pooled) / (touches_c + k)
    segment  (stops_cs + k*channel) / (touches_cs + k)

Everything comes from the ledger's touch_outcomes and the spine events table. No simulator internals.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

SEGMENTS = ("n0", "n1", "n2", "n3+")


def segment_of(n_prior: int) -> str:
    return SEGMENTS[min(max(int(n_prior), 0), len(SEGMENTS) - 1)]


class StopRiskModel:
    def __init__(self, ledger, prior_mean: float = 0.003, prior_strength: float = 2000.0, strength: float = 1500.0):
        if not (0 < prior_mean < 1) or prior_strength <= 0 or strength <= 0:
            raise ValueError("prior_mean must be in (0,1); prior_strength and strength must be > 0")
        self.ledger = ledger
        self.prior_mean, self.prior_strength, self.strength = float(prior_mean), float(prior_strength), float(strength)
        self._cache: dict = {}

    # ---------- evidence ----------
    def counts(self, at: datetime) -> dict[tuple[str, str], list[int]]:
        """(channel, segment) -> [stops, touches] over touches whose reply window closed by `at`."""
        at_iso = self.ledger._iso(at)
        hit = self._cache.get(at_iso)
        if hit is not None:
            return hit
        rows = self.ledger._conn().execute(
            "SELECT o.channel, o.n_prior, EXISTS (SELECT 1 FROM events e WHERE e.borrower_id = o.borrower_id"
            "   AND e.event_type = 'consent_changed' AND e.details LIKE 'STOP%'"
            "   AND e.occurred_at >= o.executed_at AND e.occurred_at <= o.window_end) AS stopped"
            " FROM (SELECT instruction_id, borrower_id, channel, executed_at, window_end,"
            "        ROW_NUMBER() OVER (PARTITION BY borrower_id ORDER BY executed_at, instruction_id) - 1 AS n_prior"
            "       FROM touch_outcomes WHERE window_end <= ?) o", (at_iso,)).fetchall()
        out: dict[tuple[str, str], list[int]] = {}
        for r in rows:
            acc = out.setdefault((r["channel"], segment_of(r["n_prior"])), [0, 0])
            acc[0] += int(r["stopped"])
            acc[1] += 1
        self._cache = {at_iso: out}   # one entry: decisions arrive in time order
        return out

    def n_prior(self, borrower_id: str, at: datetime) -> int:
        return self.ledger._conn().execute(
            "SELECT COUNT(*) FROM touch_outcomes WHERE borrower_id=? AND executed_at < ?",
            (borrower_id, self.ledger._iso(at))).fetchone()[0]

    # ---------- posterior ----------
    def p_stop(self, channel: str, segment: str, at: datetime) -> float:
        counts = self.counts(at)
        s = sum(v[0] for v in counts.values())
        n = sum(v[1] for v in counts.values())
        pooled = (s + self.prior_mean * self.prior_strength) / (n + self.prior_strength)
        sc = sum(v[0] for (ch, _), v in counts.items() if ch == channel)
        nc = sum(v[1] for (ch, _), v in counts.items() if ch == channel)
        p_channel = (sc + self.strength * pooled) / (nc + self.strength)
        ss, ns = counts.get((channel, segment), (0, 0))
        return (ss + self.strength * p_channel) / (ns + self.strength)

    def for_borrower(self, borrower_id: Optional[str], channel: str, at: datetime) -> float:
        seg = segment_of(self.n_prior(borrower_id, at)) if borrower_id else SEGMENTS[0]
        return self.p_stop(channel, seg, at)

    def estimates(self, at: datetime, channels: tuple[str, ...] = ("whatsapp", "sms", "ivr")) -> list[dict[str, Any]]:
        counts = self.counts(at)
        return [{"channel": ch, "segment": seg, "stops": counts.get((ch, seg), (0, 0))[0],
                 "touches": counts.get((ch, seg), (0, 0))[1], "p_stop": round(self.p_stop(ch, seg, at), 5)}
                for ch in channels for seg in SEGMENTS]
