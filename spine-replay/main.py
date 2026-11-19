"""K03: builds the account and loan spine from the team dataset and prints a report.

Run:          python3 main.py ~/Downloads/PL_Dummy_Collections_Data_500.csv
Fresh start:  python3 main.py ~/Downloads/PL_Dummy_Collections_Data_500.csv --reset

Without a file path it uses the hand-made fixture instead (clearly labelled).
"""
import os
import sys

from spine.config import AS_OF_DATE, CHANNELS, DATASET_AS_OF_DATE, DB_PATH
from spine.db import get_connection, integrity_problems
from spine.ledger import borrower_summary, is_contactable, rupees
from spine.policy import policy_version
from spine.seed import BORROWER_ID, DATASET_CUSTOMER_ID, build_spine, build_spine_from_dataset


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if "--reset" in sys.argv and os.path.exists(DB_PATH):
        os.remove(DB_PATH)
        print(f"Deleted old {DB_PATH}")

    conn = get_connection()
    dataset_candidate = None
    if args:
        dataset_candidate = os.path.expanduser(args[0])
    elif os.path.exists("PL_Dummy_Collections_Data_500.csv"):
        dataset_candidate = "PL_Dummy_Collections_Data_500.csv"
    elif os.path.exists(os.path.expanduser("~/Downloads/PL_Dummy_Collections_Data_500.csv")):
        dataset_candidate = os.path.expanduser("~/Downloads/PL_Dummy_Collections_Data_500.csv")

    if dataset_candidate:
        if not os.path.exists(dataset_candidate):
            print(f"File not found: {dataset_candidate}")
            sys.exit(1)
        build_spine_from_dataset(conn, dataset_candidate)
        borrower_id, as_of = DATASET_CUSTOMER_ID, DATASET_AS_OF_DATE
        source = (f"dataset {os.path.basename(dataset_candidate)} (loans PL100033 + PL100034 linked to one customer)\n"
                  f"Label:  SIMULATED · development fallback (500-loan HDFC demo fixture, not official data)")
    else:
        build_spine(conn)
        borrower_id, as_of = BORROWER_ID, AS_OF_DATE
        source = "hand-made fixture (no dataset file given)\nLabel:  SIMULATED"

    borrower = conn.execute("SELECT * FROM borrowers WHERE borrower_id = ?", (borrower_id,)).fetchone()
    print(f"\nKairos account and loan spine  (as of {as_of})")
    print(f"Source: {source}")
    print(f"Policy: K01 decision policy v{policy_version()} (policy/K01-decision-policy/policy.json)")
    print("=" * 72)
    print(f"Borrower {borrower['borrower_id']}: {borrower['full_name']}  (test phone {borrower['phone']})")
    contact = ", ".join(f"{ch} {'yes' if is_contactable(conn, borrower_id, ch) else 'no'}" for ch in CHANNELS)
    print(f"Can contact on: {contact}\n")

    for loan in borrower_summary(conn, borrower_id, as_of):
        cleared = "OVERDUE CLEARED" if not loan["overdue"] and loan["outstanding_paise"] == 0 else ""
        state = loan["status"].upper() + (f", {cleared}" if cleared else (", OVERDUE" if loan["overdue"] else ""))
        extra = f", risk {loan['risk_tag']}" if loan.get("risk_tag") else ""
        print(f"Loan {loan['loan_id']} ({loan['product']}{extra}): {state}")
        print(f"   overdue to collect {rupees(loan['outstanding_paise'])}, DPD {loan['dpd']} ({loan['dpd_band']})"
              + (f", future EMIs {loan['remaining_emis']}" if loan.get("remaining_emis") is not None else ""))
        for ob in loan["obligations"]:
            print(f"   EMI {ob['installment_no']}  due {ob['due_date']}  {rupees(ob['amount_due_paise']):>12}  "
                  f"paid {rupees(ob['amount_paid_paise']):>12}  -> {ob['status']}, DPD {ob['dpd']}")
        print()

    events = conn.execute("SELECT event_type, loan_id, obligation_id, policy_version FROM events WHERE borrower_id = ? "
                          "ORDER BY occurred_at", (borrower_id,)).fetchall()
    print(f"Events ({len(events)}):")
    for e in events:
        print(f"   {e['event_type']:<17} -> loan {e['loan_id']}, EMI {e['obligation_id']}  (policy v{e['policy_version']})")
    problems = integrity_problems(conn)
    print("\nIntegrity check: " + ("PASSED (no orphan records)" if not problems else f"FAILED {problems}"))
    conn.close()


if __name__ == "__main__":
    main()
