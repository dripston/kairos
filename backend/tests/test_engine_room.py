"""Engine room (/demo/engines, kairos_backend/engine_room.py) on the real full-mode run, SIMULATED.

The page's churn counts are folded from the feed's churn events and must equal the K27 engine's own state at every
day end; the learner.segments posteriors move with the evidence (and fall for WhatsApp after the day-7 drift); the
learner switches 1–30 days late from WhatsApp to SMS after the drift on seed 42; the feed is deterministic per seed;
no PII; the page loads nothing from outside; and learner.segments never changes a decision."""
from __future__ import annotations

import importlib
import json
import re
import time
from collections import defaultdict

import pytest
from fastapi.testclient import TestClient

from kairos_backend import engine_room as E
from replay import live_full as F

PHONE = re.compile(r"(?<![\w-])\d{10}(?!\w)|\+91")


@pytest.fixture(scope="module")
def feed42(tmp_path_factory):
    return E.build_feed(42, 300, 14, workdir=str(tmp_path_factory.mktemp("engines42")))


def _segments(feed) -> dict:
    return {e["d"]: e["x"] for e in feed["events"] if e["c"] == "learner" and e["k"] == "segments"}


def _cell(seg: dict, band: str, channel: str) -> dict:
    sg = next(s for s in seg["segments"] if s["band"] == band)
    return next(c for c in sg["channels"] if c["channel"] == channel)


# ---------- churn ----------
def test_churn_counts_equal_the_events(feed42):
    ev, truth = feed42["events"], feed42["truth"]
    assert [t["day"] for t in truth] == list(range(1, 15))
    for t in truth:
        folded = E.fold_buckets(ev, upto_day=t["day"])
        assert folded == {k: t[k] for k in ("cohort", "risk", "layer", "lifecycle")}, t["day"]
        for k in ("cohort", "risk", "layer", "lifecycle"):
            assert sum(folded[k].values()) == 300
    churn = [e for e in ev if e["c"] == "churn"]
    assert sum(1 for e in churn if e["k"] == "assigned") == 300
    moved = [e for e in churn if e["k"] == "moved"]
    assert len(moved) > 300 and {e["x"]["engine"] for e in moved} == {"cohort", "risk", "layer", "journey", "lifecycle"}
    # the day-0 assignments come before any move, and every move names a real rule and its inputs
    first_move = next(i for i, e in enumerate(ev) if e["c"] == "churn" and e["k"] == "moved")
    assert all(e["k"] == "assigned" for e in ev[:first_move] if e["c"] == "churn")
    rules = feed42["catalogue"]["rules"]
    assert all(e["x"].get("rule") for e in moved)
    tick_risk = [e for e in moved if e["x"]["trigger"] == "daily tick" and e["x"]["engine"] == "risk"]
    assert tick_risk and all(e["x"]["rule"].startswith("RISK.") and e["x"]["rule"] in rules for e in tick_risk)
    assert sum(1 for e in moved if e["x"].get("why")) > 0.9 * len(moved)
    exits = defaultdict(int)
    for e in moved:
        if e["x"]["engine"] == "lifecycle":
            exits[e["x"]["to"]] += 1
    assert exits["PAID_EXIT"] == truth[-1]["lifecycle"]["PAID_EXIT"]
    assert exits["STOPPED_OPT_OUT"] == truth[-1]["lifecycle"]["STOPPED_OPT_OUT"]


