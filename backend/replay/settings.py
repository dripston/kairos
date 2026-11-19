"""Settings for the K15 safety, load and story replay.

Values marked PLACEHOLDER must be replaced when the official data arrives:
  costs -> D10, templates -> D13, caps/spacing -> K01 (Ojas), budget -> campaign plan.
"""
SEED = 42
N_BORROWERS = 50_000
CAMPAIGN_DAYS = 14

CHANNELS = ("whatsapp", "sms", "email", "ivr", "voicebot")  # K01 names
SLOTS = ("morning", "afternoon", "evening")

# Cost per contact in paise (100 paise = Rs.1). PLACEHOLDER until D10.
COST_PAISE = {"whatsapp": 40, "sms": 25, "email": 5, "ivr": 150, "voicebot": 1200}

# Registered templates. PLACEHOLDER until D13. Anything not listed here is a violation.
TEMPLATES = {
    "whatsapp": ("WA_REMIND_V1",),
    "sms": ("SMS_REMIND_V1",),
    "email": ("EMAIL_REMIND_V1",),
    "ivr": ("IVR_REMIND_V1",),
    "voicebot": ("VOICE_SCRIPT_V1",),
}

# Contact rules. PLACEHOLDER until K01 is approved.
MAX_CONTACTS_PER_DAY = 1
MAX_CONTACTS_PER_7_DAYS = 3
MIN_DAYS_BETWEEN_CONTACTS = 2

DAILY_BUDGET_PAISE = 800_000  # Rs.8,000 per day. PLACEHOLDER.

# Targets from the problem statements.
PLANNING_TIME_LIMIT_SECONDS = 600       # CM-02: 50,000 decisions in under 10 minutes
CONCURRENT_CHATS = 500                  # CM-03: 500 concurrent conversations
FIRST_RESPONSE_LIMIT_SECONDS = 30       # CM-03: first response under 30 seconds
