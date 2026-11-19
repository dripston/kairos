"""Shared K09 / K06 contract: the types the learner, Supervisor, intent and replay agree on.

Owners: K09 learner + replay (Rehan / Varun), K06 Supervisor (Rehan), policy (Ojas), CRM trace
and summary (Yuval). Change this file only with all four in the loop. Everything else imports it.

Flow (one decision):
    candidates (channel × slot × template × est. cost)
      → Supervisor eligibility: Ledger.preview() per candidate (K01 gate, policy order)
      → Learner.rank(band, eligible, mode)            → Ranking (SEND best | WAIT)
      → Ledger.reserve()  (atomic, stable instruction_id)
      → Executor.dispatch() (gate re-checked at send time)
      → OutcomeFinalizer at the end of the reply window → Learner.apply_outcome() (idempotent)
    Every decision is emitted to the CRM as a `supervisor.decision` envelope (see crm_envelope).

Numbers produced with SYNTHETIC_COSTS / simulator priors are SIMULATED and must be labeled so.
"""
from __future__ import annotations

import random
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional, Protocol, Sequence

MODEL_FAMILY = "k09-beta-bernoulli"


class Mode(str, Enum):
    """Where a decision runs. Randomized exploration is allowed only off production."""
    PRODUCTION = "PRODUCTION"   # posterior mean only (greedy, deterministic)
    SANDBOX = "SANDBOX"         # approved sandbox: seeded Thompson sampling allowed
    SIMULATOR = "SIMULATOR"     # simulator / replay: seeded Thompson sampling allowed

    @property
    def allows_exploration(self) -> bool:
        return self in (Mode.SANDBOX, Mode.SIMULATOR)


# Local-time (Asia/Kolkata) slots inside the K01 contact window 08:00–19:00 (end exclusive).
SLOTS: tuple[str, ...] = ("08-11", "11-14", "14-17", "17-19")
SLOT_START_HOUR = {"08-11": 8, "11-14": 11, "14-17": 14, "17-19": 17}

# Outbound channels the learner ranks (K01 `channels`).
CHANNELS: tuple[str, ...] = ("sms", "whatsapp", "email", "ivr", "voicebot")

# SYNTHETIC per-touch costs until official D13 channel costs arrive. Label results SIMULATED.
SYNTHETIC_COSTS: dict[str, float] = {"sms": 0.20, "whatsapp": 0.40, "email": 0.05, "ivr": 1.20, "voicebot": 2.50}

# Ranking floor: an arm whose reply probability is below this is never chosen (→ WAIT).
DEFAULT_MIN_REPLY_PROB = 0.03


def slot_of(local: datetime) -> Optional[str]:
    """Slot for a local (Asia/Kolkata) time; None outside the contact window."""
    h = local.hour
    for s, start in SLOT_START_HOUR.items():
        end = int(s.split("-")[1])
        if start <= h < end:
            return s
    return None


@dataclass(frozen=True)
class Arm:
    band: str        # K01 dpd_bands id: PRE_DUE | B1 | B2 | B3 | B4
    channel: str
    slot: str

    @property
    def key(self) -> str:
        return f"{self.band}|{self.channel}|{self.slot}"


@dataclass(frozen=True)
class Candidate:
    """An action that ALREADY passed the Supervisor's eligibility check (gate preview)."""
    channel: str
    slot: str
    at: datetime                 # proposed local send time inside the slot
    template_id: Optional[str]
    est_cost_inr: float


@dataclass(frozen=True)
class ArmScore:
    band: str
    channel: str
    slot: str
    alpha: float
    beta: float
    n_obs: int                   # finalized outcomes behind this estimate
    source: str                  # "segment" | "pooled" (sparse segment → channel×slot across bands) | "prior"
    posterior_mean: float
    sampled_p: Optional[float]   # Thompson draw; None unless mode.allows_exploration
    p_used: float                # what ranking used (sampled_p in SANDBOX/SIMULATOR, else posterior_mean)
    est_cost_inr: float
    replies_per_inr: float       # p_used / est_cost_inr (0 when cost <= 0)
    qualifies: bool              # p_used >= min_reply_prob and est_cost_inr > 0
    reason: str


@dataclass
class Ranking:
    action: str                  # "SEND" | "WAIT"
    chosen: Optional[ArmScore]
    scores: list[ArmScore]
    model_version: str           # f"{MODEL_FAMILY}/{hash of priors + config}@{n updates}"
    mode: Mode
    reason: str
    decision_id: Optional[str] = None   # set when persisted (decision_scores row)

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["mode"] = self.mode.value
        return d


class Learner(Protocol):
    model_version: str

    def rank(self, band: str, candidates: Sequence[Candidate], mode: Mode,
             rng: Optional[random.Random] = None, borrower_id: Optional[str] = None,
             persist: bool = True) -> Ranking: ...

    def apply_outcome(self, outcome: "Outcome") -> bool:
        """Count a FINAL outcome (REPLY → α+1, NO_REPLY → β+1) exactly once per instruction_id.
        Returns True only the first time; PENDING / UNRESOLVED are never counted."""
        ...

    def estimates(self, band: Optional[str] = None) -> list[dict[str, Any]]:
        """Current posterior per arm (band, channel, slot, alpha, beta, mean, n_obs) for reports."""
        ...


@dataclass(frozen=True)
class Outcome:
    """One outcome per executed touch. REPLY = 1, NO_REPLY = 0; others carry no reward."""
    instruction_id: str
    borrower_id: str
    band: str
    channel: str
    slot: str
    executed_at: datetime
    window_end: datetime                 # executed_at + K01 reply_attribution.reply_window_hours
    status: str                          # PENDING | REPLY | NO_REPLY | UNRESOLVED
    reached: Optional[bool] = None       # delivered / connected — reach, NOT a reply
    reply_at: Optional[datetime] = None
    finalized_at: Optional[datetime] = None

    @property
    def reward(self) -> Optional[int]:
        return {"REPLY": 1, "NO_REPLY": 0}.get(self.status)


class CrmSink(Protocol):
    def emit(self, envelope: dict[str, Any]) -> None: ...


@dataclass
class RecordingSink:
    """Keeps envelopes in memory (tests, replay). A HTTP sink posts the same dicts to crm/ /api/events."""
    envelopes: list[dict[str, Any]] = field(default_factory=list)

    def emit(self, envelope: dict[str, Any]) -> None:
        self.envelopes.append(envelope)


def crm_envelope(*, event_type: str, borrower_id: str, occurred_at: datetime, dedupe_key: str,
                 payload: dict[str, Any], provenance: str = "SIMULATED", source: str = "kairos.k06",
                 loan_id: Optional[str] = None, campaign_id: Optional[str] = None,
                 touch_id: Optional[str] = None) -> dict[str, Any]:
    """The crm/ event contract (see crm/kairos/ingest.py): one envelope per event, idempotent on
    dedupe_key, provenance LIVE | REPLAY | SIMULATED. Supervisor decisions use
    event_type="supervisor.decision" with payload {decision, reason, policy_version, expires_at,
    instruction_id, model_version, scores}."""
    return {"event_type": event_type, "source": source, "provenance": provenance,
            "occurred_at": occurred_at.isoformat(), "dedupe_key": dedupe_key, "borrower_id": borrower_id,
            "loan_id": loan_id, "campaign_id": campaign_id, "touch_id": touch_id, "payload": payload}
