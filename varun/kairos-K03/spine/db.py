"""Database setup: tables, links between tables, and an integrity check.

Money is stored in PAISE (1 rupee = 100 paise) as whole numbers.
This avoids rounding errors that happen with decimal numbers like 0.1 + 0.2.
"""
import sqlite3

from spine.config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS borrowers (
    borrower_id TEXT PRIMARY KEY,
    full_name   TEXT NOT NULL,
    phone       TEXT NOT NULL,
    email       TEXT,
    language    TEXT NOT NULL DEFAULT 'en',
    state       TEXT,
    age         INTEGER,
    employment_type TEXT
);

-- One row per borrower per channel.
CREATE TABLE IF NOT EXISTS consents (
    borrower_id TEXT NOT NULL REFERENCES borrowers(borrower_id),
    channel     TEXT NOT NULL CHECK (channel IN ('whatsapp','sms','email','ivr','voicebot')),
    consented   INTEGER NOT NULL CHECK (consented IN (0, 1)),
    opted_out   INTEGER NOT NULL DEFAULT 0 CHECK (opted_out IN (0, 1)),
    updated_at  TEXT NOT NULL,
    source      TEXT NOT NULL DEFAULT 'provided' CHECK (source IN ('provided', 'assumed')),
    PRIMARY KEY (borrower_id, channel)
);

-- A loan's open/closed status is NOT stored. It is calculated from its
-- obligations, so it can never go out of date.
CREATE TABLE IF NOT EXISTS loans (
    loan_id         TEXT PRIMARY KEY,
    borrower_id     TEXT NOT NULL REFERENCES borrowers(borrower_id),
    product         TEXT NOT NULL,
    principal_paise INTEGER NOT NULL CHECK (principal_paise > 0),
    disbursed_on    TEXT,
    -- Extra fields from the dataset (empty for synthetic loans):
    total_outstanding_paise INTEGER,
    remaining_emis  INTEGER,          -- NULL = every EMI of the loan is in the obligations table
    risk_tag        TEXT,
    payment_link    TEXT,
    nach_mandate    INTEGER,          -- 1/0: automatic bank debit set up
    represent_eligible INTEGER,       -- 1/0: bounced debit can be presented again
    bounce_string   TEXT,             -- last 6 months, e.g. '0,1,1,0,0,1' (NA = no EMI that month)
    bucket          TEXT,             -- bucket as given in the file, e.g. '0-30'
    file_dpd        INTEGER,          -- DPD as given in the file (NULL = missing -> K01 HOLD)
    dpd_as_of       TEXT,             -- when that DPD was true (K01: older than 24 h -> HOLD)
    source          TEXT NOT NULL DEFAULT 'synthetic',
    UNIQUE (loan_id, borrower_id)
);

-- One row per EMI (amount due on a date).
CREATE TABLE IF NOT EXISTS obligations (
    obligation_id     TEXT PRIMARY KEY,
    loan_id           TEXT NOT NULL REFERENCES loans(loan_id),
    installment_no    INTEGER NOT NULL CHECK (installment_no > 0),
    due_date          TEXT NOT NULL,
    amount_due_paise  INTEGER NOT NULL CHECK (amount_due_paise > 0),
    amount_paid_paise INTEGER NOT NULL DEFAULT 0 CHECK (amount_paid_paise >= 0),
    CHECK (amount_paid_paise <= amount_due_paise),
    UNIQUE (obligation_id, loan_id),
    UNIQUE (loan_id, installment_no)
);

-- The (obligation_id, loan_id) pair must exist together in obligations,
-- so a payment can never land on an EMI belonging to a different loan.
CREATE TABLE IF NOT EXISTS payments (
    payment_id    TEXT PRIMARY KEY,
    loan_id       TEXT NOT NULL,
    obligation_id TEXT NOT NULL,
    amount_paise  INTEGER NOT NULL CHECK (amount_paise > 0),
    paid_on       TEXT NOT NULL,
    mode          TEXT NOT NULL,
    reference     TEXT NOT NULL UNIQUE,
    reversed_on   TEXT,               -- set when the bank reverses the payment (E15)
    reversal_reason TEXT,
    FOREIGN KEY (obligation_id, loan_id) REFERENCES obligations(obligation_id, loan_id)
);

-- Every loan-related event must name its obligation, and that obligation
-- must belong to that loan, and that loan must belong to that borrower.
CREATE TABLE IF NOT EXISTS events (
    event_id      TEXT PRIMARY KEY,
    borrower_id   TEXT NOT NULL REFERENCES borrowers(borrower_id),
    loan_id       TEXT,
    obligation_id TEXT,
    event_type    TEXT NOT NULL CHECK (event_type IN
                  ('touch_sent', 'reply_received', 'payment_received', 'payment_reversed', 'consent_changed')),
    channel       TEXT CHECK (channel IS NULL OR channel IN ('whatsapp','sms','email','ivr','voicebot')),
    occurred_at   TEXT NOT NULL,
    details       TEXT,
    policy_version TEXT,               -- K01: every logged decision cites the policy version
    CHECK (event_type = 'consent_changed' OR obligation_id IS NOT NULL),
    CHECK (obligation_id IS NULL OR loan_id IS NOT NULL),
    FOREIGN KEY (loan_id, borrower_id) REFERENCES loans(loan_id, borrower_id),
    FOREIGN KEY (obligation_id, loan_id) REFERENCES obligations(obligation_id, loan_id)
);
"""


def get_connection(path=DB_PATH):
    """Open the database. Use ':memory:' for a temporary in-memory database (tests)."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row          # lets us read columns by name: row["loan_id"]
    conn.execute("PRAGMA foreign_keys = ON")  # SQLite only enforces links when this is ON
    return conn


def create_schema(conn):
    conn.executescript(SCHEMA)


def integrity_problems(conn):
    """Return a list of broken links (orphan records). Empty list = all good."""
    return [tuple(row) for row in conn.execute("PRAGMA foreign_key_check")]
