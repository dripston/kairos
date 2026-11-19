"""Supervisor + Executor.

Supervisor.decide(): turns an agent recommendation into a versioned instruction —
SEND (reserved), WAIT, CANCEL or HANDOFF, always with a reason and policy_version.

Executor: routes reserved instructions to channel adapters. It re-checks every
guardrail at send time, dispatches each instruction at most once, and never
blindly retries an uncertain send — it asks the provider first.

Real adapters (Meta WhatsApp K07, SMS gateway K08, Sarvam IVR K17) implement
the Provider protocol. FakeProvider is for tests and the SIMULATED demo.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional, Protocol

from .gate import Decision
from .ledger import Instruction, Ledger, ReserveResult


@dataclass
class ProviderResult:
    status: str               # ACCEPTED | REJECTED | UNKNOWN (timeout / no answer)
    provider_ref: Optional[str] = None
    cost_paise: Optional[int] = None
    error: Optional[str] = None


class Provider(Protocol):
    name: str

    def send(self, touch: dict) -> ProviderResult: ...

    def status(self, instruction_id: str, provider_ref: Optional[str]) -> str:
        """ACCEPTED | DELIVERED | NOT_FOUND | UNKNOWN"""
        ...


class FakeProvider:
    """Deterministic stand-in. `script` lets tests force REJECTED/UNKNOWN on chosen sends."""

    def __init__(self, name: str = "fake", cost_paise: Optional[int] = None,
                 script: Optional[Callable[[dict], str]] = None):
        self.name = name
        self.cost_paise = cost_paise
        self.script = script
        self.sent: list[str] = []
        self.known: dict[str, str] = {}  # instruction_id -> provider status, as the provider sees it
        self._ids = itertools.count(1)

    def send(self, touch: dict) -> ProviderResult:
        outcome = self.script(touch) if self.script else "ACCEPTED"
        iid = touch["instruction_id"]
        if outcome == "REJECTED":
            return ProviderResult("REJECTED", error="rejected by provider")
        ref = f"{self.name}-{next(self._ids)}"
        self.sent.append(iid)
        if outcome == "UNKNOWN_BUT_DELIVERED":  # we timed out, but the provider did accept it
            self.known[iid] = "DELIVERED"
            return ProviderResult("UNKNOWN")
        if outcome == "UNKNOWN_LOST":  # we timed out and it never reached the provider
            self.sent.pop()
            return ProviderResult("UNKNOWN")
        self.known[iid] = "ACCEPTED"
        return ProviderResult("ACCEPTED", provider_ref=ref, cost_paise=self.cost_paise)

    def status(self, instruction_id: str, provider_ref: Optional[str]) -> str:
        return self.known.get(instruction_id, "NOT_FOUND")


@dataclass
class SupervisorInstruction:
    action: str
    reason: str
    rule: str
    policy_version: str
    instruction_id: str
    borrower_id: str
    obligation_id: str
    channel: str
    template_id: Optional[str]
    at: str
    expires_at: Optional[str]
    reserved: bool
    detail: dict = field(default_factory=dict)


class Supervisor:
    def __init__(self, ledger: Ledger):
        self.ledger = ledger

    def decide(self, rec: Instruction) -> SupervisorInstruction:
        """Approve-and-reserve in one step: the master guardrail runs here (approval time) and
        again in Executor.dispatch (send time)."""
        res: ReserveResult = self.ledger.reserve(rec)
        d = res.decision
        end = self.ledger.policy.raw["contact_window"]["end_exclusive"]
        local = self.ledger.policy.local(rec.at)
        expires = local.replace(hour=int(end[:2]), minute=int(end[3:]), second=0) if d.allowed else None
        return SupervisorInstruction(d.decision, d.reason, d.rule, d.policy_version, res.instruction_id,
                                     rec.borrower_id, rec.obligation_id, rec.channel, rec.template_id,
                                     local.isoformat(timespec="minutes"),
                                     expires.isoformat(timespec="minutes") if expires else None,
                                     res.reserved, d.detail | ({"idempotent_replay": True} if res.idempotent_replay else {}))


@dataclass
class DispatchResult:
    instruction_id: str
    outcome: str          # SENT | FAILED | UNCERTAIN | BLOCKED | SKIPPED
    reason: str
    provider_ref: Optional[str] = None


class Executor:
    def __init__(self, ledger: Ledger, providers: dict[str, Provider]):
        self.ledger = ledger
        self.providers = providers

    def dispatch(self, instruction_id: str, at: Optional[datetime] = None) -> DispatchResult:
        d: Decision = self.ledger.begin_dispatch(instruction_id, at)
        if not d.allowed:
            outcome = "SKIPPED" if d.rule == "IDEMPOTENCY" else "BLOCKED"
            return DispatchResult(instruction_id, outcome, d.reason)
        touch = self.ledger.touch(instruction_id)
        provider = self.providers.get(touch["channel"])
        if provider is None:
            self.ledger.record_provider_status(instruction_id, "FAILED", reason="NO_ADAPTER")
            return DispatchResult(instruction_id, "FAILED", "NO_ADAPTER")
        try:
            r = provider.send(touch)
        except Exception as exc:  # network error: we do not know whether it went out
            r = ProviderResult("UNKNOWN", error=str(exc))
        if r.status == "ACCEPTED":
            self.ledger.record_provider_status(instruction_id, "SENT", r.provider_ref)
            if r.cost_paise is not None:
                self.ledger.reconcile(instruction_id, r.cost_paise)
            return DispatchResult(instruction_id, "SENT", "ACCEPTED", r.provider_ref)
        if r.status == "REJECTED":
            self.ledger.record_provider_status(instruction_id, "FAILED", reason=r.error or "PROVIDER_REJECTED")
            return DispatchResult(instruction_id, "FAILED", r.error or "PROVIDER_REJECTED")
        self.ledger.record_provider_status(instruction_id, "UNKNOWN")
        return DispatchResult(instruction_id, "UNCERTAIN", r.error or "NO_PROVIDER_ANSWER")

    def dispatch_due(self, now: datetime) -> list[DispatchResult]:
        return [self.dispatch(t["instruction_id"], now) for t in self.ledger.due_touches(now)]

    def resolve_uncertain(self, instruction_id: str) -> str:
        """Never resend on a guess. Ask the provider what happened:
        it has the message → mark SENT/DELIVERED (no second send);
        it never got it   → mark FAILED and release budget, so the Supervisor can re-plan
                            a NEW instruction that passes every guardrail again;
        still unknown     → stay UNCERTAIN (keeps counting toward caps and budget)."""
        touch = self.ledger.touch(instruction_id)
        if touch is None or touch["status"] != "UNCERTAIN":
            return touch["status"] if touch else "UNKNOWN_INSTRUCTION"
        provider = self.providers.get(touch["channel"])
        status = provider.status(instruction_id, touch["provider_ref"]) if provider else "UNKNOWN"
        if status in ("ACCEPTED", "SENT", "DELIVERED"):
            return self.ledger.record_provider_status(instruction_id, "DELIVERED" if status == "DELIVERED" else "SENT")
        if status == "NOT_FOUND":
            return self.ledger.record_provider_status(instruction_id, "FAILED", reason="PROVIDER_NEVER_RECEIVED")
        return "UNCERTAIN"
