"""Run every case of Ojas's policy pack against OUR implementation and hold the result to the recorded
matrix (expected_status.json, the same matrix POLICY_COVERAGE.md shows).

A ratchet: a case that PASSes must keep passing; a case recorded as FAIL / NOT_IMPLEMENTED must not
silently change either - when you fix one, re-run `python scripts/policy_coverage.py --write-snapshot`
and commit the new matrix with the fix. Report-backed K15 cases (50k benchmark, K09 lift, CM-03 eval
bars) need full-scale runs and are checked by scripts/policy_coverage.py, not here.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.policy_conformance.adapters import MODULES, run_all

SNAPSHOT = Path(__file__).with_name("expected_status.json")
EXPECTED = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
RESULTS = run_all()
CASES = [(m, r) for m, rows in RESULTS.items() for r in rows if r.get("source", "live") == "live"]


def test_every_module_is_covered():
    assert set(RESULTS) == set(MODULES) == set(EXPECTED)
    counts = {m: len(rows) for m, rows in RESULTS.items()}
    assert counts["K01-decision-policy"] == 38 and counts["K13-operational-exceptions"] == 25
    assert counts["K10-inbound-conversation"] == 33 and counts["K21-propensity-intensity"] == 20
    assert counts["K15-safety-load-replay"] == 12 and counts["T01-outbound-templates"] == 300
    assert counts["K24-portfolio-intake"] == 32


@pytest.mark.parametrize("module,row", CASES, ids=[f"{m.split('-')[0]}:{r['case_id']}" for m, r in CASES])
def test_case_matches_recorded_matrix(module, row):
    want = EXPECTED[module].get(row["case_id"])
    assert want is not None, f"{row['case_id']} is not in expected_status.json; re-run scripts/policy_coverage.py"
    assert row["status"] == want, f"{row['case_id']}: {row['status']} (recorded {want}) - {row['detail']}"
