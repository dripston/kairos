"""K06 human-in-the-loop (HITL) control plane — everything a person can do to Kairos, audited.

Rules come from policy/K06-human-in-the-loop/approval_policy.json (DRAFT, pending Ojas).
Everything is deterministic: every function takes an optional `now` (the demo / simulation clock).

What a person can do                        How it stays safe
------------------------------------------  ------------------------------------------------------------
Approve / reject a held send                The send was reserved as PENDING_APPROVAL (it holds its cap
                                            slot and budget, so nothing can overbook). Approval re-runs the
                                            K01 Gate atomically; the Executor re-checks again at send time.
                                            Reject / expiry cancel it and release the budget.
Borrower actions: pause/resume, hold until  Each one needs actor + role + reason and writes ONE audit row.
a date, cancel next touch, hand off, assign, Holds use the K06 supervisor-hold flag, so the Gate enforces
do-not-contact                              them everywhere (fail closed). DNC = STOP on every channel.
Manual scheduler (+ live preview)           Same Gate + atomic reserve as automated sends, plus a dedupe
                                            rule (same EMI, channel, day). Lands in PENDING_APPROVAL.
Case queue: claim / resolve / reopen        Resolving a case never lifts a hold unless asked explicitly.
Locked by guardrails                        Overriding STOP/consent, exceeding a cap, bypassing quiet
                                            hours or the window, an unregistered template: always refused,
                                            for every role, and audited as HUMAN_ACTION_REFUSED.

Roles come from the X-Kairos-Role header in the demo API — that is NOT authentication.
Audit rows never contain raw PII: borrower ids only, and free-text reasons are scrubbed.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, Optional, Union

from spine.ledger import record_event

from .exceptions import ROUTED_STOP
from .gate import WAIT, Decision, evaluate
from .ledger import LIVE, QUEUED, Instruction, Ledger
from .policy import policy_dir

__all__ = ["HITL", "HitlError", "Forbidden", "Refused", "Conflict", "load_approval_policy", "mask", "scrub"]

HITL_SCHEMA = """
CREATE TABLE IF NOT EXISTS hitl_approvals (
  instruction_id TEXT PRIMARY KEY, borrower_id TEXT NOT NULL, rules TEXT NOT NULL, origin TEXT NOT NULL,
  requested_at TEXT NOT NULL, expires_at TEXT NOT NULL,
  decision TEXT, decided_by TEXT, decided_role TEXT, decided_at TEXT, decision_reason TEXT);
CREATE TABLE IF NOT EXISTS hitl_manual (
  instruction_id TEXT PRIMARY KEY, borrower_id TEXT NOT NULL, scheduled_by TEXT NOT NULL, role TEXT NOT NULL,
  reason TEXT NOT NULL, created_at TEXT NOT NULL, replaces TEXT, replaced_by TEXT);
CREATE TABLE IF NOT EXISTS hitl_holds (
  hold_id TEXT PRIMARY KEY, borrower_id TEXT NOT NULL, kind TEXT NOT NULL, until TEXT, ticket_id TEXT,
  active INTEGER NOT NULL DEFAULT 1, created_by TEXT, created_at TEXT,
  released_by TEXT, released_at TEXT, release_note TEXT);
CREATE INDEX IF NOT EXISTS ix_hitl_holds_borrower ON hitl_holds (borrower_id, active);
CREATE TABLE IF NOT EXISTS hitl_assignments (
  borrower_id TEXT PRIMARY KEY, agent TEXT NOT NULL, assigned_by TEXT, assigned_at TEXT);
