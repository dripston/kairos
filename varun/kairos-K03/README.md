# K03 – Synthetic account and loan spine

**Owner:** Varun · **Epic:** 01 Upload & launch · **MoSCoW:** Must · **Depends on:** K01 (Ojas)

| Tracker | Text |
|---|---|
| What to do | Create one synthetic borrower with two loans, consent, obligations and payment records. |
| Done when | Each event joins to the right obligation. Clearing one loan leaves the other open. |

## Dataset (tracker "Datasets" sheet)

Only **`PL_Dummy_Collections_Data_500.csv`** is used: the verified dev-fallback file ("use loan ID, EMI,
outstanding amounts, DPD and bucket for intake; pair with synthetic contact and payment events; keep results
labelled simulated"). **No dataset is generated.** The CSV is not committed (it holds phone numbers): download
it from the team Drive and put it next to `main.py`.

## The K03 borrower

`CUST-K03-0001` = two real rows of the file, linked to one customer (a labelled synthetic link; the file has
one loan per person and no customer ID):

| Loan | From the file | EMI | POS | TOS | DPD | K01 band |
|---|---|---:|---:|---:|---:|---|
| PL100033 | Ashok Yadav, Delhi, C3 | ₹18,862 | ₹1,44,904 | ₹1,47,672 | 15 | B1 |
| PL100034 | Ashok Yadav, Rajasthan, C3 | ₹9,362 | ₹64,467 | ₹69,199 | 4 | B1 |

**Added because the file has no such columns (all labelled):**
- **Consent:** SMS and WhatsApp yes; email, IVR and voicebot no. Stored as `source='assumed'`.
- **Payments:** one UPI payment that clears PL100033's bounced EMI.
- **Events:** two contact events on PL100034's bounced EMI.
- **Phone:** replaced with `TEST_PHONE`, so no real person can be reached.

## Run

```bash
python3 main.py --reset          # build the spine and print the report
python3 -m unittest -v           # 50 tests
python3 load_dataset.py PL_Dummy_Collections_Data_500.csv --reset   # optional: load all 500 rows with checks
```

Expected report (excerpt):

```
Loan PL100033 ...: OPEN, OVERDUE CLEARED   DPD 0 (CLEARED)   K01: CANCEL / OBLIGATION_CLEARED
Loan PL100034 ...: OPEN, OVERDUE           DPD 4 (B1)        K01: SEND / ELIGIBLE
Integrity check: PASSED (no orphan records)
```

## How "Done when" is proven (`tests/test_spine.py`)

| Requirement | Tests |
|---|---|
| One borrower, two loans, consent, obligations, payment records | `test_one_borrower_two_loans_with_every_record_type` |
| Each event joins to the right obligation | `test_every_event_joins_to_right_obligation`, `test_no_orphan_records`. The database **rejects** an event without an EMI or pointing at the other loan's EMI (2 tests). |
| Clearing one loan leaves the other open | `test_clearing_loan_a_leaves_loan_b_open`, `test_payment_on_loan_b_does_not_touch_loan_a` |
| Depends on K01 | `test_k01_cancels_cleared_loan_and_allows_open_loan`, using **Ojas's own** `policy/k01_reference_check.py` |
| Data equals the file | `test_embedded_rows_equal_the_real_file`: the test rows are exact copies of the CSV (phone excepted). It **skips** if the CSV is not present. |

Also covered:
- **Payment safety:** wrong loan, overpayment, zero, duplicate, reused bank reference, partial payment.
- **Consent and opt-out.**
- **E01:** K01 bands, missing or stale DPD → HOLD.
- **E15:** reversal reopens only that EMI.
- **Importer:** bad rows rejected with a reason; loading twice adds nothing.

## Rules from K01 (Ojas)

`policy/policy.json` and `policy/k01_reference_check.py` are identical copies of Ojas's K01 v1.1.0. DPD bands,
channels and the missing/stale DPD rule are read from the policy file, never hard-coded. Every event stores
`policy_version`. When Ojas updates K01, replace both files and run the tests.

## Open items

- **Consent:** assumed until the official consent file (D11) arrives.
- **Remaining EMIs:** does "Remaining EMIs" include the overdue EMI? Assumed **no** (`spine/config.py`). Confirm with the data owner.
- **Official data:** switch to D1/D2/D7 when K02 delivers them.