# ---------- learning loop ----------
def test_posteriors_move_with_the_evidence(feed42):
    segs = _segments(feed42)
    assert sorted(segs) == list(range(1, 15))
    moved = 0
    for d in range(2, 15):
        for band in F.SEGMENT_BANDS:
            prev = {(c[0], c[1]): c for c in next(s for s in segs[d - 1]["segments"] if s["band"] == band)["cells"]}
            for ch, slot, a, b, n, src in next(s for s in segs[d]["segments"] if s["band"] == band)["cells"]:
                _, _, a0, b0, n0, src0 = prev[(ch, slot)]
                if src != "segment" or src0 != "segment":
                    continue
                # one reward per finalized touch: alpha counts replies, beta counts no-replies; neither shrinks
                assert a >= a0 - 1e-9 and b >= b0 - 1e-9 and n >= n0
                m0, m = a0 / (a0 + b0), a / (a + b)
                if a > a0 and b == b0:
                    assert m > m0
                if b > b0 and a == a0:
                    assert m < m0
                moved += (a, b) != (a0, b0)
    assert moved >= 20
    for x in segs.values():
        for sg in x["segments"]:
            for c in sg["channels"]:
                assert 0 <= c["lo90"] <= c["mean"] <= c["hi90"] <= 1
                assert c["source"] in ("segment", "pooled", "prior") and c["n_obs"] >= 0
            assert sg["preferred"] in (None, *F.SEGMENT_CHANNELS, "email", "voicebot")
        r = x["rule"]
        assert r["decay"] == 0.7 and r["stop_penalty"] == 25.0 and r["value_floor_rel"] == 0.5
        assert r["value_floor_replies_per_inr"] >= 0
    # day 1: every arm is at its synthetic prior; WhatsApp evening (24 in 100) is the best value
    assert _cell(segs[1], "B1", "whatsapp")["source"] == "prior" and segs[1]["segments"][0]["preferred"] == "whatsapp"


def test_whatsapp_estimate_falls_after_the_drift(feed42):
    segs, drift = _segments(feed42), feed42["drift_day"]
    assert drift == 7
    assert any(e["c"] == "borrower" and e["k"] == "world_change" and e["d"] == drift for e in feed42["events"])
    for band in F.SEGMENT_BANDS:
        assert _cell(segs[14], band, "whatsapp")["mean"] < _cell(segs[drift], band, "whatsapp")["mean"], band


def test_switch_to_sms_after_the_drift_seed_42(feed42):
    segs, drift = _segments(feed42), feed42["drift_day"]
    sw = [e for e in feed42["events"] if e["c"] == "learner" and e["k"] == "switch" and e["x"].get("basis") == "posterior"]
    b1 = [e for e in sw if e["x"]["band"] == "B1"]
    assert b1, "no posterior switch for 1–30 days late"
    s = b1[0]
    assert (s["x"]["from_channel"], s["x"]["to_channel"]) == ("whatsapp", "sms") and s["x"]["after_drift"] is True
    assert drift < s["d"] <= 14
    # the switch agrees with the segments state: WhatsApp preferred the day before, SMS that day, and SMS now brings
    # more replies per rupee than WhatsApp
    seg_b1 = lambda d: next(x for x in segs[d]["segments"] if x["band"] == "B1")  # noqa: E731
    assert seg_b1(s["d"] - 1)["preferred"] == "whatsapp" and seg_b1(s["d"])["preferred"] == "sms"
    assert _cell(segs[s["d"]], "B1", "sms")["replies_per_inr"] > _cell(segs[s["d"]], "B1", "whatsapp")["replies_per_inr"]
    # the learner's own sends lean to SMS for that segment by the end (k09 lane, contact decisions)
    late = defaultdict(int)
    for e in feed42["events"]:
        if e["c"] == "learner" and e["k"] == "contact" and e["d"] >= 12 and e["x"].get("band") == "B1" \
                and e["x"].get("lane") == "k09":
            late[e["x"]["channel"]] += 1
    early = defaultdict(int)
    for e in feed42["events"]:
        if e["c"] == "learner" and e["k"] == "contact" and e["d"] <= 6 and e["x"].get("band") == "B1" \
                and e["x"].get("lane") == "k09":
            early[e["x"]["channel"]] += 1
    assert late["sms"] / sum(late.values()) > early["sms"] / sum(early.values())


def test_decisions_and_pacing(feed42):
    segs, ev = _segments(feed42), feed42["events"]
    for d, x in segs.items():
        p = x["pacing"]
        assert p is not None and p["pool_paise"] == 150000, d
        assert 0 <= p["day_committed_paise"] <= p["day_allowance_paise"], (d, p)     # K01 daily pacing holds
        spent = sum(e["x"]["cost_paise"] for e in ev if e["c"] == "executor" and e["d"] == d)
        assert spent <= p["day_allowance_paise"], (d, spent, p)
    assert all(e["x"]["cap_breaches"] == 0 for e in ev if e["c"] == "reports" and e["k"] == "day_end")
    n_contact = sum(1 for e in ev if e["c"] == "learner" and e["k"] == "contact")
    n_wait = sum(1 for e in ev if e["c"] == "learner" and e["k"] == "wait")
    assert n_contact > 1000 and n_wait > 1000
    assert feed42["counters"]["sent"] == sum(1 for e in ev if e["c"] == "executor" and e["k"] == "dispatched")


