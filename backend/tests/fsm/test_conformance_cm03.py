"""cm03.conversation conformance (runs only when kairos_backend.cm03 is present, i.e. once
cm03/whatsapp-servicing is merged): every message's state change in the seeded CM-03 eval is declared.

The engine is instrumented (state when the conversation is loaded, state when it is saved). A change must
be one declared transition, or OTP success followed by serving the parked request (AWAIT_OTP -> VERIFIED
-> X) in the same message. No conversation is VERIFIED-family without a successful OTP.
"""
from __future__ import annotations

import pytest

pytest.importorskip("kairos_backend.cm03")

from kairos_backend.cm03 import engine as E  # noqa: E402
from kairos_backend.cm03.evaluation import build_world, run_sequential  # noqa: E402
from kairos_backend.machines import CM03, CM03_VERIFIED  # noqa: E402


def declared(before: str, after: str) -> bool:
    if before == after:
        return any(t.src == before and t.dst == before for t in CM03.transitions)
    if CM03.allows(before, after):
        return True
    return before == "AWAIT_OTP" and (after == "VERIFIED" or CM03.allows("VERIFIED", after))


@pytest.mark.parametrize("seed", [7, 42])
def test_cm03_eval_takes_only_declared_transitions(policy, tmp_path, monkeypatch, seed):
    seen, pairs = {}, []
    load, save = E.ConversationEngine._load_or_create, E.ConversationEngine._save

    def _load(self, sender):
        conv = load(self, sender)
        seen[conv.conversation_id] = conv.state
        return conv

    def _save(self, conv, turn, message_id):
        pairs.append((seen.get(conv.conversation_id, "NEW"), conv.state, conv.verified))
        return save(self, conv, turn, message_id)

    monkeypatch.setattr(E.ConversationEngine, "_load_or_create", _load)
    monkeypatch.setattr(E.ConversationEngine, "_save", _save)
    world = build_world(120, seed, db_path=str(tmp_path / "cm03.db"))
    run_sequential(world)
    assert pairs
    bad = sorted({(b, a) for b, a, _ in pairs if not declared(b, a)})
    assert bad == [], f"undeclared CM-03 transitions: {bad}"
    assert all(v for b, a, v in pairs if a in CM03_VERIFIED), "a verified state without a verified OTP"
    reached = {a for _, a, _ in pairs}
    assert {"AWAIT_OTP", "VERIFIED", "HANDOFF", "CLOSED"} <= reached
