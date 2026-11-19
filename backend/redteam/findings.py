"""Known findings: guardrail gaps the suite found on main. Each is referenced by the attack(s)
that demonstrate it (Attack.finding). Those attacks are expected to FAIL until the fix lands;
pytest marks them xfail(strict=True), so a fix turns them into XPASS and forces the marker
(and this entry) to be removed. Do not edit an attack to make a finding go away."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Dict


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


_F = [
    Finding("RT-F07", "Instruction-id replay never compares the payload: a reused id for another borrower or an "
            "unknown campaign is answered SEND / reserved=true", "MEDIUM", "Rehan (K06)",
            "ledger.reserve (prior-row short-circuit)",
            "On a prior row, compare borrower_id, obligation_id, channel, campaign_id and scheduled_at; on mismatch "
            "return WAIT IDEMPOTENCY_CONFLICT (409 at the API) with reserved=false, and run the campaign "
            "existence check before the replay short-circuit."),
    Finding("RT-F09", "Free text is persisted unredacted into audit.reason and K03 events/payments",
            "MEDIUM", "Rehan (K06) for audit + record_response; Varun (K03) for reverse_payment",
            "ledger._audit (reason from reversal/pause), ledger.record_response (events.details), "
            "spine.ledger.reverse_payment (payments.reversal_reason, events.details)",
            "Apply the same redact() to free-text reasons before _audit and before writing events.details / "
            "payments.reversal_reason; store raw borrower text only in a dedicated, access-controlled store if needed."),
    Finding("RT-F10", "POST /spine/import opens any server-side path the caller names",
            "MEDIUM", "Rehan (K06)", "api.py POST /spine/import",
            "Resolve csv_path and require it to sit under an allowed data directory (e.g. backend/data); return 403 "
            "otherwise; never echo rejected row values for files outside it."),
]

FINDINGS: Dict[str, Finding] = {f.id: f for f in _F}
