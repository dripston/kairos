"""Seed: one synthetic 42-DPD borrower with two loans, one campaign, four touches.

Loans come from the team's verified dev fallback, PL_Dummy_Collections_Data_500
(an HDFC demo fixture, so results stay labeled SIMULATED). Loan ID, EMI, POS, TOS,
bucket and bounce date are copied from rows PL100328 and PL100376. That dataset
has one loan per borrower and DPD 1-30 only, so for the 42-DPD scenario:
  * both rows are joined under synthetic borrower B-1042 (the K03 spine), and
  * the demo clock is advanced 30 days, from the dataset DPD of 12 to 42.
Both adjustments are stored (`source`, `source_dpd`) and shown on the page.
Names and mobile numbers from the dataset are not copied.

The two sends that already happened go through the shared ingestion path, so
they show on the timeline like any other event.
"""
from __future__ import annotations

import sqlite3

from kairos.db import reset, set_meta, transaction
from kairos.ingest import ingest_event, utc_iso

BORROWER_ID = "B-1042"
LOAN_A = "PL100328"
LOAN_B = "PL100376"
DATASET = "PL_Dummy_Collections_Data_500"
CAMPAIGN_ID = "CMP-DPD31-60-NOV"

TOUCH_SMS = "T-1042-0001"          # SMS nudge, sent 18 Nov
TOUCH_WA = "T-1042-0002"           # WhatsApp template, sent 19 Nov
TOUCH_REMIND_A = "T-1042-0003"     # pending WhatsApp EMI reminder, Loan A
TOUCH_REMIND_B = "T-1042-0004"     # pending WhatsApp EMI reminder, Loan B

SEED_TIME = "2026-11-18T09:00:00+05:30"


def seed(conn: sqlite3.Connection) -> None:
    reset(conn)
    t0 = utc_iso(SEED_TIME)
    with transaction(conn):
        conn.execute(
            """INSERT INTO borrowers (borrower_id, display_name, dpd, persona, language, consent_voice, consent_version,
                                    synthetic) VALUES (?, ?, ?, ?, ?, 1, ?, 1)""",
            (BORROWER_ID, "Synthetic Borrower 1042", 42, "Salaried, NACH mandate, responsive on WhatsApp", "hi-IN",
             "consent-v1 (SMS, WhatsApp, voice)"),
        )
        conn.executemany(
            """INSERT INTO loans (loan_id, borrower_id, label, product, emi_amount, overdue_amount, dpd,
                                  pos, tos, bucket, source, source_dpd, status, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?)""",
            [
                # Overdue = the bounced EMI obligation (bounce date 2026-07-26 in the dataset).
                (LOAN_A, BORROWER_ID, "Loan A", "Personal Loan", 4985, 4985, 42, 14003, 15893, "31-60",
                 f"{DATASET} row {LOAN_A}", 12, t0),
                (LOAN_B, BORROWER_ID, "Loan B", "Personal Loan", 5608, 5608, 42, 56589, 60638, "31-60",
                 f"{DATASET} row {LOAN_B}", 12, t0),
            ],
        )
        conn.execute("INSERT INTO campaigns (campaign_id, name) VALUES (?, ?)",
                     (CAMPAIGN_ID, "31–60 DPD recovery, November"))
        conn.executemany(
            """INSERT INTO touches (touch_id, borrower_id, loan_id, campaign_id, channel, purpose, template_id,
                                    scheduled_for, status, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?)""",
            [
                (TOUCH_SMS, BORROWER_ID, None, CAMPAIGN_ID, "SMS", "NUDGE", "sms_dpd_nudge_v2",
                 utc_iso("2026-11-18T10:00:00+05:30"), t0),
                (TOUCH_WA, BORROWER_ID, None, CAMPAIGN_ID, "WHATSAPP", "NUDGE", "wa_emi_due_hi_v3",
                 utc_iso("2026-11-19T09:00:00+05:30"), t0),
                (TOUCH_REMIND_A, BORROWER_ID, LOAN_A, CAMPAIGN_ID, "WHATSAPP", "REMINDER", "wa_emi_reminder_hi_v1",
                 utc_iso("2026-11-21T11:00:00+05:30"), t0),
                (TOUCH_REMIND_B, BORROWER_ID, LOAN_B, CAMPAIGN_ID, "WHATSAPP", "REMINDER", "wa_emi_reminder_hi_v1",
                 utc_iso("2026-11-21T11:00:00+05:30"), t0),
            ],
        )
        for touch_id, channel, template, provider_id, at in (
            (TOUCH_SMS, "SMS", "sms_dpd_nudge_v2", "sms-msg-7781", "2026-11-18T10:00:04+05:30"),
            (TOUCH_WA, "WHATSAPP", "wa_emi_due_hi_v3", "wamid.OUT-T0002", "2026-11-19T09:00:02+05:30"),
        ):
            ingest_event(conn, {
                "event_type": "touch.sent",
                "source": "kairos.dispatch (seed)",
                "provenance": "SIMULATED",
                "occurred_at": at,
                "dedupe_key": f"sent:{touch_id}",
                "touch_id": touch_id,
                "payload": {"channel": channel, "template_id": template, "provider_message_id": provider_id},
            })
        set_meta(conn, "replay.cursor", "0")
        set_meta(conn, "replay.results", "[]")
