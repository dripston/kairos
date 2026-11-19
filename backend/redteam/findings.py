"""Known findings: guardrail gaps the suite found on main. Each is referenced by the attack(s)
that demonstrate it (Attack.finding). Those attacks are expected to FAIL until the fix lands;
pytest marks them xfail(strict=True), so a fix turns them into XPASS and forces the marker
(and this entry) to be removed. Do not edit an attack to make a finding go away."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Dict, List


@dataclass(frozen=True)
class Finding:
    id: str
    title: str
    severity: str        # HIGH | MEDIUM | LOW
    owner: str
    where: str
    fix: str

    def as_dict(self) -> dict:
        return asdict(self)


# Open findings go here. All ten from the first run are fixed (HIGH on redteam/fix-high, MEDIUM on
# redteam/fix-med); see redteam/README.md "Findings" for what each was and how it was fixed.
_F: List[Finding] = [
]

FINDINGS: Dict[str, Finding] = {f.id: f for f in _F}
