# K22 · Approved template catalog (PLACEHOLDER)

> **How to continue.** `catalog.json` here is **synthetic** (`"status": "PLACEHOLDER"`, version `0.1.0-placeholder`). None of these templates is registered with any provider. The ids, purposes, intensities and bands are realistic, so the selector, the Gate and the demo all work, but the `provider_template_name` values (`plh_…` / `PLH-…`) are invented. **When the official D13 registered-template file arrives:** (1) map each D13 row to the fields below. D13 must give us, per template, the channel, language, provider-registered name/ID (WhatsApp approved name, SMS DLT template, IVR/voicebot script), approval status and variables. **We** add `purpose`, `intensity` and `dpd_bands` if D13 doesn't have them. (2) Replace `catalog.json` (keep the file name, or point `TemplateCatalog.load(path)` at the new file). Set `catalog_version` to the D13 version and drop `"status": "PLACEHOLDER"`. (3) Run the tests: `cd backend && python -m pytest -q tests/test_templates.py`, then the full suite. Coverage tests assert every channel × en/hi has a LOW and a STANDARD reminder. If D13 lacks one, that is a real gap: the selector will WAIT on that channel, and the test should be changed to state the gap, not padded with a fake template. (4) Call `TemplateCatalog.load().sync_to_ledger(ledger)` once at startup. It upserts every D13 template and, by default, withdraws (`approved=0`) any ledger template no longer in the catalog, so placeholder ids can no longer pass the Gate. Code: `backend/kairos_backend/templates.py`. Tests: `backend/tests/test_templates.py`.

**Owner:** Yuval (K22) · **Depends on:** K01 policy 1.1.0 (`templates`, `channels`, `dpd_bands`), K13 1.0.0 (intents) · **Consumers:** Supervisor (K06), Gate `TEMPLATE_APPROVED`, frontend template table

## What the selector does

`select(channel, language, purpose, intensity, band)` returns `SEND` + a template id, or `WAIT` + a reason, for **one channel**. `select_for_channels` gives one answer per channel, so a WAIT on one channel never blocks another.

| Rule | Source | WAIT reason |
|---|---|---|
| Approved templates only | K01 `templates.approved_only` | `NO_APPROVED_TEMPLATE` |
| Language must be in K01 `templates.languages` (en, hi) and match exactly. Never falls back to another language | K01 `templates.languages`, `no_suitable_template` | `LANGUAGE_NOT_SUPPORTED` / `NO_APPROVED_TEMPLATE` |
| Permitted intensity `WAIT` → WAIT | agent 3 | `INTENSITY_WAIT` |
| Exact intensity preferred. `LOW` never escalates to `STANDARD`. `STANDARD` may de-escalate to `LOW` (reason `DE_ESCALATED`) | K22 spec | `NO_APPROVED_TEMPLATE` |
| Template must list the borrower's band | K01 `dpd_bands` | `NO_APPROVED_TEMPLATE` (`NOT_IN_SCOPE` if no band) |
| Channel must be allowed in that band | K01 `channels_allowed_by_band` | `CHANNEL_NOT_ALLOWED` |
| Ties → lowest id | determinism | (none) |

Inputs stay distinct, as the spec requires: the selector reads only the *permitted contact intensity*. It never derives intensity from reply confidence or payment likelihood.

### Intent → purpose

| Intent (agents 1–2 / K13 event) | Purpose |
|---|---|
| `NEEDS_TIME` | `ASK_PAYMENT_DATE` |
| `PTP`, `PTP_OFFERED` | `PTP_CONFIRM` |
| `CALLBACK`, `CALLBACK_REQUEST` | `CALLBACK_ACK` |
| `HARDSHIP` | `HARDSHIP_ACK` |
| `PAID`, `PAYMENT_VERIFIED` | `PAYMENT_RECEIVED` |
| `ABUSIVE`, `LEGAL_THREAT`, `FRAUD_CLAIM`, `STOP`, `OPT_OUT` | none → WAIT `INTENT_NOT_AUTOMATED` |
| anything else / none | `REMINDER` |

## Catalog fields

| Field | Meaning |
|---|---|
| `id` | Kairos template id. The naming follows the frontend (`WA_REMIND_HI_SOFT_02`, `SMS_REMIND_HI_01`, …). This is what the Ledger and Gate store |
| `channel` | K01 channel name: `sms`, `whatsapp`, `email`, `ivr`, `voicebot` |
| `lang` | lowercase code. Only K01 languages (`en`, `hi`) are ever selected |
| `purpose` | `REMINDER`, `ASK_PAYMENT_DATE`, `PTP_CONFIRM`, `PAYMENT_RECEIVED`, `CALLBACK_ACK`, `HARDSHIP_ACK` |
| `intensity` | `LOW` or `STANDARD` |
| `dpd_bands` | K01 band ids where the template may be used |
| `approved` | `true`/`false` (strict boolean). Unapproved templates load but are never selected and fail the Gate |
| `version` | positive integer, per template |
| `provider_template_name` | provider-registered name (WhatsApp approved name, SMS DLT template, IVR/voicebot script) |
| `variables` | placeholders the sender must fill |
| `notes` | optional. Extra fields are kept and ignored |

## What the placeholder covers

- `REMINDER`: all 5 channels × en/hi × LOW (all bands) and STANDARD (B1–B4 only, so a pre-due STANDARD request de-escalates to LOW).
- `ASK_PAYMENT_DATE`, `PTP_CONFIRM`, `PAYMENT_RECEIVED`, `CALLBACK_ACK`, `HARDSHIP_ACK` on SMS and WhatsApp × en/hi, all LOW (plus a STANDARD WhatsApp `ASK_PAYMENT_DATE` for B1–B4), and voicebot `CALLBACK_ACK`.
- Two **unapproved** templates prove the filter. `WA_DUE_EN_00` would win the id tie-break if approval were ignored. `EM_ASKDATE_EN_01` is the only email date-ask, so email + `ASK_PAYMENT_DATE` must WAIT.
- No real phone numbers, brand names or provider ids. `lender_name` is a variable.

The file is validated on load (`CatalogError` lists every problem with the template id): K01 channels and bands, purposes, intensities, strict booleans, unique ids.
