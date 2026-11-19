# Format alignment: K03 spine / K15 replay vs backend (K06) and crm (K05/K14)

Checked 19 Nov 2026 against `main` @ 3e391db and the policy bundle `kairos-policy.zip`.

## Already aligned

| Item | Status |
|---|---|
| K01 policy (`policy/K01-decision-policy/*`) and K13 (`policy/K13-operational-exceptions/*`) | `kairos-policy.zip` is identical to `main` (12/12 files; `K01 test_cases.csv` differs only in CRLF line endings). The spine's old `policy/policy.json` copy was identical too; it now reads the shared file. |
| Channel names | `sms, whatsapp, email, ivr, voicebot` everywhere (from K01). |
| DPD bands | PRE_DUE, B1 1–30, B2 31–60, B3 61–90, B4 91+ from K01 in spine, replay and backend gate. |
| Missing / stale DPD | Spine `file_dpd` + `dpd_as_of`; backend `dpd` + `dpd_updated_at`; both → K01 DPD_INVALID hold. |
| Dataset columns | Synthetic 50K keeps every `PL_Dummy_Collections_Data_500` column, plus five `<Channel> Consent (T/F)` columns and `Opted Out (T/F)`. |

## Needs a decision (owner in brackets)

| # | Difference | Proposal |
|---|---|---|
| 1 | **K15 caps are placeholders** (`MAX_CONTACTS_PER_DAY=1`, `MAX_CONTACTS_PER_7_DAYS=3`, `MIN_DAYS_BETWEEN_CONTACTS=2`) and conflict with approved K01 1.1.0 (per channel per day by band 3/6/9/12, 120-min same-channel gap, no weekly cap unless a lender sets one). [Varun, Ojas] | Replay should call the backend K01 gate (`kairos_backend.gate.evaluate`) instead of its own counters, so both runs use the approved caps. |
| 2 | **Money units**: spine = integer paise; backend = REAL rupees (`est_cost_inr`, `overdue_inr`, compared at 1/100 paisa); crm = integer rupees. [Rehan, Varun, Yuval] | Store and exchange integer paise; convert to ₹ only for display. |
| 3 | **Per-contact costs** (placeholder until D10): K15 `COST_PAISE` SMS 25, WhatsApp 40, email 5, IVR 150, voicebot 1200; K01 test cases use SMS ₹0.20, WhatsApp ₹0.40, email ₹0.05, IVR ₹1.50. [Ojas, Varun] | One table in `policy/` (or `replay/settings.py` only) that every component reads. |
| 4 | **Time slots**: K15 `morning/afternoon/evening`; the K09 contract draft used four 3-hour slots. [Rehan, Varun] | Use K15's three slots, defined as hour ranges inside the K01 window 08:00–19:00. |
| 5 | **Consent version** is missing in the spine (`consents` has consented/opted_out/source only); backend and E02 need the version per channel. [Varun] | Add `consent_version TEXT` to `consents` and a `<Channel> Consent Version` column (optional) to the dataset. |
| 6 | **Obligation model**: spine has borrower → loan → per-installment obligation (`obligation_id`, `installment_no`, `due_date`); backend `obligations` has no `loan_id`; crm treats the overdue EMI per loan. [Rehan, Yuval] | Use the spine's obligation IDs as backend `obligations.id` and add `loan_id`; crm `loan_id` stays the loan. |
| 7 | **Borrower IDs**: spine `CUST-K03-0001` / `SYN-000123`; crm demo `B-1042`. [Yuval] | Use spine IDs in crm seeds once the spine DB is the source. |
| 8 | **Event vocabulary**: spine `events.event_type` ∈ touch_sent, reply_received, payment_received, payment_reversed, consent_changed; backend `audit.type`; crm envelope types (`touch.sent`, `provider.delivery`, `borrower.intent`, `supervisor.decision`, `payment.verified`, …). [all] | crm envelope as the wire format; spine and backend map their rows to it (one table in this file once agreed). |
| 9 | **Outcome timing**: the DEV simulator resolves replies the same day; K01 attribution is a 24 h window with one reward per touch. | The K09 outcome finalizer adds window close, pending and late replies; the simulator can keep same-day draws. |
