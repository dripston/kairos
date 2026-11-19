"""KAIROS_FSM_STRICT=1: SQLite triggers refuse undeclared touch transitions; off by default."""
from __future__ import annotations

import sqlite3

import pytest

from kairos_backend.executor import Executor, FakeProvider
from kairos_backend.fsm_strict import TRIGGERS
from kairos_backend.ledger import Instruction
from tests.fsm.world import add_borrower, at, new_ledger


def triggers(lg):
    return {r[0] for r in lg._conn().execute("SELECT name FROM sqlite_master WHERE type='trigger'")}


def test_off_by_default_and_turning_it_off_removes_the_triggers(policy, tmp_path, monkeypatch):
    monkeypatch.setenv("KAIROS_FSM_STRICT", "1")
    lg = new_ledger(policy, tmp_path / "s.db")
    assert set(TRIGGERS) <= triggers(lg)
    lg.close()
    monkeypatch.delenv("KAIROS_FSM_STRICT")
    lg = new_ledger(policy, tmp_path / "s.db")
    assert not set(TRIGGERS) & triggers(lg)
    lg.close()


def test_declared_flow_passes_and_undeclared_writes_abort(policy, tmp_path, monkeypatch):
    monkeypatch.setenv("KAIROS_FSM_STRICT", "1")
    lg = new_ledger(policy, tmp_path / "s.db")
    add_borrower(lg, "S1")
    ex = Executor(lg, {"sms": FakeProvider("sms", script=lambda t: "UNKNOWN_BUT_DELIVERED")})
    iid = lg.reserve(Instruction("S1", "S1-A-1", "C1", "sms", "SMS_EN", at(10), 20)).instruction_id
    assert ex.dispatch(iid, at(10)).outcome == "UNCERTAIN"
    assert ex.resolve_uncertain(iid) == "DELIVERED"
    with pytest.raises(sqlite3.IntegrityError, match="KAIROS_FSM_STRICT"):
        with lg.tx() as c:  # DELIVERED is final
            c.execute("UPDATE touches SET status='RESERVED' WHERE instruction_id=?", (iid,))
    with pytest.raises(sqlite3.IntegrityError, match="KAIROS_FSM_STRICT"):
        with lg.tx() as c:  # rows are born RESERVED (or PENDING_APPROVAL), never SENT
            c.execute("INSERT INTO touches (instruction_id, borrower_id, channel, scheduled_at, day, status)"
                      " VALUES ('X', 'S1', 'sms', '2026-11-20T10:00:00+05:30', '2026-11-20', 'SENT')")
    assert lg.touch(iid)["status"] == "DELIVERED"
    lg.close()


def test_strict_mode_surfaces_finding_f1(policy, tmp_path, monkeypatch):
    """FSM-F1 in strict mode: the provider callback on a never-dispatched touch is refused by the database."""
    monkeypatch.setenv("KAIROS_FSM_STRICT", "1")
    lg = new_ledger(policy, tmp_path / "s.db")
    add_borrower(lg, "S2")
    iid = lg.reserve(Instruction("S2", "S2-A-1", "C1", "sms", "SMS_EN", at(10), 20)).instruction_id
    with pytest.raises(sqlite3.IntegrityError, match="KAIROS_FSM_STRICT"):
        lg.record_provider_status(iid, "ACCEPTED")
    assert lg.touch(iid)["status"] == "RESERVED"
    lg.close()
