"""Loads PL_Dummy_Collections_Data_500.csv into the spine.

What happens to each row:
  - one borrower per row (ID = BRW-<Loan ID>). People are NEVER merged by name:
    the file has different people with the same name.
  - one loan with the dataset's extra fields (risk tag, payment link, ...)
  - one obligation: the bounced EMI, due on the Bounce Date
  - consent: from consent columns if a future file (D11) has them; the team file has none,
    so none is created unless DATASET_ASSUMED_CONSENT is set (then labelled 'assumed')

Every row is checked first. A bad row is rejected with a reason; good rows still load.
Loading the same file twice adds nothing new.
"""
import csv
from datetime import date

from spine.config import DATASET_AS_OF_DATE, DATASET_ASSUMED_CONSENT
from spine.db import create_schema
from spine.policy import dpd_band

# The team file (PL_Dummy_Collections_Data_500) has NO consent columns. If the official consent
# file (D11) arrives with columns like these, they are read; until then consent is never invented
# here (K03 marks its own consent rows 'assumed').
CONSENT_COLUMNS = {"whatsapp": "WhatsApp Consent (T/F)", "sms": "SMS Consent (T/F)", "email": "Email Consent (T/F)",
                   "ivr": "IVR Consent (T/F)", "voicebot": "Voicebot Consent (T/F)"}
OPT_OUT_COLUMN = "Opted Out (T/F)"

# DPD is deliberately NOT required: K01 says a missing DPD puts the borrower on HOLD,
# so the row is loaded (and held), not thrown away.
REQUIRED = ["Loan ID", "Name", "Mobile Number", "Product", "EMI Amount", "POS", "TOS",
            "Bounce Date", "Remaining EMIs", "Risk Tagging", "Payment Link"]


def _flag(value, column):
    v = value.strip().upper()
    if v not in ("T", "F"):
        raise ValueError(f"{column} must be T or F, got '{value}'")
    return 1 if v == "T" else 0


def _rupees_to_paise(value, column, allow_zero=False):
    try:
        rupees = float(value)
    except ValueError:
        raise ValueError(f"{column} is not a number: '{value}'")
    paise = round(rupees * 100)
    if paise < 0 or (paise == 0 and not allow_zero):
        raise ValueError(f"{column} must be more than zero, got '{value}'")
    return paise


def _bucket_contains(bucket, dpd):
    """'0-30' -> True if dpd is between 0 and 30. Unknown formats return None."""
    try:
        low, high = (int(x) for x in bucket.split("-"))
        return low <= dpd <= high
    except ValueError:
        return None


def parse_row(row):
    """Turn one CSV row into clean values, or raise ValueError explaining what is wrong."""
    missing = [c for c in REQUIRED if not row.get(c)]
    if missing:
        raise ValueError(f"missing {', '.join(missing)}")

    loan_id = row["Loan ID"]
    phone = row["Mobile Number"]
    if not (phone.isdigit() and len(phone) == 10):
        raise ValueError(f"mobile number must be 10 digits, got '{phone}'")
    try:
        bounce_date = date.fromisoformat(row["Bounce Date"])
    except ValueError:
        raise ValueError(f"bad Bounce Date '{row['Bounce Date']}' (expected YYYY-MM-DD)")
    try:
        file_dpd = int(row["DPD"]) if row.get("DPD") else None
        remaining = int(row["Remaining EMIs"])
        age = int(row["Age"]) if row.get("Age") else None
    except ValueError:
        raise ValueError("DPD, Remaining EMIs and Age must be whole numbers")
    if remaining < 0:
        raise ValueError("Remaining EMIs cannot be negative")

    bounce_string = row.get("Bounce String (last 6 months)", "")
    months = [m.strip().upper() for m in bounce_string.split(",")] if bounce_string else []
    if months and (len(months) != 6 or any(m not in ("0", "1", "NA") for m in months)):
        raise ValueError(f"bounce string must be 6 values of 0/1/NA, got '{bounce_string}'")

    return {
        "loan_id": loan_id,
        "borrower_id": f"BRW-{loan_id}",
        "name": row["Name"],
        "phone": phone,
        "product": row["Product"],
        "emi_paise": _rupees_to_paise(row["EMI Amount"], "EMI Amount"),
        "pos_paise": _rupees_to_paise(row["POS"], "POS"),
        "tos_paise": _rupees_to_paise(row["TOS"], "TOS"),
        "file_dpd": file_dpd,
        "bounce_date": bounce_date,
        "bucket": row.get("Bucket") or None,
        "bounce_string": ",".join(months),
        "nach": _flag(row.get("NACH/ECS Mandate (T/F)", "F"), "NACH/ECS Mandate"),
        "represent": _flag(row.get("Re-presentment Eligibility (T/F)", "F"), "Re-presentment Eligibility"),
        "state": row.get("State") or None,
        "age": age,
        "employment": row.get("Employment Type") or None,
        "remaining_emis": remaining,
        "risk_tag": row["Risk Tagging"],
        "payment_link": row["Payment Link"],
        "consents": {ch: _flag(row[col], col) for ch, col in CONSENT_COLUMNS.items() if row.get(col)},
        "opted_out": _flag(row[OPT_OUT_COLUMN], OPT_OUT_COLUMN) if row.get(OPT_OUT_COLUMN) else 0,
    }


