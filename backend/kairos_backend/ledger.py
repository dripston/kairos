"""The Ledger: K06's state, built on the K03 account-and-loan spine.

Who owns what (one SQLite file, two sets of tables):
  K03 spine  borrowers, consents, loans, obligations (EMIs), payments, events
             -> the source of truth for WHO the borrower is, WHAT they owe and WHAT they paid.
             K06 never keeps a copy: DPD is computed live from EMI due dates, "cleared" is
             computed from amounts paid, consent is read from the consents table.
  K06        campaigns, enrollments, budget_days, borrower_state (holds), templates,
             touches (reservations), stop_for_day, tickets, audit

The atomic core: reserve() computes the facts and writes the reservation inside ONE write
transaction (SQLite BEGIN IMMEDIATE), so two workers can never both see "2 of 3" and both send.
Reserved touches count toward caps and budget immediately.

Counted per borrower ACROSS ALL CAMPAIGNS (K01 caps.scope). Days are local (Asia/Kolkata).
Weekly caps use a rolling 7-day window. Money is integer paise everywhere (as in K03).
"""
from __future__ import annotations

import hashlib
import json
import operator
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Iterator, Optional

from spine.db import create_schema as create_spine_schema
from spine.db import get_connection as spine_connection
from spine.ledger import PaymentError, apply_payment, record_event, reverse_payment

from .exceptions import ExceptionEvent, handle
from .gate import SEND, WAIT, Decision, Facts, evaluate
from .policy import Policy

__all__ = ["Ledger", "Instruction", "ReserveResult", "PaymentError", "stable_instruction_id", "MAX_PAISE", "check_paise"]

# statuses that may reach (or already reached) the borrower -> count toward caps and budget
LIVE = ("RESERVED", "DISPATCHING", "UNCERTAIN", "SENT", "DELIVERED")
# provider status ranks: an older/lower status never overwrites a newer one (E04)
RANK = {"RESERVED": 0, "DISPATCHING": 1, "UNCERTAIN": 2, "SENT": 3, "DELIVERED": 4}
# Ceiling for any single money value (budget per case, cost estimate, actual charge): 10^12 paise = Rs.1,000 crore.
# Far above any real figure, far below SQLite's INTEGER limit (2^63 - 1), so sums never overflow (RT-F01).
MAX_PAISE = 10 ** 12


def check_paise(value, field: str, positive: bool = False) -> int:
    """Money guard (RT-F01): a whole number of paise in [0, MAX_PAISE] ([1, MAX_PAISE] when positive).
    Anything else (negative, fractional, bool, text, overflowing) raises ValueError -> 422 at the API,
    so a bad charge can never mint budget or reach SQLite as an overflow."""
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a whole number of paise, got bool")
    try:
        v = operator.index(value)
    except TypeError:
        raise ValueError(f"{field} must be a whole number of paise, got {type(value).__name__}") from None
    lo = 1 if positive else 0
    if not lo <= v <= MAX_PAISE:
        raise ValueError(f"{field} must be between {lo} and {MAX_PAISE} paise, got {v}")
    return v