CREATE TABLE IF NOT EXISTS hitl_cases (
  ticket_id TEXT PRIMARY KEY, assignee TEXT, claimed_at TEXT, resolution_code TEXT, resolved_by TEXT,
  resolved_at TEXT, hold_lifted INTEGER NOT NULL DEFAULT 0, reopened_count INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS hitl_templates (template_id TEXT PRIMARY KEY, intensity TEXT NOT NULL, body TEXT);
CREATE TABLE IF NOT EXISTS hitl_dnc (borrower_id TEXT PRIMARY KEY, set_by TEXT, set_role TEXT, set_at TEXT);
CREATE INDEX IF NOT EXISTS ix_audit_borrower ON audit (borrower_id, id);
CREATE INDEX IF NOT EXISTS ix_audit_type ON audit (type, id);
CREATE INDEX IF NOT EXISTS ix_audit_actor ON audit (actor, id);
"""

KNOWN_RULES = {"VOICE_CHANNEL", "STANDARD_INTENSITY_HIGH_DPD", "FIRST_SEND_AFTER_HARDSHIP", "MANUAL_DISPATCH"}
BORROWER_ACTIONS = {"pause", "resume", "hold", "cancel_next", "handoff", "assign", "dnc", "record_opt_in"}
HOLD_KINDS = ("MANUAL_PAUSE", "MANUAL_HOLD", "HANDOFF")
OPEN_CASE = ("OPEN", "CLAIMED")


# ---------- errors (the API maps .http to the status code) ----------
class HitlError(Exception):
    http = 409

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code

    def as_dict(self) -> dict:
        return {"code": self.code, "message": str(self)}


class Forbidden(HitlError):
    """The role may not do this (audited)."""
    http = 403


class Refused(HitlError):
    """Locked by guardrails — nobody may do this (audited)."""
    http = 409


class Conflict(HitlError):
    """The thing is not in a state where this applies (e.g. approving a send that is no longer pending)."""
    http = 409


# ---------- policy ----------
def load_approval_policy(base: Optional[Path] = None) -> dict:
    path = Path(base or policy_dir()) / "K06-human-in-the-loop" / "approval_policy.json"
    with open(path, encoding="utf-8") as f:
        ap = json.load(f)
    unknown = {r["id"] for r in ap["approval_rules"]["rules"]} - KNOWN_RULES
    if unknown:
        raise ValueError(f"approval_policy.json has rules the code does not implement: {sorted(unknown)}")
    return ap


# ---------- PII ----------
_PHONE = re.compile(r"(?<![\w-])\+?\d[\d \-]{7,}\d")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE_KEYS = {"phone", "mobile", "phone_number", "alt_phone"}
_NAME_KEYS = {"full_name", "borrower_name", "first_name"}
_EMAIL_KEYS = {"email"}
_AMOUNT_KEYS = {"amount", "amount_inr", "outstanding"}


def scrub(text: Optional[str]) -> str:
    """Remove phone numbers and emails from free text before it is stored (audit, tickets)."""
    return _EMAIL.sub("[email]", _PHONE.sub("[phone]", text or ""))


def _mask_value(kind: str, mode: str, v):
    if v is None or mode == "full":
        return v
    if kind == "phone":
        digits = re.sub(r"\D", "", str(v))
        keep = 2 if mode == "last2" else 4
        return "•" * max(0, len(digits) - keep) + digits[-keep:]
    if kind == "name":
        return " ".join(p[0] + "." for p in str(v).split() if p) if mode == "initials" else "•••"
    if kind == "email":
        return "•••@" + str(v).split("@")[-1] if mode == "domain" and "@" in str(v) else "•••"
    if kind == "amount":
        return "•••"
    return v


def mask(record, role: str, rules: Optional[dict] = None):
    """Mask phone / name / email / amounts in a record (dict, list, nested) for a role. Unknown roles get
    the strictest view (viewer). Pass rules=approval_policy['masking'] (default: the policy on disk)."""
    rules = rules if rules is not None else _default_masking()
    r = rules.get(role) or rules["viewer"]
    if isinstance(record, list):
        return [mask(x, role, rules) for x in record]
    if not isinstance(record, dict):
        return record
    out = {}
    for k, v in record.items():
        lk = k.lower()
        if isinstance(v, (dict, list)):
            out[k] = mask(v, role, rules)
        elif lk in _PHONE_KEYS:
            out[k] = _mask_value("phone", r["phone"], v)
        elif lk in _NAME_KEYS:
            out[k] = _mask_value("name", r["name"], v)
        elif lk in _EMAIL_KEYS:
            out[k] = _mask_value("email", r["email"], v)
        elif (lk.endswith("_paise") or lk in _AMOUNT_KEYS) and isinstance(v, (int, float)):
            out[k] = _mask_value("amount", r["amount"], v)
        elif lk in ("summary_line_1", "summary_line_2", "rendered") and r["name"] != "full":
            out[k] = scrub(v) if v else v  # free text may name the borrower: strictest roles see it scrubbed
        else:
            out[k] = v
    return out


_MASKING_CACHE: dict = {}


def _default_masking() -> dict:
    if "m" not in _MASKING_CACHE:
        _MASKING_CACHE["m"] = load_approval_policy()["masking"]
    return _MASKING_CACHE["m"]


class _Template(dict):
    def __missing__(self, key):
        return "{" + key + "}"


# ---------- the control plane ----------
class HITL:
    def __init__(self, ledger: Ledger, approval_policy: Optional[dict] = None):
        self.ledger = ledger
        self.policy = ledger.policy
        self.ap = approval_policy or load_approval_policy()
        ledger._conn().executescript(HITL_SCHEMA)
        ledger.reserve_hook = self._on_reserve  # every reservation (automated or manual) passes through here

    # ---------- small helpers ----------
    @property
    def version(self) -> str:
        return self.ap["policy_version"]

    def _now(self, now: Optional[datetime]) -> datetime:
        return self.ledger._loc(now) if now else self.policy.now()

    def _iso(self, dt: datetime) -> str:
        return self.ledger._iso(dt)

    @staticmethod
    def _actor(actor: str) -> str:
        actor = (actor or "").strip() or "unknown"
        return actor if actor.startswith("human:") else f"human:{actor}"

    @staticmethod
    def _reason(reason: Optional[str]) -> str:
        r = (reason or "").strip()
        if not r:
            raise ValueError("reason is required for every human action")
        return scrub(r)

    def mask(self, record, role: str):
        return mask(record, role, self.ap["masking"])

    def permitted(self, perm: str, role: str) -> bool:
        return role in self.ap["permissions"].get(perm, [])

    def _audit(self, c, type_: str, actor: str, role: str, borrower_id=None, instruction_id=None, reason="",
               at: Optional[datetime] = None, **detail) -> None:
        self.ledger._audit(c, self._actor(actor) if not actor.startswith("system") else actor, type_, borrower_id,
                           instruction_id, None, at=at, reason=scrub(reason), role=role,
                           hitl_policy_version=self.version, **detail)

    def _refuse(self, exc: HitlError, action: str, actor: str, role: str, at: datetime, borrower_id=None,
                instruction_id=None, **detail):
        """Write the HUMAN_ACTION_REFUSED row in its own transaction, then raise."""
        with self.ledger.tx() as c:
            self._audit(c, "HUMAN_ACTION_REFUSED", actor, role, borrower_id, instruction_id, str(exc), at,
                        action=action, refusal=exc.code, **detail)
        raise exc

    def _authorize(self, perm: str, actor: str, role: str, at: datetime, borrower_id=None, instruction_id=None):
        if role not in self.ap["roles"]:
            self._refuse(Forbidden("UNKNOWN_ROLE", f"unknown role '{role}'"), perm, actor, role, at, borrower_id, instruction_id)
        if not self.permitted(perm, role):
            self._refuse(Forbidden("ROLE_NOT_PERMITTED", f"role '{role}' may not {perm}"), perm, actor, role, at,
                         borrower_id, instruction_id)

    def _require_view(self, role: str) -> None:
        if not self.permitted("view", role):
            raise Forbidden("ROLE_NOT_PERMITTED", f"role '{role}' may not view")

    def _borrower(self, c, borrower_id: str):
        b = c.execute("SELECT * FROM borrowers WHERE borrower_id=?", (borrower_id,)).fetchone()
        if b is None:
            raise KeyError(borrower_id)
        return b

    def _expiry(self, at: datetime) -> datetime:
        """Default approval expiry: end of the K01 contact window on the send's day."""
        end = self.policy.raw["contact_window"]["end_exclusive"]
        return at.replace(hour=int(end[:2]), minute=int(end[3:]), second=0, microsecond=0)

    def _touch(self, c, instruction_id: str):
        t = c.execute("SELECT * FROM touches WHERE instruction_id=?", (instruction_id,)).fetchone()
        if t is None:
            raise KeyError(instruction_id)
        return t

    def _cancel_touch(self, c, t, reason: str, now: datetime) -> None:
        c.execute("UPDATE touches SET status='CANCELED', reason=?, updated_at=? WHERE instruction_id=?",
                  (reason, self._iso(now), t["instruction_id"]))
        self.ledger._release(c, t)

    def _recheck(self, c, t, at: datetime, own_cost_reserved: bool = True) -> Decision:
        """The full Gate again for an existing touch at `at` (same as Ledger.begin_dispatch): its own
        reservation is excluded from the counts and its own cost added back to the budget."""
        ins = self.ledger.instruction_of(dict(t))
        ins.at = at
        d = self.ledger._pre_gate(c, ins)
        if d is None:
            facts, _ = self.ledger.facts_for(c, ins, exclude=t["instruction_id"])
            if own_cost_reserved:
                facts.budget_remaining_inr += t["est_cost_paise"] / 100
            d = evaluate(self.policy, facts)
        if d.allowed and self._active_holds(c, t["borrower_id"]):
            d = Decision(WAIT, "SUPERVISOR_HOLD", "SUPERVISOR_HOLD", self.policy.version, d.band, {})
        return d

    # ---------- approval rules (called inside Ledger.reserve's transaction) ----------
    def _intensity(self, c, template_id: Optional[str]) -> str:
        r = c.execute("SELECT intensity FROM hitl_templates WHERE template_id=?", (template_id,)).fetchone()
        return r["intensity"] if r else self.ap["approval_rules"]["unknown_template_intensity"]

    def _first_after_hardship(self, c, ins: Instruction) -> bool:
        st = self.ledger._state(c, ins.borrower_id)
        if not st["hardship_until"]:
            return False
        after = c.execute("SELECT COUNT(*) FROM touches WHERE borrower_id=? AND scheduled_at>=? AND instruction_id<>?"
                          " AND status IN ('RESERVED','DISPATCHING','UNCERTAIN','SENT','DELIVERED')",
                          (ins.borrower_id, st["hardship_until"], ins.instruction_id or "")).fetchone()[0]
        return after == 0

    def approval_rules_for(self, c, ins: Instruction, dpd: Optional[int]) -> list[str]:
        hits = []
        for r in self.ap["approval_rules"]["rules"]:
            if not r.get("enabled", True):
                continue
            rid = r["id"]
            if rid == "VOICE_CHANNEL" and ins.channel in r["channels"]:
                hits.append(rid)
            elif (rid == "STANDARD_INTENSITY_HIGH_DPD" and dpd is not None and dpd > r["dpd_above"]
                  and self._intensity(c, ins.template_id) in r["template_intensity"]):
                hits.append(rid)
            elif rid == "FIRST_SEND_AFTER_HARDSHIP" and self._first_after_hardship(c, ins):
                hits.append(rid)
            elif rid == "MANUAL_DISPATCH" and ins.origin in r["origins"]:
                hits.append(rid)
        return hits

    def _manual_duplicate(self, c, ins: Instruction) -> Optional[str]:
        """Manual sends: one live send per EMI + channel + day, automated or manual."""
        day = self.ledger._loc(ins.at).date().isoformat()
        live = ",".join("?" * len(LIVE))
        r = c.execute(f"SELECT instruction_id FROM touches WHERE obligation_id=? AND channel=? AND day=?"
                      f" AND status IN ({live}) AND instruction_id<>? LIMIT 1",
                      (ins.obligation_id, ins.channel, day, *LIVE, ins.instruction_id or "")).fetchone()
        return r["instruction_id"] if r else None

    def _on_reserve(self, c, ins: Instruction, d: Decision):
        """Ledger.reserve_hook: only ever stricter than the Gate. Returns (touch status, decision)."""
        if self._active_holds(c, ins.borrower_id):  # a person's hold (also enforced via supervisor_hold)
            return "RESERVED", Decision(WAIT, "SUPERVISOR_HOLD", "SUPERVISOR_HOLD", d.policy_version, d.band, d.detail)
        if ins.origin == "manual" and self.ap["manual_scheduler"]["dedupe_scope"] == "obligation_channel_day":
            dup = self._manual_duplicate(c, ins)
            if dup:
                return "RESERVED", Decision(WAIT, "MANUAL_DUPLICATE", "HITL_DEDUPE", d.policy_version, d.band,
                                            {**d.detail, "existing_instruction_id": dup})
        rules = self.approval_rules_for(c, ins, d.detail.get("dpd"))
        if not rules:
            return "RESERVED", d
        at = self.ledger._loc(ins.at)
        expires = self._expiry(at)
        c.execute("INSERT INTO hitl_approvals (instruction_id, borrower_id, rules, origin, requested_at, expires_at)"
                  " VALUES (?,?,?,?,?,?)", (ins.instruction_id, ins.borrower_id, json.dumps(rules), ins.origin,
                                            self._iso(self.policy.now()), self._iso(expires)))
        d.detail.update(touch_status="PENDING_APPROVAL", approval_rules=rules, approval_expires_at=self._iso(expires))
        return "PENDING_APPROVAL", d

    # ---------- approvals ----------
    def list_approvals(self, role: str, status: str = "PENDING", borrower_id: Optional[str] = None,
                       now: Optional[datetime] = None) -> list[dict]:
        self._require_view(role)
        self.expire_due(now)
        q = ("SELECT t.instruction_id, t.borrower_id, t.loan_id, t.obligation_id, t.campaign_id, t.channel, t.template_id,"
             " t.scheduled_at, t.status, t.reason, t.est_cost_paise, a.rules, a.origin, a.expires_at, a.decision,"
             " a.decided_by, a.decided_role, a.decided_at, a.decision_reason, m.scheduled_by, b.full_name, b.phone"
             " FROM hitl_approvals a JOIN touches t ON t.instruction_id=a.instruction_id"
             " LEFT JOIN hitl_manual m ON m.instruction_id=a.instruction_id"
             " LEFT JOIN borrowers b ON b.borrower_id=t.borrower_id WHERE 1=1")
        args: list = []
        if status.upper() == "PENDING":
            q += " AND t.status='PENDING_APPROVAL'"
        if borrower_id:
            q += " AND t.borrower_id=?"
            args.append(borrower_id)
        rows = self.ledger._conn().execute(q + " ORDER BY t.scheduled_at", args).fetchall()
        return [self.mask({**dict(r), "rules": json.loads(r["rules"])}, role) for r in rows]

    def approve(self, instruction_id: str, actor: str, role: str, reason: str, now: Optional[datetime] = None) -> dict:
        reason = self._reason(reason)
        now = self._now(now)
        self._authorize("approve", actor, role, now, instruction_id=instruction_id)
        refusal = None
        with self.ledger.tx() as c:
            t = self._touch(c, instruction_id)
            if t["status"] != "PENDING_APPROVAL":
                raise Conflict(f"NOT_PENDING_{t['status']}", f"{instruction_id} is {t['status']}, not pending approval")
            a = c.execute("SELECT * FROM hitl_approvals WHERE instruction_id=?", (instruction_id,)).fetchone()
            m = c.execute("SELECT scheduled_by FROM hitl_manual WHERE instruction_id=?", (instruction_id,)).fetchone()
            if self.ap["approval"]["maker_checker"] and m and m["scheduled_by"] == self._actor(actor):
                refusal = Forbidden("MAKER_CHECKER", "the person who scheduled a manual send cannot approve it")
                self._audit(c, "HUMAN_ACTION_REFUSED", actor, role, t["borrower_id"], instruction_id, str(refusal), now,
                            action="approve", refusal=refusal.code)
            elif now >= self.ledger._dt(a["expires_at"]):
                self._expire(c, t, now)
                return {"instruction_id": instruction_id, "outcome": "EXPIRED", "status": "CANCELED"}
            else:
                sched = self.ledger._dt(t["scheduled_at"])
                d = self._recheck(c, t, max(sched, now))
                if d.allowed:
                    c.execute("UPDATE touches SET status='RESERVED', reason='APPROVED', updated_at=? WHERE instruction_id=?",
                              (self._iso(now), instruction_id))
                    outcome = "APPROVED"
                else:  # the world changed since it was reserved: the guardrail wins, the approval cannot
                    self._cancel_touch(c, t, d.reason, now)
                    outcome = "BLOCKED_AT_RECHECK"
                c.execute("UPDATE hitl_approvals SET decision=?, decided_by=?, decided_role=?, decided_at=?, decision_reason=?"
                          " WHERE instruction_id=?", (outcome, self._actor(actor), role, self._iso(now), reason, instruction_id))
                self._audit(c, "APPROVAL_GRANTED" if d.allowed else "APPROVAL_BLOCKED", actor, role, t["borrower_id"],
                            instruction_id, reason, now, gate_decision=d.decision, gate_reason=d.reason, gate_rule=d.rule,
                            policy_version=d.policy_version, rules=json.loads(a["rules"]))
                return {"instruction_id": instruction_id, "outcome": outcome,
                        "status": "RESERVED" if d.allowed else "CANCELED", "gate": d.as_dict()}
        raise refusal

    def reject(self, instruction_id: str, actor: str, role: str, reason: str, now: Optional[datetime] = None) -> dict:
        reason = self._reason(reason)
        now = self._now(now)
        self._authorize("reject", actor, role, now, instruction_id=instruction_id)
        with self.ledger.tx() as c:
            t = self._touch(c, instruction_id)
            if t["status"] != "PENDING_APPROVAL":
                raise Conflict(f"NOT_PENDING_{t['status']}", f"{instruction_id} is {t['status']}, not pending approval")
            self._cancel_touch(c, t, "REJECTED_BY_HUMAN", now)
            c.execute("UPDATE hitl_approvals SET decision='REJECTED', decided_by=?, decided_role=?, decided_at=?,"
                      " decision_reason=? WHERE instruction_id=?", (self._actor(actor), role, self._iso(now), reason, instruction_id))
            self._audit(c, "APPROVAL_REJECTED", actor, role, t["borrower_id"], instruction_id, reason, now,
                        released_paise=t["est_cost_paise"])
        return {"instruction_id": instruction_id, "outcome": "REJECTED", "status": "CANCELED"}

    def _expire(self, c, t, now: datetime) -> None:
        self._cancel_touch(c, t, "APPROVAL_EXPIRED", now)
        c.execute("UPDATE hitl_approvals SET decision='EXPIRED', decided_by='system:hitl', decided_at=? WHERE instruction_id=?",
                  (self._iso(now), t["instruction_id"]))
        self._audit(c, "APPROVAL_EXPIRED", "system:hitl", "system", t["borrower_id"], t["instruction_id"],
                    "no decision before the approval expired", now, released_paise=t["est_cost_paise"])

    def expire_due(self, now: Optional[datetime] = None) -> int:
        """Cancel every pending approval whose expiry has passed (budget released)."""
        now = self._now(now)
        with self.ledger.tx() as c:
            rows = c.execute("SELECT t.* FROM touches t JOIN hitl_approvals a ON a.instruction_id=t.instruction_id"
                             " WHERE t.status='PENDING_APPROVAL' AND a.expires_at<=?", (self._iso(now),)).fetchall()
            for t in rows:
                self._expire(c, t, now)
        return len(rows)

    # ---------- holds ----------
    def _active_holds(self, c, borrower_id: str) -> list[dict]:
        return [dict(r) for r in c.execute("SELECT * FROM hitl_holds WHERE borrower_id=? AND active=1 ORDER BY created_at",
                                           (borrower_id,))]

    def _k13_hold_outstanding(self, c, borrower_id: str) -> bool:
        """A K13 routed-stop (abusive / legal threat / fraud claim) put a supervisor hold that no one lifted."""
        types = ",".join("?" * len(ROUTED_STOP))
        return c.execute(f"SELECT 1 FROM tickets t LEFT JOIN hitl_cases h ON h.ticket_id=t.ticket_id WHERE t.borrower_id=?"
                         f" AND t.type IN ({types}) AND COALESCE(h.hold_lifted,0)=0 LIMIT 1",
                         (borrower_id, *sorted(ROUTED_STOP))).fetchone() is not None

    def _refresh_supervisor_flag(self, c, borrower_id: str) -> bool:
        """supervisor_hold = any active HITL hold OR an un-lifted K13 routed-stop hold."""
        on = bool(self._active_holds(c, borrower_id)) or self._k13_hold_outstanding(c, borrower_id)
        self.ledger._set_state(c, borrower_id, supervisor_hold=int(on))
        return on

    def _place_hold(self, c, borrower_id: str, kind: str, actor: str, now: datetime, until: Optional[datetime] = None,
                    ticket_id: Optional[str] = None) -> tuple[str, int]:
        hid = "HLD-" + uuid.uuid4().hex[:10]
        c.execute("INSERT INTO hitl_holds (hold_id, borrower_id, kind, until, ticket_id, created_by, created_at)"
                  " VALUES (?,?,?,?,?,?,?)", (hid, borrower_id, kind, self._iso(until) if until else None, ticket_id,
                                              self._actor(actor), self._iso(now)))
        self.ledger._set_state(c, borrower_id, supervisor_hold=1)
        return hid, self.ledger._cancel_queued(c, "borrower_id=?", (borrower_id,), kind)

    def _release_holds(self, c, where: str, args: tuple, by: str, now: datetime, note: str) -> int:
        n = c.execute(f"UPDATE hitl_holds SET active=0, released_by=?, released_at=?, release_note=? WHERE active=1 AND {where}",
                      (by, self._iso(now), note, *args)).rowcount
        return n

    def _unpaid_due(self, c, borrower_id: str, day: str) -> list[str]:
        return [r[0] for r in c.execute("SELECT o.obligation_id FROM obligations o JOIN loans l ON l.loan_id=o.loan_id"
                                        " WHERE l.borrower_id=? AND o.due_date<=? AND o.amount_paid_paise<o.amount_due_paise"
                                        " ORDER BY o.due_date", (borrower_id, day))]

    def release_due_holds(self, now: Optional[datetime] = None) -> list[dict]:
        """MANUAL_HOLD whose date has come: re-check payment first (as for a PTP), then let outreach resume."""
        now = self._now(now)
        out = []
        with self.ledger.tx() as c:
            rows = c.execute("SELECT * FROM hitl_holds WHERE active=1 AND kind='MANUAL_HOLD' AND until<=?",
                             (self._iso(now),)).fetchall()
            for h in rows:
                unpaid = self._unpaid_due(c, h["borrower_id"], now.date().isoformat())
                payment = "UNPAID" if unpaid else "PAID"
                self._release_holds(c, "hold_id=?", (h["hold_id"],), "system:hitl", now, f"hold date reached; payment {payment}")
                held = self._refresh_supervisor_flag(c, h["borrower_id"])
                self._audit(c, "HOLD_RELEASED", "system:hitl", "system", h["borrower_id"], None,
                            "manual hold date reached; payment re-checked", now, hold_id=h["hold_id"], payment=payment,
                            unpaid_obligations=unpaid, still_held=held)
                out.append({"borrower_id": h["borrower_id"], "hold_id": h["hold_id"], "payment": payment,
                            "unpaid_obligations": unpaid, "still_held": held})
        return out

    def tick(self, now: Optional[datetime] = None) -> dict:
        """Run the HITL clocks: expire approvals, release manual holds whose date has come."""
        return {"expired_approvals": self.expire_due(now), "released_holds": self.release_due_holds(now)}

    # ---------- borrower actions ----------
    def act(self, borrower_id: str, action: str, actor: str, role: str, reason: str,
            now: Optional[datetime] = None, **params) -> dict:
        """One human action on one borrower. Reason mandatory; exactly one audit row per action."""
        now = self._now(now)
        locked = self.ap["locked_by_guardrails"]["actions"]
        if action in locked:
            self._refuse(Refused("LOCKED_BY_GUARDRAILS",
                                 f"'{action}' is locked by guardrails: nobody can override STOP/consent, exceed a cap, "
                                 f"bypass quiet hours or the contact window, or send an unregistered template"),
                         action, actor, role, now, borrower_id)
        if action not in BORROWER_ACTIONS:
            raise ValueError(f"unknown action '{action}' (one of {sorted(BORROWER_ACTIONS)})")
        reason = self._reason(reason)
        perm = action
        if action == "resume":
            with self.ledger.tx() as c:
                self._borrower(c, borrower_id)
                holds = self._active_holds(c, borrower_id)
                external = not holds and bool(self.ledger._state(c, borrower_id)["supervisor_hold"])
            if external:
                perm = "clear_supervisor_hold"  # lifting a hold a person did not place here needs a supervisor
        self._authorize(perm, actor, role, now, borrower_id)
        with self.ledger.tx() as c:
            self._borrower(c, borrower_id)
            fn = getattr(self, f"_do_{action}")
            detail = fn(c, borrower_id, actor, role, now, **params)
            self._audit(c, "HUMAN_ACTION", actor, role, borrower_id, detail.pop("_instruction_id", None), reason, now,
                        action=action, **detail)
        return {"borrower_id": borrower_id, "action": action, **detail}

    def _do_pause(self, c, bid, actor, role, now) -> dict:
        if any(h["kind"] == "MANUAL_PAUSE" for h in self._active_holds(c, bid)):
            return {"already_paused": True, "canceled": 0}
        hid, n = self._place_hold(c, bid, "MANUAL_PAUSE", actor, now)
        return {"hold_id": hid, "canceled": n}

    def _do_resume(self, c, bid, actor, role, now) -> dict:
        n = self._release_holds(c, "borrower_id=? AND kind='MANUAL_PAUSE'", (bid,), self._actor(actor), now, "resumed")
        cleared_external = False
        if n == 0 and self.ledger._state(c, bid)["supervisor_hold"] and not self._active_holds(c, bid):
            # a K13 routed-stop hold (or legacy supervisor hold); the caller holds clear_supervisor_hold
            for t in c.execute("SELECT ticket_id FROM tickets WHERE borrower_id=? AND type IN (%s)"
                               % ",".join("?" * len(ROUTED_STOP)), (bid, *sorted(ROUTED_STOP))).fetchall():
                c.execute("INSERT INTO hitl_cases (ticket_id, hold_lifted) VALUES (?,1)"
                          " ON CONFLICT(ticket_id) DO UPDATE SET hold_lifted=1", (t["ticket_id"],))
            self.ledger._set_state(c, bid, hardship_supervisor=0)
            cleared_external = True
        held = self._refresh_supervisor_flag(c, bid)
        st = self.ledger._state(c, bid)
        blocked = [k for k, on in (("STOP_OPT_OUT", st["stopped"]), ("SUPERVISOR_HOLD", held),
                                    ("CALLBACK_PENDING", st["callback_pending"])) if on]
        return {"released_pauses": n, "cleared_external_hold": cleared_external, "still_blocked_by": blocked}

    def _do_hold(self, c, bid, actor, role, now, until=None, days=None) -> dict:
        if until is None and days is not None:
            until = now + timedelta(days=float(days))
        if until is None:
            raise ValueError("hold needs 'until' (datetime) or 'days'")
        until = self.ledger._loc(until if isinstance(until, datetime) else datetime.fromisoformat(str(until)))
        if until <= now:
            raise ValueError("hold 'until' must be in the future")
        hid, n = self._place_hold(c, bid, "MANUAL_HOLD", actor, now, until=until)
        return {"hold_id": hid, "until": self._iso(until), "canceled": n,
                "on_release": "payment is re-checked before outreach resumes"}

    def _do_cancel_next(self, c, bid, actor, role, now) -> dict:
        q = ",".join("?" * len(QUEUED))
        t = c.execute(f"SELECT * FROM touches WHERE borrower_id=? AND status IN ({q}) ORDER BY scheduled_at LIMIT 1",
                      (bid, *QUEUED)).fetchone()
        if t is None:
            return {"canceled_instruction_id": None, "note": "nothing queued"}
        self._cancel_touch(c, t, "CANCELED_BY_HUMAN", now)
        return {"canceled_instruction_id": t["instruction_id"], "_instruction_id": t["instruction_id"],
                "channel": t["channel"], "scheduled_at": t["scheduled_at"], "released_paise": t["est_cost_paise"]}

    def _queues(self) -> list[str]:
        k13 = self.policy.exceptions.get("queues", {})
        return list(dict.fromkeys(k13.get("available", []) + self.ap["cases"]["extra_queues"]))

    def _do_handoff(self, c, bid, actor, role, now, queue=None, summary=None) -> dict:
        queue = queue or self.policy.exceptions.get("queues", {}).get("default", "COLLECTIONS_SUPERVISOR")
        if queue not in self._queues():
            raise ValueError(f"unknown queue '{queue}' (one of {self._queues()})")
        unpaid = self._unpaid_due(c, bid, "9999-12-31")
        ob = unpaid[0] if unpaid else None
        lines = (scrub(summary or "Handed off to a person from the Control Room"), f"by {self._actor(actor)} ({role})")
        sla = self.ap["cases"]["sla_hours_by_type"].get("HUMAN_HANDOFF")
        tid = self.ledger._ticket(c, queue, bid, ob, "HUMAN_HANDOFF", lines, now, (), sla)
        status = c.execute("SELECT status FROM tickets WHERE ticket_id=?", (tid,)).fetchone()["status"]
        reopened = status == "RESOLVED"
        if reopened:
            c.execute("UPDATE tickets SET status='OPEN' WHERE ticket_id=?", (tid,))
            c.execute("UPDATE hitl_cases SET reopened_count=reopened_count+1 WHERE ticket_id=?", (tid,))
        existing = c.execute("SELECT hold_id FROM hitl_holds WHERE ticket_id=? AND active=1", (tid,)).fetchone()
        if existing:
            return {"ticket_id": tid, "queue": queue, "hold_id": existing["hold_id"], "canceled": 0, "idempotent": True}
        hid, n = self._place_hold(c, bid, "HANDOFF", actor, now, ticket_id=tid)
        return {"ticket_id": tid, "queue": queue, "hold_id": hid, "canceled": n, "reopened": reopened}

    def _do_assign(self, c, bid, actor, role, now, agent=None) -> dict:
        agent = (agent or "").strip()
        if not agent:
            raise ValueError("assign needs 'agent'")
        prev = c.execute("SELECT agent FROM hitl_assignments WHERE borrower_id=?", (bid,)).fetchone()
        c.execute("INSERT INTO hitl_assignments VALUES (?,?,?,?) ON CONFLICT(borrower_id) DO UPDATE SET"
                  " agent=excluded.agent, assigned_by=excluded.assigned_by, assigned_at=excluded.assigned_at",
                  (bid, agent, self._actor(actor), self._iso(now)))
        return {"agent": agent, "previous_agent": prev["agent"] if prev else None}

    def _do_dnc(self, c, bid, actor, role, now) -> dict:
        """Do-not-contact = STOP on every channel. Kairos never undoes it; only a per-channel opt-in
        with a consent version (record_opt_in) re-enables that one channel."""
        self.ledger._set_state(c, bid, stopped=1)
        c.execute("UPDATE consents SET opted_out=1, updated_at=? WHERE borrower_id=?", (self._iso(now), bid))
        record_event(c, f"EV-DNC-{bid}-{self._iso(now)}", bid, "consent_changed", self._iso(now),
                     details="DO_NOT_CONTACT set by a person: opted out of all channels")
        n = self.ledger._cancel_queued(c, "borrower_id=?", (bid,), "DO_NOT_CONTACT")
        c.execute("INSERT OR IGNORE INTO hitl_dnc VALUES (?,?,?,?)", (bid, self._actor(actor), role, self._iso(now)))
        return {"canceled": n, "channels": "all"}

    def _do_record_opt_in(self, c, bid, actor, role, now, channel=None, consent_version=None) -> dict:
        """The borrower opted back in on ONE channel (evidence: consent_version). Other channels stay off."""
        if channel not in self.policy.raw["channels"]:
            raise ValueError(f"record_opt_in needs a valid 'channel' (one of {self.policy.raw['channels']})")
        if not (consent_version or "").strip():
            raise ValueError("record_opt_in needs the borrower's 'consent_version'")
        c.execute("INSERT INTO consents (borrower_id, channel, consented, opted_out, updated_at, source)"
                  " VALUES (?,?,1,0,?,'provided') ON CONFLICT (borrower_id, channel) DO UPDATE SET consented=1,"
                  " opted_out=0, updated_at=excluded.updated_at, source='provided'", (bid, channel, self._iso(now)))
        self.ledger._set_state(c, bid, stopped=0)
        record_event(c, f"EV-OPTIN-{bid}-{channel}-{self._iso(now)}", bid, "consent_changed", self._iso(now),
                     channel=channel, details=f"opt-in recorded by a person, consent version {consent_version}")
        return {"channel": channel, "consent_version": consent_version}

    # ---------- manual scheduler ----------
    def _target(self, c, borrower_id: str, campaign_id: Optional[str], obligation_id: Optional[str]):
        self._borrower(c, borrower_id)
        q = ("SELECT o.obligation_id, e.campaign_id FROM obligations o JOIN enrollments e ON e.loan_id=o.loan_id"
             " JOIN campaigns k ON k.id=e.campaign_id WHERE e.borrower_id=? AND o.amount_paid_paise<o.amount_due_paise"
             " AND (? IS NULL OR e.campaign_id=?) AND (? IS NULL OR o.obligation_id=?)"
             " ORDER BY o.due_date, (k.status='RUNNING') DESC, e.campaign_id LIMIT 1")
        return c.execute(q, (borrower_id, campaign_id, campaign_id, obligation_id, obligation_id)).fetchone()

    def preview_manual(self, borrower_id: str, channel: str, template_id: str, at: datetime, role: str,
                       est_cost_paise: int = 0, campaign_id: Optional[str] = None,
                       obligation_id: Optional[str] = None, purpose: Optional[str] = None) -> dict:
        """Live preview for the manual scheduler: rendered template (masked for the role), the Gate's
        verdict right now, whether it would need approval, and whether it would be a duplicate. No reservation."""
        self._require_view(role)
        c = self.ledger._conn()
        tgt = self._target(c, borrower_id, campaign_id, obligation_id)
        if tgt is None:
            return {"borrower_id": borrower_id, "allowed": False, "reason": "NO_OPEN_EMI_IN_CAMPAIGN"}
        ins = Instruction(borrower_id, tgt["obligation_id"], tgt["campaign_id"], channel, template_id, at,
                          int(est_cost_paise), purpose=purpose, instruction_id="PREVIEW", origin="manual")
        d = self.ledger.preview(ins)
        with self.ledger.tx() as c2:
            rules = self.approval_rules_for(c2, ins, d.detail.get("dpd"))
            dup = self._manual_duplicate(c2, ins)
            held = bool(self._active_holds(c2, borrower_id))
        allowed = d.allowed and not dup and not held
        reason = d.reason if not d.allowed else ("SUPERVISOR_HOLD" if held else ("MANUAL_DUPLICATE" if dup else "ELIGIBLE"))
        return {"borrower_id": borrower_id, "obligation_id": tgt["obligation_id"], "campaign_id": tgt["campaign_id"],
                "channel": channel, "template_id": template_id, "at": self._iso(self.ledger._loc(at)),
                "allowed": allowed, "reason": reason, "gate": d.as_dict(), "duplicate_of": dup,
                "would_need_approval": rules if allowed else [],
                "locked_by_guardrails": (not d.allowed) and d.reason in self.ap["locked_by_guardrails"]["gate_reasons_refused_for_humans"],
                "preview": self.render(template_id, borrower_id, tgt["obligation_id"], role)}

    def render(self, template_id: Optional[str], borrower_id: str, obligation_id: Optional[str], role: str) -> dict:
        c = self.ledger._conn()
        t = c.execute("SELECT * FROM templates WHERE id=?", (template_id,)).fetchone()
        meta = c.execute("SELECT * FROM hitl_templates WHERE template_id=?", (template_id,)).fetchone()
        b = self._borrower(c, borrower_id)
        ob = c.execute("SELECT o.*, l.payment_link FROM obligations o JOIN loans l ON l.loan_id=o.loan_id"
                       " WHERE o.obligation_id=?", (obligation_id,)).fetchone() if obligation_id else None
        values = self.mask({"full_name": b["full_name"],
                            "amount_paise": (ob["amount_due_paise"] - ob["amount_paid_paise"]) if ob else None}, role)
        amount = values["amount_paise"]
        fill = _Template(name=values["full_name"], first_name=str(values["full_name"]).split()[0],
                         amount=(f"₹{amount / 100:,.2f}" if isinstance(amount, (int, float)) else amount),
                         due_date=ob["due_date"] if ob else "", loan_id=ob["loan_id"] if ob else "",
                         pay_link=(ob["payment_link"] if ob and ob["payment_link"] else "https://pay.example/SIMULATED"))
        body = meta["body"] if meta and meta["body"] else None
        return {"template_id": template_id, "registered": t is not None, "approved": bool(t and t["approved"]),
                "channel": t["channel"] if t else None, "lang": t["lang"] if t else None,
                "intensity": meta["intensity"] if meta else self.ap["approval_rules"]["unknown_template_intensity"],
                "rendered": ("[SIMULATED] " + body.format_map(fill)) if body else None}

    def schedule_manual(self, borrower_ids: Union[str, Iterable[str]], channel: str, template_id: str, at: datetime,
                        actor: str, role: str, reason: str, est_cost_paise: int, campaign_id: Optional[str] = None,
                        obligation_id: Optional[str] = None, purpose: Optional[str] = None, force: bool = False,
                        now: Optional[datetime] = None) -> dict:
        """Ad-hoc dispatch for one borrower or a list. Same Gate + atomic reserve as automated sends (so it
        respects caps and de-duplicates against them), then PENDING_APPROVAL per approval_policy.json."""
        now = self._now(now)
        ids = [borrower_ids] if isinstance(borrower_ids, str) else list(borrower_ids)
        if force:
            self._refuse(Refused("LOCKED_BY_GUARDRAILS", "'force' is locked by guardrails: a manual send passes every check"),
                         "force_send", actor, role, now, ids[0] if len(ids) == 1 else None)
        reason = self._reason(reason)
        self._authorize("schedule_manual", actor, role, now, ids[0] if len(ids) == 1 else None)
        if not ids or len(ids) > self.ap["manual_scheduler"]["max_borrowers_per_request"]:
            raise ValueError(f"1..{self.ap['manual_scheduler']['max_borrowers_per_request']} borrowers per request")
        with self.ledger.tx() as c:
            for bid in ids:  # all-or-nothing on unknown borrowers (404) before anything is reserved
                self._borrower(c, bid)
        results = [self._schedule_one(bid, channel, template_id, at, actor, role, reason, int(est_cost_paise),
                                      campaign_id, obligation_id, purpose, now) for bid in ids]
        count = {k: sum(r["outcome"] == k for r in results) for k in ("RESERVED", "PENDING_APPROVAL", "REFUSED")}
        return {"results": results, **{k.lower(): v for k, v in count.items()}}

    def _schedule_one(self, bid, channel, template_id, at, actor, role, reason, cost, campaign_id, obligation_id,
                      purpose, now, origin="manual", replaces: Optional[str] = None) -> dict:
        with self.ledger.tx() as c:
            tgt = self._target(c, bid, campaign_id, obligation_id)
        if tgt is None:
            return self._manual_refused(bid, None, channel, template_id, at, actor, role, reason, now,
                                        "NO_OPEN_EMI_IN_CAMPAIGN", "no unpaid EMI for this borrower in an enrolled campaign")
        iid = ("MAN-" if origin == "manual" else "RSC-") + uuid.uuid4().hex[:16]
        ins = Instruction(bid, tgt["obligation_id"], tgt["campaign_id"], channel, template_id, at, cost,
                          purpose=purpose, instruction_id=iid, origin=origin)
        res = self.ledger.reserve(ins, actor=self._actor(actor))
        d = res.decision
        if not res.reserved:
            return self._manual_refused(bid, iid, channel, template_id, at, actor, role, reason, now, d.reason,
                                        f"refused by {d.rule}: {d.reason}", gate=d.as_dict())
        status = self.ledger.touch(iid)["status"]
        with self.ledger.tx() as c:
            if origin == "manual":
                c.execute("INSERT INTO hitl_manual (instruction_id, borrower_id, scheduled_by, role, reason, created_at, replaces)"
                          " VALUES (?,?,?,?,?,?,?)", (iid, bid, self._actor(actor), role, reason, self._iso(now), replaces))
            if replaces:
                c.execute("UPDATE hitl_manual SET replaced_by=? WHERE instruction_id=?", (iid, replaces))
            self._audit(c, "SCHEDULE_RESCHEDULED" if replaces else "MANUAL_SCHEDULED", actor, role, bid, iid, reason, now,
                        channel=channel, template_id=template_id, scheduled_at=self._iso(self.ledger._loc(at)),
                        obligation_id=tgt["obligation_id"], campaign_id=tgt["campaign_id"], status=status,
                        approval_rules=d.detail.get("approval_rules", []), replaces=replaces, est_cost_paise=cost)
        return {"borrower_id": bid, "instruction_id": iid, "outcome": status, "obligation_id": tgt["obligation_id"],
                "campaign_id": tgt["campaign_id"], "approval_rules": d.detail.get("approval_rules", []),
                "approval_expires_at": d.detail.get("approval_expires_at")}

    def _manual_refused(self, bid, iid, channel, template_id, at, actor, role, reason, now, code, message, gate=None) -> dict:
        locked = code in self.ap["locked_by_guardrails"]["gate_reasons_refused_for_humans"]
        with self.ledger.tx() as c:
            self._audit(c, "HUMAN_ACTION_REFUSED", actor, role, bid, iid, reason, now, action="schedule_manual",
                        refusal=code, locked_by_guardrails=locked, channel=channel, template_id=template_id,
                        scheduled_at=self._iso(self.ledger._loc(at)))
        return {"borrower_id": bid, "instruction_id": iid, "outcome": "REFUSED", "reason": code, "message": message,
                "locked_by_guardrails": locked, "gate": gate}

    def cancel_scheduled(self, instruction_id: str, actor: str, role: str, reason: str,
                         now: Optional[datetime] = None) -> dict:
        """Cancel a not-yet-dispatched touch (manual or automated); its budget is released."""
        reason = self._reason(reason)
        now = self._now(now)
        self._authorize("cancel_scheduled", actor, role, now, instruction_id=instruction_id)
        with self.ledger.tx() as c:
            t = self._touch(c, instruction_id)
            if t["status"] not in QUEUED:
                raise Conflict(f"NOT_CANCELABLE_{t['status']}", f"{instruction_id} is {t['status']}; only queued sends can be canceled")
            self._cancel_touch(c, t, "CANCELED_BY_HUMAN", now)
            c.execute("UPDATE hitl_approvals SET decision='CANCELED', decided_by=?, decided_role=?, decided_at=?,"
                      " decision_reason=? WHERE instruction_id=? AND decision IS NULL",
                      (self._actor(actor), role, self._iso(now), reason, instruction_id))
            self._audit(c, "SCHEDULE_CANCELED", actor, role, t["borrower_id"], instruction_id, reason, now,
                        previous_status=t["status"], released_paise=t["est_cost_paise"])
        return {"instruction_id": instruction_id, "status": "CANCELED", "previous_status": t["status"]}

    def reschedule(self, instruction_id: str, at: datetime, actor: str, role: str, reason: str,
                   now: Optional[datetime] = None) -> dict:
        """= cancel + a NEW reservation through the full Gate. If the new slot is refused, the original
        is restored (only if it still passes every guardrail)."""
        reason = self._reason(reason)
        now = self._now(now)
        self._authorize("reschedule", actor, role, now, instruction_id=instruction_id)
        with self.ledger.tx() as c:
            t = self._touch(c, instruction_id)
            if t["status"] not in QUEUED:
                raise Conflict(f"NOT_RESCHEDULABLE_{t['status']}", f"{instruction_id} is {t['status']}; only queued sends can move")
            manual = c.execute("SELECT 1 FROM hitl_manual WHERE instruction_id=?", (instruction_id,)).fetchone() is not None
            self._cancel_touch(c, t, "RESCHEDULED", now)
            c.execute("UPDATE hitl_approvals SET decision='RESCHEDULED', decided_by=?, decided_role=?, decided_at=?"
                      " WHERE instruction_id=? AND decision IS NULL", (self._actor(actor), role, self._iso(now), instruction_id))
        t = dict(t)
        res = self._schedule_one(t["borrower_id"], t["channel"], t["template_id"], at, actor, role, reason,
                                 t["est_cost_paise"], t["campaign_id"], t["obligation_id"], None, now,
                                 origin="manual" if manual else "auto", replaces=instruction_id)
        if res["outcome"] != "REFUSED":
            return {"instruction_id": instruction_id, "status": "CANCELED", "new": res}
        restored = self._restore(instruction_id, t["status"], now)
        return {"instruction_id": instruction_id, "status": t["status"] if restored else "CANCELED",
                "restored": restored, "new": res}

    def _restore(self, instruction_id: str, prior_status: str, now: datetime) -> bool:
        with self.ledger.tx() as c:
            t = self._touch(c, instruction_id)
            if t["status"] != "CANCELED" or t["reason"] != "RESCHEDULED":
                return False
            d = self._recheck(c, t, self.ledger._dt(t["scheduled_at"]), own_cost_reserved=False)
            if not d.allowed:
                return False
            c.execute("UPDATE touches SET status=?, reason=?, updated_at=? WHERE instruction_id=?",
                      (prior_status, "RESTORED", self._iso(now), instruction_id))
            self.ledger._commit_cost(c, t["campaign_id"], t["day"], t["est_cost_paise"])
            if prior_status == "PENDING_APPROVAL":
                c.execute("UPDATE hitl_approvals SET decision=NULL, decided_by=NULL, decided_role=NULL, decided_at=NULL"
                          " WHERE instruction_id=?", (instruction_id,))
            return True

    # ---------- case queue ----------
    def list_cases(self, role: str, queue: Optional[str] = None, status: str = "OPEN", borrower_id: Optional[str] = None,
                   now: Optional[datetime] = None) -> list[dict]:
        """Open (OPEN + CLAIMED) cases by default, earliest SLA first. status: OPEN | CLAIMED | RESOLVED | ALL."""
        self._require_view(role)
        now = self._now(now)
        q = ("SELECT t.*, h.assignee, h.claimed_at, h.resolution_code, h.resolved_by, h.resolved_at, h.hold_lifted,"
             " h.reopened_count FROM tickets t LEFT JOIN hitl_cases h ON h.ticket_id=t.ticket_id WHERE 1=1")
        args: list = []
        st = status.upper()
        if st == "OPEN":
            q += " AND t.status IN ('OPEN','CLAIMED')"
        elif st != "ALL":
            q += " AND t.status=?"
            args.append(st)
        if queue:
            q += " AND t.queue=?"
            args.append(queue)
        if borrower_id:
            q += " AND t.borrower_id=?"
            args.append(borrower_id)
        rows = self.ledger._conn().execute(q + " ORDER BY t.sla_due_at IS NULL, t.sla_due_at, t.created_at", args).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["evidence_ids"] = json.loads(d["evidence_ids"] or "[]")
            d["sla_breached"] = bool(d["sla_due_at"] and d["status"] in OPEN_CASE and self.ledger._dt(d["sla_due_at"]) < now)
            out.append(self.mask(d, role))
        return out

    def _case(self, c, ticket_id: str):
        t = c.execute("SELECT * FROM tickets WHERE ticket_id=?", (ticket_id,)).fetchone()
        if t is None:
            raise KeyError(ticket_id)
        c.execute("INSERT OR IGNORE INTO hitl_cases (ticket_id) VALUES (?)", (ticket_id,))
        return t

    def claim_case(self, ticket_id: str, actor: str, role: str, reason: str = "claimed",
                   now: Optional[datetime] = None) -> dict:
        reason = self._reason(reason)
        now = self._now(now)
        self._authorize("case_claim", actor, role, now)
        with self.ledger.tx() as c:
            t = self._case(c, ticket_id)
            if t["status"] != "OPEN":
                raise Conflict(f"NOT_CLAIMABLE_{t['status']}", f"{ticket_id} is {t['status']}")
            c.execute("UPDATE tickets SET status='CLAIMED' WHERE ticket_id=?", (ticket_id,))
            c.execute("UPDATE hitl_cases SET assignee=?, claimed_at=? WHERE ticket_id=?",
                      (self._actor(actor), self._iso(now), ticket_id))
            self._audit(c, "CASE_CLAIMED", actor, role, t["borrower_id"], None, reason, now, ticket_id=ticket_id,
                        queue=t["queue"], case_type=t["type"])
        return {"ticket_id": ticket_id, "status": "CLAIMED", "assignee": self._actor(actor)}

    def resolve_case(self, ticket_id: str, actor: str, role: str, reason: str, resolution_code: str,
                     lift_hold: bool = False, now: Optional[datetime] = None) -> dict:
        """Close a case with a resolution code. The matching hold (hardship, callback, handoff, K13
        routed-stop) is lifted ONLY when lift_hold=True and the role holds 'lift_hold'."""
        reason = self._reason(reason)
        now = self._now(now)
        if resolution_code not in self.ap["cases"]["resolution_codes"]:
            raise ValueError(f"unknown resolution_code '{resolution_code}' (one of {self.ap['cases']['resolution_codes']})")
        self._authorize("case_resolve", actor, role, now)
        if lift_hold:
            self._authorize("lift_hold", actor, role, now)
        with self.ledger.tx() as c:
            t = self._case(c, ticket_id)
            if t["status"] not in OPEN_CASE:
                raise Conflict(f"NOT_RESOLVABLE_{t['status']}", f"{ticket_id} is {t['status']}")
            lifted = self._lift_matching_hold(c, t, actor, now) if lift_hold else None
            c.execute("UPDATE tickets SET status='RESOLVED' WHERE ticket_id=?", (ticket_id,))
            c.execute("UPDATE hitl_cases SET resolution_code=?, resolved_by=?, resolved_at=? WHERE ticket_id=?",
                      (resolution_code, self._actor(actor), self._iso(now), ticket_id))
            self._audit(c, "CASE_RESOLVED", actor, role, t["borrower_id"], None, reason, now, ticket_id=ticket_id,
                        queue=t["queue"], case_type=t["type"], resolution_code=resolution_code,
                        lift_hold_requested=lift_hold, hold_lifted=lifted)
        return {"ticket_id": ticket_id, "status": "RESOLVED", "resolution_code": resolution_code, "hold_lifted": lifted}

    def _lift_matching_hold(self, c, t, actor: str, now: datetime) -> Optional[str]:
        bid, typ = t["borrower_id"], t["type"]
        if typ == "HARDSHIP":
            if not self.ledger._state(c, bid)["hardship_until"]:
                return None
            # end the hold now (kept as a timestamp so FIRST_SEND_AFTER_HARDSHIP still asks for approval)
            self.ledger._set_state(c, bid, hardship_until=self._iso(now), hardship_supervisor=0)
            return "HARDSHIP_HOLD"
        if typ in ("CALLBACK_REQUEST", "CALLBACK_DUE"):
            self.ledger._set_state(c, bid, callback_pending=0)
            return "CALLBACK_PENDING"
        if typ == "HUMAN_HANDOFF":
            n = self._release_holds(c, "ticket_id=?", (t["ticket_id"],), self._actor(actor), now, "case resolved")
            self._refresh_supervisor_flag(c, bid)
            return "HANDOFF" if n else None
        if typ in ROUTED_STOP:
            c.execute("UPDATE hitl_cases SET hold_lifted=1 WHERE ticket_id=?", (t["ticket_id"],))
            self._refresh_supervisor_flag(c, bid)
            return "SUPERVISOR_HOLD"
        return None

    def reopen_case(self, ticket_id: str, actor: str, role: str, reason: str, now: Optional[datetime] = None) -> dict:
        """Reopen a resolved case. Holds lifted at resolution are NOT re-applied automatically."""
        reason = self._reason(reason)
        now = self._now(now)
        self._authorize("case_reopen", actor, role, now)
        with self.ledger.tx() as c:
            t = self._case(c, ticket_id)
            if t["status"] != "RESOLVED":
                raise Conflict(f"NOT_REOPENABLE_{t['status']}", f"{ticket_id} is {t['status']}")
            c.execute("UPDATE tickets SET status='OPEN' WHERE ticket_id=?", (ticket_id,))
            c.execute("UPDATE hitl_cases SET reopened_count=reopened_count+1, assignee=NULL, claimed_at=NULL WHERE ticket_id=?",
                      (ticket_id,))
            self._audit(c, "CASE_REOPENED", actor, role, t["borrower_id"], None, reason, now, ticket_id=ticket_id,
                        queue=t["queue"], case_type=t["type"])
        return {"ticket_id": ticket_id, "status": "OPEN"}

    # ---------- templates (preview + intensity for approval rules) ----------
    def register_template(self, template_id: str, intensity: str, body: Optional[str], actor: str, role: str,
                          reason: str, now: Optional[datetime] = None) -> dict:
        reason = self._reason(reason)
        now = self._now(now)
        if intensity not in ("LOW", "STANDARD"):
            raise ValueError("intensity must be LOW or STANDARD")
        self._authorize("register_template", actor, role, now)
        with self.ledger.tx() as c:
            c.execute("INSERT INTO hitl_templates VALUES (?,?,?) ON CONFLICT(template_id) DO UPDATE SET"
                      " intensity=excluded.intensity, body=excluded.body", (template_id, intensity, body))
            self._audit(c, "TEMPLATE_META_SET", actor, role, None, None, reason, now, template_id=template_id,
                        intensity=intensity)
        return {"template_id": template_id, "intensity": intensity}

    # ---------- audit ----------
    def audit(self, role: str, borrower_id: Optional[str] = None, actor: Optional[str] = None,
              types: Optional[Iterable[str]] = None, instruction_id: Optional[str] = None,
              since: Optional[datetime] = None, until: Optional[datetime] = None, human_only: bool = False,
              limit: int = 200) -> list[dict]:
        """Query the durable audit store (the K06 audit table), newest first, masked for the role."""
        self._require_view(role)
        q, args = "SELECT * FROM audit WHERE 1=1", []
        for col, val in (("borrower_id", borrower_id), ("instruction_id", instruction_id)):
            if val:
                q += f" AND {col}=?"
                args.append(val)
        if actor:
            q += " AND actor=?"
            args.append(actor if actor.startswith(("human:", "system", "agent:", "tool:")) or actor in
                        ("supervisor", "executor", "provider", "borrower") else self._actor(actor))
        types = [t for t in (types or []) if t]
        if types:
            q += f" AND type IN ({','.join('?' * len(types))})"
            args.extend(types)
        if human_only:
            q += " AND actor LIKE 'human:%'"
        if since:
            q += " AND ts>=?"
            args.append(self._iso(since))
        if until:
            q += " AND ts<?"
            args.append(self._iso(until))
        rows = self.ledger._conn().execute(q + " ORDER BY id DESC LIMIT ?", (*args, int(limit))).fetchall()
        return [self.mask({**dict(r), "detail": json.loads(r["detail"] or "{}")}, role) for r in rows]

    # ---------- for the UI ----------
    def borrower_controls(self, borrower_id: str, role: str) -> dict:
        """What the CRM 'Borrower actions' card needs: holds, assignment, DNC, and what this role may do."""
        self._require_view(role)
        c = self.ledger._conn()
        self._borrower(c, borrower_id)
        st = self.ledger._state(c, borrower_id)
        a = c.execute("SELECT agent, assigned_by, assigned_at FROM hitl_assignments WHERE borrower_id=?", (borrower_id,)).fetchone()
        dnc = c.execute("SELECT * FROM hitl_dnc WHERE borrower_id=?", (borrower_id,)).fetchone()
        return {"borrower_id": borrower_id, "holds": self._active_holds(c, borrower_id),
                "supervisor_hold": bool(st["supervisor_hold"]), "stopped": bool(st["stopped"]),
                "dnc": dict(dnc) if dnc else None, "assignment": dict(a) if a else None,
                "allowed_actions": sorted(x for x in BORROWER_ACTIONS if self.permitted(x, role)),
                "locked_by_guardrails": self.ap["locked_by_guardrails"]["actions"]}
