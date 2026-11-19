"""GET /health (liveness) and GET /health/details (readiness). Models are off in tests (conftest)."""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIROS_DB", str(tmp_path / "api.db"))
    import kairos_backend.api as api
    importlib.reload(api)
    return TestClient(api.app)


def test_health_is_fast_and_stable(client):
    d = client.get("/health").json()
    assert d["ok"] is True and d["env"] == "SIMULATED" and isinstance(d["policy_version"], str)
    assert set(d) == {"ok", "policy_version", "env"}  # the UI header and host health checks rely on this shape


def test_health_details_reports_every_check(client):
    d = client.get("/health/details").json()
    assert d["ok"] is True and d["env"] == "SIMULATED"
    assert d["uptime_s"] >= 0 and d["started_at"]
    c = d["checks"]
    assert c["database"]["ok"] is True and set(c["database"]["rows"]) >= {"borrowers", "loans", "campaigns", "touches", "audit"}
    assert c["policy"]["version"]
    assert c["lifecycle"]["accepting"] is True
    assert c["models"]["configured"] is False and "live" not in c["models"]


def test_health_details_probe_without_models_makes_no_calls(client):
    m = client.get("/health/details?probe=true").json()["checks"]["models"]
    assert m["ok"] is True and "live" not in m


def test_health_details_never_contains_the_key(client, monkeypatch):
    fake = "s" + "k_" + "neverleak" * 3  # built at runtime so the repo secret scan stays clean
    monkeypatch.setenv("SARVAM_API_KEY", fake)
    assert fake not in client.get("/health/details").text


def test_explain_without_recorded_decisions_makes_no_model_call(client):
    d = client.get("/ai/explain?borrower_id=NOPE-1", headers={"X-Kairos-Role": "operator"}).json()
    assert d["outcome"] == "NO_FACTS" and d["ai_generated"] is False
