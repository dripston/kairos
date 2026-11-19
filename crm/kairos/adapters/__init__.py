"""Interfaces Kairos expects from teammates' components.

Until the real implementations land, `kairos.adapters.fixtures` provides
clearly labeled stand-ins. To swap one in, implement the protocol and pass it
to `Adapters(...)` (or register a provider adapter in `kairos.web`). Nothing
else changes: every output still enters through `kairos.ingest.ingest_event`.

Owners (from the tracker):
  provider adapters (WhatsApp K07, SMS K08, D7/LMS K11, Sarvam IVR)  Rehan
  supervisor + executor (K06), channel/time Rechurn (K09)            Rehan
  propensity + nudge intensity (K21)                                 Ojas
  template selector (K22), voicebot (K18), summarizer (K05)          Yuval
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

try:  # Python 3.8+
    from typing import Protocol
except ImportError:  # pragma: no cover
    Protocol = object  # type: ignore

Envelope = Dict[str, Any]


@dataclass(frozen=True)
class IntentResult:
    intent: str                      # NEEDS_TIME | CLAIMS_PAID | CALLBACK | HARDSHIP | STOP | OTHER
    confidence: float
    classified_by: str
    loan_id: Optional[str] = None    # set when the borrower refers to a specific loan
    agreed_date: Optional[str] = None
    claimed_amount: Optional[int] = None
    claimed_reference: Optional[str] = None


@dataclass(frozen=True)
class AgentOutput:
    agent: str                       # summarizer | channel_time | propensity | template
    task_id: str                     # K05 | K09 | K21 | K22
    owner: str
    headline: str                    # one line for the timeline
    output: Dict[str, Any] = field(default_factory=dict)
    is_fixture: bool = True


@dataclass(frozen=True)
class SupervisorDecision:
    decision: str                    # SEND | WAIT | CANCEL | HANDOFF
    reason: str
    decided_by: str
    policy_version: str
    expires_at: Optional[str] = None  # ISO timestamp with offset; a WAIT holds until then
    scope_loan_id: Optional[str] = None


class ProviderAdapter(Protocol):
    """Turns a raw webhook (SMS DLR, WhatsApp inbound, D7 ledger, Sarvam call) into an event envelope."""
    name: str
    is_fixture: bool

    def normalize(self, raw: Dict[str, Any]) -> Envelope: ...


class IntentClassifier(Protocol):
    name: str
    is_fixture: bool

    def classify(self, message: Dict[str, Any]) -> IntentResult: ...


class RecommendationAgents(Protocol):
    """K09 channel/time, K21 propensity/intensity and K22 template. K05 is built in."""
    name: str
    is_fixture: bool

    def recommend(self, context: Dict[str, Any]) -> List[AgentOutput]: ...


class Supervisor(Protocol):
    """Reads the four agent outputs + borrower state; returns SEND/WAIT/CANCEL/HANDOFF."""
    name: str
    is_fixture: bool

    def decide(self, context: Dict[str, Any]) -> SupervisorDecision: ...


@dataclass
class Adapters:
    """The components ingestion calls while handling an event."""
    intent: IntentClassifier
    agents: RecommendationAgents
    supervisor: Supervisor


def default_adapters() -> Adapters:
    from kairos.adapters.fixtures import FixtureAgents, FixtureIntentClassifier, FixtureSupervisor

    return Adapters(intent=FixtureIntentClassifier(), agents=FixtureAgents(), supervisor=FixtureSupervisor())
