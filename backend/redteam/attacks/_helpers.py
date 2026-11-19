"""Small helpers shared by attack modules (registers no attacks)."""
from __future__ import annotations

import re
import threading
from typing import Any, Callable, List, Tuple

from kairos_backend.ledger import Ledger, ReserveResult


class SetupError(RuntimeError):
    """The attack could not build its scenario — reported as ERROR, never as PASS."""


def must(cond: Any, msg: str) -> None:
    if not cond:
        raise SetupError(msg)


def dr(res: ReserveResult) -> Tuple[str, str]:
    return res.decision.decision, res.decision.reason


def race(n: int, fn: Callable[[int], Any], ledger: Ledger) -> List[Any]:
    """Run fn(0..n-1) in n threads released together by a barrier. Each thread closes its own
    SQLite connections. Exceptions are re-raised in the caller."""
    out: List[Any] = [None] * n
    errors: List[BaseException] = []
    barrier = threading.Barrier(n)

    def worker(i: int) -> None:
        try:
            barrier.wait()
            out[i] = fn(i)
        except BaseException as e:  # pragma: no cover - surfaced below
            errors.append(e)
        finally:
            ledger.close()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    if errors:
        raise errors[0]
    return out


def is_4xx(status: int) -> bool:
    return 400 <= status < 500


PHONE_RE = re.compile(r"\+?\d[\d\s-]{8,}\d")


def pii_hits(text: Any, name: str, phone: str) -> List[str]:
    """Which identity markers appear in `text` (the values themselves are never reported)."""
    if text is None:
        return []
    s = str(text)
    digits = re.sub(r"\D", "", phone)
    hits = []
    if name and name.lower() in s.lower():
        hits.append("full_name")
    if phone in s or (digits and digits[-10:] in re.sub(r"\D", "", s)):
        hits.append("phone")
    return hits
