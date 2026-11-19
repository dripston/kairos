"""kairos_backend.fsm: declaration-time validation, deterministic step(), renderers, audit records."""
from __future__ import annotations

import random
from datetime import datetime

import pytest

from kairos_backend.fsm import (AmbiguousTransition, GuardError, Machine, MachineDefinitionError, NoTransition,
                                Transition as T, UnknownEvent, UnknownState, parse_guard)


def door(**kw):
    tr = [T("CLOSED", "OPEN", "OPENED", "!locked", rule="R1"),
          T("CLOSED", "OPEN", "CLOSED", "locked", rule="R2"),
          T("OPENED", "CLOSE", "CLOSED", rule="R3"),
          T("CLOSED", "SMASH", "BROKEN", rule="R4")]
    return Machine("door", "1", ["CLOSED", "OPENED", "BROKEN"], "CLOSED", ["BROKEN"], kw.pop("transitions", tr), **kw)


def test_step_is_deterministic_and_returns_the_fired_transition():
    m = door()
    assert m.step("CLOSED", "OPEN", {"locked": False}) == ("OPENED", m.transitions[0])
    assert m.step("CLOSED", "OPEN", {"locked": True})[0] == "CLOSED"
    assert m.step("OPENED", "CLOSE")[1].rule == "R3"


def test_unknown_state_event_and_missing_transition_are_rejected():
    m = door()
    with pytest.raises(UnknownState):
        m.step("AJAR", "OPEN", {"locked": False})
    with pytest.raises(UnknownEvent):
        m.step("CLOSED", "KICK")
    with pytest.raises(NoTransition):
        m.step("OPENED", "OPEN", {"locked": False})
    with pytest.raises(NoTransition):
        m.step("BROKEN", "CLOSE")


def test_missing_fact_fails_closed():
    with pytest.raises(GuardError):
        door().step("CLOSED", "OPEN", {})
    with pytest.raises(GuardError):
        door().step("CLOSED", "OPEN", {"locked": "no"})  # facts must be real bools


@pytest.mark.parametrize("bad,msg", [
    (dict(transitions=[T("CLOSED", "OPEN", "OPENED"), T("OPENED", "CLOSE", "CLOSED")]), "unreachable"),
    (dict(transitions=[T("CLOSED", "OPEN", "OPENED"), T("CLOSED", "SMASH", "BROKEN")]), "has no exit"),
    (dict(transitions=[T("CLOSED", "OPEN", "OPENED"), T("OPENED", "CLOSE", "CLOSED"), T("CLOSED", "SMASH", "BROKEN"),
                       T("BROKEN", "FIX", "CLOSED")]), "terminal state 'BROKEN' has exits"),
    (dict(transitions=[T("CLOSED", "OPEN", "OPENED"), T("CLOSED", "OPEN", "BROKEN"), T("OPENED", "CLOSE", "CLOSED")]),
     "ambiguous"),
    (dict(transitions=[T("CLOSED", "OPEN", "OPENED", "a"), T("CLOSED", "OPEN", "BROKEN", "b"),
                       T("OPENED", "CLOSE", "CLOSED")]), "not provably exclusive"),
    (dict(transitions=[T("CLOSED", "OPEN", "OPENED", "a & b"), T("CLOSED", "OPEN", "BROKEN", "b & a"),
                       T("OPENED", "CLOSE", "CLOSED")]), "not provably exclusive"),
    (dict(transitions=[T("CLOSED", "OPEN", "OPENED", "else"), T("CLOSED", "OPEN", "BROKEN", "else"),
                       T("OPENED", "CLOSE", "CLOSED")]), "more than one 'else'"),
    (dict(transitions=[T("CLOSED", "OPEN", "AJAR"), T("OPENED", "CLOSE", "CLOSED")]), "unknown state"),
    (dict(transitions=[T("CLOSED", "OPEN", "OPENED"), T("OPENED", "CLOSE", "CLOSED"), T("CLOSED", "SMASH", "BROKEN")],
          events=["OPEN", "CLOSE"]), "undeclared event"),
    (dict(transitions=[T("CLOSED", "OPEN", "OPENED", "!!x"), T("OPENED", "CLOSE", "CLOSED"),
                       T("CLOSED", "SMASH", "BROKEN")]), "bad guard"),
])
def test_invalid_declarations_fail_at_build_time(bad, msg):
    with pytest.raises(MachineDefinitionError, match=msg):
        door(**bad)


