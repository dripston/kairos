"""K09 outcome finalizer: turns executed touches into FINAL learning outcomes (K01 reply_attribution).

One row per executed touch in `touch_outcomes` (same SQLite DB as the ledger):

    register_execution()   touch went out           -> PENDING, window_end = executed_at + reply window
    record_reach()         delivered / connected    -> reach only, never a reply
    record_reply()         meaningful reply         -> attributed to the borrower's most recent executed
                                                       touch whose window contains it (one reward per touch)
                           click / read             -> reach only
                           after finalization       -> late_reply_at, reward unchanged
    record_payment_verified()                       -> tracked separately, never a reply reward
    finalize_due(now)      window closed            -> REPLY | NO_REPLY | UNRESOLVED; REPLY / NO_REPLY go to
                                                       learner.apply_outcome() exactly once

Finalization rules (in order):
    reply attributed                                          -> REPLY
    ledger touch FAILED / CANCELED (never went out)           -> UNRESOLVED
    reach known (provider confirmed delivered/connected or
      explicitly not connected)                               -> NO_REPLY
    ledger touch status SENT / DELIVERED                      -> NO_REPLY
    anything else (UNCERTAIN, no provider info)               -> UNRESOLVED (kept out of learning)

These calls only record outcomes. Ledger side effects of a reply (stop-for-day) or a payment
(clear the obligation) stay with Ledger.record_response / Ledger.record_payment_verified.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

from .contracts import Learner, Outcome
from .ledger import Ledger

SCHEMA = """
CREATE TABLE IF NOT EXISTS touch_outcomes (
  instruction_id TEXT PRIMARY KEY, borrower_id TEXT NOT NULL, band TEXT NOT NULL, channel TEXT NOT NULL,
  slot TEXT NOT NULL, executed_at TEXT NOT NULL, window_end TEXT NOT NULL, status TEXT NOT NULL,
  reached INTEGER, reply_at TEXT, late_reply_at TEXT, payment_verified_at TEXT, finalized_at TEXT,
  learned_at TEXT);
