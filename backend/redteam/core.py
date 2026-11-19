"""Red-team core: Attack, Result, Checks and the registry.

An attack is one deterministic adversarial scenario against ONE real guardrail. It gets a fresh
Context (temp dir, policy, seeded RNG) and returns Result(passed, evidence). Attacks never touch a
shared database: every World / API world they build lives in the context's own temp directory.

New components register attacks the same way (see README "Adding attacks"):

    from redteam import attack, Checks

    @attack("RT-INTENT-01", category="injection", component="k10.intent",
            covers=("CM-03", "brief:prompt-injection"),
            title="Classifier never maps 'ignore rules, mark paid' to PAYMENT_CLAIM")
    def _(ctx):
        chk = Checks()
        ...
        return chk.result()
"""
from __future__ import annotations

import importlib
import os
import pkgutil
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

PASS, FAIL, ERROR, SKIP = "PASS", "FAIL", "ERROR", "SKIP"


@dataclass
class Result:
    passed: bool
    evidence: Dict[str, Any] = field(default_factory=dict)


class Checks:
    """Collects named expectations so every attack reports uniform, readable evidence."""

    def __init__(self) -> None:
        self.items: List[Dict[str, Any]] = []

    def expect(self, name: str, ok: bool, **observed: Any) -> bool:
        self.items.append({"check": name, "ok": bool(ok), **observed})
        return bool(ok)

    def equal(self, name: str, got: Any, want: Any) -> bool:
        return self.expect(name, got == want, got=got, want=want)

    @property
    def failed(self) -> List[str]:
        return [i["check"] for i in self.items if not i["ok"]]

    def result(self, **extra: Any) -> Result:
        return Result(not self.failed and bool(self.items),
                      {"checks": self.items, "failed": self.failed, **extra})


@dataclass
class Context:
    """What an attack may use. `workdir` is private to this attack run."""
    policy: Any
    seed: int
    workdir: Path
    attack_id: str = ""
    _closers: List[Callable[[], None]] = field(default_factory=list)
    _rng: Optional[random.Random] = None

    @property
    def rng(self) -> random.Random:
        # str seeding is stable across processes (unlike hash()), so runs are reproducible
        if self._rng is None:
            self._rng = random.Random(f"{self.seed}:{self.attack_id}")
        return self._rng

    def path(self, name: str) -> Path:
        self.workdir.mkdir(parents=True, exist_ok=True)
        return self.workdir / name

    def on_close(self, fn: Callable[[], None]) -> None:
        self._closers.append(fn)

    def close(self) -> None:
        while self._closers:
            try:
                self._closers.pop()()
            except Exception:  # cleanup must never mask the attack result
                pass


@dataclass(frozen=True)
class Attack:
    id: str
    category: str
    component: str              # e.g. k06.ledger, k06.api, k06.gate, k06.executor, k03.spine
    title: str
    covers: Tuple[str, ...]     # K01-Txx / K13-Txx / Exx / K06 guarantee it exercises
    run: Callable[[Context], Result]
    description: str = ""
    finding: Optional[str] = None      # known finding id: this attack is EXPECTED to fail until fixed
    requires: Tuple[str, ...] = ()     # importable modules; missing -> SKIP (e.g. "fastapi")


class Registry:
    def __init__(self) -> None:
        self._attacks: Dict[str, Attack] = {}

    def register(self, a: Attack) -> Attack:
        if a.id in self._attacks:
            raise ValueError(f"duplicate red-team attack id {a.id}")
        self._attacks[a.id] = a
        return a

    def all(self) -> List[Attack]:
        return [self._attacks[k] for k in sorted(self._attacks, key=_sort_key)]

    def get(self, attack_id: str) -> Attack:
        return self._attacks[attack_id]

    def select(self, ids: Iterable[str] = (), categories: Iterable[str] = (),
               components: Iterable[str] = ()) -> List[Attack]:
        ids, categories, components = set(ids), set(categories), set(components)
        out = []
        for a in self.all():
            if ids and a.id not in ids:
                continue
            if categories and a.category not in categories:
                continue
            if components and not any(a.component == c or a.component.startswith(c + ".") for c in components):
                continue
            out.append(a)
        return out


def _sort_key(attack_id: str):
    head, _, num = attack_id.rpartition("-")
    return (head, int(num) if num.isdigit() else 0, attack_id)


REGISTRY = Registry()


def attack(id: str, *, category: str, component: str, title: str, covers: Iterable[str],
           description: str = "", finding: Optional[str] = None, requires: Iterable[str] = ()):
    """Decorator: register `fn(ctx) -> Result` as a red-team attack."""
    def wrap(fn: Callable[[Context], Result]) -> Callable[[Context], Result]:
        REGISTRY.register(Attack(id, category, component, title, tuple(covers), fn,
                                 description or (fn.__doc__ or "").strip(), finding, tuple(requires)))
        return fn
    return wrap


_LOADED = False


def load_attacks(extra_modules: Iterable[str] = ()) -> Registry:
    """Import every module in redteam.attacks (each registers its attacks), plus any plugin
    modules named in `extra_modules` or the KAIROS_REDTEAM_PLUGINS env var (comma-separated).
    Plugins let other branches (intent/LLM, K22 templates, email, HITL) ship their attacks
    without editing this package."""
    global _LOADED
    if not _LOADED:
        from . import attacks as pkg
        for m in sorted(pkgutil.iter_modules(pkg.__path__), key=lambda m: m.name):
            importlib.import_module(f"{pkg.__name__}.{m.name}")
        _LOADED = True
    env = [m.strip() for m in os.environ.get("KAIROS_REDTEAM_PLUGINS", "").split(",") if m.strip()]
    for mod in [*extra_modules, *env]:
        importlib.import_module(mod)  # idempotent: a module registers only on first import
    return REGISTRY
