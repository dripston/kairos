"""Live collections view (/demo/live, replay/live.py): start / state / stop / control, determinism, counters
equal the replay's own report, zero cap breaches, SIMULATED labels and no PII in payloads."""
from __future__ import annotations

import importlib
import json
import re
import time

import pytest
from fastapi.testclient import TestClient

from replay.live import LiveCampaign, LiveManager, Busy, money

N, DAYS = 120, 5


def _run(seed=42, n=N, days=DAYS) -> dict:
    c = LiveCampaign(seed=seed, borrowers=n, days=days, day_seconds=0).start()
    c.join(120)
    assert c.status == "finished", c.error
    return c.state(0, 10_000)


@pytest.fixture(scope="module")
def run42():
    return _run()


def test_counters_equal_the_replay_report_and_no_breaches(run42):
    st = run42
    assert st["provenance"] == "SIMULATED" and "SIMULATED" in st["label"]
    for arm in ("kairos", "cn10"):
        c, r = st["counters"][arm], st["final_reports"][arm]
        assert c["spent_paise"] == r["spend_paise"]
        assert c["sent"] == r["contacts_sent"]
        assert c["replies"] == r["meaningful_replies"]
        assert c["verified_payments"] == r["verified_payments"]
        assert c["recovered_paise"] == r["recovered_paise"]
        assert c["stop_opt_outs"] == r["opt_outs"]
        assert c["cap_breaches"] == 0 and r["cap_breaches_k01"] == 0
        assert c["stages"]["due"] >= c["stages"]["contacted"] >= c["stages"]["replied"] >= c["stages"]["paid"]
    # the money recovered is the verified EMI amounts in the paid events
    for arm in ("kairos", "cn10"):
        paid = [e for e in st["events"] if e["arm"] == arm and e["kind"] == "paid"]
        assert sum(e["amount_paise"] for e in paid) == st["counters"][arm]["recovered_paise"]
    assert [s["day"] for s in st["series"]["kairos"]] == list(range(1, DAYS + 1))


def test_deterministic_given_the_seed(run42):
    again = _run()
    strip = lambda st: (st["counters"], [{k: v for k, v in e.items()} for e in st["events"]])  # noqa: E731
    assert strip(again) == strip(run42)
    other = _run(seed=7)
    assert other["counters"] != run42["counters"]


def test_event_kinds_and_plain_text(run42):
    kinds = {e["kind"] for e in run42["events"]}
    assert {"decision", "wait", "blocked", "sent", "delivered", "reply", "learner", "learning", "day_end"} <= kinds
    for e in run42["events"]:
        assert e["provenance"] == "SIMULATED"
        if e["kind"] in ("decision", "wait", "blocked", "reply", "paid", "stop", "promise", "learner", "switch"):
            assert e["text"] and "None" not in e["text"]
    blocked = [e for e in run42["events"] if e["kind"] == "blocked"]
    assert all(e["label"] for e in blocked)
    seqs = [e["seq"] for e in run42["events"]]
    assert seqs == list(range(1, len(seqs) + 1))


PHONE = re.compile(r"(?<!\d)\d{10}(?!\d)|\+91")


def test_no_pii_in_payloads(run42):
    blob = json.dumps(run42, ensure_ascii=False)
    assert not PHONE.search(blob)
    assert not re.search(r"[\w.]+@[\w-]+\.[a-z]{2,}", blob)                 # no e-mail addresses
    assert "Mobile" not in blob and "full_name" not in blob and "phone" not in blob.lower()
    for e in run42["events"]:
        if e.get("name"):
            assert " " not in e["name"]                      # synthetic first name only, never a surname
        if e.get("borrower_id"):
            assert e["borrower_id"].startswith("BRW-")


def test_money_format():
    assert money(420000) == "₹4,200" and money(1767230000) == "₹1,76,72,300" and money(40) == "₹0.40"


def test_one_run_at_a_time_and_stop():
    m = LiveManager()
    run = m.start(seed=1, borrowers=40, days=3, speed=0.25)
    with pytest.raises(Busy):
        m.start(seed=2, borrowers=40, days=3)
    out = m.stop(timeout=30)
    assert out["stopped"] and out["clean"] and run.status == "stopped"
    m.start(seed=2, borrowers=40, days=1, speed=64).join(60)   # a new run is allowed after a stop
    assert m.state()["status"] == "finished"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIROS_DB", str(tmp_path / "api.db"))
    import kairos_backend.api as api
    api = importlib.reload(api)
    return TestClient(api.app), api


def _wait(c, status, timeout=90):
    end = time.time() + timeout
    while time.time() < end:
        st = c.get("/demo/live/state").json()
        if st["status"] in status:
            return st
        time.sleep(0.1)
    raise AssertionError(f"timed out waiting for {status}: {st['status']}")


def test_api_start_state_control_stop(client):
    c, api = client
    assert c.get("/demo/live/state").json()["status"] == "idle"
    page = c.get("/demo/live")
    assert page.status_code == 200 and "SIMULATED" in page.text
    assert not re.search(r"""(src|href)=["']https?://""", page.text)        # no CDN, no external assets
    r = c.post("/demo/live/start", json={"seed": 3, "borrowers": 40, "days": 2, "speed": 64})
    assert r.status_code == 200 and r.json()["provenance"] == "SIMULATED"
    assert c.post("/demo/live/start", json={"seed": 3}).status_code in (200, 409)
    assert c.post("/demo/live/control", json={"speed": 64}).json()["speed"] == 64
    st = _wait(c, ("finished",))
    assert st["counters"]["kairos"]["cap_breaches"] == 0 and st["counters"]["cn10"]["cap_breaches"] == 0
    # cursor paging returns every event exactly once
    seen, cur = [], 0
    while True:
        page = c.get("/demo/live/state", params={"cursor": cur, "limit": 500}).json()
        if not page["events"]:
            break
        seen += [e["seq"] for e in page["events"]]
        cur = page["cursor"]
    assert seen == list(range(1, st["total_events"] + 1))
    assert c.post("/demo/live/start", json={"bogus": 1}).status_code == 422
    assert c.post("/demo/live/start", json={"speed": 1000}).status_code == 422

    c.post("/demo/live/start", json={"seed": 4, "borrowers": 40, "days": 3, "speed": 0.25})
    assert c.post("/demo/live/start", json={"seed": 5}).status_code == 409
    assert c.post("/demo/live/control", json={"paused": True}).json()["paused"] is True
    out = c.post("/demo/live/stop").json()
    assert out["stopped"] and out["status"] == "stopped"


def test_lifespan_shutdown_stops_a_live_run(client):
    c, api = client
    with TestClient(api.app) as cc:
        cc.post("/demo/live/start", json={"seed": 6, "borrowers": 40, "days": 3, "speed": 0.25})
        run = api.live_runs.current
    assert not run.running and run.status in ("stopped", "finished")
