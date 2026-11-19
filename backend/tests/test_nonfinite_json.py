"""Regression (E2E QA, 2026-11-20): a decision dated before the spine sync stamp has a DPD age of inf.

Before the fix the Gate put {"dpd_age_hours": inf} in the decision detail: /preview, /decide and
/hitl/manual/preview answered 500, and the audit row written by /decide (or the manual preview) made every later
GET /audit, /events/since and /hitl/audit a 500 too, until the server restarted. On the deployed demo database
(spine_synced_at 2026-11-28, campaign from 2026-11-23) any date from 11-23 to 11-27 triggered it, including the
CRM page's Manual send preview.
"""
import importlib
import json
import math
import os
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from kairos_backend.gate import Facts, evaluate
from kairos_backend.safejson import dumps, finite
from spine.config import DATASET_AS_OF_DATE as AS_OF  # 2026-08-07, a Friday
from spine.synthetic_dataset import write_dataset

BID, OB = "BRW-SL100001", "SL100001-OD1"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIROS_DB", str(tmp_path / "api.db"))
    monkeypatch.setenv("KAIROS_IMPORT_DIR", str(tmp_path / "imports"))
    (tmp_path / "imports").mkdir()
    import kairos_backend.api as api
    api = importlib.reload(api)
    c = TestClient(api.app, raise_server_exceptions=False)
    write_dataset(os.path.join(os.environ["KAIROS_IMPORT_DIR"], "syn.csv"), 20, seed=42)
    assert c.post("/spine/import", json={"csv_path": "syn.csv"}).json()["loaded"] == 20
    c.put("/campaigns", json={"id": "C1", "budget_per_case_paise": 5_000, "start_date": str(AS_OF),
                              "end_date": "2026-08-20"})
    c.put("/templates", json={"id": "SMS_EN", "channel": "sms", "lang": "en"})
    assert c.post("/campaigns/C1/enroll", json={"loan_ids": ["SL100001"]}).json()["enrolled"] == 1
    # the demo-seed situation: the simulation clock already synced the spine at a later date than the decision
    api.ledger.mark_spine_synced(datetime(2026, 8, 12))
    return c, api


def _body(at="2026-08-10T10:00:00+05:30", **kw):
    return {"borrower_id": BID, "obligation_id": OB, "campaign_id": "C1", "channel": "sms", "template_id": "SMS_EN",
            "at": at, "est_cost_paise": 20, **kw}


def _no_bare_nonfinite(text: str) -> None:
    for word in ("Infinity", "NaN"):
        assert word not in text, text[:400]
    json.loads(text)  # strict JSON


def test_preview_and_decide_before_sync_stamp_are_200_with_stale_flag(client):
    c, _ = client
    for path, body in (("/preview", _body()), ("/decide", _body(instruction_id="QA-INF-1"))):
        r = c.post(path, json=body)
        assert r.status_code == 200, (path, r.status_code, r.text[:300])
        _no_bare_nonfinite(r.text)
        out = r.json()
        assert out["reason"] == "DPD_INVALID" and out["rule"] == "DPD_VALID"
        assert out["detail"]["dpd_age_hours"] is None and out["detail"]["stale"] is True
        assert out["detail"]["dpd_stamp_after_decision"] is True


def test_manual_preview_before_sync_stamp_is_200(client):
    c, _ = client
    r = c.post("/hitl/manual/preview", headers={"X-Kairos-Role": "operator"},
               json={"borrower_id": BID, "obligation_id": OB, "campaign_id": "C1", "channel": "sms",
                     "template_id": "SMS_EN", "at": "2026-08-10T15:00", "est_cost_paise": 20})
    assert r.status_code == 200, r.text[:300]
    _no_bare_nonfinite(r.text)


def test_audit_reads_stay_200_after_a_stale_decision(client):
    c, api = client
    assert c.post("/decide", json=_body(instruction_id="QA-INF-2")).status_code == 200
    c.post("/hitl/manual/preview", headers={"X-Kairos-Role": "operator"},
           json={"borrower_id": BID, "obligation_id": OB, "campaign_id": "C1", "channel": "sms",
                 "template_id": "SMS_EN", "at": "2026-08-10T15:00", "est_cost_paise": 20})
    # nothing non-finite reached the database
    stored = [r[0] for r in api.ledger._conn().execute("SELECT detail FROM audit")]
    assert stored and not any(w in d for d in stored for w in ("Infinity", "NaN"))
    for path in ("/audit?limit=50", f"/audit?limit=50&borrower_id={BID}", "/events/since?cursor=0&limit=500",
                 "/hitl/audit?limit=50"):
        r = c.get(path, headers={"X-Kairos-Role": "supervisor"})
        assert r.status_code == 200, (path, r.status_code, r.text[:300])
        _no_bare_nonfinite(r.text)


def test_a_legacy_infinity_row_in_the_audit_table_no_longer_breaks_reads(client):
    """A row written before this fix (detail '{"dpd_age_hours": Infinity}') reads back as null, not a 500."""
    c, api = client
    with api.ledger.tx() as con:
        con.execute("INSERT INTO audit (ts, actor, type, borrower_id, detail) VALUES (?,?,?,?,?)",
                    ("2026-08-10T10:00:00+05:30", "supervisor", "DECISION", BID, '{"dpd_age_hours": Infinity}'))
    for path in ("/audit?limit=5", "/events/since?cursor=0&limit=500", "/hitl/audit?limit=5"):
        r = c.get(path, headers={"X-Kairos-Role": "supervisor"})
        assert r.status_code == 200, (path, r.status_code, r.text[:300])
        _no_bare_nonfinite(r.text)


def test_gate_evaluate_refuses_non_finite_facts_with_422(client):
    c, _ = client
    facts = {"at": "2026-08-10T10:00:00+05:30", "channel": "sms", "dpd": 3}
    assert c.post("/gate/evaluate", json=facts).status_code == 200
    for field in ("dpd_age_hours", "est_cost_inr", "mins_since_last_same_channel"):
        for word in ("Infinity", "NaN", "-Infinity"):
            raw = json.dumps(facts)[:-1] + f', "{field}": {word}}}'
            r = c.post("/gate/evaluate", content=raw, headers={"Content-Type": "application/json"})
            assert r.status_code == 422, (field, word, r.status_code, r.text[:300])
            _no_bare_nonfinite(r.text)


def test_gate_treats_nan_and_inf_age_as_stale(policy):
    for age in (float("inf"), float("nan")):
        d = evaluate(policy, Facts(at=datetime(2026, 8, 10, 10, 0), channel="sms", dpd=3, dpd_age_hours=age))
        assert (d.decision, d.reason) == ("WAIT", "DPD_INVALID"), age
        assert d.detail == {"dpd_age_hours": None, "stale": True}
        _no_bare_nonfinite(json.dumps(d.as_dict()))


def test_response_serialiser_never_emits_infinity_or_nan(client):
    _, api = client
    content = {"a": float("inf"), "b": [float("-inf"), {"c": float("nan")}, (1.5, float("nan"))], "d": 2.0, "e": "NaN-free"}
    body = api.SafeJSONResponse(content).body.decode()
    assert json.loads(body) == {"a": None, "b": [None, {"c": None}, [1.5, None]], "d": 2.0, "e": "NaN-free"}
    assert "Infinity" not in body
    assert finite(content)["d"] == 2.0 and math.isnan(content["b"][1]["c"])  # input left untouched
    assert dumps({"x": float("inf")}) == '{"x": null}'
