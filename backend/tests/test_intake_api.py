"""K24 upload preview (/intake/preview) and K28 viewer masking (/borrowers/{id})."""
from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

SAMPLE = Path(__file__).resolve().parents[2] / "policy" / "K24-portfolio-intake" / "sample_upload_v1.csv"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIROS_DB", str(tmp_path / "api.db"))
    import kairos_backend.api as api
    importlib.reload(api)
    return TestClient(api.app)


def body(**kw):
    b = {"csv_text": SAMPLE.read_text(encoding="utf-8-sig"), "upload_date": "2026-11-22",
         "start_date": "2026-11-22", "end_date": "2026-12-21", "budget_per_case_inr": 10}
    b.update(kw)
    return b


def test_preview_matches_ojas_worked_example(client):
    # policy/K24-portfolio-intake/README.md section 8
    r = client.post("/intake/preview", json=body())
    assert r.status_code == 200
    out = r.json()
    assert (out["rows_received"], out["accepted"]) == (14, 6)
    assert sum(out["quarantined_by_reason"].values()) == 8
    assert out["quarantined_by_reason"]["DUPLICATE_LOAN_ID"] == 2
    assert (out["eligible_loans"], out["budgeted_loans"], out["campaign_pool_inr"]) == (5, 5, 50.0)
    assert out["first_contact"] == "2026-11-23 08:00"
    assert out["contact_days"] == 25 and out["can_arm"] is True
    assert out["provenance"] == "SIMULATED"


def test_preview_without_budget_set_cannot_arm(client):
    out = client.post("/intake/preview", json=body(budget_all_eligible=False)).json()
    assert out["budgeted_loans"] == 0 and out["can_arm"] is False
    assert out["arm_blocked_reason"] == "BUDGET_SET_NOT_SPECIFIED"


def test_preview_echoes_no_file_values(client):
    text = SAMPLE.read_text(encoding="utf-8-sig")
    out = client.post("/intake/preview", json=body()).text
    for row in text.splitlines()[1:]:
        for cell in row.split(","):
            cell = cell.strip().strip('"')
            if len(cell) >= 10 and cell.isdigit():  # mobile numbers never come back
                assert cell not in out


@pytest.mark.parametrize("patch", [{"end_date": "2026-11-01"}, {"budget_per_case_inr": 0},
                                   {"csv_text": "Loan ID,Customer ID\n"}, {"unknown": 1}])
def test_preview_rejects_bad_input(client, patch):
    assert client.post("/intake/preview", json=body(**patch)).status_code == 422


@pytest.mark.parametrize("role,expected", [("viewer", "R. K."), ("operator", "Ravi Kumar"), ("policy_owner", "Ravi Kumar")])
def test_viewer_sees_initials_only(client, monkeypatch, role, expected):
    import kairos_backend.api as api
    monkeypatch.setattr(api.ledger, "borrower_state",
                        lambda bid, as_of=None: {"borrower_id": bid, "full_name": "Ravi Kumar", "phone": "+919000000001"})
    out = client.get("/borrowers/B-1", headers={"X-Kairos-Role": role}).json()
    assert out["full_name"] == expected
    assert (out["phone"] == "+919000000001") is (role == "policy_owner")
