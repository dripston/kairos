"""Runs attacks in isolation and builds the red-team report (environment SIMULATED)."""
from __future__ import annotations

import importlib.util
import json
import platform
import shutil
import subprocess
import tempfile
import time
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .core import ERROR, FAIL, PASS, SKIP, Attack, Context
from .findings import FINDINGS

DEFAULT_SEED = 20261119
ENVIRONMENT = "SIMULATED"


@dataclass
class Outcome:
    attack: Attack
    status: str
    evidence: Dict[str, Any] = field(default_factory=dict)
    duration_ms: float = 0.0

    @property
    def known_finding(self) -> bool:
        return self.status == FAIL and self.attack.finding is not None

    def as_dict(self) -> dict:
        a = self.attack
        return {"id": a.id, "category": a.category, "component": a.component, "title": a.title,
                "covers": list(a.covers), "status": self.status, "finding": a.finding,
                "duration_ms": round(self.duration_ms, 1), "evidence": self.evidence}


def missing_requirements(a: Attack) -> List[str]:
    return [m for m in a.requires if importlib.util.find_spec(m) is None]


def execute(a: Attack, policy, seed: int = DEFAULT_SEED, workdir: Optional[Path] = None) -> Outcome:
    """Run one attack against a fresh private workdir. Exceptions become ERROR (never PASS)."""
    missing = missing_requirements(a)
    if missing:
        return Outcome(a, SKIP, {"missing": missing})
    own = workdir is None
    wd = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix=f"redteam-{a.id}-"))
    ctx = Context(policy=policy, seed=seed, workdir=wd, attack_id=a.id)
    t0 = time.perf_counter()
    try:
        r = a.run(ctx)
        status, evidence = (PASS if r.passed else FAIL), r.evidence
    except Exception as exc:  # an attack that crashes is a broken test, reported loudly
        status, evidence = ERROR, {"error": repr(exc), "traceback": traceback.format_exc(limit=8)}
    finally:
        ctx.close()
        if own:
            shutil.rmtree(wd, ignore_errors=True)
    return Outcome(a, status, evidence, (time.perf_counter() - t0) * 1000)


def run_all(attacks: Iterable[Attack], policy, seed: int = DEFAULT_SEED) -> List[Outcome]:
    return [execute(a, policy, seed) for a in attacks]


def _git_commit(cwd: Path) -> Optional[str]:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=cwd, capture_output=True, text=True, timeout=5)
        return out.stdout.strip() or None
    except Exception:
        return None


def build_report(outcomes: List[Outcome], policy, seed: int, started: datetime, finished: datetime,
                 run_id: Optional[str] = None) -> dict:
    counts = {s: sum(o.status == s for o in outcomes) for s in (PASS, FAIL, ERROR, SKIP)}
    unexpected = [o.attack.id for o in outcomes if o.status in (FAIL, ERROR) and not o.known_finding]
    fixed = [o.attack.id for o in outcomes if o.status == PASS and o.attack.finding]
    used = sorted({o.attack.finding for o in outcomes if o.attack.finding})
    return {
        "run_id": run_id or f"RT-{started.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:6]}",
        "environment": ENVIRONMENT,
        "synthetic_data_only": True,
        "seed": seed,
        "policy_version": policy.version,
        "exceptions_policy_version": policy.exceptions_version,
        "started_at": started.isoformat(timespec="seconds"),
        "finished_at": finished.isoformat(timespec="seconds"),
        "git_commit": _git_commit(Path(__file__).resolve().parent),
        "python": platform.python_version(),
        "totals": {"attacks": len(outcomes), **counts, "known_findings": sum(o.known_finding for o in outcomes),
                   "unexpected_failures": len(unexpected)},
        "unexpected_failures": unexpected,
        "findings_apparently_fixed": fixed,
        "findings": [{**FINDINGS[f].as_dict(), "attacks": [o.attack.id for o in outcomes if o.attack.finding == f],
                      "still_failing": any(o.attack.finding == f and o.status == FAIL for o in outcomes)}
                     for f in used if f in FINDINGS],
        "results": [o.as_dict() for o in outcomes],
    }


def table(outcomes: List[Outcome]) -> str:
    rows = [("ID", "CATEGORY", "RESULT", "FINDING", "TITLE")]
    for o in outcomes:
        res = o.status
        if o.status == PASS and o.attack.finding:
            res = "PASS*"  # finding appears fixed
        rows.append((o.attack.id, o.attack.category, res, o.attack.finding or "", o.attack.title))
    w = [max(len(r[i]) for r in rows) for i in range(4)]
    lines = ["  ".join(r[i].ljust(w[i]) for i in range(4)) + "  " + r[4] for r in rows]
    lines.insert(1, "-" * min(140, len(lines[0]) + 20))
    return "\n".join(lines)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def write_report(report: dict, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, default=str, ensure_ascii=False), encoding="utf-8")
    return path
