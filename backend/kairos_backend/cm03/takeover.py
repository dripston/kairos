"""CM-03 human takeover: a person takes a WhatsApp conversation from the bot, talks to the borrower,
decides, and hands it back.

    owner flag     conv.data["owner"] = {"kind": "human", "agent": "human:<id>", "since": <iso>,
                   "ticket_id": <case or None>}. No flag (or kind "bot") = the bot owns it.
    guard          engine._step: a human-owned conversation gets NO bot reply (the inbound is logged for the
                   agent). engine._reply refuses to send anything while a human owns it, whatever the path
                   (D7 recheck included). STOP is still honoured at once: every channel, queued sends canceled.
                   A human-owned conversation never times out into a new bot chat.
    agent message  sent through the same dry-run WhatsApp adapter and logged (scrubbed) on the CRM timeline as
                   AGENT_REPLY. Refused once the borrower has opted out (STOP).
    hand back      owner -> bot; a HANDOFF conversation resumes (VERIFIED if verified, else NEW: the next
                   message starts identification again). handoff_reason is kept, so the conversation still
                   counts as human-handled in the resolution metrics.
    audit          every action writes ONE K06 audit row (actor human:<agent>, borrower id only, no text).
    cases          with a HITL control plane (kairos_backend.hitl) the conversation's ticket is claimed on
                   takeover and resolved on hand back; hardship holds go through HITL.act("hold").

Everything is SIMULATED: the WhatsApp adapter is dry-run.
"""
from __future__ import annotations

from typing import Optional

from . import PROVENANCE
from .store import mask_phone, scrub

HUMAN = "human"
CLAIM_DECISIONS = ("approve", "reject")
MAX_AGENT_TEXT = 1000


def owner_of(data) -> Optional[dict]:
    """The owner record of a conversation's data dict (or a Conversation), None for the bot."""
    d = getattr(data, "data", data) or {}
    o = d.get("owner") if isinstance(d, dict) else None
    return o if isinstance(o, dict) and o.get("kind") == HUMAN else None


def human_owned(data) -> bool:
    return owner_of(data) is not None


class TakeoverError(Exception):
    def __init__(self, code: str, message: str, http: int = 409):
        super().__init__(message)
        self.code, self.http = code, http

    def as_dict(self) -> dict:
        return {"error": self.code, "message": str(self)}


def _agent(agent: str) -> str:
    a = (agent or "").strip()
    if not a:
        raise TakeoverError("AGENT_REQUIRED", "an agent id is required", 422)
    return a if a.startswith("human:") else f"human:{a}"


