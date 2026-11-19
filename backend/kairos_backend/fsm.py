"""A tiny declarative finite-state-machine toolkit (stdlib only, Python 3.9).

Every agentic component in Kairos is declared as a deterministic FSM: named states, a closed
event alphabet, guards and transitions. Code decides transitions; an LLM may only *classify*
(produce an event or a fact a guard reads) or *phrase* (words said inside a state). It never
picks the next state.

    m = Machine("k06.touch", "1", states=[...], initial="PROPOSED", terminal=[...],
                transitions=[Transition("RESERVED", "BEGIN_DISPATCH", "DISPATCHING", guard="gate_send",
                                        rule="K06 send-time re-check")])
    new_state, fired = m.step("RESERVED", "BEGIN_DISPATCH", {"gate_send": True})

Validation runs when a Machine is built (so a bad declaration fails at import):
  * every state is reachable from `initial`
  * every non-terminal state has an exit (a transition to a different state)
  * terminal states have no exit (self-loops are allowed: an ignored or idempotent event)
  * a (state, event) pair with more than one transition needs guards that are provably mutually
    exclusive (see below) — otherwise the machine would be nondeterministic
  * transitions only use declared states and events

Guards are small boolean expressions over named facts: ``"a"``, ``"!a"``, ``"a & !b & c"`` or
``"else"`` (fires only when no sibling guard holds). Each name is resolved, in order, from the
Machine's ``guards`` dict (a predicate ``ctx -> bool``) or from ``ctx[name]`` (a bool fact).
A missing fact is an error, never a silent False (fail closed).

Two guards are *provably exclusive* when one contains a literal and the other its negation
(``a & b`` vs ``!a``), or when each contains a different member of a declared ``exclusive``
group (enum-like facts, e.g. ``{"answered", "retry_left", "retries_exhausted"}``). The runtime
re-checks it: if two guards of one (state, event) are true at once, step() raises
AmbiguousTransition instead of picking one.

step() is pure: it reads only (state, event, ctx) and the declaration. ``record()`` turns a fired
transition into a CRM-envelope-style audit record (machine, version, from, event, guard, to, rule).
"""
from __future__ import annotations

import hashlib
import json
from collections import OrderedDict, deque
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

__all__ = ["Transition", "Machine", "FsmError", "MachineDefinitionError", "UnknownState", "UnknownEvent",
           "NoTransition", "AmbiguousTransition", "GuardError", "ELSE", "parse_guard"]

ELSE = "else"
FSM_EVENT_TYPE = "fsm.transition"


# ---------- errors ----------
class FsmError(Exception):
    """Base class for every FSM error."""


class MachineDefinitionError(FsmError):
    """The declaration itself is invalid (raised when the Machine is built)."""


class UnknownState(FsmError):
    pass


class UnknownEvent(FsmError):
    pass


class NoTransition(FsmError):
    """The event is known but no transition fires from this state (none declared, or no guard holds)."""


class AmbiguousTransition(FsmError):
    """More than one guarded transition holds at once: the declaration's exclusivity was violated."""


class GuardError(FsmError):
    """A guard needs a fact that the context does not provide."""


# ---------- guards ----------
Literal = Tuple[str, bool]  # (name, positive)


def parse_guard(expr: Optional[str]) -> Optional[Tuple[Literal, ...]]:
    """'a & !b' -> (('a', True), ('b', False)); None/'' -> None (unguarded); 'else' -> ()."""
    if expr is None or not str(expr).strip():
        return None
    expr = str(expr).strip()
    if expr == ELSE:
        return ()
    lits: List[Literal] = []
    for part in expr.split("&"):
        p = part.strip()
        positive = not p.startswith("!")
        name = p[1:].strip() if not positive else p
        if not name or not all(ch.isalnum() or ch in "_." for ch in name):
            raise MachineDefinitionError(f"bad guard literal {part!r} in {expr!r}")
        lits.append((name, positive))
    names = [n for n, _ in lits]
    if len(set(names)) != len(names):
        raise MachineDefinitionError(f"guard {expr!r} repeats a fact")
    return tuple(lits)


