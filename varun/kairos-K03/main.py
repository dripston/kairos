"""K03: builds the account and loan spine from the team dataset and prints a report.

Run:          python3 main.py                     (finds PL_Dummy_Collections_Data_500.csv automatically)
              python3 main.py path/to/PL_Dummy_Collections_Data_500.csv
Fresh start:  add --reset

The dataset is the one in the tracker's 'Datasets' sheet (verified Drive file). Nothing is invented:
both loans are real rows of that file. If the file is not found, the program stops and says where to put it.
"""
import os
import sys

from spine.config import CHANNELS, DATASET_AS_OF_DATE, DB_PATH
from spine.dataset_file import NAME, find_dataset
from spine.db import get_connection, integrity_problems
from spine.k01_gate import k01_decision
from spine.ledger import borrower_summary, is_contactable, rupees
from spine.policy import policy_version
from spine.seed import DATASET_CUSTOMER_ID, DATASET_LOAN_A, DATASET_LOAN_B, build_spine_from_dataset


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    path = find_dataset(args[0] if args else None)
    if not path:
        print(f"{NAME} not found.\nDownload it from the team Drive (tracker 'Datasets' sheet) and put it next to main.py,\n"
              f"or run: python3 main.py /path/to/{NAME}")
        sys.exit(1)
    if "--reset" in sys.argv and os.path.exists(DB_PATH):
        os.remove(DB_PATH)
        print(f"Deleted old {DB_PATH}")

    conn = get_connection()
    build_spine_from_dataset(conn, path)
    borrower_id, as_of = DATASET_CUSTOMER_ID, DATASET_AS_OF_DATE

    borrower = conn.execute("SELECT * FROM borrowers WHERE borrower_id = ?", (borrower_id,)).fetchone()
    print(f"\nKairos account and loan spine (K03)  ·  as of {as_of}")
    print(f"Source: {path}\n        loans {DATASET_LOAN_A} + {DATASET_LOAN_B} linked to one customer")
    print("Label:  SIMULATED · development fallback (500-loan HDFC demo fixture, not official data)")
    print(f"Policy: K01 decision policy v{policy_version()} (policy/policy.json)")
    print("=" * 72)
    print(f"Borrower {borrower['borrower_id']}: {borrower['full_name']}  (test phone {borrower['phone']})")
    contact = ", ".join(f"{ch} {'yes' if is_contactable(conn, borrower_id, ch) else 'no'}" for ch in CHANNELS)
    print(f"Can contact on: {contact}  (consent assumed: the file has no consent column)\n")

    for loan in borrower_summary(conn, borrower_id, as_of):
        state = "OVERDUE CLEARED" if not loan["overdue"] and loan["outstanding_paise"] == 0 else "OVERDUE"
        print(f"Loan {loan['loan_id']} ({loan['product']}, risk {loan['risk_tag']}): {loan['status'].upper()}, {state}")
        print(f"   overdue to collect {rupees(loan['outstanding_paise'])}, DPD {loan['dpd']} ({loan['dpd_band']}), "
              f"future EMIs {loan['remaining_emis']}")
        for ob in loan["obligations"]:
            print(f"   EMI {ob['obligation_id']}  due {ob['due_date']}  {rupees(ob['amount_due_paise']):>12}  "
                  f"paid {rupees(ob['amount_paid_paise']):>12}  -> {ob['status']}, DPD {ob['dpd']}")
        decision, reason = k01_decision(conn, loan["loan_id"], "sms")
        print(f"   K01 (Ojas's reference) for a queued SMS: {decision} / {reason}\n")

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
