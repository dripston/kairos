"""KAIROS_FSM_STRICT=1: the database itself refuses touch status changes the k06.touch machine does not declare.

Off by default (behaviour unchanged). When the environment variable is set to 1 as a Ledger opens its
file, two SQLite triggers are installed on `touches`:

  * INSERT  — a new row must be a state PROPOSED --RESERVE--> can reach (RESERVED / PENDING_APPROVAL)
  * UPDATE  — a status change OLD -> NEW must be a declared transition (any event, any guard)

A violation aborts the statement with "KAIROS_FSM_STRICT: undeclared touch transition" (an
sqlite3.IntegrityError), so the surrounding transaction rolls back. Triggers sit in the database, so
they also catch writers outside ledger.py (HITL, voice, channel adapters). The allowed pairs come from
the k06.touch+hitl declaration (a superset of k06.touch). When the variable is not set, opening a
Ledger drops the triggers again, so a strict test run never leaves a production database strict.
"""
from __future__ import annotations

import os

from .machines import TOUCH_HITL

ENV = "KAIROS_FSM_STRICT"
TRIGGERS = ("fsm_strict_touch_insert", "fsm_strict_touch_update")


def enabled() -> bool:
    return os.environ.get(ENV, "").strip() == "1"


def sync_strict_mode(ledger) -> bool:
    """Install (KAIROS_FSM_STRICT=1) or remove the triggers on this ledger's database. Returns enabled()."""
    c = ledger._conn()
    if not enabled():
        for t in TRIGGERS:
            c.execute(f"DROP TRIGGER IF EXISTS {t}")
        return False
    pairs = TOUCH_HITL.allowed_pairs()
    born = sorted({dst for src, dst in pairs if src == "PROPOSED" and dst != "REFUSED"})
    allowed = " OR ".join(f"(OLD.status='{s}' AND NEW.status='{d}')" for s, d in pairs if s != "PROPOSED")
    c.execute("BEGIN IMMEDIATE")
    try:
        for t in TRIGGERS:
            c.execute(f"DROP TRIGGER IF EXISTS {t}")
        c.execute(f"""CREATE TRIGGER fsm_strict_touch_insert BEFORE INSERT ON touches
            WHEN NEW.status NOT IN ({",".join(f"'{s}'" for s in born)})
            BEGIN SELECT RAISE(ABORT, 'KAIROS_FSM_STRICT: undeclared touch transition (insert)'); END""")
        c.execute(f"""CREATE TRIGGER fsm_strict_touch_update BEFORE UPDATE OF status ON touches
            WHEN OLD.status <> NEW.status AND NOT ({allowed})
            BEGIN SELECT RAISE(ABORT, 'KAIROS_FSM_STRICT: undeclared touch transition'); END""")
        c.execute("COMMIT")
    except BaseException:
        c.execute("ROLLBACK")
        raise
    return True
