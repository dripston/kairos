"""K06 Supervisor as a deterministic state machine.

plan():     RECEIVED → BORROWER_CHECK → ELIGIBILITY → RANK → RESERVE → DECIDED
dispatch(): DISPATCHING → DISPATCHED | BLOCKED | SKIPPED

Every step reads facts from the Ledger — which reads the K03 spine: DPD/band live from the loan's
oldest unpaid EMI, consent from spine consents, "cleared" from what was paid on that EMI — and rules
from the Gate (K01 evaluation_order); the learner only ranks what already passed eligibility.
One plan is for ONE EMI (spine obligation) of one loan; money is integer paise. Each plan/dispatch records its
transitions and emits CRM envelopes (contracts.crm_envelope) whose dedupe_key is stable,
so replaying the same plan emits identical envelopes and never reserves twice.

Deterministic: same inputs + same DB state + same rng seed → same transitions and
envelopes. No wall-clock reads inside a decision; `at` is the only clock.
"""
from __future__ import annotations

import hashlib
import json
import random
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Optional, Sequence

from . import gate
from .contracts import CHANNELS, Candidate, CrmSink, Learner, Mode, Ranking, crm_envelope, slot_of
from .executor import Executor
from .gate import CANCEL, SEND, WAIT, Decision
from .ledger import LIVE, Instruction, Ledger, stable_instruction_id

# ---------- states ----------
RECEIVED = "RECEIVED"
BORROWER_CHECK = "BORROWER_CHECK"
ELIGIBILITY = "ELIGIBILITY"
RANK = "RANK"
RESERVE = "RESERVE"
DECIDED = "DECIDED"
DISPATCHING = "DISPATCHING"
DISPATCHED = "DISPATCHED"   # passed the send-time check and went to the provider (SENT | FAILED | UNCERTAIN)
BLOCKED = "BLOCKED"         # send-time check failed, or the touch was canceled before dispatch → CANCEL
SKIPPED = "SKIPPED"         # not dispatchable any more (already in flight / sent / failed): no second send

PLAN_STATES = (RECEIVED, BORROWER_CHECK, ELIGIBILITY, RANK, RESERVE, DECIDED)
DISPATCH_STATES = (DISPATCHING, DISPATCHED, BLOCKED, SKIPPED)

# Borrower-level K01/K13 rules: channel-independent, checked before any candidate.
# They run in policy evaluation_order (gate.rule_order); first hit wins.
BORROWER_RULES = ("STOP_OPT_OUT", "HARDSHIP_HOLD", "CALLBACK_PENDING", "SUPERVISOR_HOLD", "PTP_HOLD",
                  "OBLIGATION_CLEARED")

DECISION_EVENT = "supervisor.decision"
DISPATCH_EVENT = "touch.dispatched"


def _h(*parts: Any) -> str:
    return hashlib.sha1(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:20]


@dataclass(frozen=True)
class Transition:
    frm: Optional[str]
    to: str
    event: str
    detail: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"from": self.frm, "to": self.to, "event": self.event, "detail": self.detail}


@dataclass
class PlanResult:
    plan_id: str
    borrower_id: str
    obligation_id: str
    campaign_id: str
    band: str
    mode: Mode
    decision: str                       # SEND | WAIT | CANCEL | HANDOFF
    reason: str
    rule: str
    policy_version: str
    instruction_id: Optional[str]       # set only for SEND
    reserved: bool
    idempotent_replay: bool             # SEND that re-found its own earlier reservation
    channel: Optional[str]
    slot: Optional[str]
    template_id: Optional[str]
    send_at: Optional[str]              # chosen candidate's local send time
    expires_at: Optional[str]           # contact-window end on the send day
    ranking: Optional[Ranking]
    eligible: list[Candidate]
    rejected: list[dict]
    transitions: list[Transition]
    envelope: dict

    @property
    def state(self) -> str:
        return self.transitions[-1].to

    @property
    def states(self) -> list[str]:
        return [t.to for t in self.transitions]


