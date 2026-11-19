"""Graceful start / shutdown for the API process (and anything else that dispatches).

    lc = Lifecycle(ledger, grace_s=None)    # grace: KAIROS_SHUTDOWN_GRACE_S, default 10 s
    lc.start()                              # accept dispatch work; start registered tick loops
    with lc.dispatch_slot():                # every dispatch runs inside a slot
        executor.dispatch(iid)
    lc.shutdown()                           # SIGTERM / SIGINT path (FastAPI lifespan calls it)

shutdown():
  1. stops accepting new dispatch work (dispatch_slot raises ShuttingDown -> 503 at the API);
  2. stops the tick loops (HITL expiry, voice follow-ups, churn tick, ...) and joins their threads;
  3. waits up to the grace period for in-flight dispatches to finish;
  4. any touch still DISPATCHING after that is marked UNCERTAIN — never re-sent: begin_dispatch only
     dispatches RESERVED touches, and Executor.resolve_uncertain asks the provider later (K06 rule);
  5. writes one SHUTDOWN audit row with the counts, checkpoints the WAL and closes every SQLite
     connection the Ledger opened (all threads).
It is idempotent (a second call returns the first result) and start() after shutdown() works again
(the Ledger reopens connections lazily), which is what a restart in the same process needs.
"""
from __future__ import annotations

import os
import threading
import time
from contextlib import contextmanager
from typing import Callable, Iterator, Optional

DEFAULT_GRACE_S = 10.0


class ShuttingDown(RuntimeError):
    """New dispatch work refused: the process is shutting down."""


def grace_from_env(default: float = DEFAULT_GRACE_S) -> float:
    try:
        return max(0.0, float(os.environ.get("KAIROS_SHUTDOWN_GRACE_S", default)))
    except ValueError:
        return default


class TickLoop:
    """A background loop calling `fn()` every `interval_s` until stopped. Stops between ticks, never mid-tick."""

    def __init__(self, name: str, fn: Callable[[], object], interval_s: float):
        self.name, self.fn, self.interval_s = name, fn, interval_s
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.ticks = 0
        self.errors = 0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=f"kairos-tick-{self.name}", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(self.interval_s):
            try:
                self.fn()
                self.ticks += 1
            except Exception:  # a failing tick must not kill the loop; it is retried next interval
                self.errors += 1

    def stop(self, timeout: float) -> bool:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
            return not self._thread.is_alive()
        return True


class Lifecycle:
    def __init__(self, ledger, grace_s: Optional[float] = None, clock: Callable[[], float] = time.monotonic):
        self.ledger = ledger
        self.grace_s = grace_from_env() if grace_s is None else grace_s
        self.clock = clock
        self.loops: list[TickLoop] = []
        self._cv = threading.Condition()
        self._inflight = 0
        self.accepting = True
        self.result: Optional[dict] = None
        self._lock = threading.Lock()

    # ---------- start ----------
    def add_loop(self, loop: TickLoop) -> TickLoop:
        self.loops.append(loop)
        return loop

    def start(self) -> None:
        with self._lock:
            self.accepting = True
            self.result = None
            for lp in self.loops:
                lp.start()

    # ---------- dispatch slots ----------
    @contextmanager
    def dispatch_slot(self) -> Iterator[None]:
        with self._cv:
            if not self.accepting:
                raise ShuttingDown("shutting down: no new dispatch work")
            self._inflight += 1
        try:
            yield
        finally:
            with self._cv:
                self._inflight -= 1
                self._cv.notify_all()

    @property
    def inflight(self) -> int:
        return self._inflight

    # ---------- shutdown ----------
    def shutdown(self, reason: str = "SIGTERM", grace_s: Optional[float] = None) -> dict:
        with self._lock:
            if self.result is not None:
                return self.result
            grace = self.grace_s if grace_s is None else grace_s
            with self._cv:
                self.accepting = False
                inflight_at_start = self._inflight
            loops = {lp.name: lp.stop(timeout=max(grace, 1.0)) for lp in self.loops}
            deadline = self.clock() + grace
            with self._cv:
                while self._inflight and self.clock() < deadline:
                    self._cv.wait(timeout=max(0.0, min(0.1, deadline - self.clock())))
                still = self._inflight
            marked = self.ledger.mark_inflight_uncertain("SHUTDOWN_GRACE_EXPIRED")
            counts = {"reason": reason, "grace_s": grace, "inflight_at_start": inflight_at_start,
                      "inflight_at_deadline": still, "finished_in_grace": inflight_at_start - still,
                      "marked_uncertain": len(marked), "uncertain_ids": marked, "loops_stopped": loops}
            with self.ledger.tx() as c:
                self.ledger._audit(c, "system", "SHUTDOWN", reason=reason, **{k: v for k, v in counts.items()
                                                                              if k != "reason"})
            self.ledger.close_all()
            self.result = counts
            return counts
