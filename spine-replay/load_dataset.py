"""Loads the team dataset into the spine and prints a check report.

Run:          python3 load_dataset.py ~/Downloads/PL_Dummy_Collections_Data_500.csv
Fresh start:  python3 load_dataset.py ~/Downloads/PL_Dummy_Collections_Data_500.csv --reset
"""
import os
import sys
from collections import Counter

from spine.config import CHANNELS, DATASET_AS_OF_DATE, DATASET_DB_PATH
from spine.db import get_connection, integrity_problems
from spine.importer import import_dataset
from spine.ledger import is_contactable, loan_summary, rupees
from spine.policy import policy_version


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print("Please give the CSV path, e.g.\n  python3 load_dataset.py ~/Downloads/PL_Dummy_Collections_Data_500.csv")
        sys.exit(1)
    path = os.path.expanduser(args[0])
    if not os.path.exists(path):
        print(f"File not found: {path}")
        sys.exit(1)
    if "--reset" in sys.argv and os.path.exists(DATASET_DB_PATH):
        os.remove(DATASET_DB_PATH)

    conn = get_connection(DATASET_DB_PATH)
    report = import_dataset(conn, path)

    print(f"\nDataset import  ({os.path.basename(path)}, DPD as of {report['as_of']})")
    print(f"Label: SIMULATED · development fallback   Policy: K01 v{policy_version()}")
    print("=" * 64)
    print(f"Rows read: {report['rows_read']}   Loaded: {report['loaded']}   "
          f"Already loaded before: {report['already_present']}   Rejected: {len(report['rejected'])}")
    for r in report["rejected"][:10]:
        print(f"   REJECTED line {r['line']} {r['loan_id']}: {r['reason']}")

    loan_ids = [r["loan_id"] for r in conn.execute("SELECT loan_id FROM loans WHERE source = 'dataset'")]
    loans = [loan_summary(conn, lid, DATASET_AS_OF_DATE) for lid in loan_ids]
    by_risk = Counter(l["risk_tag"] for l in loans)
    by_band = Counter(l["dpd_band"] for l in loans)
    overdue = sum(l["outstanding_paise"] for l in loans)
    contactable = sum(1 for l in loans if any(is_contactable(conn, l["borrower_id"], ch) for ch in CHANNELS))

    print(f"\nLoans in spine: {len(loans)}   Overdue EMIs to collect: {rupees(overdue)}")
    print("By risk tag:  " + ", ".join(f"{k}: {v}" for k, v in sorted(by_risk.items())))
    print("By K01 band:  " + ", ".join(f"{k}: {v}" for k, v in sorted(by_band.items())))

    print("\nCHECKS")
    mism = report["dpd_mismatches"]
    with_dpd = len(loans) - len(report["dpd_missing_held"])
    print(f"  {'PASS' if not mism else 'WARN'}  DPD matches the file for {with_dpd - len(mism)}/{with_dpd} loans that have a DPD"
          + (f"  (e.g. {mism[0]})" if mism else ""))
    band_issues = report["bucket_vs_policy_band"]
    print(f"  {'PASS' if not band_issues else 'WARN'}  File bucket agrees with K01 bands"
          + (f": {len(band_issues)} loans disagree, e.g. DPD {band_issues[0]['dpd']} in bucket "
             f"'{band_issues[0]['file_bucket']}' is {band_issues[0]['policy_band']} in K01"
             if band_issues else ""))
    held = report["dpd_missing_held"]
    print(f"  {'INFO' if held else 'PASS'}  Missing DPD: {len(held)} loans loaded and put on HOLD (K01)"
          + (f", e.g. {held[0]}" if held else ""))
    print(f"  INFO  {report['duplicate_names']} loans share a name with another loan (kept as separate borrowers)")
    if report["consent_from_file"]:
        print(f"  PASS  Consent read from the file: {contactable}/{len(loans)} borrowers can be contacted")
    elif report["assumed_consent_channels"]:
        print(f"  WARN  Consent is ASSUMED for {report['assumed_consent_channels']} (file has no consent column)")
    else:
        print(f"  WARN  No consent in the file: {contactable}/{len(loans)} borrowers can be contacted")
    real_looking = conn.execute(
        "SELECT COUNT(*) FROM borrowers b JOIN loans l ON l.borrower_id = b.borrower_id "
        "WHERE l.source = 'dataset' AND substr(b.phone, 1, 1) IN ('6', '7', '8', '9')").fetchone()[0]
    if real_looking:
        print(f"  WARN  {real_looking} mobile numbers look real: send only to approved test numbers")
    else:
        print("  PASS  No real-looking mobile numbers (all start with 0)")
    problems = integrity_problems(conn)
    print(f"  {'PASS' if not problems else 'FAIL'}  Integrity check (no orphan records)")

    print("\nSample loans:")
    for l in loans[:3]:
        print(f"  {l['loan_id']}  {l['risk_tag']:<26} overdue {rupees(l['outstanding_paise']):>12}  "
              f"DPD {l['dpd']:>2} ({l['dpd_band']})  future EMIs {l['remaining_emis']}")
    print(f"\nSaved to {DATASET_DB_PATH}")
    conn.close()


if __name__ == "__main__":
    main()