@dataclass
class DispatchOutcome:
    instruction_id: str
    outcome: str                        # SENT | FAILED | UNCERTAIN | BLOCKED | SKIPPED
    decision: Optional[str]             # SEND (went to provider) | CANCEL (blocked) | None (skipped)
    reason: str
    rule: str
    policy_version: str
    gate_decision: Optional[str]        # what the gate said at send time when blocked (WAIT/CANCEL/HANDOFF)
    provider_ref: Optional[str]
    transitions: list[Transition]
    envelopes: list[dict]

    @property
    def state(self) -> str:
        return self.transitions[-1].to

    @property
    def sent(self) -> bool:
        return self.outcome in ("SENT", "UNCERTAIN")


class SupervisorFSM:
    def __init__(self, ledger: Ledger, executor: Executor, learner: Learner, sink: CrmSink,
                 mode: Mode = Mode.PRODUCTION, provenance: str = "SIMULATED"):
        self.ledger = ledger
        self.policy = ledger.policy
        self.executor = executor
        self.learner = learner
        self.sink = sink
        self.mode = mode
        self.provenance = provenance

    # ---------- helpers ----------
    def _local(self, dt: datetime) -> datetime:
        return self.policy.local(dt)

    def _expires(self, send_at: datetime) -> str:
        end = self.policy.raw["contact_window"]["end_exclusive"]
        loc = self._local(send_at)
        return loc.replace(hour=int(end[:2]), minute=int(end[3:]), second=0, microsecond=0).isoformat(timespec="minutes")

    def _rule_rank(self) -> dict[str, int]:
        order = {"CONTROL": -1}
        for i, rid in enumerate(gate.rule_order(self.policy)):
            order[rid] = i
        return order

    def _borrower_check(self, probe: Instruction) -> tuple[Optional[Decision], Optional[str], str]:
        """Borrower-level holds from the same facts the gate sees, in policy order.
        Returns (block or None, live band from the spine DPD or None, loan_id of the EMI)."""
        with self.ledger.tx() as c:
            facts, extra = self.ledger.facts_for(c, probe)
        ctx = gate._Ctx(self.policy, facts)  # the gate's own rule context; rules stay pure
        for rid in gate.rule_order(self.policy):
            if rid not in BORROWER_RULES:
                continue
            hit = gate.RULES[rid](ctx)
            if hit:
                decision, reason, detail = hit
                return (Decision(decision, reason, rid, self.policy.version, ctx.band, dict(detail)),
                        ctx.band, extra["loan_id"])
        return None, ctx.band, extra["loan_id"]

    @staticmethod
    def _cand_dict(c: Candidate) -> dict:
        return {"channel": c.channel, "slot": c.slot, "template_id": c.template_id,
                "at": c.at.isoformat(timespec="minutes"), "est_cost_paise": c.est_cost_paise}

    @staticmethod
    def _top_scores(ranking: Optional[Ranking], n: int = 3) -> list[dict]:
        if ranking is None:
            return []
        ordered = sorted(ranking.scores, key=lambda s: (not s.qualifies, -s.replies_per_inr, -s.p_used,
                                                        s.channel, s.slot))
        return [asdict(s) for s in ordered[:n]]

    def _emit(self, env: dict) -> dict:
        self.sink.emit(env)
        return env

    # ---------- plan ----------
    def plan(self, borrower_id: str, obligation_id: str, campaign_id: str, band: Optional[str],
             candidates: Sequence[Candidate], at: datetime, rng: Optional[random.Random] = None) -> PlanResult:
        """Plan one touch for one EMI. `band=None` uses the live band from the spine (the loan's
        oldest unpaid EMI on `at`); an explicit band is what the learner ranks for."""
        at_local = self._local(at)
        cands = list(candidates)
        # BORROWER_CHECK facts first: they also give the live band and the EMI's loan
        probe = Instruction(borrower_id, obligation_id, campaign_id, cands[0].channel if cands else CHANNELS[0],
                            None, at_local, 0)
        blocked, live_band, loan_id = self._borrower_check(probe)
        band = band if band is not None else live_band
        plan_id = "PLN-" + _h(borrower_id, obligation_id, campaign_id, band, self.mode.value,
                              at_local.isoformat(timespec="seconds"), [self._cand_dict(c) for c in cands])
        tr: list[Transition] = [Transition(None, RECEIVED, "PLAN_REQUESTED",
                                           {"plan_id": plan_id, "at": at_local.isoformat(timespec="minutes"),
                                            "candidates": len(cands), "mode": self.mode.value})]
        ctx: dict[str, Any] = dict(plan_id=plan_id, borrower_id=borrower_id, obligation_id=obligation_id,
                                   loan_id=loan_id, campaign_id=campaign_id, band=band, at=at_local, transitions=tr)

        tr.append(Transition(RECEIVED, BORROWER_CHECK, "CHECK_BORROWER", {"band": band, "loan_id": loan_id}))
        if blocked is not None:
            tr.append(Transition(BORROWER_CHECK, DECIDED, blocked.decision,
                                 {"rule": blocked.rule, "reason": blocked.reason, **blocked.detail}))
            return self._finish(ctx, blocked.decision, blocked.reason, blocked.rule)
        tr.append(Transition(BORROWER_CHECK, ELIGIBILITY, "BORROWER_CLEAR"))

        # ELIGIBILITY: gate preview per candidate. A candidate whose stable id is already live is
        # this plan's own earlier reservation; it is left out of every preview so a replay sees
        # the same world the first run saw.
        ins_of = []
        for c in cands:
            iid = stable_instruction_id(borrower_id, obligation_id, c.channel, self._local(c.at))
            ins_of.append(Instruction(borrower_id, obligation_id, campaign_id, c.channel, c.template_id, c.at,
                                      int(c.est_cost_paise), instruction_id=iid))
        existing = {i.instruction_id: self.ledger.touch(i.instruction_id) for i in ins_of}
        own = next((i.instruction_id for i in ins_of
                    if existing[i.instruction_id] and existing[i.instruction_id]["status"] in LIVE), None)
        eligible: list[tuple[Candidate, Instruction, Decision]] = []
        rejected: list[dict] = []
        for c, ins in zip(cands, ins_of):
            d = self.ledger.preview(ins, exclude=own)
            prior = existing[ins.instruction_id]
            if d.allowed and prior is not None and prior["status"] not in LIVE:
                d = Decision(WAIT, f"INSTRUCTION_{prior['status']}", "IDEMPOTENCY", self.policy.version)
            elif d.allowed and slot_of(self._local(c.at)) != c.slot:
                d = Decision(WAIT, "SLOT_MISMATCH", "CANDIDATE", self.policy.version,
                             detail={"slot_at": slot_of(self._local(c.at))})
            if d.allowed:
                eligible.append((c, ins, d))
            else:
                rejected.append({**self._cand_dict(c), "instruction_id": ins.instruction_id,
                                 "decision": d.decision, "rule": d.rule, "reason": d.reason,
                                 "detail": {k: v for k, v in d.detail.items() if k != "budget"}})
        ctx["rejected"] = rejected
        ctx["eligible"] = [c for c, _, _ in eligible]
        if not eligible:
            if not rejected:
                dec, reason, rule = WAIT, "NO_CANDIDATES", "ELIGIBILITY"
            else:
                rank = self._rule_rank()
                first = min(enumerate(rejected), key=lambda ir: (rank.get(ir[1]["rule"], len(rank)), ir[0]))[1]
                dec, reason, rule = first["decision"], first["reason"], first["rule"]
            tr.append(Transition(ELIGIBILITY, DECIDED, dec,
                                 {"rule": rule, "reason": reason, "rejected": len(rejected)}))
            return self._finish(ctx, dec, reason, rule)
        tr.append(Transition(ELIGIBILITY, RANK, "ELIGIBLE",
                             {"eligible": [f"{c.channel}|{c.slot}|{c.at.isoformat(timespec='minutes')}"
                                           for c, _, _ in eligible],
                              "rejected": [f"{r['channel']}|{r['slot']}:{r['reason']}" for r in rejected]}))

        # RANK
        if band is None:  # no live band (missing DPD) yet every candidate passed: cannot rank → WAIT
            tr.append(Transition(RANK, DECIDED, WAIT, {"rule": "DPD_VALID", "reason": "NO_BAND"}))
            return self._finish(ctx, WAIT, "NO_BAND", "DPD_VALID")
        ranking = self.learner.rank(band, [c for c, _, _ in eligible], self.mode, rng=rng, borrower_id=borrower_id)
        ctx["ranking"] = ranking
        if ranking.action != SEND or ranking.chosen is None:
            tr.append(Transition(RANK, DECIDED, WAIT, {"rule": "LEARNER", "reason": "LEARNER_WAIT",
                                                       "learner_reason": ranking.reason,
                                                       "model_version": ranking.model_version}))
            return self._finish(ctx, WAIT, "LEARNER_WAIT", "LEARNER")
        ch = ranking.chosen
        pick = next(((c, ins, d) for c, ins, d in eligible if c.channel == ch.channel and c.slot == ch.slot), None)
        if pick is None:
            tr.append(Transition(RANK, DECIDED, WAIT, {"rule": "LEARNER", "reason": "LEARNER_CHOICE_NOT_ELIGIBLE",
                                                       "chosen": f"{ch.channel}|{ch.slot}"}))
            return self._finish(ctx, WAIT, "LEARNER_CHOICE_NOT_ELIGIBLE", "LEARNER")
        cand, ins, approved = pick
        tr.append(Transition(RANK, RESERVE, "RANKED", {"chosen": f"{cand.channel}|{cand.slot}",
                                                       "instruction_id": ins.instruction_id,
                                                       "model_version": ranking.model_version}))

        # RESERVE (atomic; a stable id seen before is an idempotent replay, never a second reservation)
        res = self.ledger.reserve(ins)
        if not res.reserved:
            d = res.decision
            tr.append(Transition(RESERVE, DECIDED, d.decision, {"rule": d.rule, "reason": d.reason,
                                                                "instruction_id": res.instruction_id,
                                                                "idempotent_replay": res.idempotent_replay}))
            return self._finish(ctx, d.decision, d.reason, d.rule)
        tr.append(Transition(RESERVE, DECIDED, SEND, {"instruction_id": res.instruction_id,
                                                      "idempotent_replay": res.idempotent_replay}))
        ctx.update(chosen=cand, instruction_id=res.instruction_id, idempotent_replay=res.idempotent_replay)
        # reason/rule from the approval-time gate decision, identical on a replay
        return self._finish(ctx, SEND, approved.reason, approved.rule)

    def _finish(self, ctx: dict, decision: str, reason: str, rule: str) -> PlanResult:
        ranking: Optional[Ranking] = ctx.get("ranking")
        chosen: Optional[Candidate] = ctx.get("chosen") if decision == SEND else None
        iid = ctx.get("instruction_id") if decision == SEND else None
        expires = self._expires(chosen.at) if chosen else None
        chosen_d = {"channel": chosen.channel, "slot": chosen.slot, "template_id": chosen.template_id} if chosen else None
        rejected = ctx.get("rejected", [])
        payload = {
            "decision": decision, "reason": reason, "rule": rule, "policy_version": self.policy.version,
            "expires_at": expires, "instruction_id": iid,
            "model_version": ranking.model_version if ranking else None,
            "chosen": chosen_d, "scores": self._top_scores(ranking), "rejected": rejected,
            "plan_id": ctx["plan_id"], "band": ctx["band"], "mode": self.mode.value,
            "obligation_id": ctx["obligation_id"],
            "send_at": chosen.at.isoformat(timespec="minutes") if chosen else None,
        }
        env = crm_envelope(
            event_type=DECISION_EVENT, borrower_id=ctx["borrower_id"], occurred_at=ctx["at"],
            dedupe_key=f"k06:decision:{ctx['plan_id']}:" + _h(decision, rule, reason, iid),
            payload=payload, provenance=self.provenance, loan_id=ctx["loan_id"],
            campaign_id=ctx["campaign_id"], touch_id=iid)
        self._emit(env)
        return PlanResult(
            plan_id=ctx["plan_id"], borrower_id=ctx["borrower_id"], obligation_id=ctx["obligation_id"],
            campaign_id=ctx["campaign_id"], band=ctx["band"], mode=self.mode, decision=decision, reason=reason,
            rule=rule, policy_version=self.policy.version, instruction_id=iid, reserved=decision == SEND,
            idempotent_replay=bool(ctx.get("idempotent_replay")) and decision == SEND,
            channel=chosen.channel if chosen else None, slot=chosen.slot if chosen else None,
            template_id=chosen.template_id if chosen else None,
            send_at=payload["send_at"], expires_at=expires, ranking=ranking,
            eligible=ctx.get("eligible", []), rejected=rejected, transitions=ctx["transitions"], envelope=env)

    # ---------- dispatch ----------
    def dispatch(self, instruction_id: str, at: datetime) -> DispatchOutcome:
        at_local = self._local(at)
        tr = [Transition(None, DISPATCHING, "DISPATCH_REQUESTED",
                         {"instruction_id": instruction_id, "at": at_local.isoformat(timespec="minutes")})]
        res = self.executor.dispatch(instruction_id, at_local)  # re-checks the gate via ledger.begin_dispatch
        touch = self.ledger.touch(instruction_id)
        gate_decision: Optional[str] = None
        if res.outcome in ("SENT", "FAILED", "UNCERTAIN"):
            state, decision, rule, reason = DISPATCHED, SEND, "ALL_PASSED", res.reason
        elif res.outcome == "BLOCKED" or touch["status"] == "CANCELED":
            state, decision = BLOCKED, CANCEL
            gate_decision, rule, reason = self._blocking_rule(touch, at_local, res)
        else:
            state, decision, rule, reason = SKIPPED, None, "IDEMPOTENCY", res.reason
        outcome = BLOCKED if state == BLOCKED else res.outcome
        tr.append(Transition(DISPATCHING, state, outcome, {"rule": rule, "reason": reason,
                                                           "touch_status": touch["status"]}))

        send_at = datetime.fromisoformat(touch["scheduled_at"])
        payload = {
            "decision": decision, "outcome": outcome, "reason": reason, "rule": rule,
            "policy_version": self.policy.version, "expires_at": self._expires(send_at),
            "instruction_id": instruction_id, "model_version": None,
            "chosen": {"channel": touch["channel"], "slot": slot_of(self._local(send_at)),
                       "template_id": touch["template_id"]},
            "scores": [], "rejected": [], "gate_decision": gate_decision, "provider_ref": res.provider_ref,
            "touch_status": touch["status"], "obligation_id": touch["obligation_id"],
        }
        common = dict(borrower_id=touch["borrower_id"], occurred_at=at_local, provenance=self.provenance,
                      loan_id=touch["loan_id"], campaign_id=touch["campaign_id"], touch_id=instruction_id)
        envs = []
        if decision == CANCEL:  # the reserved instruction is withdrawn: a Supervisor decision in its own right
            envs.append(self._emit(crm_envelope(
                event_type=DECISION_EVENT, dedupe_key=f"k06:decision:dispatch:{instruction_id}:" + _h(CANCEL, rule, reason),
                payload=dict(payload), **common)))
        envs.append(self._emit(crm_envelope(event_type=DISPATCH_EVENT,
                                            dedupe_key=f"k06:dispatch:{instruction_id}:{outcome}",
                                            payload=payload, **common)))
        return DispatchOutcome(instruction_id, outcome, decision, reason, rule, self.policy.version, gate_decision,
                               res.provider_ref, tr, envs)

    def _blocking_rule(self, touch: dict, at_local: datetime, res) -> tuple[str, str, str]:
        """(gate decision, rule, reason) that stopped this touch.

        Send-time block → the ledger's own SEND_TIME_CHECK audit row for this instruction.
        Canceled before dispatch (STOP, verified payment, holds — the ledger cancels queued work
        when the event lands) → re-run the gate for this touch at `at` to name the rule."""
        if res.outcome == "BLOCKED":
            for row in self.ledger.audit_log(limit=50, borrower_id=touch["borrower_id"]):
                if row["type"] == "SEND_TIME_CHECK" and row["instruction_id"] == touch["instruction_id"]:
                    return row["decision"], row["rule"], row["reason"]
        ins = self.ledger.instruction_of(touch)
        ins.at = at_local
        d = self.ledger.preview(ins)
        if not d.allowed:
            return d.decision, d.rule, d.reason
        return WAIT, "LEDGER_CANCELED", touch["reason"] or "CANCELED"
