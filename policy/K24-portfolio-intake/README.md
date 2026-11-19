# K24 · Portfolio Upload + Scheduled Start — v1.1.0 (draft for sign-off)

**Owner:** Ojas (rules) · **Build:** Rehan · **Depends on:** K01 v1.1.0 (quiet days, window, budget, stop conditions)
**Files:** `intake_rules.json` (rules) · `sample_upload_v1.csv` / `sample_upload_v2.csv` (demo files, including demo borrower C-1042 with 2 loans and one example of every bad row) · `expected_v1.csv`, `expected_merge_v2.csv` · `reference_check.py` (stdlib validator + preview + merge; `python3 reference_check.py` → 31/31 pass)

## 1. File
CSV or XLSX, header in row 1. Column names are matched **case-insensitively, ignoring spaces, hyphens, dots and underscores**, with common aliases ("Loan No", "LAN", "CIF", "Mobile", "EMI Due Date", "Days Past Due" …), so lenders don't need to reformat.

## 2. Mandatory fields (decided: core 8)
`loan_id`, `customer_id`, `borrower_name`, `registered_mobile`, `emi_amount`, `overdue_amount`, `due_date`, `dpd`.
Optional: email, language, per-channel consents, payment history, NACH bounce reason, AA consent, location, product.

## 3. Quarantined at upload (decided: invalid data only)
| Reason | Rule |
|---|---|
| `MISSING_FIELD:<name>` | A mandatory field is empty |
| `INVALID_MOBILE` | After removing spaces, +91 and a leading 0: not 10 digits starting 6–9 |
| `INVALID_AMOUNT` | EMI not above 0, or overdue negative or non-numeric |
| `INVALID_DATE` | Due date not a real date (YYYY-MM-DD, DD-MM-YYYY, DD/MM/YYYY) |
| `INVALID_DPD` | DPD not a whole number |
| `DUPLICATE_LOAN_ID` | Same loan twice in one file: **all copies** quarantined, because we can't tell which is right |

**Loaded with a warning (not rejected):** DPD before −6 (K01 won't contact it); DPD differs from the due date by more than a day (the file's DPD is used); no consent columns (those channels count as not consented).
**Enforced at send time, not upload:** STOP/do-not-contact, a loan in two campaigns (the Supervisor sends once).

## 4. Grouping (decided: by customer_id)
- **One contact plan per customer.** K01 caps, holds, STOP and stop-for-day apply across all of a customer's loans, so a borrower with two loans never gets two message streams.
- **Money stays per loan.** Payments, receipts, DPD and clearing are tracked per loan, so paying one EMI leaves the other open (demo scene S5).
- Any registered mobile on any of the customer's loans is valid for Kaira's identity check (K10).

## 5. Re-upload mid-campaign (decided: merge and update)
| Loan in new file? | Result |
|---|---|
| Existing, values changed | **UPDATED**: amounts, DPD and dates refreshed; journey, holds, promises and history **kept** |
| Existing, same values | UNCHANGED |
| New | **ADDED** and enrolled |
| Missing from new file | **REMOVED_BY_LENDER**: no new sends; history kept |

Every upload gets an `upload_id` and version number. **Uploading the same file twice changes nothing** and queues nothing.

## 6. Budget scope (decided: explicit loan set)
- **A case = one loan account**, always linked to its customer. A customer with 2 loans = 2 cases.
- **The budget is calculated only on an explicitly selected set of loans.** Eligible = accepted at upload and in scope (DPD ≥ −6). The ops user selects which eligible loans are budgeted, either as a list (or a `budget_flag` column) or as a filter resolved to a fixed list at arm time.
- **Not specified → arming blocked.** The preview proposes "all eligible loans" and the user must confirm it.
- **Never budgeted:** quarantined rows, DPD before −6, loans removed by the lender, loans not selected.
- **Pool = budget per case × budgeted loans.** Only budgeted loans are contacted for collection.
- **Cost per touch:** a message to a customer is charged across that customer's budgeted loans in equal shares (₹0.40 to C-1042 = ₹0.20 per loan).
- **Re-upload never changes the set silently.** New loans join only when the user selects them (or confirms a filter match). Removed loans leave the set; the pool recomputes; spend already incurred stays counted.

## 7. Campaign window and start
- **Inputs:** allocation start date, allocation end date, budget per case, collection target (amount % and count %), optional lender caps (K01 format).
- Loan dates (due date) and campaign dates (allocation) are kept separate.
- **Preview before arming:** rows received, accepted, quarantined by reason, warnings, customers, customers with more than one loan, campaign pool (budget per case × budgeted loans), first and last contact time.
- **Start:** automatically at **08:00 on the first contact day** on or after the allocation start. Sundays and national holidays are skipped (e.g. start 22 Nov, a Sunday → first send Mon 23 Nov 08:00).
- **End:** no new outbound sends **from 19:00 on the allocation end date** (end date inclusive). Kaira keeps answering inbound messages.
- **Early stop:** K01 rules: budget pool used up, or collection target met.

## 8. Example preview (sample_upload_v1.csv, ₹10 per case, 22 Nov – 21 Dec)
14 rows → **6 accepted**, 8 quarantined (2 duplicate, 2 bad amount, 1 bad mobile, 1 missing name, 1 bad date, 1 bad DPD) · 3 warnings · 5 customers, 1 with two loans · 5 eligible (L1011 out of scope), user confirms all 5 → pool ₹50, 4 customers covered · first contact 23 Nov 08:00 · last contact 21 Dec before 19:00 · 25 contact days.

## 9. Decisions log and open items
**Decided (19 Nov, Ojas):** core 8 mandatory; quarantine invalid data only; group by customer_id; merge on re-upload.
**Decided (19 Nov, Ojas):** a case is a loan account linked to its customer; budgeting always on an explicitly specified set of loans.
**Open:** quarantined rows can be fixed and re-uploaded (merge adds them as new); no in-app editing.