# ---------- determinism, read-only, privacy, assets ----------
def test_deterministic_per_seed(tmp_path):
    for d in "abc":
        (tmp_path / d).mkdir()
    a = E.build_feed(7, 150, 8, workdir=str(tmp_path / "a"))
    b = E.build_feed(7, 150, 8, workdir=str(tmp_path / "b"))
    c = E.build_feed(8, 150, 8, workdir=str(tmp_path / "c"))
    dump = lambda f: json.dumps({k: f[k] for k in ("events", "truth", "final", "counters")}, sort_keys=True)  # noqa: E731
    assert dump(a) == dump(b)
    assert dump(a) != dump(c)


def test_segments_event_never_changes_a_decision(tmp_path, monkeypatch):
    def run(wd):
        c = F.FullCampaign(seed=11, borrowers=150, days=8, day_seconds=0, workdir=str(wd)).start()
        c.join(300)
        assert c.status == "finished", c.error
        return [(e["t"], e["component"], e["kind"], e["borrower_id"], e["summary"]) for e in c.events
                if e["kind"] != "segments"]
    (tmp_path / "with").mkdir()
    (tmp_path / "without").mkdir()
    with_seg = run(tmp_path / "with")
    monkeypatch.setattr(F.FullStackRun, "_segments_event", lambda self, at: None)
    without = run(tmp_path / "without")
    assert with_seg == without


def test_no_pii(feed42):
    blob = json.dumps(feed42, ensure_ascii=False)
    assert not PHONE.search(blob)
    assert not re.search(r"[\w.]+@[\w-]+\.[a-z]{2,}", blob)
    assert '"phone' not in blob.lower() and "full_name" not in blob and "surname" not in blob.lower()
    for e in feed42["events"]:
        if "n" in e:
            assert re.fullmatch(r"[A-Z][a-z]+", e["n"]), e["n"]
        if "b" in e:
            assert e["b"].startswith("BRW-")
        assert "text" not in (e.get("x") or {})       # borrower reply texts never reach the page
    page = E.PAGE.read_text(encoding="utf-8")
    assert "function masked(name, bid)" in page       # the page shows "First I." only


def test_page_loads_nothing_from_outside():
    page = E.PAGE.read_text(encoding="utf-8")
    assert not re.search(r"""(src|href)\s*=\s*["']?(https?:)?//""", page, re.I)
    assert "@import" not in page and "<link" not in page.lower() and "cdn" not in page.lower()
    urls = set(re.findall(r"https?://[^\s\"')]+", page))
    assert urls <= {"http://www.w3.org/2000/svg"}, urls        # the SVG namespace, not a fetch
    assert re.findall(r"""fetch\(\s*["']([^"']+)""", page) == ["/demo/engines/api/run?seed="]
    assert "prefers-reduced-motion" in page


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIROS_DB", str(tmp_path / "api.db"))
    import kairos_backend.api as api
    api = importlib.reload(api)
    return TestClient(api.app)


def test_api_page_and_run(client):
    r = client.get("/demo/engines")
    assert r.status_code == 200 and "Engine room" in r.text and "SIMULATED" in r.text
    params = {"seed": 5, "borrowers": 60, "days": 8}
    end = time.time() + 120
    while True:
        j = client.get("/demo/engines/api/run", params=params).json()
        if j["status"] != "computing" or time.time() > end:
            break
        time.sleep(0.1)
    assert j["status"] == "ready", j
    assert j["provenance"] == "SIMULATED" and j["seed"] == 5 and j["days"] == 8
    assert len(j["truth"]) == 8 and j["events"][0]["i"] == 1
    assert client.get("/demo/engines/api/run", params={"seed": 5, "borrowers": 10, "days": 8}).status_code == 422
