"""Fresh synthetic worlds for attacks: a K03 spine + K06 ledger in a private SQLite file.

Synthetic data only: names are "Synthetic Borrower <id>", phones are +91 0xxxxxxxxx (Indian mobile
numbers never start with 0, so nothing can reach a real person — same rule as K03's generator).
"""
from __future__ import annotations

import importlib
import os
import sqlite3
from dataclasses import replace
from datetime import date, datetime, timedelta
from typing import Callable, Dict, Iterable, Optional

from kairos_backend.executor import Executor, FakeProvider, Supervisor
from kairos_backend.ledger import Instruction, Ledger
from kairos_backend.policy import Policy, load_policy, policy_dir

from .core import Context

D = date(2026, 11, 20)        # Friday — a contact day
SUNDAY = date(2026, 11, 22)
HOLIDAY = date(2027, 1, 26)   # Republic Day, in policy.json national_holidays (a Tuesday)

TEMPLATES = {"SMS_EN": ("sms", "en"), "WA_HI": ("whatsapp", "hi"), "EM_EN": ("email", "en"),
             "IVR_HI": ("ivr", "hi"), "VB_EN": ("voicebot", "en")}
CH_TPL = {ch: tid for tid, (ch, _) in TEMPLATES.items()}
ALL_CONSENT = {"sms": 1, "whatsapp": 1, "email": 1, "ivr": 1, "voicebot": 1}
CHANNELS = tuple(ALL_CONSENT)


def at(hh: int, mm: int = 0, day: date = D) -> datetime:
    """Naive datetimes are local IST, exactly as the ledger treats them."""
    return datetime(day.year, day.month, day.day, hh, mm)


def redteam_policy() -> Policy:
    """K01 + K13 with the example lender registered as 'EXAMPLE' (as the K01 reference check does)."""
    p = load_policy(lender_files=[policy_dir() / "K01-decision-policy" / "lender_caps.example.json"])
    p.lenders["EXAMPLE"] = p.lenders.pop("LENDER-EXAMPLE")
    return p


def with_official_caps(policy: Policy, caps: dict) -> Policy:
    return replace(policy, official_caps=caps)


def with_lender(policy: Policy, lender_id: str, caps: dict) -> Policy:
    return replace(policy, lenders={**policy.lenders, lender_id: caps})