class Takeover:
    """Human-in-the-loop on a CM-03 conversation. `hitl` (kairos_backend.hitl.HITL) is optional: without it
    the owner flag, the guard, agent messages, claim decisions and the audit still work."""

    def __init__(self, engine, hitl=None, role: str = "supervisor"):
        self.engine = engine
        self.hitl = hitl
        self.role = role

    # ------------------------------------------------------------------ helpers
    def _iso(self) -> str:
        return self.engine.clock().isoformat(timespec="seconds")

    def _load(self, conversation_id: str):
        conv = self.engine.get(conversation_id)
        if conv is None:
            raise TakeoverError("UNKNOWN_CONVERSATION", f"unknown conversation {conversation_id}", 404)
        return conv

    def _audit(self, type_: str, agent: str, borrower_id: Optional[str], **detail) -> None:
        led = self.engine.ledger
        with led.tx() as c:
            led._audit(c, agent, type_, borrower_id, at=self.engine.clock(), provenance=PROVENANCE, **detail)

    def _require_owner(self, conv, agent: str) -> dict:
        o = owner_of(conv.data)
        if o is None:
            raise TakeoverError("NOT_TAKEN_OVER", "take the conversation over first; the bot owns it")
        if o.get("agent") != agent:
            raise TakeoverError("OWNED_BY_ANOTHER_AGENT", "another agent owns this conversation")
        return o

    def _commit(self, conv, turn) -> None:
        self.engine._save(conv, turn, None)

    def tickets(self, conversation_id: str) -> list:
        """Case ids opened for this conversation (create_ticket tool calls on its timeline), oldest first."""
        out = []
        for e in self.engine.timeline(conversation_id):
            if e["kind"] == "TOOL_CALL" and e["name"] == "create_ticket":
                tid = ((e["payload"] or {}).get("result") or {}).get("ticket_id")
                if tid and tid not in out:
                    out.append(tid)
        return out

    def _ticket_status(self, ticket_id: str) -> Optional[str]:
        r = self.engine.ledger._conn().execute("SELECT status FROM tickets WHERE ticket_id=?", (ticket_id,)).fetchone()
        return r["status"] if r else None

    def latest_claim(self, conversation_id: str) -> Optional[dict]:
        r = self.engine.ledger._conn().execute(
            "SELECT * FROM cm03_claims WHERE conversation_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (conversation_id,)).fetchone()
        return dict(r) if r else None

    # ------------------------------------------------------------------ actions
    def take_over(self, conversation_id: str, agent: str, reason: str = "taken over from the bot") -> dict:
        agent = _agent(agent)
        conv0 = self._load(conversation_id)
        with self.engine._lock_for(conv0.sender):
            conv = self._load(conversation_id)
            o = owner_of(conv.data)
            if o is not None and o.get("agent") != agent:
                raise TakeoverError("OWNED_BY_ANOTHER_AGENT", "another agent owns this conversation")
            if conv.state == "CLOSED":
                raise TakeoverError("CONVERSATION_CLOSED", "the conversation has ended")
            if o is not None:
                return {"conversation_id": conversation_id, "owner": o, "idempotent": True}
            ticket = None
            if self.hitl is not None:
                ticket = self._claim_case(conversation_id, agent)
            from .tools import Turn
            turn = Turn(conv.conversation_id, conv.verified)
            owner = {"kind": HUMAN, "agent": agent, "since": self._iso(), "ticket_id": ticket,
                     "state_at_takeover": conv.state}
            conv.data["owner"] = owner
            turn.add("TAKEOVER", agent, {"from": "bot", "state": conv.state, "ticket_id": ticket}, self._iso())
            self._commit(conv, turn)
        self._audit("CM03_TAKEOVER", agent, conv.borrower_id, conversation_id=conversation_id, state=conv.state,
                    ticket=ticket, reason=scrub(reason)[:160])
        return {"conversation_id": conversation_id, "owner": owner, "ticket_id": ticket}

    def _claim_case(self, conversation_id: str, agent: str) -> Optional[str]:
        tickets = self.tickets(conversation_id)
        if not tickets:
            return None
        tid = tickets[-1]
        st = self._ticket_status(tid)
        now = self.engine.clock()
        if st == "RESOLVED":   # a later hand-off of the same type on the same day attaches to the same case
            self.hitl.reopen_case(tid, agent, self.role, "conversation handed off again", now=now)
            st = "OPEN"
        if st == "OPEN":
            self.hitl.claim_case(tid, agent, self.role, "took over the WhatsApp conversation", now=now)
        return tid

    def agent_message(self, conversation_id: str, agent: str, text: str) -> dict:
        agent = _agent(agent)
        body = " ".join((text or "").split())
        if not body:
            raise TakeoverError("EMPTY_MESSAGE", "message text is required", 422)
        if len(body) > MAX_AGENT_TEXT:
            raise TakeoverError("MESSAGE_TOO_LONG", f"at most {MAX_AGENT_TEXT} characters", 422)
        conv0 = self._load(conversation_id)
        with self.engine._lock_for(conv0.sender):
            conv = self._load(conversation_id)
            self._require_owner(conv, agent)
            if conv.data.get("stopped") or self._opted_out(conv.borrower_id):
                raise TakeoverError("BORROWER_OPTED_OUT", "the borrower sent STOP; no further messages")
            from .tools import Turn
            turn = Turn(conv.conversation_id, conv.verified)
            res = self.engine.tools.send_whatsapp_reply(turn, conv.sender, body)
            conv.data["agent_messages"] = conv.data.get("agent_messages", 0) + 1
            turn.add("AGENT_REPLY", agent, {"text": scrub(body), "to": mask_phone(conv.sender), "chars": len(body)},
                     self._iso())
            self._commit(conv, turn)
        self._audit("CM03_AGENT_MESSAGE", agent, conv.borrower_id, conversation_id=conversation_id, chars=len(body),
                    channel="whatsapp", delivery=res.get("status"))
        return {"conversation_id": conversation_id, "sent": True, "status": res.get("status"), "text": body}

    def _opted_out(self, borrower_id: Optional[str]) -> bool:
        if not borrower_id:
            return False
        r = self.engine.ledger._conn().execute("SELECT stopped FROM borrower_state WHERE borrower_id=?",
                                               (borrower_id,)).fetchone()
        return bool(r and r["stopped"])

    def decide_claim(self, conversation_id: str, agent: str, decision: str, reason: str = "") -> dict:
        """approve: the claim goes (back) to the bank check. Only the D7 feed can mark money paid, never a person.
        reject: the claim is closed as not genuine; reminders carry on."""
        agent = _agent(agent)
        if decision not in CLAIM_DECISIONS:
            raise TakeoverError("BAD_DECISION", f"decision must be one of {CLAIM_DECISIONS}", 422)
        conv = self._load(conversation_id)
        self._require_owner(conv, agent)
        claim = self.latest_claim(conversation_id)
        now = self._iso()
        if decision == "reject":
            new = "REJECTED"
        else:
            new = "PENDING" if claim and claim.get("reference") else "PENDING_NO_REF"
        led = self.engine.ledger
        with led.tx() as c:
            if claim is None:   # the proof was refused before a claim was recorded (e.g. an edited screenshot)
                import uuid
                cid = "CLM-" + uuid.uuid4().hex[:10]
                c.execute("INSERT INTO cm03_claims (claim_id, conversation_id, borrower_id, obligation_id, reference,"
                          " reference_key, amount_paise, claimed_date, status, reason, created_at, updated_at)"
                          " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                          (cid, conversation_id, conv.borrower_id, None, None, None, None, None, new,
                           f"agent decision ({decision})", now, now))
                prev = None
            else:
                cid, prev = claim["claim_id"], claim["status"]
                c.execute("UPDATE cm03_claims SET status=?, reason=?, updated_at=? WHERE claim_id=?",
                          (new, f"agent decision ({decision})", now, cid))
        from .tools import Turn
        with self.engine._lock_for(conv.sender):
            conv = self._load(conversation_id)
            turn = Turn(conv.conversation_id, conv.verified)
            turn.add("AGENT_DECISION", f"CLAIM_{decision.upper()}", {"claim_id": cid, "from": prev, "to": new,
                                                                    "marks_paid": False}, now)
            conv.data["claim_decision"] = decision
            self._commit(conv, turn)
        self._audit(f"CM03_CLAIM_{'REJECTED' if decision == 'reject' else 'APPROVED'}", agent, conv.borrower_id,
                    conversation_id=conversation_id, claim_id=cid, previous=prev, status=new, marks_paid=False,
                    reason=scrub(reason)[:160])
        return {"claim_id": cid, "previous": prev, "status": new, "marks_paid": False}

    def hold_outbound(self, conversation_id: str, agent: str, days: int = 30,
                      reason: str = "hardship confirmed by the agent") -> dict:
        """Hardship confirmed: hold this borrower's outbound collections (every channel) for `days`, through the
        K06 HITL control plane (one HUMAN_ACTION audit row; queued sends canceled, budget released)."""
        agent = _agent(agent)
        if self.hitl is None:
            raise TakeoverError("NO_HITL", "holding outbound needs the HITL control plane", 501)
        conv = self._load(conversation_id)
        self._require_owner(conv, agent)
        if not conv.borrower_id:
            raise TakeoverError("UNKNOWN_BORROWER", "this number is not linked to a borrower")
        now = self.engine.clock()
        out = self.hitl.act(conv.borrower_id, "hold", agent, self.role, reason, now=now, days=days)
        from .tools import Turn
        with self.engine._lock_for(conv.sender):
            conv = self._load(conversation_id)
            turn = Turn(conv.conversation_id, conv.verified)
            turn.add("AGENT_DECISION", "HOLD_OUTBOUND", {"days": days, "until": out.get("until"),
                                                         "canceled": out.get("canceled")}, self._iso())
            conv.data["hardship_confirmed"] = True
            self._commit(conv, turn)
        return {"borrower_id": conv.borrower_id, "hold_id": out.get("hold_id"), "until": out.get("until"),
                "canceled": out.get("canceled", 0), "days": days}

    def hand_back(self, conversation_id: str, agent: str, resolution_code: Optional[str] = None,
                  reason: str = "handed back to the bot") -> dict:
        agent = _agent(agent)
        conv0 = self._load(conversation_id)
        with self.engine._lock_for(conv0.sender):
            conv = self._load(conversation_id)
            o = self._require_owner(conv, agent)
            before = conv.state
            if conv.data.get("stopped"):
                conv.state = "CLOSED"     # the borrower opted out while a person owned the chat
            elif conv.state == "HANDOFF":
                conv.state = "VERIFIED" if conv.verified else "NEW"
            conv.data.pop("owner", None)
            conv.data.setdefault("handbacks", 0)
            conv.data["handbacks"] += 1
            from .tools import Turn
            turn = Turn(conv.conversation_id, conv.verified)
            turn.add("HAND_BACK", agent, {"to": "bot", "state_before": before, "state_after": conv.state}, self._iso())
            self._commit(conv, turn)
        resolved = None
        tid = o.get("ticket_id")
        if self.hitl is not None and tid and self._ticket_status(tid) in ("OPEN", "CLAIMED"):
            code = resolution_code or ("HARDSHIP_CONFIRMED" if conv.data.get("hardship_confirmed") else
                                       "CONTACTED_RESOLVED" if conv.data.get("claim_decision") or conv.data.get("agent_messages")
                                       else "NO_ACTION_NEEDED")
            self.hitl.resolve_case(tid, agent, self.role, reason, code, now=self.engine.clock())
            resolved = code
        self._audit("CM03_HAND_BACK", agent, conv.borrower_id, conversation_id=conversation_id, state_before=before,
                    state_after=conv.state, ticket=tid, resolution_code=resolved)
        return {"conversation_id": conversation_id, "owner": "bot", "state": conv.state, "ticket_id": tid,
                "resolution_code": resolved}


__all__ = ["Takeover", "TakeoverError", "owner_of", "human_owned", "HUMAN"]
