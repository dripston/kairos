"""Guardrail red-team suite in pytest: one parametrized case per attack (backend/redteam).

Attacks that demonstrate a known finding are xfail(strict=True) with the finding id, so they stay
visible in every run and turn into a hard failure (XPASS) the moment the fix lands — at which point
remove `finding=` from the attack and the entry from redteam/findings.py.
"""
import importlib.util
import json
import os
from pathlib import Path

import pytest

from redteam import (ENVIRONMENT, FAIL, FINDINGS, PASS, SKIP, build_report, execute, load_attacks,
                     redteam_policy)
from redteam.runner import utcnow

SEED = int(os.environ.get("KAIROS_REDTEAM_SEED", "20261119"))
ATTACKS = load_attacks().all()


@pytest.fixture(scope="module")
def rt_policy():
    return redteam_policy()


def _param(a):
    marks = ()
    if a.finding:
        f = FINDINGS[a.finding]
        marks = pytest.mark.xfail(strict=True, reason=f"{a.finding} [{f.severity}] {f.title} (owner {f.owner})")
    return pytest.param(a, id=a.id, marks=marks)


@pytest.mark.parametrize("attack", [_param(a) for a in ATTACKS])
def test_attack(attack, rt_policy, tmp_path):
    out = execute(attack, rt_policy, SEED, tmp_path)
    if out.status == SKIP:
        pytest.skip(f"missing {out.evidence.get('missing')}")
    failed = [c for c in out.evidence.get("checks", []) if not c["ok"]]
    assert out.status == PASS, f"{attack.id} {out.status}: " + json.dumps(
        failed or out.evidence, default=str, ensure_ascii=True)[:3000]


def test_registry_is_well_formed():
    ids = [a.id for a in ATTACKS]
    assert len(ids) == len(set(ids)) and len(ids) >= 40
    for a in ATTACKS:
        assert a.id.startswith("RT-") and a.category and a.component and a.title and a.covers, a.id
        assert a.finding is None or a.finding in FINDINGS, f"{a.id} cites unknown finding {a.finding}"
    used = {a.finding for a in ATTACKS if a.finding}
    assert used == set(FINDINGS), f"findings without an attack: {set(FINDINGS) - used}"


def test_every_requested_category_is_covered():
    cats = {a.category for a in ATTACKS}
    assert {"caps", "consent", "time", "dpd", "payment", "templates", "budget", "uncertain", "injection",
            "pii", "api"} <= cats


def test_report_has_run_metadata(rt_policy):
    started = utcnow()
    outs = [execute(a, rt_policy, SEED) for a in load_attacks().select(ids=["RT-TIME-01", "RT-CAP-03"])]
    rep = build_report(outs, rt_policy, SEED, started, utcnow())
    assert rep["environment"] == ENVIRONMENT == "SIMULATED" and rep["synthetic_data_only"] is True
    assert rep["seed"] == SEED and rep["policy_version"] == rt_policy.version
    assert rep["run_id"].startswith("RT-") and rep["started_at"] <= rep["finished_at"]
    assert {r["id"]: r["status"] for r in rep["results"]} == {"RT-TIME-01": PASS, "RT-CAP-03": FAIL}
    assert rep["totals"]["known_findings"] == 1 and rep["unexpected_failures"] == []
    assert rep["findings"][0]["id"] == "RT-F07" and rep["findings"][0]["still_failing"] is True


def test_runs_are_deterministic(rt_policy):
    """Same seed -> same verdicts and same check-level evidence (ids and timings aside)."""
    pick = load_attacks().select(ids=["RT-INJ-01", "RT-CAP-01", "RT-PAY-04"])

    def verdicts():
        return [(o.attack.id, o.status, [(c["check"], c["ok"]) for c in o.evidence.get("checks", [])])
                for o in (execute(a, rt_policy, SEED) for a in pick)]

    assert verdicts() == verdicts()


def test_script_writes_report(tmp_path):
    spec = importlib.util.spec_from_file_location("run_redteam", Path(__file__).resolve().parents[1] / "scripts" / "run_redteam.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    main = mod.main

    path = tmp_path / "rt.json"
    assert main(["--id", "RT-CON-01", "--id", "RT-CAP-03", "--report", str(path)]) == 0  # CAP-03 is a known finding
    rep = json.loads(path.read_text(encoding="utf-8"))
    assert rep["environment"] == "SIMULATED" and rep["totals"]["attacks"] == 2
    assert main(["--id", "RT-CAP-03", "--strict", "--report", str(path)]) == 1