class World:
    """One K06 ledger on a fresh spine. Campaigns C1 and C2 run D..D+10."""

    def __init__(self, ctx: Context, name: str = "world", policy: Optional[Policy] = None,
                 campaigns: Iterable[str] = ("C1", "C2"), start: date = D, days: int = 10,
                 budget_per_case_paise: int = 10_000):
        self.ctx = ctx
        self.policy = policy or ctx.policy
        self.ledger = Ledger(self.policy, ctx.path(f"{name}.db"))
        ctx.on_close(self.ledger.close)
        for tid, (ch, lang) in TEMPLATES.items():
            self.ledger.upsert_template(tid, ch, lang)
        for cid in campaigns:
            self.ledger.upsert_campaign(cid, budget_per_case_paise=budget_per_case_paise, start_date=start,
                                        end_date=start + timedelta(days=days))
        self.ledger.start_day(start)  # daily LMS refresh -> DPD is fresh on `start`
        self.supervisor = Supervisor(self.ledger)

    # ---------- synthetic K03 data ----------
    def phone(self) -> str:
        return "+910" + "".join(str(self.ctx.rng.randrange(10)) for _ in range(9))

    def borrower(self, bid: str, loan: str = "A", dpd: int = 12, emi_paise: int = 500_000,
                 consents: Optional[Dict[str, int]] = None, as_of: date = D, installments: int = 1,
                 enroll: Iterable[str] = ("C1",), name: Optional[str] = None) -> str:
        """Borrower + loan with `installments` monthly EMIs; EMI 1 is `dpd` days past due on `as_of`.
        Returns the loan id. Obligation ids are <loan>-<n>."""
        conn = self.ledger._spine()
        loan_id = f"{bid}-{loan}"
        with conn:
            conn.execute("INSERT OR IGNORE INTO borrowers (borrower_id, full_name, phone, language) VALUES (?,?,?, 'en')",
                         (bid, name or f"Synthetic Borrower {bid}", self.phone()))
            for ch, ok in (consents if consents is not None else ALL_CONSENT).items():
                conn.execute("INSERT OR IGNORE INTO consents (borrower_id, channel, consented, opted_out, updated_at)"
                             " VALUES (?,?,?,0,?)", (bid, ch, ok, as_of.isoformat()))
            conn.execute("INSERT INTO loans (loan_id, borrower_id, product, principal_paise) VALUES (?,?,'personal_loan',?)",
                         (loan_id, bid, emi_paise * 12))
            first_due = as_of - timedelta(days=dpd)
            for n in range(1, installments + 1):
                conn.execute("INSERT INTO obligations (obligation_id, loan_id, installment_no, due_date, amount_due_paise)"
                             " VALUES (?,?,?,?,?)", (f"{loan_id}-{n}", loan_id, n,
                                                     (first_due + timedelta(days=30 * (n - 1))).isoformat(), emi_paise))
        for cid in enroll:
            self.ledger.enroll(cid, [loan_id])
        return loan_id

    def identity(self, bid: str) -> dict:
        r = self.ledger._conn().execute("SELECT full_name, phone FROM borrowers WHERE borrower_id=?", (bid,)).fetchone()
        return dict(r)

    # ---------- instructions ----------
    def ins(self, bid: str = "B1", ch: str = "sms", t: Optional[datetime] = None, loan: str = "A", n: int = 1,
            camp: str = "C1", cost: int = 20, tpl: Optional[str] = None, iid: Optional[str] = None,
            purpose: Optional[str] = None) -> Instruction:
        return Instruction(bid, f"{bid}-{loan}-{n}", camp, ch, CH_TPL.get(ch) if tpl is None else tpl,
                           t or at(10), cost, purpose=purpose, instruction_id=iid)

    def executor(self, script: Optional[Callable[[dict], str]] = None, cost_paise: Optional[int] = None,
                 providers: Optional[dict] = None) -> Executor:
        provs = providers or {ch: FakeProvider(ch, cost_paise=cost_paise, script=script) for ch in CHANNELS}
        return Executor(self.ledger, provs)

    def touches(self, bid: Optional[str] = None) -> list:
        q, args = "SELECT * FROM touches", ()
        if bid:
            q, args = q + " WHERE borrower_id=?", (bid,)
        return [dict(r) for r in self.ledger._conn().execute(q + " ORDER BY scheduled_at", args)]

    def live_touches(self, bid: Optional[str] = None) -> list:
        from kairos_backend.ledger import LIVE
        return [t for t in self.touches(bid) if t["status"] in LIVE]

    def snapshot(self) -> dict:
        """Protected state that borrower-supplied text must never change."""
        c = self.ledger._conn()
        q = lambda sql: [tuple(r) for r in c.execute(sql)]  # noqa: E731
        return {
            "obligations": q("SELECT obligation_id, amount_due_paise, amount_paid_paise, due_date FROM obligations ORDER BY 1"),
            "payments": q("SELECT payment_id, obligation_id, amount_paise, reference, reversed_on FROM payments ORDER BY 1"),
            "consents": q("SELECT borrower_id, channel, consented, opted_out FROM consents ORDER BY 1, 2"),
            "holds": q("SELECT borrower_id, stopped, hardship_until, callback_pending, supervisor_hold, ptp_date,"
                       " broken_ptp, lender_id FROM borrower_state ORDER BY 1"),
            "campaigns": q("SELECT id, status, committed_paise, collected_paise, cured_count FROM campaigns ORDER BY 1"),
            "templates": q("SELECT * FROM templates ORDER BY 1"),
            "settings": q("SELECT * FROM settings WHERE key <> 'spine_synced_at' ORDER BY 1"),
            "tables": q("SELECT name FROM sqlite_master WHERE type='table' ORDER BY 1"),
        }


class ApiWorld(World):
    """The real FastAPI app (kairos_backend.api) bound to a private DB, with a TestClient that
    turns server exceptions into HTTP 500 instead of raising — exactly what a caller would see."""

    def __init__(self, ctx: Context, name: str = "api"):
        from fastapi.testclient import TestClient

        db = ctx.path(f"{name}.db")
        prev = os.environ.get("KAIROS_DB")
        os.environ["KAIROS_DB"] = str(db)
        try:
            import kairos_backend.api as api_mod
            self.api = importlib.reload(api_mod)
        finally:
            if prev is None:
                os.environ.pop("KAIROS_DB", None)
            else:
                os.environ["KAIROS_DB"] = prev
        self.ctx = ctx
        self.policy = self.api.policy
        self.ledger = self.api.ledger
        ctx.on_close(self.ledger.close)
        self.client = TestClient(self.api.app, raise_server_exceptions=False)
        for tid, (ch, lang) in TEMPLATES.items():
            self.ledger.upsert_template(tid, ch, lang)
        for cid in ("C1", "C2"):
            self.ledger.upsert_campaign(cid, budget_per_case_paise=10_000, start_date=D, end_date=D + timedelta(days=10))
        self.ledger.start_day(D)
        self.supervisor = self.api.supervisor

    def call(self, method: str, url: str, **kw) -> dict:
        r = self.client.request(method, url, **kw)
        try:
            body = r.json()
        except ValueError:
            body = r.text[:200]
        return {"route": f"{method} {url.split('?')[0]}", "status": r.status_code, "body": body}

    def decide_body(self, bid: str = "B1", ch: str = "sms", t: str = "2026-11-20T10:00:00", **kw) -> dict:
        return {"borrower_id": bid, "obligation_id": f"{bid}-A-1", "campaign_id": "C1", "channel": ch,
                "template_id": CH_TPL[ch], "at": t, "est_cost_paise": 20, **kw}


def table_rows(ledger: Ledger, table: str) -> list:
    try:
        return [dict(r) for r in ledger._conn().execute(f"SELECT * FROM {table}")]
    except sqlite3.Error:
        return []
