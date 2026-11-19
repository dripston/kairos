"""SQLite storage for the Kairos CRM slice (K05 / K14).

Provider delivery, borrower intent and verified payment live in separate
tables on purpose: a delivery receipt never implies a reply, and a borrower's
"I paid" never changes a loan until the ledger confirms it.
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional, Union

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = Path(os.environ.get("KAIROS_DB", REPO_ROOT / "var" / "kairos.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS borrowers (
  borrower_id   TEXT PRIMARY KEY,
  display_name  TEXT NOT NULL,
  dpd           INTEGER NOT NULL,
  persona       TEXT,
  language      TEXT,
  consent_voice INTEGER NOT NULL DEFAULT 0,   -- voice (IVR / voicebot) consent on file
  consent_version TEXT,
  synthetic     INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS loans (
  loan_id         TEXT PRIMARY KEY,
  borrower_id     TEXT NOT NULL REFERENCES borrowers(borrower_id),
  label           TEXT NOT NULL,
  product         TEXT NOT NULL,
  emi_amount      INTEGER NOT NULL,
  overdue_amount  INTEGER NOT NULL,
  dpd             INTEGER NOT NULL,
  pos             INTEGER,
  tos             INTEGER,
  bucket          TEXT,
  source          TEXT,            -- dataset row this loan came from
  source_dpd      INTEGER,         -- DPD as published in that dataset
  status          TEXT NOT NULL CHECK (status IN ('OPEN', 'CLEARED')),
  status_reason   TEXT,
  updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS campaigns (
  campaign_id  TEXT PRIMARY KEY,
  name         TEXT NOT NULL
);

-- One row per planned or sent outbound contact. Touch IDs join provider
-- callbacks and borrower replies back to the send that caused them.
CREATE TABLE IF NOT EXISTS touches (
  touch_id             TEXT PRIMARY KEY,
  borrower_id          TEXT NOT NULL REFERENCES borrowers(borrower_id),
  loan_id              TEXT REFERENCES loans(loan_id),
  campaign_id          TEXT NOT NULL REFERENCES campaigns(campaign_id),
  channel              TEXT NOT NULL CHECK (channel IN ('SMS', 'WHATSAPP', 'EMAIL', 'IVR', 'VOICE')),
  purpose              TEXT NOT NULL,
  template_id          TEXT,
  provider_message_id  TEXT UNIQUE,
  scheduled_for        TEXT NOT NULL,
  status               TEXT NOT NULL CHECK (status IN ('PENDING', 'SENT', 'CANCELED', 'BLOCKED')),
  status_reason        TEXT,
  updated_at           TEXT NOT NULL
);

-- The single event log. Every input (provider callback, borrower message,
-- adapter output, ledger webhook, Kairos's own side effects) lands here
-- through kairos.ingest.ingest_event. dedupe_key is the idempotency key.
CREATE TABLE IF NOT EXISTS events (
  seq                 INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id            TEXT NOT NULL UNIQUE,
  dedupe_key          TEXT NOT NULL UNIQUE,
  event_type          TEXT NOT NULL,
  source              TEXT NOT NULL,
  provenance          TEXT NOT NULL CHECK (provenance IN ('LIVE', 'REPLAY', 'SIMULATED')),
  fixture_of          TEXT,
  occurred_at         TEXT NOT NULL,
  received_at         TEXT NOT NULL,
  borrower_id         TEXT NOT NULL REFERENCES borrowers(borrower_id),
  loan_id             TEXT REFERENCES loans(loan_id),
  campaign_id         TEXT REFERENCES campaigns(campaign_id),
  touch_id            TEXT REFERENCES touches(touch_id),
  causation_event_id  TEXT REFERENCES events(event_id),
  payload_json        TEXT NOT NULL
);

-- Duplicates are recorded for audit, never projected onto the timeline.
CREATE TABLE IF NOT EXISTS duplicate_events (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  dedupe_key          TEXT NOT NULL,
  attempted_event_id  TEXT NOT NULL,
  original_event_id   TEXT NOT NULL REFERENCES events(event_id),
  event_type          TEXT NOT NULL,
  borrower_id         TEXT,
  received_at         TEXT NOT NULL
);

-- Provider delivery: what the channel provider says happened to a send.
CREATE TABLE IF NOT EXISTS provider_deliveries (
  touch_id             TEXT PRIMARY KEY REFERENCES touches(touch_id),
  provider             TEXT NOT NULL,
  provider_message_id  TEXT NOT NULL,
  status               TEXT NOT NULL CHECK (status IN ('SENT', 'DELIVERED', 'READ', 'FAILED')),
  read_status          TEXT NOT NULL CHECK (read_status IN ('UNKNOWN', 'READ')),
  status_at            TEXT NOT NULL,
  last_event_id        TEXT NOT NULL REFERENCES events(event_id)
);

CREATE TABLE IF NOT EXISTS borrower_messages (
  event_id             TEXT PRIMARY KEY REFERENCES events(event_id),
  borrower_id          TEXT NOT NULL REFERENCES borrowers(borrower_id),
  touch_id             TEXT REFERENCES touches(touch_id),
  channel              TEXT NOT NULL,
  provider_message_id  TEXT NOT NULL,
  text                 TEXT NOT NULL,
  received_at          TEXT NOT NULL
);

-- Borrower intent: what the borrower means, as classified from a message.
CREATE TABLE IF NOT EXISTS borrower_intents (
  event_id          TEXT PRIMARY KEY REFERENCES events(event_id),
  borrower_id       TEXT NOT NULL REFERENCES borrowers(borrower_id),
  loan_id           TEXT REFERENCES loans(loan_id),
  campaign_id       TEXT REFERENCES campaigns(campaign_id),
  touch_id          TEXT REFERENCES touches(touch_id),
  source_event_id   TEXT NOT NULL REFERENCES events(event_id),   -- the message or call it came from
  intent            TEXT NOT NULL CHECK (intent IN ('NEEDS_TIME', 'CLAIMS_PAID', 'CALLBACK', 'HARDSHIP', 'STOP', 'OTHER')),
  confidence        REAL,
  agreed_date       TEXT,
  classified_by     TEXT NOT NULL,
  occurred_at       TEXT NOT NULL
);

-- The four agent outputs the supervisor reads (K05 summary, K09 channel/time,
-- K21 propensity + intensity, K22 template), one row per agent per run.
CREATE TABLE IF NOT EXISTS agent_outputs (
  event_id     TEXT NOT NULL REFERENCES events(event_id),
  agent        TEXT NOT NULL,
  task_id      TEXT NOT NULL,
  owner        TEXT NOT NULL,
  run_id       TEXT NOT NULL,
  is_fixture   INTEGER NOT NULL,
  headline     TEXT NOT NULL,
  output_json  TEXT NOT NULL,
  PRIMARY KEY (event_id, agent)
);

CREATE TABLE IF NOT EXISTS supervisor_decisions (
  event_id          TEXT PRIMARY KEY REFERENCES events(event_id),
  borrower_id       TEXT NOT NULL REFERENCES borrowers(borrower_id),
  campaign_id       TEXT REFERENCES campaigns(campaign_id),
  trigger_event_id  TEXT NOT NULL REFERENCES events(event_id),
  inputs_event_id   TEXT REFERENCES events(event_id),   -- the agent.outputs event it read
  decision          TEXT NOT NULL CHECK (decision IN ('SEND', 'WAIT', 'CANCEL', 'HANDOFF')),
  scope_loan_id     TEXT REFERENCES loans(loan_id),
  reason            TEXT NOT NULL,
  policy_version    TEXT NOT NULL,
  expires_at        TEXT,
  decided_by        TEXT NOT NULL,
  occurred_at       TEXT NOT NULL
);

-- Sarvam IVR / voicebot call dispositions (provider side; never an intent by itself).
CREATE TABLE IF NOT EXISTS call_outcomes (
  event_id     TEXT PRIMARY KEY REFERENCES events(event_id),
  borrower_id  TEXT NOT NULL REFERENCES borrowers(borrower_id),
  touch_id     TEXT REFERENCES touches(touch_id),
  provider     TEXT NOT NULL,
  flow         TEXT NOT NULL CHECK (flow IN ('IVR', 'VOICEBOT')),
  call_id      TEXT NOT NULL,
  disposition  TEXT NOT NULL CHECK (disposition IN ('ANSWERED', 'NO_ANSWER', 'BUSY', 'FAILED', 'COMPLETED', 'DECLINED')),
  dtmf         TEXT,
  attempts     INTEGER,
  exit_reason  TEXT,
  duration_s   INTEGER,
  transcript_json TEXT,           -- [{"speaker": "bot"|"borrower", "text": ...}]
  occurred_at  TEXT NOT NULL
);

-- A borrower's claim of payment. Never changes a loan by itself.
CREATE TABLE IF NOT EXISTS payment_claims (
  claim_id              TEXT PRIMARY KEY,
  event_id              TEXT NOT NULL UNIQUE REFERENCES events(event_id),
  borrower_id           TEXT NOT NULL REFERENCES borrowers(borrower_id),
  loan_id               TEXT NOT NULL REFERENCES loans(loan_id),
  intent_event_id       TEXT REFERENCES events(event_id),
  amount                INTEGER,
  claimed_reference     TEXT,
  status                TEXT NOT NULL CHECK (status IN ('VERIFICATION_PENDING', 'VERIFIED', 'REJECTED')),
  opened_at             TEXT NOT NULL,
  resolved_at           TEXT,
  resolved_by_event_id  TEXT REFERENCES events(event_id)
);

-- Verified payment: only the ledger (D7 settlement + LMS allocation) writes here.
CREATE TABLE IF NOT EXISTS verified_payments (
  event_id          TEXT PRIMARY KEY REFERENCES events(event_id),
  borrower_id       TEXT NOT NULL REFERENCES borrowers(borrower_id),
  loan_id           TEXT NOT NULL REFERENCES loans(loan_id),
  amount            INTEGER NOT NULL,
  ledger_ref        TEXT NOT NULL UNIQUE,
  settled_at        TEXT NOT NULL,
  lms_allocated     INTEGER NOT NULL,
  matched_claim_id  TEXT REFERENCES payment_claims(claim_id)
);

-- Exactly one timeline entry per accepted event (event_id is UNIQUE), so a
-- duplicate can never add a row even if a handler misbehaves.
CREATE TABLE IF NOT EXISTS timeline_entries (
  entry_id     INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id     TEXT NOT NULL UNIQUE REFERENCES events(event_id),
  borrower_id  TEXT NOT NULL REFERENCES borrowers(borrower_id),
  occurred_at  TEXT NOT NULL,
  category     TEXT NOT NULL,
  title        TEXT NOT NULL,
  detail       TEXT,
  loan_id      TEXT,
  campaign_id  TEXT,
  touch_id     TEXT,
  provenance   TEXT NOT NULL,
  fixture_of   TEXT
);

CREATE INDEX IF NOT EXISTS idx_timeline_borrower ON timeline_entries (borrower_id, occurred_at, entry_id);
CREATE INDEX IF NOT EXISTS idx_events_borrower ON events (borrower_id, occurred_at, seq);

CREATE TABLE IF NOT EXISTS meta (
  key    TEXT PRIMARY KEY,
  value  TEXT NOT NULL
);
"""