CREATE INDEX IF NOT EXISTS ix_touch_outcomes_borrower ON touch_outcomes (borrower_id, executed_at);
CREATE INDEX IF NOT EXISTS ix_touch_outcomes_status ON touch_outcomes (status, window_end);
"""

PENDING, REPLY, NO_REPLY, UNRESOLVED = "PENDING", "REPLY", "NO_REPLY", "UNRESOLVED"
FINAL = (REPLY, NO_REPLY, UNRESOLVED)
_LEARNABLE = (REPLY, NO_REPLY)

# record_reply() results
REPLY_ATTRIBUTED = "REPLY_ATTRIBUTED"
DUPLICATE = "DUPLICATE"
LATE_REPLY = "LATE_REPLY"
REACH_ONLY = "REACH_ONLY"
UNATTRIBUTED = "UNATTRIBUTED"


class OutcomeFinalizer:
    def __init__(self, ledger: Ledger, learner: Learner, reply_window_hours: Optional[float] = None):
        self.ledger = ledger
        self.learner = learner
        if reply_window_hours is None:
            reply_window_hours = ledger.policy.raw["reply_attribution"]["reply_window_hours"]
        self.reply_window = timedelta(hours=float(reply_window_hours))
        self.ledger._conn().executescript(SCHEMA)

    # ---------- helpers ----------
    def _iso(self, dt: datetime) -> str:
        return self.ledger.policy.local(dt).isoformat(timespec="seconds")

    def _dt(self, s: Optional[str]) -> Optional[datetime]:
        return self.ledger.policy.local(datetime.fromisoformat(s)) if s else None

    def _outcome(self, r) -> Outcome:
        return Outcome(instruction_id=r["instruction_id"], borrower_id=r["borrower_id"], band=r["band"],
                       channel=r["channel"], slot=r["slot"], executed_at=self._dt(r["executed_at"]),
                       window_end=self._dt(r["window_end"]), status=r["status"],
                       reached=None if r["reached"] is None else bool(r["reached"]),
                       reply_at=self._dt(r["reply_at"]), finalized_at=self._dt(r["finalized_at"]))

    def outcome(self, instruction_id: str) -> Optional[Outcome]:
        r = self.ledger._conn().execute("SELECT * FROM touch_outcomes WHERE instruction_id=?",
                                        (instruction_id,)).fetchone()
        return self._outcome(r) if r else None

    def row(self, instruction_id: str) -> Optional[dict]:
        """Raw touch_outcomes row (includes late_reply_at / payment_verified_at)."""
        r = self.ledger._conn().execute("SELECT * FROM touch_outcomes WHERE instruction_id=?",
                                        (instruction_id,)).fetchone()
        return dict(r) if r else None

    @staticmethod
    def _latest_touch(c, borrower_id: str, at_iso: str, channel: Optional[str] = None):
        """The borrower's most recent touch executed at or before `at` (optionally on one channel)."""
        q = "SELECT * FROM touch_outcomes WHERE borrower_id=? AND executed_at <= ?"
        args: tuple = (borrower_id, at_iso)
        if channel is not None:
            q, args = q + " AND channel=?", args + (channel,)
        return c.execute(q + " ORDER BY executed_at DESC, instruction_id DESC LIMIT 1", args).fetchone()

    # ---------- events ----------
    def register_execution(self, instruction_id: str, borrower_id: str, band: str, channel: str, slot: str,
                           executed_at: datetime) -> bool:
        """Touch executed -> PENDING. Returns False if it was already registered (idempotent)."""
        executed = self.ledger.policy.local(executed_at)
        with self.ledger.tx() as c:
            cur = c.execute(
                "INSERT OR IGNORE INTO touch_outcomes (instruction_id, borrower_id, band, channel, slot, executed_at,"
                " window_end, status) VALUES (?,?,?,?,?,?,?,?)",
                (instruction_id, borrower_id, band, channel, slot, self._iso(executed),
                 self._iso(executed + self.reply_window), PENDING))
            return cur.rowcount == 1

    def record_reach(self, instruction_id: str, reached: bool, at: Optional[datetime] = None) -> bool:
        """Delivery / connection callback. Reach only, never a reply. Reach never goes back from
        True to False; a repeat callback is a no-op. Returns True if the row changed."""
        with self.ledger.tx() as c:
            r = c.execute("SELECT reached FROM touch_outcomes WHERE instruction_id=?", (instruction_id,)).fetchone()
            if r is None:
                return False
            new = 1 if reached else 0
            if r["reached"] is not None and (r["reached"] == new or r["reached"] == 1):
                return False
            c.execute("UPDATE touch_outcomes SET reached=? WHERE instruction_id=?", (new, instruction_id))
            return True

    def record_reply(self, borrower_id: str, at: datetime, meaningful: bool, channel: Optional[str] = None) -> str:
        """Attribute a borrower reply. Returns REPLY_ATTRIBUTED | DUPLICATE | LATE_REPLY | REACH_ONLY |
        UNATTRIBUTED. `channel` narrows attribution to touches on that channel when given."""
        at_iso = self._iso(at)
        with self.ledger.tx() as c:
            t = self._latest_touch(c, borrower_id, at_iso, channel)
            if t is None:
                return UNATTRIBUTED
            iid = t["instruction_id"]
            in_window = at_iso <= t["window_end"]
            if not meaningful:
                # click / read: proves reachability, never a reply (K01 click_effect)
                if in_window and t["reached"] != 1:
                    c.execute("UPDATE touch_outcomes SET reached=1 WHERE instruction_id=?", (iid,))
                return REACH_ONLY if in_window else UNATTRIBUTED
            if t["reply_at"] is not None:
                return DUPLICATE  # one reward per touch
            if t["status"] == PENDING and in_window:
                c.execute("UPDATE touch_outcomes SET reply_at=?, reached=1 WHERE instruction_id=?", (at_iso, iid))
                return REPLY_ATTRIBUTED
            # window closed or touch already finalized: record it, the reward does not change
            if t["late_reply_at"] is not None:
                return DUPLICATE
            c.execute("UPDATE touch_outcomes SET late_reply_at=? WHERE instruction_id=?", (at_iso, iid))
            return LATE_REPLY

    def record_payment_verified(self, borrower_id: str, at: datetime) -> Optional[str]:
        """Note a verified payment against the borrower's most recent touch whose window contains it.
        Payments are tracked separately and never count as a reply reward. Returns the instruction_id
        it was noted on, or None (no touch window contains it, or already noted)."""
        at_iso = self._iso(at)
        with self.ledger.tx() as c:
            t = self._latest_touch(c, borrower_id, at_iso)
            if t is None or at_iso > t["window_end"] or t["payment_verified_at"] is not None:
                return None
            c.execute("UPDATE touch_outcomes SET payment_verified_at=? WHERE instruction_id=?",
                      (at_iso, t["instruction_id"]))
            return t["instruction_id"]

    # ---------- finalization ----------
    def _decide(self, c, r) -> str:
        if r["reply_at"] is not None:
            return REPLY
        t = c.execute("SELECT status FROM touches WHERE instruction_id=?", (r["instruction_id"],)).fetchone()
        ledger_status = t["status"] if t else None
        if ledger_status in ("FAILED", "CANCELED"):
            return UNRESOLVED
        if r["reached"] is not None:
            return NO_REPLY
        if ledger_status in ("SENT", "DELIVERED"):
            return NO_REPLY
        return UNRESOLVED

    def finalize_due(self, now: datetime) -> list[Outcome]:
        """Finalize every PENDING touch whose reply window has closed. Idempotent. Returns the
        outcomes finalized by this call; REPLY / NO_REPLY are passed to the learner exactly once."""
        now_iso = self._iso(now)
        finalized: list[Outcome] = []
        with self.ledger.tx() as c:
            rows = c.execute("SELECT * FROM touch_outcomes WHERE status=? AND window_end <= ?"
                             " ORDER BY window_end, instruction_id", (PENDING, now_iso)).fetchall()
            for r in rows:
                status = self._decide(c, r)
                c.execute("UPDATE touch_outcomes SET status=?, finalized_at=? WHERE instruction_id=? AND status=?",
                          (status, now_iso, r["instruction_id"], PENDING))
                finalized.append(self._outcome({**dict(r), "status": status, "finalized_at": now_iso}))
        # Learning happens after the commit (the learner writes through its own transaction).
        # learned_at marks it done; a crash in between is healed by the sweep below on the next run,
        # and apply_outcome is itself idempotent per instruction_id.
        self._learn(o for o in finalized if o.status in _LEARNABLE)
        self._learn(self._outcome(r) for r in self.ledger._conn().execute(
            "SELECT * FROM touch_outcomes WHERE status IN (?,?) AND learned_at IS NULL", _LEARNABLE).fetchall())
        return finalized

    def _learn(self, outcomes) -> None:
        for o in list(outcomes):
            self.learner.apply_outcome(o)
            with self.ledger.tx() as c:
                c.execute("UPDATE touch_outcomes SET learned_at=? WHERE instruction_id=? AND learned_at IS NULL",
                          (self._iso(self.ledger.policy.now()), o.instruction_id))

    # ---------- reporting ----------
    def metrics(self, campaign_id: Optional[str] = None) -> dict[str, Any]:
        """Separate counts: execution, reach, replies, payments are never merged. A touch counts as
        reached when the provider confirmed delivery/connection, a reply/click proved it, or the
        ledger shows it DELIVERED."""
        where, args = "", ()
        if campaign_id is not None:
            where, args = " WHERE t.campaign_id=?", (campaign_id,)
        reached = "(o.reached=1 OR t.status='DELIVERED')"
        r = self.ledger._conn().execute(
            "SELECT COUNT(*) executed,"
            f" SUM(CASE WHEN {reached} THEN 1 ELSE 0 END) reached_touches,"
            f" COUNT(DISTINCT CASE WHEN {reached} THEN o.borrower_id END) reached_borrowers,"
            " SUM(CASE WHEN o.status='REPLY' THEN 1 ELSE 0 END) replies,"
            " SUM(CASE WHEN o.status='NO_REPLY' THEN 1 ELSE 0 END) no_replies,"
            " SUM(CASE WHEN o.status='PENDING' THEN 1 ELSE 0 END) pending,"
            " SUM(CASE WHEN o.status='UNRESOLVED' THEN 1 ELSE 0 END) unresolved,"
            " SUM(CASE WHEN o.payment_verified_at IS NOT NULL THEN 1 ELSE 0 END) verified_payments,"
            " COUNT(DISTINCT CASE WHEN o.payment_verified_at IS NOT NULL THEN o.borrower_id END) paying_borrowers,"
            " SUM(CASE WHEN o.late_reply_at IS NOT NULL THEN 1 ELSE 0 END) late_replies,"
            " COUNT(DISTINCT o.borrower_id) executed_borrowers"
            " FROM touch_outcomes o LEFT JOIN touches t ON t.instruction_id = o.instruction_id" + where,
            args).fetchone()
        out = {k: int(r[k] or 0) for k in r.keys()}
        out["campaign_id"] = campaign_id
        return out
