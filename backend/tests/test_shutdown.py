"""Graceful shutdown (kairos_backend/lifecycle.py + the API lifespan): in-flight dispatches finish or become
UNCERTAIN (never re-sent), new work is refused, loops stop, a SHUTDOWN audit row is written, SQLite is
closed, and a restart works."""
from __future__ import annotations

import importlib
import json
import threading
import time
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from kairos_backend.executor import Executor, FakeProvider, ProviderResult
from kairos_backend.ledger import Instruction, Ledger
from kairos_backend.lifecycle import Lifecycle, ShuttingDown, TickLoop
from tests.spine_helpers import add_loan

D = date(2026, 11, 20)
AT = datetime(2026, 11, 20, 10)


class BlockingProvider:
    """Holds every send until released: an SMS gateway that is slow to answer."""

    def __init__(self):
        self.entered, self.release = threading.Event(), threading.Event()
        self.sends = []

    def send(self, touch):
        self.sends.append(touch["instruction_id"])
        self.entered.set()
        self.release.wait(5)
        return ProviderResult("ACCEPTED", provider_ref=f"p-{touch['instruction_id']}")

    def status(self, instruction_id, provider_ref=None):
        return "ACCEPTED" if instruction_id in self.sends else "NOT_FOUND"


@pytest.fixture
def world(policy, tmp_path):
    path = tmp_path / "shutdown.db"
    lg = Ledger(policy, path)
    lg.upsert_template("SMS_EN", "sms", "en")
    lg.upsert_campaign("C1", budget_per_case_paise=10_000, start_date=D, end_date=D + timedelta(days=5))
    ob = add_loan(lg, "B1", D, campaign="C1")
    lg.start_day(D)
    iid = lg.reserve(Instruction("B1", ob, "C1", "sms", "SMS_EN", AT, 20)).instruction_id
    yield lg, path, iid
    lg.close_all()


def audits(path, policy, type_="SHUTDOWN"):
    lg = Ledger(policy, path)
    rows = [{**r, "detail": r["detail"] if isinstance(r["detail"], dict) else json.loads(r["detail"] or "{}")}
            for r in lg.audit_log(limit=500) if r["type"] == type_]
    lg.close()
    return rows


def test_inflight_dispatch_past_grace_is_uncertain_and_never_resent(world, policy):
    lg, path, iid = world
    prov = BlockingProvider()
    ex = Executor(lg, {"sms": prov})
    lc = Lifecycle(lg, grace_s=0.2)
    lc.start()

    def work():
        with lc.dispatch_slot():
            ex.dispatch(iid, AT)

    t = threading.Thread(target=work)
    t.start()
    assert prov.entered.wait(2)
    res = lc.shutdown("SIGTERM")
    assert res["inflight_at_start"] == 1 and res["marked_uncertain"] == 1 and res["uncertain_ids"] == [iid]
    with pytest.raises(ShuttingDown):
        with lc.dispatch_slot():
            pass
    # restart: a new process / Ledger on the same file never sends the touch again
    lg2 = Ledger(policy, path)
    assert lg2.touch(iid)["status"] == "UNCERTAIN"
    again = Executor(lg2, {"sms": prov}).dispatch(iid, AT)
    assert again.outcome == "SKIPPED" and prov.sends == [iid]
    assert Executor(lg2, {"sms": prov}).resolve_uncertain(iid) in ("SENT", "DELIVERED")  # asks the provider
    lg2.close()
    prov.release.set()
    t.join(5)
    row = audits(path, policy)[0]
    detail = row["detail"]
    assert row["reason"] == "SIGTERM" and detail["marked_uncertain"] == 1 and detail["inflight_at_start"] == 1


