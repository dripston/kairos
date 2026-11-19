"""The Ledger: the state behind the Gate, with atomic check-and-reserve.

Why it exists: the Gate is pure — it is told "2 SMS sent today". The Ledger is
what makes that number true under concurrency. reserve() computes the facts and
writes the reservation inside ONE write transaction (SQLite BEGIN IMMEDIATE), so
two workers can never both see "2 of 3" and both send. Reserved touches count
toward caps and budget immediately; that is what keeps parallel workers inside
every limit.

Counted per borrower ACROSS ALL CAMPAIGNS (K01 caps.scope). Days are local
(Asia/Kolkata) calendar days. Weekly caps use a rolling 7-day window (stricter
than a calendar week, so it can never under-count).
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterator, Optional

from .exceptions import ExceptionEvent, handle
from .gate import CANCEL, HANDOFF, SEND, WAIT, Decision, Facts, evaluate
from .policy import Policy

# statuses that may reach (or already reached) the borrower -> count toward caps and budget
LIVE = ("RESERVED", "DISPATCHING", "UNCERTAIN", "SENT", "DELIVERED")
# provider status ranks: an older/lower status never overwrites a newer one (E04)
RANK = {"RESERVED": 0, "DISPATCHING": 1, "UNCERTAIN": 2, "SENT": 3, "DELIVERED": 4}

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS campaigns (
  id TEXT PRIMARY KEY, name TEXT, status TEXT NOT NULL DEFAULT 'RUNNING',
  budget_per_case_inr REAL NOT NULL, allocated_cases INTEGER NOT NULL,
  start_date TEXT NOT NULL, end_date TEXT NOT NULL,
  overdue_at_start_inr REAL NOT NULL DEFAULT 0,
  target_amount_pct REAL, target_count_pct REAL,
  committed_inr REAL NOT NULL DEFAULT 0, collected_inr REAL NOT NULL DEFAULT 0,
  cured_count INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS budget_days (
  campaign_id TEXT, day TEXT, allowance_inr REAL NOT NULL, committed_inr REAL NOT NULL DEFAULT 0,
  PRIMARY KEY (campaign_id, day));
CREATE TABLE IF NOT EXISTS borrowers (
  id TEXT PRIMARY KEY, dpd INTEGER, dpd_updated_at TEXT, lender_id TEXT,
  consent TEXT NOT NULL DEFAULT '{}', consent_version TEXT NOT NULL DEFAULT '{}',
  opted_out INTEGER NOT NULL DEFAULT 0, verified_overdue INTEGER NOT NULL DEFAULT 1,
  hardship_until TEXT, hardship_supervisor INTEGER NOT NULL DEFAULT 0,
  callback_pending INTEGER NOT NULL DEFAULT 0, supervisor_hold INTEGER NOT NULL DEFAULT 0,
  ptp_date TEXT, ptp_obligation TEXT, broken_ptp INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS obligations (
  id TEXT PRIMARY KEY, borrower_id TEXT NOT NULL, campaign_id TEXT NOT NULL,
  overdue_inr REAL NOT NULL DEFAULT 0, cleared INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS templates (
  id TEXT PRIMARY KEY, channel TEXT NOT NULL, lang TEXT NOT NULL, approved INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS touches (
  instruction_id TEXT PRIMARY KEY, borrower_id TEXT NOT NULL, obligation_id TEXT, campaign_id TEXT,
  channel TEXT NOT NULL, template_id TEXT, scheduled_at TEXT NOT NULL, day TEXT NOT NULL,
  status TEXT NOT NULL, reason TEXT, est_cost_inr REAL NOT NULL DEFAULT 0, actual_cost_inr REAL,
  reconciled INTEGER NOT NULL DEFAULT 0, provider_ref TEXT, created_at TEXT, updated_at TEXT);
CREATE INDEX IF NOT EXISTS ix_touch_borrower_day ON touches (borrower_id, day);
CREATE INDEX IF NOT EXISTS ix_touch_borrower_channel ON touches (borrower_id, channel, scheduled_at);
CREATE INDEX IF NOT EXISTS ix_touch_status ON touches (status, scheduled_at);
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
    """One proposed touch, as issued by the Supervisor. instruction_id is stable: the same
    instruction can be submitted twice (retries, two workers) and is reserved only once."""
    borrower_id: str
    obligation_id: str
    campaign_id: str
    channel: str
    template_id: Optional[str]
    at: datetime
    est_cost_inr: float
    purpose: Optional[str] = None
    instruction_id: Optional[str] = None  # None → stable id derived from the touch itself
    version: int = 1


def stable_instruction_id(borrower_id: str, obligation_id: str, channel: str, at: datetime, version: int = 1) -> str:
    """Same borrower + obligation + channel + slot + version → same id, so two workers (or a
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
        with self._conn() as c:
            c.executescript(SCHEMA)

    # ---------- connection / transactions ----------
    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=30, isolation_level=None, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA busy_timeout=30000")
            self._local.conn = conn
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
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    # ---------- time helpers ----------
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

    # ---------- setup / upserts ----------
    def set_setting(self, key: str, value) -> None:
        with self.tx() as c:
            c.execute("INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                      (key, json.dumps(value)))

    def setting(self, key: str, default=None):
        r = self._conn().execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(r["value"]) if r else default

    def upsert_campaign(self, id: str, budget_per_case_inr: float, allocated_cases: int, start_date: date,
                        end_date: date, overdue_at_start_inr: float = 0.0, target_amount_pct: Optional[float] = None,
                        target_count_pct: Optional[float] = None, name: str = "", status: str = "RUNNING") -> None:
        """budget_per_case_inr and the collection targets are K01 REQUIRED_INPUTs — the caller supplies them."""
        with self.tx() as c:
            c.execute(
                "INSERT INTO campaigns (id, name, status, budget_per_case_inr, allocated_cases, start_date, end_date,"
                " overdue_at_start_inr, target_amount_pct, target_count_pct) VALUES (?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(id) DO UPDATE SET name=excluded.name, status=excluded.status,"
                " budget_per_case_inr=excluded.budget_per_case_inr, allocated_cases=excluded.allocated_cases,"
                " start_date=excluded.start_date, end_date=excluded.end_date,"
                " overdue_at_start_inr=excluded.overdue_at_start_inr, target_amount_pct=excluded.target_amount_pct,"
                " target_count_pct=excluded.target_count_pct",
                (id, name, status, budget_per_case_inr, allocated_cases, start_date.isoformat(), end_date.isoformat(),
                 overdue_at_start_inr, target_amount_pct, target_count_pct),
            )

    def set_campaign_status(self, campaign_id: str, status: str, actor: str = "human") -> int:
        """PAUSED / STOPPED cancel queued work for the campaign; returns how many touches were canceled."""
        with self.tx() as c:
            c.execute("UPDATE campaigns SET status=? WHERE id=?", (status, campaign_id))
            n = 0
            if status in ("PAUSED", "STOPPED"):
                n = self._cancel_queued(c, "campaign_id=?", (campaign_id,), f"CAMPAIGN_{status}")
            self._audit(c, actor, f"CAMPAIGN_{status}", reason=f"campaign {campaign_id}", canceled=n)
            return n

    def upsert_borrower(self, id: str, dpd: Optional[int], dpd_updated_at: datetime, consent: dict[str, bool],
                        lender_id: Optional[str] = None, verified_overdue: bool = True,
                        consent_version: Optional[dict[str, str]] = None) -> None:
        with self.tx() as c:
            c.execute(
                "INSERT INTO borrowers (id, dpd, dpd_updated_at, lender_id, consent, consent_version, verified_overdue)"
                " VALUES (?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET dpd=excluded.dpd,"
                " dpd_updated_at=excluded.dpd_updated_at, lender_id=excluded.lender_id, consent=excluded.consent,"
                " consent_version=excluded.consent_version, verified_overdue=excluded.verified_overdue",
                (id, dpd, self._iso(dpd_updated_at), lender_id, json.dumps(consent),
                 json.dumps(consent_version or {}), int(verified_overdue)),
            )

    def upsert_obligation(self, id: str, borrower_id: str, campaign_id: str, overdue_inr: float = 0.0) -> None:
        with self.tx() as c:
            c.execute("INSERT INTO obligations (id, borrower_id, campaign_id, overdue_inr) VALUES (?,?,?,?)"
                      " ON CONFLICT(id) DO UPDATE SET overdue_inr=excluded.overdue_inr", (id, borrower_id, campaign_id, overdue_inr))

    def upsert_template(self, id: str, channel: str, lang: str, approved: bool = True) -> None:
        with self.tx() as c:
            c.execute("INSERT INTO templates VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET channel=excluded.channel,"
                      " lang=excluded.lang, approved=excluded.approved", (id, channel, lang, int(approved)))

    # ---------- facts ----------
    def _budget_remaining(self, c, campaign_id: str, day: date) -> tuple[float, dict]:
        """min(pool remaining, today's pacing allowance remaining). Pacing = remaining pool /
        remaining contact days, fixed once per day; unspent budget carries forward (K01 §9)."""
        camp = c.execute("SELECT * FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
        pool = camp["budget_per_case_inr"] * camp["allocated_cases"]
        pool_left = pool - camp["committed_inr"]
        row = c.execute("SELECT * FROM budget_days WHERE campaign_id=? AND day=?", (campaign_id, day.isoformat())).fetchone()
        if row is None:
            days_left = len(self.policy.contact_days(day, date.fromisoformat(camp["end_date"])))
            allowance = max(0.0, pool_left / days_left) if days_left else 0.0
            c.execute("INSERT INTO budget_days (campaign_id, day, allowance_inr) VALUES (?,?,?)",
                      (campaign_id, day.isoformat(), allowance))
            row = {"allowance_inr": allowance, "committed_inr": 0.0}
        today_left = row["allowance_inr"] - row["committed_inr"]
        if not self.setting("enforce_daily_pacing", True):
            today_left = pool_left
        # money is compared at 1/100 paisa precision so float drift (2.0 - 4*0.4 = 0.3999…)
        # can never block or allow a send by mistake
        return round(min(pool_left, today_left), 4), {"pool_inr": pool, "pool_left": round(pool_left, 2),
                                            "today_allowance": round(row["allowance_inr"], 2), "today_left": round(today_left, 2)}

    def _target_met(self, camp) -> bool:
        if camp["target_amount_pct"] is not None and camp["overdue_at_start_inr"] > 0:
            if camp["collected_inr"] / camp["overdue_at_start_inr"] * 100 >= camp["target_amount_pct"]:
                return True
        if camp["target_count_pct"] is not None and camp["allocated_cases"] > 0:
            if camp["cured_count"] / camp["allocated_cases"] * 100 >= camp["target_count_pct"]:
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
        b = c.execute("SELECT * FROM borrowers WHERE id=?", (ins.borrower_id,)).fetchone()
        if b is None:
            raise KeyError(f"unknown borrower {ins.borrower_id}")
        ob = c.execute("SELECT * FROM obligations WHERE id=?", (ins.obligation_id,)).fetchone()
        camp = c.execute("SELECT * FROM campaigns WHERE id=?", (ins.campaign_id,)).fetchone()
        if camp is None:
            raise KeyError(f"unknown campaign {ins.campaign_id}")
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
        consent = json.loads(b["consent"])
        dpd_at = self._dt(b["dpd_updated_at"])
        facts = Facts(
            at=at,
            channel=ins.channel,
            dpd=b["dpd"],
            dpd_age_hours=max(0.0, (at - dpd_at).total_seconds() / 3600) if dpd_at else float("inf"),
            consent=bool(consent.get(ins.channel)),
            opted_out=bool(b["opted_out"]),
            hardship_until=self._dt(b["hardship_until"]),
            hardship_supervisor=bool(b["hardship_supervisor"]),
            callback_pending=bool(b["callback_pending"]),
            supervisor_hold=bool(b["supervisor_hold"]),
            ptp_date=date.fromisoformat(b["ptp_date"]) if b["ptp_date"] else None,
            obligation_cleared=bool(ob and ob["cleared"]),
            stop_for_day=c.execute("SELECT 1 FROM stop_for_day WHERE borrower_id=? AND day=?",
                                   (ins.borrower_id, day.isoformat())).fetchone() is not None,
            sends_today_channel=count("day=? AND channel=?", (day.isoformat(), ins.channel)),
            sends_today_all=count("day=?", (day.isoformat(),)),
            sends_week_channel=count("day BETWEEN ? AND ? AND channel=?", (week_start, day.isoformat(), ins.channel)),
            sends_week_all=count("day BETWEEN ? AND ?", (week_start, day.isoformat())),
            mins_since_last_same_channel=min(gaps) if gaps else None,
            lender_id=b["lender_id"],
            template_approved=self._template_ok(c, ins.template_id, ins.channel),
            budget_remaining_inr=budget_left,
            est_cost_inr=ins.est_cost_inr,
            campaign_target_met=self._target_met(camp),
            purpose=ins.purpose,
            verified_overdue=bool(b["verified_overdue"]),
        )
        return facts, {"budget": budget_detail}

    def _pre_gate(self, c, ins: Instruction) -> Optional[Decision]:
        """Operational stops that sit outside the policy rulebook: a person paused everything,
        the campaign is not running, or the date is outside the allocation period."""
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
        return None

    # ---------- the atomic core ----------
    def preview(self, ins: Instruction) -> Decision:
        """Approval-time check (no reservation). Used by the Supervisor."""
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
            if d is None:
                facts, extra = self.facts_for(c, ins)
                d = evaluate(self.policy, facts)
                d.detail.update(extra)
            self._audit(c, actor, "DECISION", ins.borrower_id, ins.instruction_id, d, at=ins.at,
                        channel=ins.channel, obligation_id=ins.obligation_id, campaign_id=ins.campaign_id,
                        est_cost_inr=ins.est_cost_inr, **d.detail)
            if not d.allowed:
                return ReserveResult(d, ins.instruction_id, False)
            at = self._loc(ins.at)
            now = self._iso(self.policy.now())
            c.execute(
                "INSERT INTO touches (instruction_id, borrower_id, obligation_id, campaign_id, channel, template_id,"
                " scheduled_at, day, status, reason, est_cost_inr, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,'RESERVED','ELIGIBLE',?,?,?)",
                (ins.instruction_id, ins.borrower_id, ins.obligation_id, ins.campaign_id, ins.channel,
                 ins.template_id, self._iso(at), at.date().isoformat(), ins.est_cost_inr, now, now),
            )
            self._commit_cost(c, ins.campaign_id, at.date().isoformat(), ins.est_cost_inr)
            return ReserveResult(d, ins.instruction_id, True)

    def _commit_cost(self, c, campaign_id: str, day: str, delta: float) -> None:
        c.execute("UPDATE campaigns SET committed_inr = committed_inr + ? WHERE id=?", (delta, campaign_id))
        c.execute("UPDATE budget_days SET committed_inr = committed_inr + ? WHERE campaign_id=? AND day=?",
                  (delta, campaign_id, day))

    def _release(self, c, t) -> None:
        cost = t["actual_cost_inr"] if t["reconciled"] else t["est_cost_inr"]
        self._commit_cost(c, t["campaign_id"], t["day"], -(cost or 0))

    def touch(self, instruction_id: str) -> Optional[dict]:
        r = self._conn().execute("SELECT * FROM touches WHERE instruction_id=?", (instruction_id,)).fetchone()
        return dict(r) if r else None

    def instruction_of(self, t: dict) -> Instruction:
        return Instruction(t["borrower_id"], t["obligation_id"], t["campaign_id"], t["channel"], t["template_id"],
                           datetime.fromisoformat(t["scheduled_at"]), t["est_cost_inr"], instruction_id=t["instruction_id"])

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
                facts.budget_remaining_inr = round(facts.budget_remaining_inr + t["est_cost_inr"], 4)  # own cost already reserved
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
        FAILED/REJECTED releases the reservation; UNKNOWN keeps it (conservative)."""
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
            self._audit(c, "provider", "PROVIDER_STATUS", t["borrower_id"], instruction_id,
                        reason=status, previous=cur, now=new)
            return new

    def reconcile(self, instruction_id: str, actual_cost_inr: float) -> bool:
        """Replace the estimate with the actual charge — exactly once. Returns False on a repeat."""
        with self.tx() as c:
            t = c.execute("SELECT * FROM touches WHERE instruction_id=?", (instruction_id,)).fetchone()
            if t is None:
                raise KeyError(instruction_id)
            if t["reconciled"] or t["status"] not in LIVE:
                return False
            self._commit_cost(c, t["campaign_id"], t["day"], actual_cost_inr - t["est_cost_inr"])
            c.execute("UPDATE touches SET actual_cost_inr=?, reconciled=1, updated_at=? WHERE instruction_id=?",
                      (actual_cost_inr, self._iso(self.policy.now()), instruction_id))
            self._audit(c, "executor", "RECONCILED", t["borrower_id"], instruction_id,
                        estimate=t["est_cost_inr"], actual=actual_cost_inr)
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
    def _stop_for_day(self, c, borrower_id: str, at: datetime, trigger: str) -> int:
        day = self._loc(at).date().isoformat()
        c.execute("INSERT OR IGNORE INTO stop_for_day VALUES (?,?,?)", (borrower_id, day, trigger))
        return self._cancel_queued(c, "borrower_id=? AND day=?", (borrower_id, day), "STOP_FOR_DAY")

    def record_response(self, borrower_id: str, at: datetime, channel: Optional[str] = None, kind: str = "reply") -> int:
        """Any reply or borrower-behaviour disposition on any channel → stop for the day (K01 §5)."""
        with self.tx() as c:
            n = self._stop_for_day(c, borrower_id, at, "BORROWER_RESPONSE")
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
        """STOP on any channel suppresses ALL channels and cancels every queued send (K01 holds.STOP_OPT_OUT)."""
        with self.tx() as c:
            c.execute("UPDATE borrowers SET opted_out=1 WHERE id=?", (borrower_id,))
            n = self._cancel_queued(c, "borrower_id=?", (borrower_id,), "STOP_OPT_OUT")
            self._audit(c, "borrower", "STOP_RECEIVED", borrower_id, at=at, channel=channel, canceled=n)
            return n

    def opt_in(self, borrower_id: str, channel: str, consent_version: str, at: datetime) -> None:
        """Re-entry is per channel with the consent version recorded; other channels stay off."""
        with self.tx() as c:
            b = c.execute("SELECT consent, consent_version FROM borrowers WHERE id=?", (borrower_id,)).fetchone()
            consent = {k: False for k in json.loads(b["consent"])} | {channel: True}
            versions = json.loads(b["consent_version"]) | {channel: consent_version}
            c.execute("UPDATE borrowers SET opted_out=0, consent=?, consent_version=? WHERE id=?",
                      (json.dumps(consent), json.dumps(versions), borrower_id))
            self._audit(c, "borrower", "OPT_IN", borrower_id, at=at, channel=channel, consent_version=consent_version)

    def record_payment_verified(self, borrower_id: str, obligation_id: str, amount_inr: float, at: datetime) -> int:
        """Verified (D7/LMS) payment clears the obligation and cancels ITS queued touches only.
        Other obligations stay open (E14/E15)."""
        with self.tx() as c:
            ob = c.execute("SELECT * FROM obligations WHERE id=?", (obligation_id,)).fetchone()
            if ob is None:
                raise KeyError(obligation_id)
            newly = not ob["cleared"]
            c.execute("UPDATE obligations SET cleared=1 WHERE id=?", (obligation_id,))
            c.execute("UPDATE campaigns SET collected_inr = collected_inr + ?, cured_count = cured_count + ? WHERE id=?",
                      (amount_inr, 1 if newly else 0, ob["campaign_id"]))
            b = c.execute("SELECT ptp_obligation FROM borrowers WHERE id=?", (borrower_id,)).fetchone()
            if b and b["ptp_obligation"] == obligation_id:
                c.execute("UPDATE borrowers SET ptp_date=NULL, ptp_obligation=NULL WHERE id=?", (borrower_id,))
            n = self._cancel_queued(c, "obligation_id=?", (obligation_id,), "OBLIGATION_CLEARED")
            self._audit(c, "tool:d7", "PAYMENT_VERIFIED", borrower_id, at=at, obligation_id=obligation_id,
                        amount_inr=amount_inr, canceled=n)
            return n

    def start_day(self, day: date) -> list[str]:
        """Day roll-over: a PTP whose date has passed unpaid becomes BROKEN_PTP and its hold ends."""
        broken = []
        with self.tx() as c:
            rows = c.execute("SELECT b.id, b.ptp_obligation FROM borrowers b WHERE b.ptp_date IS NOT NULL AND b.ptp_date < ?",
                             (day.isoformat(),)).fetchall()
            for r in rows:
                cleared = c.execute("SELECT cleared FROM obligations WHERE id=?", (r["ptp_obligation"],)).fetchone()
                if not (cleared and cleared["cleared"]):
                    c.execute("UPDATE borrowers SET broken_ptp = broken_ptp + 1 WHERE id=?", (r["id"],))
                    broken.append(r["id"])
                    self._audit(c, "supervisor", "BROKEN_PTP", r["id"], reason="BROKEN_PTP", obligation_id=r["ptp_obligation"])
                c.execute("UPDATE borrowers SET ptp_date=NULL, ptp_obligation=NULL WHERE id=?", (r["id"],))
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
        """Run the K13 decision table and apply its hold to the ledger. Every hold also cancels
        that borrower's queued sends so their budget is released for others."""
        out = handle(self.policy, event)
        at = self._loc(at)
        with self.tx() as c:
            canceled, ticket = 0, None
            if out.hold == "STOP_FOR_DAY":
                canceled = self._stop_for_day(c, borrower_id, at, "BORROWER_RESPONSE")
            elif out.hold == "PTP_HOLD":
                ptp = (at + timedelta(days=event.date_offset_days or 0)).date()
                c.execute("UPDATE borrowers SET ptp_date=?, ptp_obligation=? WHERE id=?", (ptp.isoformat(), obligation_id, borrower_id))
                c.execute("INSERT OR IGNORE INTO stop_for_day VALUES (?,?,?)", (borrower_id, at.date().isoformat(), "BORROWER_RESPONSE"))
                canceled = self._cancel_queued(c, "borrower_id=?", (borrower_id,), "PTP_HOLD")
            elif out.hold == "HARDSHIP_HOLD":
                hs = self.policy.raw["holds"]["HARDSHIP"]
                days = event.date_offset_days if event.date_offset_days is not None else hs["default_pause_days"]
                until = at + timedelta(days=days)
                c.execute("UPDATE borrowers SET hardship_until=?, hardship_supervisor=? WHERE id=?",
                          (self._iso(until), int(bool(out.queue)), borrower_id))
                canceled = self._cancel_queued(c, "borrower_id=?", (borrower_id,), "HARDSHIP_HOLD")
            elif out.hold == "CALLBACK_PENDING":
                c.execute("UPDATE borrowers SET callback_pending=1 WHERE id=?", (borrower_id,))
                canceled = self._cancel_queued(c, "borrower_id=?", (borrower_id,), "CALLBACK_PENDING")
            elif out.hold == "SUPERVISOR_HOLD":
                c.execute("UPDATE borrowers SET supervisor_hold=1 WHERE id=?", (borrower_id,))
                canceled = self._cancel_queued(c, "borrower_id=?", (borrower_id,), "SUPERVISOR_HOLD")
            if out.action in ("CLOSE_CALLBACK_TASK", "SEND_MISSED_CALLBACK_MSG"):
                c.execute("UPDATE borrowers SET callback_pending=0 WHERE id=?", (borrower_id,))
            if out.queue:
                sla = self.policy.exceptions.get("callback", {}).get("sla_working_hours") if event.event.startswith("CALLBACK") else None
                ticket = self._ticket(c, out.queue, borrower_id, obligation_id, event.event, summary, at, evidence_ids, sla)
            self._audit(c, "agent:conversation", f"EXCEPTION_{event.event}", borrower_id, at=at, reason=out.action,
                        hold=out.hold, queue=out.queue, ticket=ticket, canceled=canceled)
        return {"action": out.action, "hold": out.hold, "queue": out.queue, "ticket_id": ticket, "canceled": canceled}

    def clear_supervisor_hold(self, borrower_id: str, actor: str = "human:supervisor") -> None:
        with self.tx() as c:
            c.execute("UPDATE borrowers SET supervisor_hold=0, hardship_supervisor=0 WHERE id=?", (borrower_id,))
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
    def borrower_state(self, borrower_id: str) -> dict:
        c = self._conn()
        b = c.execute("SELECT * FROM borrowers WHERE id=?", (borrower_id,)).fetchone()
        if b is None:
            raise KeyError(borrower_id)
        out = dict(b)
        out["consent"] = json.loads(out["consent"])
        out["consent_version"] = json.loads(out["consent_version"])
        out["obligations"] = [dict(r) for r in c.execute("SELECT * FROM obligations WHERE borrower_id=?", (borrower_id,))]
        out["touches"] = [dict(r) for r in c.execute("SELECT * FROM touches WHERE borrower_id=? ORDER BY scheduled_at", (borrower_id,))]
        out["stop_for_day"] = [dict(r) for r in c.execute("SELECT day, trigger FROM stop_for_day WHERE borrower_id=?", (borrower_id,))]
        out["tickets"] = [dict(r) for r in c.execute("SELECT * FROM tickets WHERE borrower_id=?", (borrower_id,))]
        return out

    def campaign_state(self, campaign_id: str) -> dict:
        c = self._conn()
        camp = c.execute("SELECT * FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
        if camp is None:
            raise KeyError(campaign_id)
        out = dict(camp)
        out["pool_inr"] = camp["budget_per_case_inr"] * camp["allocated_cases"]
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
        """Independent post-hoc audit: recount every borrower/day/channel among delivered-or-live
        touches and compare with the effective caps. Should always be empty."""
        c = self._conn()
        live = ",".join("?" * len(LIVE))
        out = []
        for r in c.execute(f"SELECT t.borrower_id, t.day, t.channel, COUNT(*) n, b.dpd, b.lender_id FROM touches t"
                           f" JOIN borrowers b ON b.id=t.borrower_id WHERE t.status IN ({live})"
                           " GROUP BY t.borrower_id, t.day, t.channel", LIVE):
            band = self.policy.band_for(r["dpd"]) if r["dpd"] is not None else None
            if band is None:
                continue
            cap = self.policy.channel_day_cap(band, r["channel"], r["lender_id"])
            if cap is not None and r["n"] > cap:
                out.append(dict(r) | {"cap": cap})
        return out
