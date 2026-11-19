"""Settings for the account and loan spine (task K03).

Everything that another task might change lives here, in one place.
The DPD bands are PLACEHOLDERS until K01 (Ojas) locks the decision policy.
"""
from datetime import date

# Where the SQLite database file is saved (created automatically).
DB_PATH = "kairos_spine.db"

# The "today" used to calculate how late a payment is.
# It is fixed (not the real clock) so results are the same on every run.
AS_OF_DATE = date(2026, 11, 19)

# Replace with a team member's APPROVED sandbox test number before K04/K07
# send real messages. Never use a number that could belong to a real person.
TEST_PHONE = "+910000000000"

from spine.policy import channels as _policy_channels

CHANNELS = _policy_channels()  # from policy/policy.json (K01): sms, whatsapp, email, ivr, voicebot

# DPD bands are NOT set here any more: they come from policy/policy.json (K01). See spine/policy.py.


# ---------------- Dataset import (PL_Dummy_Collections_Data_500) ----------------
DATASET_DB_PATH = "kairos_dataset.db"

# The file was produced on this date: Bounce Date + DPD = 2026-08-07 for every row.
# DPD for the dataset is calculated from this date, not from AS_OF_DATE.
DATASET_AS_OF_DATE = date(2026, 8, 7)

# The file has NO consent column. Default: nobody is contactable.
# If the team agrees an assumption, list channels here, e.g. ("sms", "whatsapp").
# Rows created this way are marked source='assumed' so they are never mistaken for real consent.
DATASET_ASSUMED_CONSENT = ()

# Unclear in the file: does "Remaining EMIs" include the overdue EMI? CONFIRM with the data owner.
REMAINING_EMIS_INCLUDES_OVERDUE = False