K06_SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS campaigns (
  id TEXT PRIMARY KEY, name TEXT, status TEXT NOT NULL DEFAULT 'RUNNING',
  budget_per_case_paise INTEGER NOT NULL, start_date TEXT NOT NULL, end_date TEXT NOT NULL,
  target_amount_pct REAL, target_count_pct REAL,
  allocated_cases INTEGER NOT NULL DEFAULT 0, overdue_at_start_paise INTEGER NOT NULL DEFAULT 0,
  committed_paise INTEGER NOT NULL DEFAULT 0, collected_paise INTEGER NOT NULL DEFAULT 0,
  cured_count INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS enrollments (
  campaign_id TEXT NOT NULL, loan_id TEXT NOT NULL REFERENCES loans(loan_id), borrower_id TEXT NOT NULL,
  overdue_at_start_paise INTEGER NOT NULL, cured INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (campaign_id, loan_id));
CREATE INDEX IF NOT EXISTS ix_enroll_loan ON enrollments (loan_id);
CREATE TABLE IF NOT EXISTS budget_days (
  campaign_id TEXT, day TEXT, allowance_paise INTEGER NOT NULL, committed_paise INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (campaign_id, day));
CREATE TABLE IF NOT EXISTS borrower_state (
  borrower_id TEXT PRIMARY KEY REFERENCES borrowers(borrower_id), lender_id TEXT,
  stopped INTEGER NOT NULL DEFAULT 0,
  hardship_until TEXT, hardship_supervisor INTEGER NOT NULL DEFAULT 0,
  callback_pending INTEGER NOT NULL DEFAULT 0, supervisor_hold INTEGER NOT NULL DEFAULT 0,
  ptp_date TEXT, ptp_obligation TEXT, broken_ptp INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS templates (
  id TEXT PRIMARY KEY, channel TEXT NOT NULL, lang TEXT NOT NULL, approved INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS touches (
  instruction_id TEXT PRIMARY KEY, borrower_id TEXT NOT NULL, loan_id TEXT, obligation_id TEXT, campaign_id TEXT,
  channel TEXT NOT NULL, template_id TEXT, scheduled_at TEXT NOT NULL, day TEXT NOT NULL,
  status TEXT NOT NULL, reason TEXT, est_cost_paise INTEGER NOT NULL DEFAULT 0, actual_cost_paise INTEGER,
  reconciled INTEGER NOT NULL DEFAULT 0, provider_ref TEXT, created_at TEXT, updated_at TEXT);
CREATE INDEX IF NOT EXISTS ix_touch_borrower_day ON touches (borrower_id, day);
CREATE INDEX IF NOT EXISTS ix_touch_borrower_channel ON touches (borrower_id, channel, scheduled_at);
CREATE INDEX IF NOT EXISTS ix_touch_status ON touches (status, scheduled_at);
CREATE INDEX IF NOT EXISTS ix_touch_obligation ON touches (obligation_id, status);
CREATE TABLE IF NOT EXISTS stop_for_day (
  borrower_id TEXT, day TEXT, trigger TEXT, PRIMARY KEY (borrower_id, day));
CREATE TABLE IF NOT EXISTS tickets (
  ticket_id TEXT PRIMARY KEY, idempotency_key TEXT UNIQUE, queue TEXT, borrower_id TEXT, obligation_id TEXT,
  type TEXT, summary_line_1 TEXT, summary_line_2 TEXT, evidence_ids TEXT, status TEXT NOT NULL DEFAULT 'OPEN',
  created_at TEXT, sla_due_at TEXT, policy_version TEXT);
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, actor TEXT, type TEXT, borrower_id TEXT,
  instruction_id TEXT, decision TEXT, reason TEXT, rule TEXT, policy_version TEXT, detail TEXT);
"""


@dataclass
class Instruction:
    """One proposed touch for ONE EMI (K03 obligation). instruction_id is stable: the same
    instruction can be submitted twice (retries, two workers) and is reserved only once."""
    borrower_id: str
    obligation_id: str
    campaign_id: str
    channel: str
    template_id: Optional[str]
    at: datetime
    est_cost_paise: int
    purpose: Optional[str] = None
    instruction_id: Optional[str] = None  # None -> stable id derived from the touch itself
    version: int = 1


def stable_instruction_id(borrower_id: str, obligation_id: str, channel: str, at: datetime, version: int = 1) -> str:
    """Same borrower + EMI + channel + slot + version -> same id, so two workers (or a
    retry) proposing the identical touch collapse into one reservation."""
    key = f"{borrower_id}|{obligation_id}|{channel}|{at.isoformat(timespec='minutes')}|v{version}"
    return "INS-" + hashlib.sha1(key.encode()).hexdigest()[:16]


@dataclass
class ReserveResult:
    decision: Decision
    instruction_id: str
    reserved: bool
    idempotent_replay: bool = False

    def as_dict(self) -> dict:
        return {**self.decision.as_dict(), "instruction_id": self.instruction_id,
                "reserved": self.reserved, "idempotent_replay": self.idempotent_replay}


class Ledger:
    def __init__(self, policy: Policy, path: str | Path):
        self.policy = policy
        self.path = str(path)
        self._local = threading.local()
        create_spine_schema(self._spine())   # K03 tables
        self._conn().executescript(K06_SCHEMA)  # K06 tables

    # ---------- connections ----------
    def _conn(self) -> sqlite3.Connection:
        """K06 connection: autocommit; every write goes through tx()."""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=30, isolation_level=None, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA busy_timeout=30000")
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
        return conn

    def _spine(self) -> sqlite3.Connection:
        """K03-style connection for calling spine functions, which manage their own
        transactions with `with conn:` (so they must not run inside tx())."""
        conn = getattr(self._local, "spine", None)
        if conn is None:
            conn = spine_connection(self.path)
            conn.execute("PRAGMA busy_timeout=30000")
            self._local.spine = conn
        return conn

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """One write transaction. BEGIN IMMEDIATE takes the write lock up front, so the
        read-check-write inside cannot interleave with another writer."""
        c = self._conn()
        c.execute("BEGIN IMMEDIATE")
        try:
            yield c
            c.execute("COMMIT")
        except BaseException:
            c.execute("ROLLBACK")
            raise

    def close(self):
        for name in ("conn", "spine"):
            conn = getattr(self._local, name, None)
            if conn is not None:
                conn.close()
                setattr(self._local, name, None)

    # ---------- helpers ----------
    def _loc(self, dt: datetime) -> datetime:
        return self.policy.local(dt)

    def _iso(self, dt: datetime) -> str:
        return self._loc(dt).isoformat(timespec="seconds")

    def _dt(self, s: Optional[str]) -> Optional[datetime]:
        return self._loc(datetime.fromisoformat(s)) if s else None

    def _audit(self, c, actor, type_, borrower_id=None, instruction_id=None, decision=None, at=None, **detail):
        c.execute(
            "INSERT INTO audit (ts, actor, type, borrower_id, instruction_id, decision, reason, rule, policy_version, detail)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (self._iso(at or self.policy.now()), actor, type_, borrower_id, instruction_id,
             decision.decision if decision else None, decision.reason if decision else detail.pop("reason", None),
             decision.rule if decision else None, self.policy.version, json.dumps(detail, default=str)),
        )

    def _state(self, c, borrower_id: str) -> dict:
        r = c.execute("SELECT * FROM borrower_state WHERE borrower_id=?", (borrower_id,)).fetchone()
        return dict(r) if r else {"lender_id": None, "stopped": 0, "hardship_until": None, "hardship_supervisor": 0,
                                  "callback_pending": 0, "supervisor_hold": 0, "ptp_date": None,
                                  "ptp_obligation": None, "broken_ptp": 0}

    def _set_state(self, c, borrower_id: str, **fields) -> None:
        c.execute("INSERT OR IGNORE INTO borrower_state (borrower_id) VALUES (?)", (borrower_id,))
        sets = ", ".join(f"{k}=?" for k in fields)
        c.execute(f"UPDATE borrower_state SET {sets} WHERE borrower_id=?", (*fields.values(), borrower_id))

    # ---------- reading the K03 spine ----------
    def _obligation(self, c, obligation_id: str):
        return c.execute(
            "SELECT o.*, l.borrower_id, l.source, l.file_dpd, l.dpd_as_of FROM obligations o"
            " JOIN loans l ON l.loan_id = o.loan_id WHERE o.obligation_id=?", (obligation_id,)).fetchone()

    def _loan_dpd(self, c, ob, day: date) -> Optional[int]:
        """Days past due of the loan = its oldest unpaid EMI (K03 rule). Negative = days until due
        (PRE_DUE). None when the feed gave no DPD for a dataset loan (K01: HOLD, never guess)."""
        if ob["source"] == "dataset" and ob["file_dpd"] is None:
            return None
        r = c.execute("SELECT MIN(due_date) d FROM obligations WHERE loan_id=? AND amount_paid_paise < amount_due_paise",
                      (ob["loan_id"],)).fetchone()
        return (day - date.fromisoformat(r["d"])).days if r["d"] else 0

    def _dpd_age_hours(self, ob, at: datetime) -> float:
        """K01 stale rule: how old the LMS data behind this DPD is. Fresh = the spine was synced
        (mark_spine_synced / start_day) or the loan's own dpd_as_of, whichever is newer."""
        stamps = [s for s in (self.setting("spine_synced_at"), ob["dpd_as_of"]) if s]
        if not stamps:
            return float("inf")
        latest = max(self._dt(s) for s in stamps)
        return max(0.0, (at - latest).total_seconds() / 3600)

    def _loan_cured(self, c, loan_id: str, day: date) -> bool:
        return c.execute("SELECT 1 FROM obligations WHERE loan_id=? AND due_date<=? AND amount_paid_paise<amount_due_paise",
                         (loan_id, day.isoformat())).fetchone() is None

    def mark_spine_synced(self, at: datetime) -> None:
        """Call after each LMS/payment feed refresh. In simulations, start_day() does it."""
        self.set_setting("spine_synced_at", self._iso(at))

    # ---------- settings / setup ----------
    def set_setting(self, key: str, value) -> None:
        with self.tx() as c:
            c.execute("INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                      (key, json.dumps(value)))

    def setting(self, key: str, default=None):
        r = self._conn().execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(r["value"]) if r else default

    def upsert_campaign(self, id: str, budget_per_case_paise: int, start_date: date, end_date: date,
                        target_amount_pct: Optional[float] = None, target_count_pct: Optional[float] = None,
                        name: str = "", status: str = "RUNNING") -> None:
        """budget_per_case and the collection targets are K01 REQUIRED_INPUTs — the caller supplies them.
        The pool is budget_per_case x enrolled cases (see enroll())."""
        budget_per_case_paise = check_paise(budget_per_case_paise, "budget_per_case_paise", positive=True)
        with self.tx() as c:
            c.execute(
                "INSERT INTO campaigns (id, name, status, budget_per_case_paise, start_date, end_date,"
                " target_amount_pct, target_count_pct) VALUES (?,?,?,?,?,?,?,?)"
                " ON CONFLICT(id) DO UPDATE SET name=excluded.name, status=excluded.status,"
                " budget_per_case_paise=excluded.budget_per_case_paise, start_date=excluded.start_date,"
                " end_date=excluded.end_date, target_amount_pct=excluded.target_amount_pct,"
                " target_count_pct=excluded.target_count_pct",
                (id, name, status, budget_per_case_paise, start_date.isoformat(), end_date.isoformat(),
                 target_amount_pct, target_count_pct),
            )

    def enroll(self, campaign_id: str, loan_ids: list[str], as_of: Optional[date] = None) -> int:
        """Allocate loans (cases) to a campaign. Overdue at allocation start = EMIs already due
        on that date and still unpaid (K01 collection_target.amount_pct denominator). Re-enrolling
        a loan does nothing. Returns how many loans were newly enrolled."""
        with self.tx() as c:
            camp = c.execute("SELECT * FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
            if camp is None:
                raise KeyError(f"unknown campaign {campaign_id}")
            day = (as_of or date.fromisoformat(camp["start_date"])).isoformat()
            added, overdue_total = 0, 0
            for loan_id in loan_ids:
                loan = c.execute("SELECT borrower_id FROM loans WHERE loan_id=?", (loan_id,)).fetchone()
                if loan is None:
                    raise KeyError(f"unknown loan {loan_id}")
                overdue = c.execute("SELECT COALESCE(SUM(amount_due_paise - amount_paid_paise), 0) FROM obligations"
                                    " WHERE loan_id=? AND due_date<=?", (loan_id, day)).fetchone()[0]
                cur = c.execute("INSERT OR IGNORE INTO enrollments VALUES (?,?,?,?,?)",
                                (campaign_id, loan_id, loan["borrower_id"], overdue, int(overdue == 0)))
                if cur.rowcount:
                    added += 1
                    overdue_total += overdue
            c.execute("UPDATE campaigns SET allocated_cases = allocated_cases + ?, overdue_at_start_paise ="
                      " overdue_at_start_paise + ? WHERE id=?", (added, overdue_total, campaign_id))
            self._audit(c, "system", "ENROLLED", reason=f"campaign {campaign_id}", added=added)
            return added

    def set_campaign_status(self, campaign_id: str, status: str, actor: str = "human") -> int:
        """PAUSED / STOPPED cancel queued work for the campaign; returns how many touches were canceled."""
        with self.tx() as c:
            if c.execute("UPDATE campaigns SET status=? WHERE id=?", (status, campaign_id)).rowcount == 0:
                raise KeyError(campaign_id)
            n = 0
            if status in ("PAUSED", "STOPPED"):
                n = self._cancel_queued(c, "campaign_id=?", (campaign_id,), f"CAMPAIGN_{status}")
            self._audit(c, actor, f"CAMPAIGN_{status}", reason=f"campaign {campaign_id}", canceled=n)
            return n

    def set_lender(self, borrower_id: str, lender_id: Optional[str]) -> None:
        with self.tx() as c:
            self._set_state(c, borrower_id, lender_id=lender_id)

    def upsert_template(self, id: str, channel: str, lang: str, approved: bool = True) -> None:
        with self.tx() as c:
            c.execute("INSERT INTO templates VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET channel=excluded.channel,"
                      " lang=excluded.lang, approved=excluded.approved", (id, channel, lang, int(approved)))

    def import_dataset(self, csv_path: str | Path, as_of: Optional[date] = None) -> dict:
        """Load a PL_Dummy-format CSV into the spine with K03's importer (validation + report)."""
        from spine.importer import import_dataset
        kwargs = {"as_of": as_of} if as_of else {}
        return import_dataset(self._spine(), str(csv_path), **kwargs)

    # ---------- facts ----------
    def _budget_remaining(self, c, campaign_id: str, day: date) -> tuple[int, dict]:
        """min(pool left, today's pacing allowance left), in paise. Pacing = remaining pool /
        remaining contact days, fixed once per day (floored, so it can never overshoot);
        unspent budget carries forward (K01 §9)."""
        camp = c.execute("SELECT * FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
        pool = camp["budget_per_case_paise"] * camp["allocated_cases"]
        pool_left = pool - camp["committed_paise"]
        row = c.execute("SELECT * FROM budget_days WHERE campaign_id=? AND day=?", (campaign_id, day.isoformat())).fetchone()
        if row is None:
            days_left = len(self.policy.contact_days(day, date.fromisoformat(camp["end_date"])))
            allowance = max(0, pool_left // days_left) if days_left else 0
            c.execute("INSERT INTO budget_days (campaign_id, day, allowance_paise) VALUES (?,?,?)",
                      (campaign_id, day.isoformat(), allowance))
            row = {"allowance_paise": allowance, "committed_paise": 0}
        today_left = row["allowance_paise"] - row["committed_paise"]
        if not self.setting("enforce_daily_pacing", True):
            today_left = pool_left
        return min(pool_left, today_left), {"pool_paise": pool, "pool_left_paise": pool_left,
                                            "today_allowance_paise": row["allowance_paise"], "today_left_paise": today_left}

    def _target_met(self, camp) -> bool:
        if camp["target_amount_pct"] is not None and camp["overdue_at_start_paise"] > 0:
            if camp["collected_paise"] * 100 >= camp["target_amount_pct"] * camp["overdue_at_start_paise"]:
                return True
        if camp["target_count_pct"] is not None and camp["allocated_cases"] > 0:
            if camp["cured_count"] * 100 >= camp["target_count_pct"] * camp["allocated_cases"]:
                return True
        return False

    def _template_ok(self, c, template_id: Optional[str], channel: str) -> bool:
        if not template_id:
            return False
        t = c.execute("SELECT * FROM templates WHERE id=?", (template_id,)).fetchone()
        return bool(t and t["approved"] and t["channel"] == channel and t["lang"] in self.policy.template_languages)

    def facts_for(self, c, ins: Instruction, exclude: Optional[str] = None) -> tuple[Facts, dict]:
        at = self._loc(ins.at)
        day = at.date()
        ob = self._obligation(c, ins.obligation_id)
        if ob is None:
            raise KeyError(f"unknown obligation {ins.obligation_id}")
        if ob["borrower_id"] != ins.borrower_id:
            raise KeyError(f"obligation {ins.obligation_id} does not belong to borrower {ins.borrower_id}")
        camp = c.execute("SELECT * FROM campaigns WHERE id=?", (ins.campaign_id,)).fetchone()
        st = self._state(c, ins.borrower_id)
        consent = c.execute("SELECT consented, opted_out FROM consents WHERE borrower_id=? AND channel=?",
                            (ins.borrower_id, ins.channel)).fetchone()
        dpd = self._loan_dpd(c, ob, day)
        outstanding = ob["amount_due_paise"] - ob["amount_paid_paise"]
        ex = exclude or ""
        live = ",".join("?" * len(LIVE))

        def count(where: str, args: tuple) -> int:
            return c.execute(f"SELECT COUNT(*) FROM touches WHERE borrower_id=? AND status IN ({live}) AND instruction_id<>? AND {where}",
                             (ins.borrower_id, *LIVE, ex, *args)).fetchone()[0]

        week_start = (day - timedelta(days=6)).isoformat()
        same_ch = c.execute(f"SELECT scheduled_at FROM touches WHERE borrower_id=? AND channel=? AND status IN ({live})"
                            " AND instruction_id<>? AND day BETWEEN ? AND ?",
                            (ins.borrower_id, ins.channel, *LIVE, ex, (day - timedelta(days=1)).isoformat(),
                             (day + timedelta(days=1)).isoformat())).fetchall()
        gaps = [abs((at - self._dt(r["scheduled_at"])).total_seconds()) / 60 for r in same_ch]
        budget_left, budget_detail = self._budget_remaining(c, ins.campaign_id, day)
        facts = Facts(
            at=at,
            channel=ins.channel,
            dpd=dpd,
            dpd_age_hours=self._dpd_age_hours(ob, at),
            consent=bool(consent and consent["consented"] and not consent["opted_out"]),
            opted_out=bool(st["stopped"] or (consent and consent["opted_out"])),
            hardship_until=self._dt(st["hardship_until"]),
            hardship_supervisor=bool(st["hardship_supervisor"]),
            callback_pending=bool(st["callback_pending"]),
            supervisor_hold=bool(st["supervisor_hold"]),
            ptp_date=date.fromisoformat(st["ptp_date"]) if st["ptp_date"] else None,
            obligation_cleared=outstanding <= 0,
            stop_for_day=c.execute("SELECT 1 FROM stop_for_day WHERE borrower_id=? AND day=?",
                                   (ins.borrower_id, day.isoformat())).fetchone() is not None,
            sends_today_channel=count("day=? AND channel=?", (day.isoformat(), ins.channel)),
            sends_today_all=count("day=?", (day.isoformat(),)),
            sends_week_channel=count("day BETWEEN ? AND ? AND channel=?", (week_start, day.isoformat(), ins.channel)),
            sends_week_all=count("day BETWEEN ? AND ?", (week_start, day.isoformat())),
            mins_since_last_same_channel=min(gaps) if gaps else None,
            lender_id=st["lender_id"],
            template_approved=self._template_ok(c, ins.template_id, ins.channel),
            budget_remaining_inr=budget_left / 100,
            est_cost_inr=ins.est_cost_paise / 100,
            campaign_target_met=self._target_met(camp),
            purpose=ins.purpose,
            verified_overdue=outstanding > 0 and ob["due_date"] < day.isoformat(),
        )
        return facts, {"budget": budget_detail, "loan_id": ob["loan_id"], "dpd": dpd}

    def _pre_gate(self, c, ins: Instruction) -> Optional[Decision]:
        """Operational stops outside the policy rulebook: a person paused everything, the campaign
        is not running, the date is outside the allocation period, or the loan isn't in the campaign."""
        if self.setting("outbound_paused", False):
            return Decision(WAIT, "OUTBOUND_PAUSED", "CONTROL", self.policy.version)
        camp = c.execute("SELECT * FROM campaigns WHERE id=?", (ins.campaign_id,)).fetchone()
        if camp is None:
            raise KeyError(f"unknown campaign {ins.campaign_id}")
        if camp["status"] != "RUNNING":
            return Decision(WAIT, f"CAMPAIGN_{camp['status']}", "CONTROL", self.policy.version)
        day = self._loc(ins.at).date().isoformat()
        if not (camp["start_date"] <= day <= camp["end_date"]):
            return Decision(WAIT, "OUTSIDE_CAMPAIGN_PERIOD", "CONTROL", self.policy.version)
        enrolled = c.execute("SELECT 1 FROM enrollments e JOIN obligations o ON o.loan_id = e.loan_id"
                             " WHERE e.campaign_id=? AND o.obligation_id=?", (ins.campaign_id, ins.obligation_id)).fetchone()
        if enrolled is None:
            return Decision(WAIT, "NOT_ENROLLED", "CONTROL", self.policy.version)
        return None

    # ---------- the atomic core ----------
    def preview(self, ins: Instruction) -> Decision:
        """Approval-time check (no reservation)."""
        check_paise(ins.est_cost_paise, "est_cost_paise")
        with self.tx() as c:  # write tx because pacing rows may be initialised
            pre = self._pre_gate(c, ins)
            if pre:
                return pre
            facts, extra = self.facts_for(c, ins)
            d = evaluate(self.policy, facts)
            d.detail.update(extra)
            return d

    def reserve(self, ins: Instruction, actor: str = "supervisor") -> ReserveResult:
        """Check every rule and, if allowed, reserve the touch and its cost — atomically.
        Re-submitting the same instruction_id never creates a second reservation."""
        ins.est_cost_paise = check_paise(ins.est_cost_paise, "est_cost_paise")
        if ins.instruction_id is None:
            ins.instruction_id = stable_instruction_id(ins.borrower_id, ins.obligation_id, ins.channel,
                                                       self._loc(ins.at), ins.version)
        with self.tx() as c:
            prior = c.execute("SELECT * FROM touches WHERE instruction_id=?", (ins.instruction_id,)).fetchone()
            if prior is not None:
                ok = prior["status"] in LIVE
                d = Decision(SEND if ok else WAIT, "ALREADY_RESERVED" if ok else (prior["reason"] or prior["status"]),
                             "IDEMPOTENCY", self.policy.version)
                return ReserveResult(d, ins.instruction_id, ok, idempotent_replay=True)
            d = self._pre_gate(c, ins)
            loan_id = None
            if d is None:
                facts, extra = self.facts_for(c, ins)
                loan_id = extra["loan_id"]
                d = evaluate(self.policy, facts)
                d.detail.update(extra)
            self._audit(c, actor, "DECISION", ins.borrower_id, ins.instruction_id, d, at=ins.at,
                        channel=ins.channel, obligation_id=ins.obligation_id, campaign_id=ins.campaign_id,
                        est_cost_paise=ins.est_cost_paise, **d.detail)
            if not d.allowed:
                return ReserveResult(d, ins.instruction_id, False)
            at = self._loc(ins.at)
            now = self._iso(self.policy.now())
            c.execute(
                "INSERT INTO touches (instruction_id, borrower_id, loan_id, obligation_id, campaign_id, channel, template_id,"
                " scheduled_at, day, status, reason, est_cost_paise, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,'RESERVED','ELIGIBLE',?,?,?)",
                (ins.instruction_id, ins.borrower_id, loan_id, ins.obligation_id, ins.campaign_id, ins.channel,
                 ins.template_id, self._iso(at), at.date().isoformat(), ins.est_cost_paise, now, now),
            )
            self._commit_cost(c, ins.campaign_id, at.date().isoformat(), ins.est_cost_paise)
            return ReserveResult(d, ins.instruction_id, True)

    def _commit_cost(self, c, campaign_id: str, day: str, delta: int) -> None:
        c.execute("UPDATE campaigns SET committed_paise = committed_paise + ? WHERE id=?", (delta, campaign_id))
        c.execute("UPDATE budget_days SET committed_paise = committed_paise + ? WHERE campaign_id=? AND day=?",
                  (delta, campaign_id, day))

    def _release(self, c, t) -> None:
        cost = t["actual_cost_paise"] if t["reconciled"] else t["est_cost_paise"]
        self._commit_cost(c, t["campaign_id"], t["day"], -(cost or 0))

    def touch(self, instruction_id: str) -> Optional[dict]:
        r = self._conn().execute("SELECT * FROM touches WHERE instruction_id=?", (instruction_id,)).fetchone()
        return dict(r) if r else None

    def instruction_of(self, t: dict) -> Instruction:
        return Instruction(t["borrower_id"], t["obligation_id"], t["campaign_id"], t["channel"], t["template_id"],
                           datetime.fromisoformat(t["scheduled_at"]), t["est_cost_paise"], instruction_id=t["instruction_id"])

    def begin_dispatch(self, instruction_id: str, at: Optional[datetime] = None) -> Decision:
        """Send-time re-check (master guardrail runs at approval AND at send). The touch's own
        reservation is excluded from the counts. Anything no longer allowed is canceled and its
        budget released; otherwise the touch moves to DISPATCHING exactly once."""
        with self.tx() as c:
            t = c.execute("SELECT * FROM touches WHERE instruction_id=?", (instruction_id,)).fetchone()
            if t is None:
                raise KeyError(instruction_id)
            if t["status"] != "RESERVED":
                return Decision(WAIT, f"NOT_DISPATCHABLE_{t['status']}", "IDEMPOTENCY", self.policy.version)
            ins = self.instruction_of(dict(t))
            if at is not None:
                ins.at = at
            d = self._pre_gate(c, ins)
            if d is None:
                facts, _ = self.facts_for(c, ins, exclude=instruction_id)
                facts.budget_remaining_inr += t["est_cost_paise"] / 100  # its own cost is already reserved
                d = evaluate(self.policy, facts)
            if d.allowed:
                c.execute("UPDATE touches SET status='DISPATCHING', updated_at=? WHERE instruction_id=?",
                          (self._iso(self.policy.now()), instruction_id))
            else:
                c.execute("UPDATE touches SET status='CANCELED', reason=?, updated_at=? WHERE instruction_id=?",
                          (d.reason, self._iso(self.policy.now()), instruction_id))
                self._release(c, t)
            self._audit(c, "executor", "SEND_TIME_CHECK", t["borrower_id"], instruction_id, d, at=ins.at)
            return d

    def record_provider_status(self, instruction_id: str, status: str, provider_ref: Optional[str] = None,
                               reason: Optional[str] = None) -> str:
        """Provider callbacks. Never downgrade (a late SENT after DELIVERED is ignored — E04).
        FAILED/REJECTED releases the reservation; UNKNOWN keeps it (conservative). The first time a
        touch is confirmed sent, a K03 'touch_sent' event is written against its EMI."""
        status = status.upper()
        with self.tx() as c:
            t = c.execute("SELECT * FROM touches WHERE instruction_id=?", (instruction_id,)).fetchone()
            if t is None:
                raise KeyError(instruction_id)
            cur = t["status"]
            if cur in ("CANCELED", "FAILED"):
                return cur
            if status in ("FAILED", "REJECTED"):
                if cur in ("SENT", "DELIVERED"):
                    return cur
                c.execute("UPDATE touches SET status='FAILED', reason=?, updated_at=? WHERE instruction_id=?",
                          (reason or "PROVIDER_REJECTED", self._iso(self.policy.now()), instruction_id))
                self._release(c, t)
                new = "FAILED"
            else:
                # READ is reachability only (never a reply, K01 §7) — it ranks with DELIVERED
                target = {"ACCEPTED": "SENT", "UNKNOWN": "UNCERTAIN", "READ": "DELIVERED"}.get(status, status)
                if target not in RANK:
                    raise ValueError(f"unknown provider status {status}")
                new = target if RANK[target] > RANK.get(cur, -1) else cur
                c.execute("UPDATE touches SET status=?, provider_ref=COALESCE(?, provider_ref), updated_at=?"
                          " WHERE instruction_id=?", (new, provider_ref, self._iso(self.policy.now()), instruction_id))
                if new in ("SENT", "DELIVERED") and t["obligation_id"]:
                    record_event(c, f"EV-{instruction_id}", t["borrower_id"], "touch_sent", t["scheduled_at"],
                                 channel=t["channel"], loan_id=t["loan_id"], obligation_id=t["obligation_id"],
                                 details=f"{t['template_id']} via {t['campaign_id']}")
            self._audit(c, "provider", "PROVIDER_STATUS", t["borrower_id"], instruction_id,
                        reason=status, previous=cur, now=new)
            return new

    def reconcile(self, instruction_id: str, actual_cost_paise: int) -> bool:
        """Replace the estimate with the actual charge — exactly once. Returns False on a repeat.
        The charge must be a whole, non-negative number of paise (<= MAX_PAISE): a negative 'charge'
        would hand budget back to the campaign (RT-F01)."""
        actual_cost_paise = check_paise(actual_cost_paise, "actual_cost_paise")
        with self.tx() as c:
            t = c.execute("SELECT * FROM touches WHERE instruction_id=?", (instruction_id,)).fetchone()
            if t is None:
                raise KeyError(instruction_id)
            if t["reconciled"] or t["status"] not in LIVE:
                return False
            self._commit_cost(c, t["campaign_id"], t["day"], actual_cost_paise - t["est_cost_paise"])
            c.execute("UPDATE touches SET actual_cost_paise=?, reconciled=1, updated_at=? WHERE instruction_id=?",
                      (actual_cost_paise, self._iso(self.policy.now()), instruction_id))
            self._audit(c, "executor", "RECONCILED", t["borrower_id"], instruction_id,
                        estimate_paise=t["est_cost_paise"], actual_paise=actual_cost_paise)
            return True

    # ---------- cancellations ----------
    def _cancel_queued(self, c, where: str, args: tuple, reason: str) -> int:
        rows = c.execute(f"SELECT * FROM touches WHERE status='RESERVED' AND {where}", args).fetchall()
        for t in rows:
            self._release(c, t)
        c.execute(f"UPDATE touches SET status='CANCELED', reason=?, updated_at=? WHERE status='RESERVED' AND {where}",
                  (reason, self._iso(self.policy.now()), *args))
        return len(rows)

    # ---------- borrower events ----------
    def _event_obligation(self, c, borrower_id: str, channel: Optional[str]) -> Optional[sqlite3.Row]:
        """Which EMI a reply belongs to: the last touch on that channel, else the oldest unpaid EMI."""
        r = c.execute("SELECT loan_id, obligation_id FROM touches WHERE borrower_id=? AND obligation_id IS NOT NULL"
                      " AND (? IS NULL OR channel=?) AND status IN ('SENT','DELIVERED','UNCERTAIN')"
                      " ORDER BY scheduled_at DESC LIMIT 1", (borrower_id, channel, channel)).fetchone()
        if r:
            return r
        return c.execute("SELECT o.loan_id, o.obligation_id FROM obligations o JOIN loans l ON l.loan_id=o.loan_id"
                         " WHERE l.borrower_id=? AND o.amount_paid_paise < o.amount_due_paise ORDER BY o.due_date LIMIT 1",
                         (borrower_id,)).fetchone()

    def _stop_for_day(self, c, borrower_id: str, at: datetime, trigger: str) -> int:
        day = self._loc(at).date().isoformat()
        c.execute("INSERT OR IGNORE INTO stop_for_day VALUES (?,?,?)", (borrower_id, day, trigger))
        return self._cancel_queued(c, "borrower_id=? AND day=?", (borrower_id, day), "STOP_FOR_DAY")

    def record_response(self, borrower_id: str, at: datetime, channel: Optional[str] = None, kind: str = "reply",
                        details: str = "") -> int:
        """Any reply or borrower-behaviour disposition on any channel -> stop for the day (K01 §5).
        Also written to the K03 spine as 'reply_received' against the EMI it relates to."""
        with self.tx() as c:
            n = self._stop_for_day(c, borrower_id, at, "BORROWER_RESPONSE")
            ob = self._event_obligation(c, borrower_id, channel)
            if ob:
                record_event(c, f"EV-REPLY-{borrower_id}-{self._iso(at)}-{channel}", borrower_id, "reply_received",
                             self._iso(at), channel=channel, loan_id=ob["loan_id"], obligation_id=ob["obligation_id"],
                             details=details or kind)
            self._audit(c, "borrower", "RESPONSE", borrower_id, at=at, reason=kind, channel=channel, canceled=n)
            return n

    def record_call(self, borrower_id: str, at: datetime, answered_seconds: float, channel: str = "ivr") -> int:
        with self.tx() as c:
            n = 0
            if answered_seconds > self.policy.call_pickup_threshold_s:
                n = self._stop_for_day(c, borrower_id, at, "CALL_PICKUP")
            self._audit(c, "provider", "CALL_RESULT", borrower_id, at=at, channel=channel,
                        answered_seconds=answered_seconds, canceled=n)
            return n

    def record_stop(self, borrower_id: str, at: datetime, channel: Optional[str] = None) -> int:
        """STOP on any channel suppresses ALL channels and cancels every queued send (K01 holds.STOP_OPT_OUT).
        The K03 consents table is updated too, so every reader of the spine sees the opt-out."""
        with self.tx() as c:
            self._set_state(c, borrower_id, stopped=1)
            c.execute("UPDATE consents SET opted_out=1, updated_at=? WHERE borrower_id=?", (self._iso(at), borrower_id))
            record_event(c, f"EV-STOP-{borrower_id}-{self._iso(at)}", borrower_id, "consent_changed", self._iso(at),
                         channel=channel, details="STOP: opted out of all channels")
            n = self._cancel_queued(c, "borrower_id=?", (borrower_id,), "STOP_OPT_OUT")
            self._audit(c, "borrower", "STOP_RECEIVED", borrower_id, at=at, channel=channel, canceled=n)
            return n

    def opt_in(self, borrower_id: str, channel: str, consent_version: str, at: datetime) -> None:
        """Re-entry is per channel with the consent version recorded; other channels stay opted out."""
        with self.tx() as c:
            c.execute("INSERT INTO consents (borrower_id, channel, consented, opted_out, updated_at, source)"
                      " VALUES (?,?,1,0,?,'provided') ON CONFLICT (borrower_id, channel) DO UPDATE SET consented=1,"
                      " opted_out=0, updated_at=excluded.updated_at, source='provided'", (borrower_id, channel, self._iso(at)))
            self._set_state(c, borrower_id, stopped=0)
            record_event(c, f"EV-OPTIN-{borrower_id}-{channel}-{self._iso(at)}", borrower_id, "consent_changed",
                         self._iso(at), channel=channel, details=f"opt-in, consent version {consent_version}")
            self._audit(c, "borrower", "OPT_IN", borrower_id, at=at, channel=channel, consent_version=consent_version)

    def _refresh_progress(self, c, loan_id: str, day: date, collected_delta: int) -> None:
        """Keep each campaign's collection progress current for this loan (K01 collection_target)."""
        cured_now = self._loan_cured(c, loan_id, day)
        for e in c.execute("SELECT e.*, k.start_date, k.end_date FROM enrollments e JOIN campaigns k ON k.id=e.campaign_id"
                           " WHERE e.loan_id=?", (loan_id,)).fetchall():
            in_period = e["start_date"] <= day.isoformat() <= e["end_date"]
            cured_delta = int(cured_now) - e["cured"]
            c.execute("UPDATE enrollments SET cured=? WHERE campaign_id=? AND loan_id=?", (int(cured_now), e["campaign_id"], loan_id))
            c.execute("UPDATE campaigns SET collected_paise = collected_paise + ?, cured_count = cured_count + ? WHERE id=?",
                      (collected_delta if in_period else 0, cured_delta, e["campaign_id"]))

    def record_payment_verified(self, obligation_id: str, amount_paise: int, at: datetime, payment_id: str,
                                reference: str, mode: str = "upi") -> dict:
        """A payment verified against D7/LMS. Applied through K03's apply_payment (one EMI of one loan,
        never more than is due, a bank reference can be used only once). If that EMI is now fully
        paid, ITS queued touches are canceled; other EMIs and loans stay open (E14/E15).
        Raises spine.ledger.PaymentError for an invalid claim (e.g. reused reference)."""
        at = self._loc(at)
        with self.tx() as c:
            ob = self._obligation(c, obligation_id)
        if ob is None:
            raise KeyError(f"unknown obligation {obligation_id}")
        applied = apply_payment(self._spine(), payment_id, ob["loan_id"], obligation_id, int(amount_paise),
                                at.date().isoformat(), mode, reference)
        with self.tx() as c:
            cleared, n = False, 0
            if applied:
                after = self._obligation(c, obligation_id)
                cleared = after["amount_paid_paise"] >= after["amount_due_paise"]
                if cleared:
                    n = self._cancel_queued(c, "obligation_id=?", (obligation_id,), "OBLIGATION_CLEARED")
                    st = self._state(c, ob["borrower_id"])
                    if st["ptp_obligation"] == obligation_id:
                        self._set_state(c, ob["borrower_id"], ptp_date=None, ptp_obligation=None)
                self._refresh_progress(c, ob["loan_id"], at.date(), int(amount_paise))
            self._audit(c, "tool:d7", "PAYMENT_VERIFIED", ob["borrower_id"], at=at, obligation_id=obligation_id,
                        amount_paise=amount_paise, applied=applied, cleared=cleared, canceled=n, reference=reference)
        return {"applied": applied, "obligation_cleared": cleared, "canceled": n}

    def record_payment_reversed(self, payment_id: str, at: datetime, reason: str) -> bool:
        """The bank reversed a payment (E15): K03 makes that EMI due again; collection progress is
        rolled back. The Supervisor replans the EMI on its next pass."""
        at = self._loc(at)
        p = self._conn().execute("SELECT * FROM payments WHERE payment_id=?", (payment_id,)).fetchone()
        if p is None:
            raise KeyError(payment_id)
        done = reverse_payment(self._spine(), payment_id, at.date().isoformat(), reason)
        if done:
            with self.tx() as c:
                self._refresh_progress(c, p["loan_id"], at.date(), -p["amount_paise"])
                borrower = c.execute("SELECT borrower_id FROM loans WHERE loan_id=?", (p["loan_id"],)).fetchone()[0]
                self._audit(c, "tool:d7", "PAYMENT_REVERSED", borrower, at=at, obligation_id=p["obligation_id"],
                            amount_paise=p["amount_paise"], reason=reason)
        return done

    def start_day(self, day: date) -> list[str]:
        """Day roll-over (simulation clock): the daily LMS refresh marks the spine fresh, and a PTP
        whose date has passed unpaid becomes BROKEN_PTP and its hold ends."""
        self.mark_spine_synced(datetime.combine(day, time(0, 0)))
        broken = []
        with self.tx() as c:
            rows = c.execute("SELECT borrower_id, ptp_obligation FROM borrower_state WHERE ptp_date IS NOT NULL AND ptp_date < ?",
                             (day.isoformat(),)).fetchall()
            for r in rows:
                ob = self._obligation(c, r["ptp_obligation"]) if r["ptp_obligation"] else None
                paid = ob is not None and ob["amount_paid_paise"] >= ob["amount_due_paise"]
                if not paid:
                    c.execute("UPDATE borrower_state SET broken_ptp = broken_ptp + 1 WHERE borrower_id=?", (r["borrower_id"],))
                    broken.append(r["borrower_id"])
                    self._audit(c, "supervisor", "BROKEN_PTP", r["borrower_id"], reason="BROKEN_PTP",
                                obligation_id=r["ptp_obligation"])
                self._set_state(c, r["borrower_id"], ptp_date=None, ptp_obligation=None)
        return broken

    def _ticket(self, c, queue: str, borrower_id: str, obligation_id: Optional[str], type_: str, summary: tuple[str, str],
                at: datetime, evidence_ids=(), sla_hours: Optional[float] = None) -> str:
        key = f"{borrower_id}:{obligation_id}:{type_}:{self._loc(at).date().isoformat()}"
        existing = c.execute("SELECT ticket_id FROM tickets WHERE idempotency_key=?", (key,)).fetchone()
        if existing:
            return existing["ticket_id"]  # repeat requests attach to the same open task
        tid = "TKT-" + uuid.uuid4().hex[:10]
        sla = self._iso(self._loc(at) + timedelta(hours=sla_hours)) if sla_hours else None
        c.execute("INSERT INTO tickets (ticket_id, idempotency_key, queue, borrower_id, obligation_id, type, summary_line_1,"
                  " summary_line_2, evidence_ids, created_at, sla_due_at, policy_version) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                  (tid, key, queue, borrower_id, obligation_id, type_, summary[0], summary[1], json.dumps(list(evidence_ids)),
                   self._iso(at), sla, self.policy.exceptions_version or self.policy.version))
        return tid

    def apply_exception(self, borrower_id: str, event: ExceptionEvent, at: datetime,
                        obligation_id: Optional[str] = None, summary: tuple[str, str] = ("", ""),
                        evidence_ids=()) -> dict:
        """Run the K13 decision table and apply its hold. Every hold also cancels the borrower's
        queued sends so their budget returns to the pool; the Supervisor replans after the hold."""
        out = handle(self.policy, event)
        at = self._loc(at)
        with self.tx() as c:
            canceled, ticket = 0, None
            if out.hold == "STOP_FOR_DAY":
                canceled = self._stop_for_day(c, borrower_id, at, "BORROWER_RESPONSE")
            elif out.hold == "PTP_HOLD":
                ptp = (at + timedelta(days=event.date_offset_days or 0)).date()
                self._set_state(c, borrower_id, ptp_date=ptp.isoformat(), ptp_obligation=obligation_id)
                c.execute("INSERT OR IGNORE INTO stop_for_day VALUES (?,?,?)", (borrower_id, at.date().isoformat(), "BORROWER_RESPONSE"))
                canceled = self._cancel_queued(c, "borrower_id=?", (borrower_id,), "PTP_HOLD")
            elif out.hold == "HARDSHIP_HOLD":
                hs = self.policy.raw["holds"]["HARDSHIP"]
                days = event.date_offset_days if event.date_offset_days is not None else hs["default_pause_days"]
                self._set_state(c, borrower_id, hardship_until=self._iso(at + timedelta(days=days)),
                                hardship_supervisor=int(bool(out.queue)))
                canceled = self._cancel_queued(c, "borrower_id=?", (borrower_id,), "HARDSHIP_HOLD")
            elif out.hold == "CALLBACK_PENDING":
                self._set_state(c, borrower_id, callback_pending=1)
                canceled = self._cancel_queued(c, "borrower_id=?", (borrower_id,), "CALLBACK_PENDING")
            elif out.hold == "SUPERVISOR_HOLD":
                self._set_state(c, borrower_id, supervisor_hold=1)
                canceled = self._cancel_queued(c, "borrower_id=?", (borrower_id,), "SUPERVISOR_HOLD")
            if out.action in ("CLOSE_CALLBACK_TASK", "SEND_MISSED_CALLBACK_MSG"):
                self._set_state(c, borrower_id, callback_pending=0)
            if out.queue:
                sla = self.policy.exceptions.get("callback", {}).get("sla_working_hours") if event.event.startswith("CALLBACK") else None
                ticket = self._ticket(c, out.queue, borrower_id, obligation_id, event.event, summary, at, evidence_ids, sla)
            self._audit(c, "agent:conversation", f"EXCEPTION_{event.event}", borrower_id, at=at, reason=out.action,
                        hold=out.hold, queue=out.queue, ticket=ticket, canceled=canceled)
        return {"action": out.action, "hold": out.hold, "queue": out.queue, "ticket_id": ticket, "canceled": canceled}

    def clear_supervisor_hold(self, borrower_id: str, actor: str = "human:supervisor") -> None:
        with self.tx() as c:
            self._set_state(c, borrower_id, supervisor_hold=0, hardship_supervisor=0)
            self._audit(c, actor, "SUPERVISOR_CLEARED", borrower_id)

    def set_outbound_paused(self, paused: bool, actor: str = "human:operator", reason: str = "") -> int:
        """Global kill switch. Pausing cancels everything queued (budget released); the
        Supervisor replans after resume. Returns the number of touches canceled."""
        with self.tx() as c:
            c.execute("INSERT INTO settings VALUES ('outbound_paused', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                      (json.dumps(paused),))
            n = self._cancel_queued(c, "1=1", (), "OUTBOUND_PAUSED") if paused else 0
            self._audit(c, actor, "OUTBOUND_PAUSED" if paused else "OUTBOUND_RESUMED", reason=reason, canceled=n)
            return n

    # ---------- reads ----------
    def borrower_state(self, borrower_id: str, as_of: Optional[date] = None) -> dict:
        """One borrower: K03 identity, consent, loans and EMIs + K06 holds, touches, tickets."""
        c = self._conn()
        b = c.execute("SELECT * FROM borrowers WHERE borrower_id=?", (borrower_id,)).fetchone()
        if b is None:
            raise KeyError(borrower_id)
        day = as_of or self.policy.now().date()
        out = dict(b) | {"state": self._state(c, borrower_id)}
        out["consents"] = [dict(r) for r in c.execute("SELECT channel, consented, opted_out, source, updated_at FROM consents"
                                                       " WHERE borrower_id=?", (borrower_id,))]
        loans = []
        for ln in c.execute("SELECT * FROM loans WHERE borrower_id=? ORDER BY loan_id", (borrower_id,)).fetchall():
            obs = [dict(o) | {"outstanding_paise": o["amount_due_paise"] - o["amount_paid_paise"]}
                   for o in c.execute("SELECT * FROM obligations WHERE loan_id=? ORDER BY installment_no", (ln["loan_id"],))]
            first = c.execute("SELECT o.*, l.source, l.file_dpd, l.dpd_as_of, l.borrower_id FROM obligations o JOIN loans l"
                              " ON l.loan_id=o.loan_id WHERE o.loan_id=? LIMIT 1", (ln["loan_id"],)).fetchone()
            dpd = self._loan_dpd(c, first, day) if first else None
            loans.append({"loan_id": ln["loan_id"], "product": ln["product"], "dpd": dpd,
                          "band": self.policy.band_for(dpd) if dpd is not None else None,
                          "cured": self._loan_cured(c, ln["loan_id"], day), "obligations": obs})
        out["loans"] = loans
        out["touches"] = [dict(r) for r in c.execute("SELECT * FROM touches WHERE borrower_id=? ORDER BY scheduled_at", (borrower_id,))]
        out["stop_for_day"] = [dict(r) for r in c.execute("SELECT day, trigger FROM stop_for_day WHERE borrower_id=?", (borrower_id,))]
        out["tickets"] = [dict(r) for r in c.execute("SELECT * FROM tickets WHERE borrower_id=?", (borrower_id,))]
        out["events"] = [dict(r) for r in c.execute("SELECT * FROM events WHERE borrower_id=? ORDER BY occurred_at", (borrower_id,))]
        return out

    def campaign_state(self, campaign_id: str) -> dict:
        c = self._conn()
        camp = c.execute("SELECT * FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
        if camp is None:
            raise KeyError(campaign_id)
        out = dict(camp)
        out["pool_paise"] = camp["budget_per_case_paise"] * camp["allocated_cases"]
        out["target_met"] = self._target_met(camp)
        out["days"] = [dict(r) for r in c.execute("SELECT * FROM budget_days WHERE campaign_id=? ORDER BY day", (campaign_id,))]
        return out

    def audit_log(self, limit: int = 200, borrower_id: Optional[str] = None) -> list[dict]:
        q, args = "SELECT * FROM audit", ()
        if borrower_id:
            q, args = q + " WHERE borrower_id=?", (borrower_id,)
        rows = self._conn().execute(q + " ORDER BY id DESC LIMIT ?", (*args, limit)).fetchall()
        return [{**dict(r), "detail": json.loads(r["detail"] or "{}")} for r in rows]

    def due_touches(self, now: datetime) -> list[dict]:
        rows = self._conn().execute("SELECT * FROM touches WHERE status='RESERVED' AND scheduled_at <= ? ORDER BY scheduled_at",
                                    (self._iso(now),)).fetchall()
        return [dict(r) for r in rows]

    def uncertain_touches(self) -> list[dict]:
        return [dict(r) for r in self._conn().execute("SELECT * FROM touches WHERE status='UNCERTAIN'")]

    def cap_breaches(self) -> list[dict]:
        """Independent post-hoc audit: recount every borrower/day/channel among live touches and
        compare with the effective caps (band from the loan's DPD on that day). Should always be empty."""
        c = self._conn()
        live = ",".join("?" * len(LIVE))
        out = []
        for r in c.execute(f"SELECT t.borrower_id, t.day, t.channel, COUNT(*) n, MIN(t.obligation_id) ob FROM touches t"
                           f" WHERE t.status IN ({live}) GROUP BY t.borrower_id, t.day, t.channel", LIVE):
            ob = self._obligation(c, r["ob"])
            dpd = self._loan_dpd(c, ob, date.fromisoformat(r["day"])) if ob else None
            band = self.policy.band_for(dpd) if dpd is not None else None
            if band is None:
                continue
            cap = self.policy.channel_day_cap(band, r["channel"], self._state(c, r["borrower_id"])["lender_id"])
            if cap is not None and r["n"] > cap:
                out.append(dict(r) | {"cap": cap, "band": band})
        return out
