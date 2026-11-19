"""K17/K18 voice tables (same SQLite file as the ledger and the K03 spine).

These tables only ADD voice facts. They never replace a ledger fact: holds, STOP, consent, PTP and
payments stay in the ledger / spine and are written through the ledger's own APIs.

  voice_calls           one row per Sarvam call attempt; call_id holds the idempotency key (outbound attempt_id,
                        inbound interaction_id; legacy: call / interaction id). status DONE | ATTEMPT (non-terminal,
                        Sarvam will retry) | UNMATCHED. Never phone numbers, recording URL or transcript text
  voice_preferences     borrower language / voice preference (explicit, or observed by a bot)
  voice_payment_claims  "I already paid" claims: PENDING_VERIFICATION -> VERIFIED (ledger) / REJECTED (fraud
                        evidence) / EXPIRED (48 h); never a payment
  voice_followups       PTP_FOLLOW_UP calls due after an accepted promise; CALLBACK requests the gate
                        could not reserve yet only because the DPD data is not fresh for that day
  voice_contact_flags   phone number on file reached the wrong person -> UNVERIFIED
"""
from __future__ import annotations

VOICE_SCHEMA = """
CREATE TABLE IF NOT EXISTS voice_calls (
  call_id TEXT PRIMARY KEY, app_id TEXT, use_case TEXT, borrower_id TEXT, obligation_id TEXT, touch_id TEXT,
  status TEXT NOT NULL, received_at TEXT NOT NULL, connected INTEGER, duration_seconds REAL,
  disposition TEXT, effective_disposition TEXT, confidence REAL, rule TEXT, action TEXT,
  report_ordinal INTEGER, report_label TEXT, policy_version TEXT, detail TEXT, call_status TEXT,
  raw_status TEXT, failure_reason TEXT, interaction_id TEXT, ended_at TEXT, metadata TEXT, shape TEXT,
  completion_status TEXT, next_action_status TEXT, retry_attempt INTEGER, variant_id TEXT, attempt_detail TEXT);
CREATE INDEX IF NOT EXISTS ix_voice_calls_borrower ON voice_calls (borrower_id, received_at);
CREATE TABLE IF NOT EXISTS voice_preferences (
  borrower_id TEXT PRIMARY KEY, preferred_language TEXT, preferred_voice TEXT, observed_language TEXT,
  source TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS voice_payment_claims (
  call_id TEXT PRIMARY KEY, borrower_id TEXT NOT NULL, obligation_id TEXT, claimed_at TEXT NOT NULL,
  pause_until TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'PENDING_VERIFICATION', resolved_at TEXT, reason TEXT);
CREATE TABLE IF NOT EXISTS voice_followups (
  call_id TEXT PRIMARY KEY, borrower_id TEXT NOT NULL, obligation_id TEXT, use_case TEXT NOT NULL,
  promise_date TEXT, callback_at TEXT, due_on TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'PENDING',
  closed_by TEXT);
CREATE INDEX IF NOT EXISTS ix_voice_followups_due ON voice_followups (status, due_on);
CREATE TABLE IF NOT EXISTS voice_contact_flags (
  borrower_id TEXT PRIMARY KEY, status TEXT NOT NULL, reason TEXT, call_id TEXT, flagged_at TEXT);
"""


# columns added after the first release: an existing voice_calls table gets them on start
_ADDED_COLUMNS = (("voice_calls", "call_status", "TEXT"), ("voice_calls", "raw_status", "TEXT"),
                  ("voice_calls", "failure_reason", "TEXT"), ("voice_calls", "interaction_id", "TEXT"),
                  ("voice_calls", "ended_at", "TEXT"), ("voice_calls", "metadata", "TEXT"),
                  ("voice_calls", "shape", "TEXT"), ("voice_calls", "completion_status", "TEXT"),
                  ("voice_calls", "next_action_status", "TEXT"), ("voice_calls", "retry_attempt", "INTEGER"),
                  ("voice_calls", "variant_id", "TEXT"), ("voice_calls", "attempt_detail", "TEXT"),
                  # FSM-F4: a REJECTED claim records when and on what evidence
                  ("voice_payment_claims", "resolved_at", "TEXT"), ("voice_payment_claims", "reason", "TEXT"))


def ensure_schema(ledger) -> None:
    conn = ledger._conn()
    conn.executescript(VOICE_SCHEMA)
    for table, col, typ in _ADDED_COLUMNS:
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if col not in have:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