SCHEMA_VERSION = 3

# Child tables first so foreign keys never block a reset.
_RESET_ORDER = [
    "timeline_entries", "duplicate_events", "verified_payments", "payment_claims", "call_outcomes",
    "supervisor_decisions", "agent_outputs", "borrower_intents", "borrower_messages",
    "provider_deliveries", "events", "touches", "campaigns", "loans", "borrowers", "meta",
]


def connect(path: Optional[Union[str, Path]] = None) -> sqlite3.Connection:
    """Open the database, creating the schema if needed.

    Autocommit mode: writes are grouped explicitly with `transaction()`.
    """
    target = str(path if path is not None else DEFAULT_DB_PATH)
    if target != ":memory:":
        Path(target).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target, isolation_level=None, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if target != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    if conn.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
        # Demo database: on a schema change, rebuild instead of migrating.
        conn.execute("PRAGMA foreign_keys = OFF")
        for table in _RESET_ORDER:
            conn.execute(f"DROP TABLE IF EXISTS {table}")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(SCHEMA)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[None]:
    """Run a block atomically. Nested calls join the outer transaction."""
    if conn.in_transaction:
        yield
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def reset(conn: sqlite3.Connection) -> None:
    with transaction(conn):
        for table in _RESET_ORDER:
            conn.execute(f"DELETE FROM {table}")
        conn.execute("DELETE FROM sqlite_sequence")


def get_meta(conn: sqlite3.Connection, key: str, default: Optional[str] = None) -> Optional[str]:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
