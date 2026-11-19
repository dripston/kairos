"""Journey sessions shared by several browsers (E2E QA on the deployment, 20 Nov 2026).

At 04:12 IST a POST /demo/journey/outbound/api/step hung for minutes: the request had picked up the session just
before another client's /start replaced it (or /close closed it); its call was queued behind the old worker's stop
marker and waited the full 120 s worker timeout, then failed with a 500. Now a call on a closed or replaced session
is refused at once with 409 SESSION_REPLACED, a page that passes its session id never acts on someone else's
session, and an opt-in presenter lock keeps other clients from restarting a session during a talk.
"""
from __future__ import annotations

import threading
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kairos_backend import journey_inbound as JI
from kairos_backend import journey_outbound as JR
from replay import outbound_journey as J

SMALL = {"borrowers": 20, "days": 2}
API = "/demo/journey/outbound/api"


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(JR.router)
    old = JR.manager
    JR.manager = J.Manager()
    c = TestClient(app, raise_server_exceptions=False)
    yield c
    JR.manager.close(presenter=getattr(JR.manager, "_presenter", None) and JR.manager._presenter[0])
    JR.manager = old


def test_worker_refuses_calls_after_shutdown_instead_of_hanging():
    w = J._Worker()
    assert w.call(lambda: 41 + 1) == 42
    ran = []
    w.shutdown(lambda: ran.append("final"))
    assert ran == ["final"] and not w.t.is_alive()
    t = time.monotonic()
    with pytest.raises(J.SessionClosed):
        w.call(lambda: 1, timeout=60)
    assert time.monotonic() - t < 1.0
    w.shutdown()  # idempotent


def test_step_on_a_session_replaced_meanwhile_is_a_fast_409(client):
    """The deployment's hang: the step request holds the old session when another client's /start replaces it."""
    assert client.post(f"{API}/start", json=SMALL).status_code == 200
    orig, picked = JR.manager.get, threading.Event()

    def get_then_lose_the_race(*a, **k):
        s = orig(*a, **k)
        picked.set()
        time.sleep(0.5)  # the other client's /start lands here
        return s

    JR.manager.get = get_then_lose_the_race
    res = {}

    def step():
        t = time.monotonic()
        r = client.post(f"{API}/step")
        res.update(status=r.status_code, body=r.json(), secs=time.monotonic() - t)

    th = threading.Thread(target=step)
    th.start()
    assert picked.wait(30)
    JR.manager.get = orig
    assert client.post(f"{API}/start", json=SMALL).status_code == 200
    th.join(60)
    assert not th.is_alive(), "step still hanging"
    assert res["status"] == 409 and res["body"]["detail"]["code"] == "SESSION_REPLACED", res
    assert res["secs"] < 10, res["secs"]  # was 120 s + a 500


def test_step_racing_close_never_hangs(client):
    assert client.post(f"{API}/start", json=SMALL).status_code == 200
    res = []

    def step():
        t = time.monotonic()
        r = client.post(f"{API}/step")
        res.append((r.status_code, time.monotonic() - t))

    ths = [threading.Thread(target=step) for _ in range(3)]
    for th in ths:
        th.start()
    time.sleep(0.05)
    assert client.post(f"{API}/close").json() == {"closed": True}
    for th in ths:
        th.join(60)
    assert all(not th.is_alive() for th in ths)
    assert all(st in (200, 409) and secs < 30 for st, secs in res), res


def test_session_header_detects_a_replaced_session(client):
    a = client.post(f"{API}/start", json=SMALL).json()["session_id"]
    assert client.post(f"{API}/step", headers={"X-Journey-Session": a}).status_code == 200
    b = client.post(f"{API}/start", json=SMALL).json()["session_id"]
    assert a != b
    for path, body in ((f"{API}/step", None), (f"{API}/spotlight/hold", {"on": True}), (f"{API}/outbound", {"paused": True})):
        r = client.post(path, json=body, headers={"X-Journey-Session": a})
        assert r.status_code == 409 and r.json()["detail"]["code"] == "SESSION_REPLACED", (path, r.text)
    assert client.get(f"{API}/state", headers={"X-Journey-Session": a}).status_code == 409
    ok = client.post(f"{API}/step", headers={"X-Journey-Session": b})
    assert ok.status_code == 200 and ok.json()["session_id"] == b
    assert client.post(f"{API}/step").status_code == 200  # no header: unchanged behaviour (curl, old pages)
    assert client.post(f"{API}/step", headers={"X-Journey-Session": "not-hex!"}).status_code == 422


def test_opt_in_presenter_lock(client):
    assert client.post(f"{API}/start?presenter=talk-2026", json=SMALL).status_code == 200
    for path in ("start", "close"):
        r = client.post(f"{API}/{path}", json=SMALL if path == "start" else None)
        assert r.status_code == 409 and r.json()["detail"]["code"] == "PRESENTER_LOCK", r.text
        r = client.post(f"{API}/{path}?presenter=someone-else", json=SMALL if path == "start" else None)
        assert r.status_code == 409
    # the presenter (query or header) may restart; reads and steps stay open to everyone
    assert client.post(f"{API}/start", json=SMALL, headers={"X-Journey-Presenter": "talk-2026"}).status_code == 200
    assert client.post(f"{API}/step").status_code == 200 and client.get(f"{API}/state").status_code == 200
    assert client.post(f"{API}/close?presenter=talk-2026").json() == {"closed": True}  # releases the lock
    assert client.post(f"{API}/start", json=SMALL).status_code == 200                  # default behaviour again
    assert client.post(f"{API}/start?presenter=ab", json=SMALL).status_code == 422     # token too short


def test_presenter_lock_expires():
    now = [1000.0]
    m = J.Manager(clock=lambda: now[0])
    m._presenter = ("talk-2026", now[0] + J.Manager.PRESENTER_HOLD_S)
    assert m.presenter_locked() and not m.presenter_locked("talk-2026")
    now[0] += J.Manager.PRESENTER_HOLD_S + 1
    assert not m.presenter_locked()
    m.close()  # nothing running, lock expired: allowed


def test_inbound_request_on_a_world_reset_meanwhile_is_409_not_500():
    app = FastAPI()
    app.include_router(JI.router)
    c = TestClient(app, raise_server_exceptions=False)
    try:
        assert c.get("/demo/journey/inbound/state").status_code == 200
        orig, picked = JI.world, threading.Event()

        def world_then_lose_the_race():
            w = orig()
            picked.set()
            time.sleep(0.5)  # another presenter presses Reset demo here
            return w

        JI.world = world_then_lose_the_race
        res = {}
        th = threading.Thread(target=lambda: res.update(r=c.post("/demo/journey/inbound/send",
                                                                 json={"phone": "A", "scenario": "emi"})))
        th.start()
        assert picked.wait(30)
        JI.world = orig
        assert c.post("/demo/journey/inbound/reset").status_code == 200
        th.join(30)
        assert res["r"].status_code == 409 and res["r"].json()["detail"]["code"] == "WORLD_RESET", res["r"].text
        assert c.get("/demo/journey/inbound/state").status_code == 200  # the new world serves
    finally:
        JI.world = orig
        JI.reset_world().close()
        JI._world = None