def read_rows(path):
    """Read the CSV, removing stray spaces around headers and values (the real file has them)."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        header = [h.strip() for h in next(reader)]
        for line_no, values in enumerate(reader, start=2):
            if not any(v.strip() for v in values):
                continue  # skip blank lines
            yield line_no, {h: (values[i].strip() if i < len(values) else "") for i, h in enumerate(header)}


def import_dataset(conn, path, as_of=DATASET_AS_OF_DATE):
    """Load the file. Returns a report dict: what loaded, what was rejected and why, and warnings."""
    create_schema(conn)
    report = {"file": str(path), "as_of": as_of.isoformat(), "rows_read": 0, "loaded": 0,
              "already_present": 0, "rejected": [], "dpd_mismatches": [], "bucket_vs_policy_band": [],
              "duplicate_names": 0, "assumed_consent_channels": [], "consent_from_file": False,
              "dpd_missing_held": []}
    seen_loans, names = set(), {}
    conn.execute("BEGIN")  # one transaction for the whole file: fast, and all-or-nothing on a crash
    try:
        _import_rows(conn, path, as_of, report, seen_loans, names)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    report["duplicate_names"] = sum(count for count in names.values() if count > 1)
    return report


def _import_rows(conn, path, as_of, report, seen_loans, names):
    for line_no, row in read_rows(path):
        report["rows_read"] += 1
        try:
            r = parse_row(row)
            if r["loan_id"] in seen_loans:
                raise ValueError(f"Loan ID {r['loan_id']} appears twice in the file")
        except ValueError as e:
            report["rejected"].append({"line": line_no, "loan_id": row.get("Loan ID", ""), "reason": str(e)})
            continue
        seen_loans.add(r["loan_id"])
        names[r["name"]] = names.get(r["name"], 0) + 1

        # Check the file against our own calculation and against the K01 bands.
        if r["file_dpd"] is None:
            report["dpd_missing_held"].append(r["loan_id"])     # K01: HOLD, loaded but never contacted
        else:
            our_dpd = (as_of - r["bounce_date"]).days
            if our_dpd != r["file_dpd"]:
                report["dpd_mismatches"].append({"loan_id": r["loan_id"], "file_dpd": r["file_dpd"], "our_dpd": our_dpd})
        band = dpd_band(r["file_dpd"])
        inside = _bucket_contains(r["bucket"], r["file_dpd"]) if r["file_dpd"] is not None and r["bucket"] else None
        # Flag when the DPD is outside its own bucket, or the bucket starts in a different K01 band.
        if inside is not None and (not inside or band != _bucket_start_band(r["bucket"])):
            report["bucket_vs_policy_band"].append(
                {"loan_id": r["loan_id"], "dpd": r["file_dpd"], "file_bucket": r["bucket"], "policy_band": band})

        exists = conn.execute("SELECT 1 FROM loans WHERE loan_id = ?", (r["loan_id"],)).fetchone()
        consent_source = insert_parsed_loan(conn, r, as_of)
        if consent_source == "provided":
            report["consent_from_file"] = True
        elif consent_source == "assumed":
            report["assumed_consent_channels"] = list(DATASET_ASSUMED_CONSENT)
        report["already_present" if exists else "loaded"] += 1


def insert_parsed_loan(conn, r, as_of, borrower_id=None, phone=None, consents=None):
    """Insert one parsed dataset row: borrower, loan and its bounced EMI (+ consent).

    borrower_id / phone / consents let a caller link several loans to one customer,
    replace a real-looking phone number, or give consent the file does not have.
    Returns where consent came from: 'provided', 'assumed' or None.
    """
    borrower_id = borrower_id or r["borrower_id"]
    conn.execute(
        """INSERT INTO borrowers (borrower_id, full_name, phone, language, state, age, employment_type)
           VALUES (?, ?, ?, 'en', ?, ?, ?) ON CONFLICT (borrower_id) DO NOTHING""",
        (borrower_id, r["name"], phone or r["phone"], r["state"], r["age"], r["employment"]))
    conn.execute(
        """INSERT INTO loans (loan_id, borrower_id, product, principal_paise, total_outstanding_paise,
               remaining_emis, risk_tag, payment_link, nach_mandate, represent_eligible, bounce_string,
               bucket, file_dpd, dpd_as_of, source)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'dataset') ON CONFLICT (loan_id) DO NOTHING""",
        (r["loan_id"], borrower_id, r["product"], r["pos_paise"], r["tos_paise"], r["remaining_emis"],
         r["risk_tag"], r["payment_link"], r["nach"], r["represent"], r["bounce_string"],
         r["bucket"], r["file_dpd"], f"{as_of.isoformat()}T00:00:00"))
    conn.execute(
        """INSERT INTO obligations (obligation_id, loan_id, installment_no, due_date, amount_due_paise)
           VALUES (?, ?, 1, ?, ?) ON CONFLICT (obligation_id) DO NOTHING""",
        (f"{r['loan_id']}-OD1", r["loan_id"], r["bounce_date"].isoformat(), r["emi_paise"]))

    if consents is not None:
        rows, source = [(ch, flag) for ch, flag in consents.items()], "assumed"
    elif r["consents"]:
        rows, source = list(r["consents"].items()), "provided"
    elif DATASET_ASSUMED_CONSENT:
        rows, source = [(ch, 1) for ch in DATASET_ASSUMED_CONSENT], "assumed"
    else:
        return None
    for channel, flag in rows:
        conn.execute(
            """INSERT INTO consents (borrower_id, channel, consented, opted_out, updated_at, source)
               VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (borrower_id, channel) DO NOTHING""",
            (borrower_id, channel, flag, r["opted_out"], as_of.isoformat(), source))
    return source


def find_rows(path, loan_ids):
    """Return parsed rows for the given Loan IDs, in the order asked. Raises if any is missing."""
    wanted, found = set(loan_ids), {}
    for _, row in read_rows(path):
        if row.get("Loan ID") in wanted:
            found[row["Loan ID"]] = parse_row(row)
    missing = [lid for lid in loan_ids if lid not in found]
    if missing:
        raise ValueError(f"Loan IDs not found in {path}: {', '.join(missing)}")
    return [found[lid] for lid in loan_ids]


def _bucket_start_band(bucket):
    """The K01 band where a file bucket starts: '0-30' -> B1, '31-60' -> B2.
    A loan disagrees when its own DPD falls in a different K01 band (e.g. DPD 31 in bucket '0-30')."""
    low = int(bucket.split("-")[0])
    return dpd_band(max(low, 1))
