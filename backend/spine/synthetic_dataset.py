"""Generates a synthetic collections dataset in the SAME format as
PL_Dummy_Collections_Data_500.csv, plus consent and opt-out columns.

Safety: every mobile number starts with 0. Indian mobile numbers start with 6-9,
so these numbers can never reach a real phone.
Same seed = exactly the same file, every time.
"""
import csv
import random
from datetime import timedelta

from spine.config import DATASET_AS_OF_DATE

BASE_COLUMNS = ["Loan ID", "Name", "Mobile Number", "Product", "EMI Amount", "POS", "TOS", "DPD",
                "Bounce Date", "Bucket", "Bounce String (last 6 months)", "NACH/ECS Mandate (T/F)",
                "Re-presentment Eligibility (T/F)", "State", "Age", "Employment Type", "Remaining EMIs",
                "Risk Tagging", "Payment Link"]
CONSENT_COLUMNS = {
    "whatsapp": "WhatsApp Consent (T/F)",
    "sms": "SMS Consent (T/F)",
    "email": "Email Consent (T/F)",
    "ivr": "IVR Consent (T/F)",
    "voicebot": "Voicebot Consent (T/F)",
}
OPT_OUT_COLUMN = "Opted Out (T/F)"
COLUMNS = BASE_COLUMNS + list(CONSENT_COLUMNS.values()) + [OPT_OUT_COLUMN]

FIRST = ["Aarav", "Aadhya", "Aditya", "Amit", "Ananya", "Anil", "Anjali", "Arjun", "Ashok", "Deepak",
         "Divya", "Diya", "Gaurav", "Ishaan", "Ishita", "Karan", "Kavya", "Krishna", "Kritika", "Mahesh",
         "Manoj", "Meera", "Neha", "Nikhil", "Nisha", "Pooja", "Prakash", "Preeti", "Priya", "Rahul",
         "Rajesh", "Ramesh", "Ravi", "Reyansh", "Riya", "Rohan", "Saanvi", "Sai", "Sanjay", "Shreya",
         "Sneha", "Sunil", "Sunita", "Suresh", "Swati", "Varun", "Vihaan", "Vijay", "Vikram", "Vivaan"]
LAST = ["Agarwal", "Banerjee", "Bhatt", "Chatterjee", "Chauhan", "Das", "Desai", "Ghosh", "Gupta", "Iyer",
        "Joshi", "Kapoor", "Kulkarni", "Kumar", "Malhotra", "Mehta", "Menon", "Mishra", "Naik", "Nair",
        "Pandey", "Patel", "Pillai", "Rao", "Reddy", "Shah", "Sharma", "Singh", "Verma", "Yadav"]
STATES = ["Andhra Pradesh", "Assam", "Bihar", "Delhi", "Gujarat", "Haryana", "Karnataka", "Kerala",
          "Madhya Pradesh", "Maharashtra", "Odisha", "Punjab", "Rajasthan", "Tamil Nadu", "Telangana",
          "Uttar Pradesh", "West Bengal"]
CONSENT_RATES = {"whatsapp": 0.75, "sms": 0.95, "email": 0.50, "ivr": 0.60, "voicebot": 0.40}
# Buckets follow the K01 policy.json bands: B1 1-30, B2 31-60, B3 61-90, B4 91+.
BUCKETS = [(1, 30, "1-30"), (31, 60, "31-60"), (61, 90, "61-90"), (91, 180, "91-180")]
# E01: the first rows sit exactly on every K01 band boundary, so the switch is always testable.
BOUNDARY_DPDS = [1, 30, 31, 60, 61, 90, 91]
# E01: every 1,000th row has no DPD, to prove "missing DPD -> HOLD".
MISSING_DPD_EVERY = 1000
BUCKET_WEIGHTS = [0.45, 0.25, 0.18, 0.12]


def _tf(flag):
    return "T" if flag else "F"


def make_row(i, rng, as_of=DATASET_AS_OF_DATE):
    low, high, bucket = rng.choices(BUCKETS, weights=BUCKET_WEIGHTS)[0]
    dpd = rng.randint(low, high)
    if i < len(BOUNDARY_DPDS):
        dpd = BOUNDARY_DPDS[i]
        bucket = next(name for lo, hi, name in BUCKETS if lo <= dpd <= hi)
    remaining = rng.randint(1, 60)
    emi = rng.randint(2_000, 25_000)
    pos = max(int(emi * remaining * rng.uniform(0.85, 0.97)), emi // 2)
    overdue_emis = 1 + dpd // 30                    # 30+ DPD means more than one EMI missed
    tos = pos + emi * overdue_emis + rng.randint(200, 2_500)  # + charges

    # Bounce history for the last 6 months; the current month is always a bounce.
    history_months = rng.choice([1, 2, 3, 6, 6, 6, 6, 6])   # some loans are new
    bounce_rate = rng.choice([0.05, 0.15, 0.3, 0.5])
    history = ["NA"] * (6 - history_months) + [
        "1" if rng.random() < bounce_rate else "0" for _ in range(history_months - 1)] + ["1"]
    past_bounces = history[:-1].count("1")

    if pos > 500_000:
        risk = "C5 - High-Ticket Override"
    elif past_bounces == 0 and "0" not in history[:-1]:
        risk = "C1 - First-Time Bounce"
    elif past_bounces >= 3:
        risk = "C4 - High Risk"
    elif past_bounces >= 1:
        risk = "C3 - Medium Risk"
    else:
        risk = "C2 - Low Risk"

    nach = rng.random() < 0.75
    loan_id = f"SL{100001 + i}"
    row = {
        "Loan ID": loan_id,
        "Name": f"{rng.choice(FIRST)} {rng.choice(LAST)}",
        "Mobile Number": f"0{i:09d}",               # starts with 0: never a real Indian mobile
        "Product": "Personal Loan",
        "EMI Amount": emi,
        "POS": pos,
        "TOS": tos,
        "DPD": "" if i % MISSING_DPD_EVERY == MISSING_DPD_EVERY - 1 else dpd,
        "Bounce Date": (as_of - timedelta(days=dpd)).isoformat(),
        "Bucket": bucket,
        "Bounce String (last 6 months)": ",".join(history),
        "NACH/ECS Mandate (T/F)": _tf(nach),
        "Re-presentment Eligibility (T/F)": _tf(nach and rng.random() < 0.6),
        "State": rng.choice(STATES),
        "Age": rng.randint(22, 58),
        "Employment Type": rng.choice(["Salaried", "Self-Employed"]),
        "Remaining EMIs": remaining,
        "Risk Tagging": risk,
        "Payment Link": f"https://pay.lender.example/pl/{loan_id}",
    }
    for channel, column in CONSENT_COLUMNS.items():
        row[column] = _tf(rng.random() < CONSENT_RATES[channel])
    row[OPT_OUT_COLUMN] = _tf(rng.random() < 0.02)
    return row


def write_dataset(path, rows, seed):
    rng = random.Random(seed)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        for i in range(rows):
            writer.writerow(make_row(i, rng))