@dataclass(frozen=True)
class Transition:
    """One declared transition. `rule` names the policy / code rule that justifies it;
    `action` names the side effect the implementation performs when it fires."""
    src: str
    event: str
    dst: str
    guard: Optional[str] = None
    action: Optional[str] = None
    rule: Optional[str] = None
    note: str = ""

    @property
    def literals(self) -> Optional[Tuple[Literal, ...]]:
        return parse_guard(self.guard)

    @property
    def is_else(self) -> bool:
        return self.guard is not None and self.guard.strip() == ELSE

    @property
    def self_loop(self) -> bool:
        return self.src == self.dst

    def as_dict(self) -> dict:
        return {"from": self.src, "event": self.event, "guard": self.guard, "to": self.dst,
                "action": self.action, "rule": self.rule}


def _exclusive(a: Tuple[Literal, ...], b: Tuple[Literal, ...], groups: Sequence[frozenset]) -> bool:
    for name, pos in a:
        if (name, not pos) in b:
            return True
    pa = {n for n, p in a if p}
    pb = {n for n, p in b if p}
    for g in groups:
        x, y = pa & g, pb & g
        if x and y and not (x & y):
            return True
    return False


# ---------- the machine ----------
class Machine:
    def __init__(self, name: str, version: str, states: Iterable[str], initial: str, terminal: Iterable[str],
                 transitions: Iterable[Transition], *, events: Optional[Iterable[str]] = None,
                 guards: Optional[Mapping[str, Callable[[Mapping[str, Any]], bool]]] = None,
                 exclusive: Iterable[Iterable[str]] = (), description: str = "",
                 enforced_by: Sequence[str] = (), proven_by: Sequence[str] = (),
                 state_notes: Optional[Mapping[str, str]] = None, llm: str = "none",
                 source: str = ""):
        self.name = name
        self.version = str(version)
        self.states: Tuple[str, ...] = tuple(OrderedDict.fromkeys(states))
        self.initial = initial
        self.terminal = frozenset(terminal)
        self.transitions: Tuple[Transition, ...] = tuple(transitions)
        used_events = list(OrderedDict.fromkeys(t.event for t in self.transitions))
        self.events: Tuple[str, ...] = tuple(OrderedDict.fromkeys(list(events) if events is not None else used_events))
        self.guards: Dict[str, Callable[[Mapping[str, Any]], bool]] = dict(guards or {})
        self.exclusive: Tuple[frozenset, ...] = tuple(frozenset(g) for g in exclusive)
        self.description = description
        self.enforced_by = tuple(enforced_by)
        self.proven_by = tuple(proven_by)
        self.state_notes = dict(state_notes or {})
        self.llm = llm
        self.source = source
        self._by_key: Dict[Tuple[str, str], Tuple[Transition, ...]] = {}
        for t in self.transitions:
            self._by_key.setdefault((t.src, t.event), ())
            self._by_key[(t.src, t.event)] += (t,)
        self.validate()

    # ----- validation -----
    def validate(self) -> None:
        errs: List[str] = []
        st = set(self.states)
        if len(st) != len(self.states):
            errs.append("duplicate states")
        if self.initial not in st:
            errs.append(f"initial {self.initial!r} is not a state")
        for s in self.terminal - st:
            errs.append(f"terminal {s!r} is not a state")
        ev = set(self.events)
        for t in self.transitions:
            for s in (t.src, t.dst):
                if s not in st:
                    errs.append(f"{t.src} --{t.event}--> {t.dst}: unknown state {s!r}")
            if t.event not in ev:
                errs.append(f"{t.src} --{t.event}--> {t.dst}: undeclared event {t.event!r}")
            try:
                t.literals
            except MachineDefinitionError as e:
                errs.append(str(e))
        if errs:
            raise MachineDefinitionError(f"{self.name}: " + "; ".join(errs))
        # reachability
        seen, todo = {self.initial}, deque([self.initial])
        while todo:
            s = todo.popleft()
            for t in self.transitions:
                if t.src == s and t.dst not in seen:
                    seen.add(t.dst)
                    todo.append(t.dst)
        for s in self.states:
            if s not in seen:
                errs.append(f"state {s!r} is unreachable from {self.initial!r}")
        # exits
        for s in self.states:
            exits = [t for t in self.transitions if t.src == s and t.dst != s]
            if s in self.terminal and exits:
                errs.append(f"terminal state {s!r} has exits: " + ", ".join(f"{t.event}->{t.dst}" for t in exits))
            if s not in self.terminal and not exits:
                errs.append(f"non-terminal state {s!r} has no exit")
        # determinism
        for (src, event), group in self._by_key.items():
            if len(group) == 1:
                continue
            elses = [t for t in group if t.is_else]
            plain = [t for t in group if t.guard is None]
            if plain:
                errs.append(f"({src}, {event}) has {len(group)} transitions and an unguarded one: ambiguous")
                continue
            if len(elses) > 1:
                errs.append(f"({src}, {event}) has more than one 'else'")
            guarded = [t for t in group if not t.is_else]
            gs = [t.guard.replace(" ", "") for t in guarded]
            if len(set(gs)) != len(gs):
                errs.append(f"({src}, {event}) repeats a guard")
            for i, a in enumerate(guarded):
                for b in guarded[i + 1:]:
                    if not _exclusive(a.literals, b.literals, self.exclusive):
                        errs.append(f"({src}, {event}): guards {a.guard!r} and {b.guard!r} are not provably exclusive")
        if errs:
            raise MachineDefinitionError(f"{self.name}: " + "; ".join(errs))

    # ----- queries -----
    def transitions_from(self, state: str, event: Optional[str] = None) -> Tuple[Transition, ...]:
        if event is not None:
            return self._by_key.get((state, event), ())
        return tuple(t for t in self.transitions if t.src == state)

    def allows(self, src: str, dst: str) -> bool:
        """Is there any declared transition src -> dst (any event, any guard)?"""
        return any(t.src == src and t.dst == dst for t in self.transitions)

    def allowed_pairs(self, include_self_loops: bool = False) -> List[Tuple[str, str]]:
        out = OrderedDict()
        for t in self.transitions:
            if include_self_loops or t.src != t.dst:
                out[(t.src, t.dst)] = True
        return list(out)

    @property
    def fingerprint(self) -> str:
        """Stable hash of the declaration (states, events, transitions): changes iff the machine does."""
        body = {"name": self.name, "version": self.version, "states": list(self.states), "initial": self.initial,
                "terminal": sorted(self.terminal), "events": list(self.events),
                "transitions": [t.as_dict() for t in self.transitions]}
        return hashlib.sha1(json.dumps(body, sort_keys=True).encode()).hexdigest()[:12]

    # ----- execution -----
    def _fact(self, name: str, ctx: Mapping[str, Any]) -> bool:
        if name in self.guards:
            return bool(self.guards[name](ctx))
        if name in ctx:
            v = ctx[name]
            if not isinstance(v, bool):
                raise GuardError(f"{self.name}: fact {name!r} must be a bool, got {type(v).__name__}")
            return v
        raise GuardError(f"{self.name}: guard needs fact {name!r}, not in context")

    def holds(self, t: Transition, ctx: Mapping[str, Any]) -> bool:
        lits = t.literals
        if lits is None:
            return True
        return all(self._fact(n, ctx) == pos for n, pos in lits)

    def step(self, state: str, event: str, ctx: Optional[Mapping[str, Any]] = None) -> Tuple[str, Transition]:
        """Pure and deterministic: (state, event, ctx) -> (new_state, the transition that fired)."""
        ctx = ctx or {}
        if state not in self.states:
            raise UnknownState(f"{self.name}: unknown state {state!r}")
        if event not in self.events:
            raise UnknownEvent(f"{self.name}: unknown event {event!r}")
        group = self._by_key.get((state, event), ())
        if not group:
            raise NoTransition(f"{self.name}: no transition for {event!r} in state {state!r}")
        if len(group) == 1 and group[0].guard is None:
            return group[0].dst, group[0]
        fired = [t for t in group if not t.is_else and self.holds(t, ctx)]
        if len(fired) > 1:
            raise AmbiguousTransition(f"{self.name}: ({state}, {event}) guards "
                                      f"{[t.guard for t in fired]} all hold")
        if fired:
            return fired[0].dst, fired[0]
        for t in group:
            if t.is_else:
                return t.dst, t
        raise NoTransition(f"{self.name}: no guard holds for {event!r} in state {state!r} "
                           f"(guards: {[t.guard for t in group]})")

    def run(self, events: Iterable[Tuple[str, Mapping[str, Any]]], state: Optional[str] = None
            ) -> Tuple[str, List[Transition]]:
        """Fold a sequence of (event, ctx) from `state` (default: initial)."""
        s = self.initial if state is None else state
        path = []
        for ev, ctx in events:
            s, t = self.step(s, ev, ctx)
            path.append(t)
        return s, path

    def record(self, fired: Transition, *, subject_id: str, occurred_at: datetime,
               provenance: str = "SIMULATED", borrower_id: Optional[str] = None, seq: Optional[int] = None,
               detail: Optional[Mapping[str, Any]] = None) -> dict:
        """CRM-envelope-style audit record for one fired transition (same keys as contracts.crm_envelope).
        dedupe_key is stable: the same transition of the same subject at the same time is one record."""
        payload = {"machine": self.name, "version": self.version, "fingerprint": self.fingerprint,
                   "from": fired.src, "event": fired.event, "guard": fired.guard, "to": fired.dst,
                   "rule": fired.rule, "action": fired.action, "subject_id": subject_id}
        if detail:
            payload["detail"] = dict(detail)
        key = "|".join(str(x) for x in (self.name, self.version, subject_id, occurred_at.isoformat(), fired.src,
                                         fired.event, fired.dst, "" if seq is None else seq))
        return {"event_type": FSM_EVENT_TYPE, "source": f"kairos.fsm.{self.name}", "provenance": provenance,
                "occurred_at": occurred_at.isoformat(), "dedupe_key": "fsm:" + hashlib.sha1(key.encode()).hexdigest()[:20],
                "borrower_id": borrower_id, "loan_id": None, "campaign_id": None, "touch_id": None,
                "payload": payload}

    def step_and_record(self, state: str, event: str, ctx: Optional[Mapping[str, Any]] = None, **record_kw
                        ) -> Tuple[str, Transition, dict]:
        new, fired = self.step(state, event, ctx)
        return new, fired, self.record(fired, **record_kw)

    # ----- renderers -----
    @staticmethod
    def _label(t: Transition) -> str:
        lab = t.event + (f" [{t.guard}]" if t.guard else "")
        return lab.replace(":", " ").replace(";", ",")

    def to_mermaid(self, include_self_loops: bool = False) -> str:
        """Mermaid stateDiagram-v2. Self-loops (ignored / idempotent events) are left out by default:
        they are in the table."""
        lines = ["stateDiagram-v2", f"    [*] --> {self.initial}"]
        for s in self.states:
            note = self.state_notes.get(s)
            if note:
                lines.append(f"    {s} : {note.replace(':', ' ')}")
        merged: "OrderedDict[Tuple[str, str], List[str]]" = OrderedDict()
        for t in self.transitions:
            if t.self_loop and not include_self_loops:
                continue
            merged.setdefault((t.src, t.dst), []).append(self._label(t))
        for (src, dst), labels in merged.items():
            lines.append(f"    {src} --> {dst} : {' / '.join(labels)}")
        for s in self.states:
            if s in self.terminal:
                lines.append(f"    {s} --> [*]")
        return "\n".join(lines)

    def to_table(self, include_self_loops: bool = True) -> str:
        """Markdown transition table: from | event | guard | to | action | rule."""
        def cell(v: Optional[str]) -> str:
            return (v or "").replace("|", "\\|") or "—"
        rows = ["| From | Event | Guard | To | Action | Rule / policy |", "|---|---|---|---|---|---|"]
        for t in self.transitions:
            if t.self_loop and not include_self_loops:
                continue
            rows.append(f"| {t.src} | {t.event} | {cell(t.guard)} | {t.dst} | {cell(t.action)} | {cell(t.rule)} |")
        return "\n".join(rows)

    def summary(self) -> dict:
        return {"name": self.name, "version": self.version, "fingerprint": self.fingerprint,
                "states": len(self.states), "events": len(self.events), "transitions": len(self.transitions),
                "terminal": sorted(self.terminal)}

    def __repr__(self) -> str:
        return f"Machine({self.name!r}, v{self.version}, {len(self.states)} states, {len(self.transitions)} transitions)"