def test_inflight_dispatch_inside_grace_finishes_normally(world, policy):
    lg, path, iid = world
    prov = BlockingProvider()
    ex = Executor(lg, {"sms": prov})
    lc = Lifecycle(lg, grace_s=5)

    def work():
        with lc.dispatch_slot():
            ex.dispatch(iid, AT)

    t = threading.Thread(target=work)
    t.start()
    assert prov.entered.wait(2)
    threading.Timer(0.1, prov.release.set).start()
    res = lc.shutdown()
    t.join(5)
    assert res["finished_in_grace"] == 1 and res["marked_uncertain"] == 0
    lg2 = Ledger(policy, path)
    assert lg2.touch(iid)["status"] == "SENT"
    lg2.close()


def test_shutdown_is_idempotent_and_restart_works(world, policy):
    lg, path, iid = world
    calls = []
    lc = Lifecycle(lg, grace_s=0)
    loop = lc.add_loop(TickLoop("tick", lambda: calls.append(1), 0.01))
    lc.start()
    time.sleep(0.05)
    first = lc.shutdown()
    assert lc.shutdown() is first and first["loops_stopped"] == {"tick": True}
    n = len(calls)
    time.sleep(0.05)
    assert len(calls) == n  # the loop really stopped
    lc.start()  # restart in the same process: connections reopen lazily, the loop runs again
    with lc.dispatch_slot():
        assert Executor(lg, {"sms": FakeProvider("sms")}).dispatch(iid, AT).outcome == "SENT"
    lc.shutdown("SIGINT")
    assert sorted(r["reason"] for r in audits(path, policy)) == ["SIGINT", "SIGTERM"]  # one row per shutdown
    assert loop.ticks >= 1


def test_api_lifespan_writes_shutdown_row_and_refuses_dispatch_while_down(tmp_path, monkeypatch, policy):
    db = tmp_path / "api.db"
    monkeypatch.setenv("KAIROS_DB", str(db))
    monkeypatch.setenv("KAIROS_SHUTDOWN_GRACE_S", "0.5")
    import kairos_backend.api as api
    api = importlib.reload(api)
    with TestClient(api.app) as c:
        assert c.post("/dispatch/NOPE").status_code == 404     # accepting: an unknown id is just a 404
    assert api.lifecycle.result["reason"] == "LIFESPAN_SHUTDOWN" and api.lifecycle.result["grace_s"] == 0.5
    c = TestClient(api.app)                                    # no lifespan: the app is shut down
    assert c.post("/dispatch/NOPE").status_code == 503
    assert c.post("/demo/journey/run", json={"seed": 1}).status_code == 503
    with TestClient(api.app) as c2:                            # restart: accepting again
        assert c2.post("/dispatch/NOPE").status_code == 404
    rows = audits(db, policy)
    assert len(rows) == 2 and all(r["detail"]["marked_uncertain"] == 0 for r in rows)


def test_journey_ctrl_c_exits_130_and_leaves_earlier_reports_untouched(tmp_path, monkeypatch, capsys):
    import importlib.util
    from replay import journey as J
    spec = importlib.util.spec_from_file_location("run_journey", J.__file__.replace("replay/journey.py",
                                                                                   "scripts/run_journey.py"))
    rj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rj)
    out = tmp_path / "journey"
    out.mkdir()
    (out / "summary.json").write_text('{"previous": true}')

    def boom(self, *a):
        raise KeyboardInterrupt

    monkeypatch.setattr(J.Journey, "step6_payment", boom)
    assert rj.main(["--quick", "--out", str(out)]) == 130
    assert "interrupted" in capsys.readouterr().out
    assert sorted(p.name for p in out.iterdir()) == ["summary.json"]          # nothing half-written
    assert (out / "summary.json").read_text() == '{"previous": true}'


def test_interruptible_and_atomic_write(tmp_path):
    from kairos_backend.safeio import atomic_write_text, interruptible

    def main():
        raise KeyboardInterrupt

    assert interruptible(main, lambda: "2 of 3 arms done.") == 130
    p = atomic_write_text(tmp_path / "r.json", "{}")
    assert p.read_text() == "{}" and [x.name for x in tmp_path.iterdir()] == ["r.json"]