def test_exclusive_groups_and_else_make_guards_deterministic():
    tr = [T("A", "GO", "B", "red"), T("A", "GO", "C", "green"), T("A", "GO", "D", "else"),
          T("B", "BACK", "A"), T("C", "BACK", "A"), T("D", "BACK", "A")]
    with pytest.raises(MachineDefinitionError):
        Machine("light", "1", "ABCD", "A", [], tr)
    m = Machine("light", "1", "ABCD", "A", [], tr, exclusive=[{"red", "green"}])
    assert m.step("A", "GO", {"red": True, "green": False})[0] == "B"
    assert m.step("A", "GO", {"red": False, "green": False})[0] == "D"
    with pytest.raises(AmbiguousTransition):  # the declaration promised exclusivity; the runtime checks it
        m.step("A", "GO", {"red": True, "green": True})


def test_guard_predicates_take_precedence_over_context_facts():
    m = door(guards={"locked": lambda c: c["keys"] == 0})
    assert m.step("CLOSED", "OPEN", {"keys": 1})[0] == "OPENED"
    assert m.step("CLOSED", "OPEN", {"keys": 0})[0] == "CLOSED"


def test_parse_guard():
    assert parse_guard(None) is None and parse_guard("") is None
    assert parse_guard("else") == ()
    assert parse_guard("a & !b") == (("a", True), ("b", False))
    with pytest.raises(MachineDefinitionError):
        parse_guard("a & !a")


def test_seeded_random_runs_are_reproducible():
    m = door()
    def walk(seed):
        rng, s, path = random.Random(seed), m.initial, []
        for _ in range(50):
            options = [e for e in m.events if m.transitions_from(s, e)]
            if not options:
                break
            ev = rng.choice(options)
            s, t = m.step(s, ev, {"locked": rng.random() < 0.3})
            path.append((t.src, t.event, t.dst))
        return path
    assert walk(7) == walk(7) and walk(7) != walk(8)


def test_renderers_and_audit_record():
    m = door()
    md = m.to_mermaid()
    assert md.startswith("stateDiagram-v2\n    [*] --> CLOSED")
    assert "CLOSED --> OPENED : OPEN [!locked]" in md and "BROKEN --> [*]" in md
    assert "CLOSED --> CLOSED" not in md and "CLOSED --> CLOSED" in m.to_mermaid(include_self_loops=True)
    table = m.to_table()
    assert table.splitlines()[0] == "| From | Event | Guard | To | Action | Rule / policy |"
    assert "| CLOSED | OPEN | !locked | OPENED | — | R1 |" in table
    new, fired, rec = m.step_and_record("CLOSED", "OPEN", {"locked": False}, subject_id="door-1",
                                        occurred_at=datetime(2026, 11, 20, 10), borrower_id="B1")
    assert new == "OPENED"
    p = rec["payload"]
    assert (p["machine"], p["version"], p["from"], p["event"], p["guard"], p["to"], p["rule"]) == \
        ("door", "1", "CLOSED", "OPEN", "!locked", "OPENED", "R1")
    assert rec["event_type"] == "fsm.transition" and rec["provenance"] == "SIMULATED"
    assert set(rec) >= {"event_type", "source", "provenance", "occurred_at", "dedupe_key", "borrower_id", "payload"}
    again = m.record(fired, subject_id="door-1", occurred_at=datetime(2026, 11, 20, 10), borrower_id="B1")
    assert again["dedupe_key"] == rec["dedupe_key"]  # stable: replays dedupe
    assert m.allows("CLOSED", "BROKEN") and not m.allows("BROKEN", "CLOSED")
    assert m.fingerprint == door().fingerprint != door(transitions=list(m.transitions) + [T("OPENED", "SMASH", "BROKEN")]).fingerprint
