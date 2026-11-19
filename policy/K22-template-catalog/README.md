# K22 · Template catalog (collections-compliance reviewed, synthetic)

> **What "approved" means here.** Every template carries an `approval` block. `APPROVED_FOR_DEMO` means the template **passed the Kairos collections-compliance checklist** (`backend/kairos_backend/template_compliance.py`, 14 deterministic rules) for the **synthetic demo**. It is **not** approval for real borrowers. Real use still needs **(1) the lender's compliance/legal sign-off, (2) TRAI DLT registration of every SMS template under the lender's header, (3) Meta approval of every WhatsApp template (UTILITY category), and (4) recorded IVR prompts.** `catalog.json` is still `"status": "PLACEHOLDER"` (version `0.2.0-placeholder`): provider ids are placeholders (`PENDING_DLT`, `PENDING_HEADER`, `plh_…`).

**Owner:** Yuval (K22) · **Depends on:** K01 policy 1.1.0 (`templates`, `channels`, `dpd_bands`), K13 1.0.0 (intents) · **Consumers:** Supervisor (K06), Gate `TEMPLATE_APPROVED`, frontend template table

## Where the text comes from

All wording is built from CreditNirvana's own material, not invented from scratch:

- **CN "Personal Loan — 10-Day Post-Due Digital Collections Strategy" (HDFC project):** the four fixed tone tiers (T1 Informational, T2 Standard reminder, T3 Escalated, T4 Final notice) for WhatsApp, SMS and the one-way IVR voice blast, and the optional NACH re-presentment notice. The T1 WhatsApp and SMS texts are CN's, with merge fields swapped in. Kairos adds email T1–T4 in the same tones.
- **CN Collection Orchestration Engine PRD v3:** a template library per channel, merge fields rendered from loan data, every channel keeps at least one template, PTP journeys pause nudges until the promised date.
- **HDFC demo notes:** DLT templates, dynamic merge tags, character validation, English + Hindi (regional languages later).
- **K01 / K13:** languages `en`/`hi`, approved templates only, no suitable template → WAIT. Purposes for K13 events: `ASK_PAYMENT_DATE`, `PTP_CONFIRM`, `PAYMENT_RECEIVED`, `CALLBACK_ACK`, `HARDSHIP_ACK`, plus `STOP_CONFIRM` for STOP handling.
- **Sarvam voice agents** `Kairos - EMI Reminder Call (K17 IVR)` and `Kairos - Callback Voicebot (K18)` (both draft v1, read on 2026-11-19). They own the opening and closing lines of each call. The catalog rows (`type: voicebot_script`) give the Gate a template id for each voice touch and record the agent's `app_id`, `prompt_sha`, opening line and controls.

**Changes from the CN source (flag if wrong):**

- **"Penal interest" → "penal charges".** RBI's penal-charges circular (Aug 2023, in force from 1 Jan 2024) says lenders levy *penal charges*, not *penal interest*. The checklist treats "penal interest" as a misleading consequence.
- **T4 is limited to B2+ (31+ DPD)**, as this build specifies. CN's 10-day plan sends T4 inside days 1–10, which is B1. If Product wants T4 within the 10-day window, widen `dpd_bands` on the T4 rows (the checklist's `TIER_BANDS` rule and `TIER_BANDS_ALLOWED` must change too).
- **IVR blasts start with the intended recipient** ("This message is for {{name}}. If you are not {{name}}, please disconnect…") before any loan detail. A one-way blast cannot verify identity, so this limits disclosure to whoever picks up.
- **English SMS were shortened to fit one 160-character GSM-7 segment** at maximum placeholder length, and use `Rs.`, because `₹` would force Unicode.
- **K18 `callback_reason`.** The agent's default value ("…about your loan EMI") mentions the loan before identity is confirmed. The catalog pins `callback_reason` to "you asked us to call you back".

## The template set

83 templates: 82 `APPROVED_FOR_DEMO`, 1 `FAILED_CHECKLIST` (the negative control). Counts by channel: SMS 24, WhatsApp 25, email 22, IVR 8, voicebot 4. Counts by language: en 42, hi 41.

| Channel | Purpose / tier | Intensity | Bands | en | hi |
|---|---|---|---|---|---|
| sms | REMINDER · T1 Informational | LOW | B1–B4 | `SMS_REMIND_EN_01` (approved) | `SMS_REMIND_HI_01` (approved) |
| sms | REMINDER · T2 Standard | LOW | B1–B4 | `SMS_LATEFEE_EN_01` (approved) | `SMS_LATEFEE_HI_01` (approved) |
| sms | REMINDER · T3 Escalated | STANDARD | B1–B4 | `SMS_DUE_EN_01` (approved) | `SMS_DUE_HI_02` (approved) |
| sms | REMINDER · T4 Final notice | STANDARD | B2–B4 | `SMS_FINAL_EN_01` (approved) | `SMS_FINAL_HI_01` (approved) |
| sms | PRE_DUE_REMINDER | LOW | PRE_DUE | `SMS_PREDUE_EN_01` (approved) | `SMS_PREDUE_HI_01` (approved) |
| sms | NACH_REPRESENTMENT | LOW | B1 | `SMS_NACH_EN_01` (approved) | `SMS_NACH_HI_01` (approved) |
| sms | ASK_PAYMENT_DATE | LOW | PRE_DUE–B4 | `SMS_ASKDATE_EN_01` (approved) | `SMS_ASKDATE_HI_01` (approved) |
| sms | PTP_CONFIRM | LOW | PRE_DUE–B4 | `SMS_PTPCONF_EN_01` (approved) | `SMS_PTPCONF_HI_01` (approved) |
| sms | PAYMENT_RECEIVED | LOW | PRE_DUE–B4 | `SMS_PAIDACK_EN_01` (approved) | `SMS_PAIDACK_HI_01` (approved) |
| sms | CALLBACK_ACK | LOW | PRE_DUE–B4 | `SMS_CBACK_EN_01` (approved) | `SMS_CBACK_HI_01` (approved) |
| sms | HARDSHIP_ACK | LOW | PRE_DUE–B4 | `SMS_HARDSHIP_EN_01` (approved) | `SMS_HARDSHIP_HI_01` (approved) |
| sms | STOP_CONFIRM | LOW | PRE_DUE–B4 | `SMS_STOP_EN_01` (approved) | `SMS_STOP_HI_01` (approved) |
| whatsapp | REMINDER · T1 Informational | LOW | B1–B4 | `WA_REMIND_EN_SOFT_01` (approved) | `WA_REMIND_HI_SOFT_02` (approved) |
| whatsapp | REMINDER · T2 Standard | LOW | B1–B4 | `WA_LATEFEE_EN_01` (approved) | `WA_LATEFEE_HI_01` (approved) |
| whatsapp | REMINDER · T3 Escalated | STANDARD | B1–B4 | `WA_PAYLINK_EN_01` (approved) | `WA_PAYLINK_HI_01` (approved) |
| whatsapp | REMINDER · T3 Escalated (legacy draft, negative control) | STANDARD | B1–B4 | `WA_DUE_EN_00` **FAILED_CHECKLIST** | — |
| whatsapp | REMINDER · T4 Final notice | STANDARD | B2–B4 | `WA_FINAL_EN_01` (approved) | `WA_FINAL_HI_01` (approved) |
| whatsapp | PRE_DUE_REMINDER | LOW | PRE_DUE | `WA_PREDUE_EN_01` (approved) | `WA_PREDUE_HI_01` (approved) |
| whatsapp | NACH_REPRESENTMENT | LOW | B1 | `WA_NACH_EN_01` (approved) | `WA_NACH_HI_01` (approved) |
| whatsapp | ASK_PAYMENT_DATE | LOW | PRE_DUE–B4 | `WA_ASKDATE_EN_01` (approved) | `WA_ASKDATE_HI_01` (approved) |
| whatsapp | PTP_CONFIRM | LOW | PRE_DUE–B4 | `WA_PTPCONF_EN_01` (approved) | `WA_PTPCONF_HI_01` (approved) |
| whatsapp | PAYMENT_RECEIVED | LOW | PRE_DUE–B4 | `WA_PAIDACK_EN_01` (approved) | `WA_PAIDACK_HI_01` (approved) |
| whatsapp | CALLBACK_ACK | LOW | PRE_DUE–B4 | `WA_CBACK_EN_01` (approved) | `WA_CBACK_HI_01` (approved) |
| whatsapp | HARDSHIP_ACK | LOW | PRE_DUE–B4 | `WA_HARDSHIP_EN_01` (approved) | `WA_HARDSHIP_HI_01` (approved) |
| whatsapp | STOP_CONFIRM | LOW | PRE_DUE–B4 | `WA_STOP_EN_01` (approved) | `WA_STOP_HI_01` (approved) |
| email | REMINDER · T1 Informational | LOW | B1–B4 | `EM_STATEMENT_EN_01` (approved) | `EM_STATEMENT_HI_01` (approved) |
| email | REMINDER · T2 Standard | LOW | B1–B4 | `EM_LATEFEE_EN_01` (approved) | `EM_LATEFEE_HI_01` (approved) |
| email | REMINDER · T3 Escalated | STANDARD | B1–B4 | `EM_DUE_EN_01` (approved) | `EM_DUE_HI_01` (approved) |
| email | REMINDER · T4 Final notice | STANDARD | B2–B4 | `EM_FINAL_EN_01` (approved) | `EM_FINAL_HI_01` (approved) |
| email | PRE_DUE_REMINDER | LOW | PRE_DUE | `EM_PREDUE_EN_01` (approved) | `EM_PREDUE_HI_01` (approved) |
| email | ASK_PAYMENT_DATE | LOW | PRE_DUE–B4 | `EM_ASKDATE_EN_01` (approved) | `EM_ASKDATE_HI_01` (approved) |
| email | PTP_CONFIRM | LOW | PRE_DUE–B4 | `EM_PTPCONF_EN_01` (approved) | `EM_PTPCONF_HI_01` (approved) |
| email | PAYMENT_RECEIVED | LOW | PRE_DUE–B4 | `EM_PAIDACK_EN_01` (approved) | `EM_PAIDACK_HI_01` (approved) |
| email | CALLBACK_ACK | LOW | PRE_DUE–B4 | `EM_CBACK_EN_01` (approved) | `EM_CBACK_HI_01` (approved) |
| email | HARDSHIP_ACK | LOW | PRE_DUE–B4 | `EM_HARDSHIP_EN_01` (approved) | `EM_HARDSHIP_HI_01` (approved) |
| email | STOP_CONFIRM | LOW | PRE_DUE–B4 | `EM_STOP_EN_01` (approved) | `EM_STOP_HI_01` (approved) |
| ivr | REMINDER · T1 Informational | LOW | B1–B4 | `IVR_REMIND_EN_SOFT_01` (approved) | `IVR_REMIND_HI_SOFT_01` (approved) |
| ivr | REMINDER · T2 Standard | LOW | B1–B4 | `IVR_LATEFEE_EN_01` (approved) | `IVR_LATEFEE_HI_01` (approved) |
| ivr | REMINDER · T3 Escalated | STANDARD | B1–B4 | `IVR_DUE_EN_01` (approved) | `IVR_DUE_HI_01` (approved) |
| ivr | REMINDER · T4 Final notice | STANDARD | B2–B4 | `IVR_FINAL_EN_01` (approved) | `IVR_FINAL_HI_01` (approved) |
| voicebot | REMINDER | LOW | B1–B4 | `VB_REMIND_EN_SOFT_01` (approved) | `VB_REMIND_HI_SOFT_01` (approved) |
| voicebot | CALLBACK_ACK | LOW | B1–B4 | `VB_CALLBACK_EN_01` (approved) | `VB_CALLBACK_HI_01` (approved) |

**`WA_DUE_EN_00` fails on purpose.** It is a legacy draft kept as a negative control. Its text is "Dear {{name}}, your loan {{loan_number}} is overdue. Pay {{amount}} immediately or legal action will be initiated and your employer will be informed." It fails 8 rules: `LENDER_IDENTIFIED`, `CONSEQUENCES_PERMITTED` ("legal action"), `NO_THIRD_PARTY_DISCLOSURE` ("employer"), `PAY_AND_HELP` (no helpline), `ACCOUNT_LAST4_ONLY` and `PLACEHOLDERS_ALLOWED` (`{{loan_number}}`), `LANGUAGE_PARITY` (no hi) and `PURPOSE_CONTENT` (T3 without days overdue). It sorts before `WA_PAYLINK_EN_01`, so it would win the id tie-break if approval were ignored. Tests prove it is never selected and that the Gate blocks it.

**Known gaps (stated, not padded):** the IVR blast is one-way, so it has no pre-due, NACH or K13-reply templates. Those WAIT on `ivr`. Voicebot has only the two Sarvam agents: a LOW reminder (K17, which only ever states late charges) and the callback (K18). A STANDARD voicebot reminder de-escalates to LOW. A pre-due voicebot touch WAITs.

### SMS length (at maximum placeholder length)

English SMS are GSM-7, 107–160 characters (one segment). Hindi SMS are Unicode (UCS-2: 70 characters for one segment, 67 per part in a multi-part SMS), 112–217 units, which is 2–4 segments. The checklist caps Hindi at 4 segments. Maximum placeholder lengths are `name` 20, `lender_name` 15 (the short brand registered with DLT), `amount` 9, dates 9, `payment_link` 22, `helpline` 13 and `loan_last4` 4.

## Merge fields

`{{name}}`, `{{lender_name}}`, `{{loan_last4}}`, `{{amount}}`, `{{due_date}}`, `{{days_overdue}}`, `{{payment_link}}`, `{{helpline}}`, `{{final_date}}`, `{{represent_date}}`, `{{promise_date}}`. No other placeholder passes the checklist. `variables` must equal the placeholders the text uses. The en and hi versions of a template (same `family`) must use the same set. For DLT registration each `{{field}}` becomes a `{#var#}` slot.

## The collections-compliance checklist

`python -m kairos_backend.template_compliance` prints the report. `--stamp` rewrites every `approval` block and `approved` flag after an edit. `tests/test_template_compliance.py` fails if the stored blocks differ from the live checklist. The rules are based on the RBI Fair Practices Code for lenders and RBI guidance on recovery agents and digital lending (no intimidation, harassment or humiliation, no disclosure to third parties, identify the lender), the RBI penal-charges circular, and TRAI TCCCPR 2018 (registered header + DLT content template).

| Rule | Checks |
|---|---|
| `LENDER_IDENTIFIED` | `{{lender_name}}` in the message. SMS also needs a `sender_id` (DLT header) |
| `NO_THREATS_OR_ABUSE` | No threat, intimidation, abuse or shaming terms (en + hi lists: arrest, police, jail, defaulter, shame, …) |
| `CONSEQUENCES_PERMITTED` | Only consequences the agreement allows (late fee, penal charges, bureau reporting, next stage of collections), introduced by tier: T1 none, T2 late fee, T3 + penal charges + bureau, T4 + next stage. Acknowledgements, pre-due and NACH notices: none. Always rejected: legal action, court, seizure, recovery-agent or home/office visits, "penal interest" |
| `NO_THIRD_PARTY_DISCLOSURE` | No telling or contacting the employer, family, relatives, neighbours, colleagues, friends or references |
| `PAY_AND_HELP` | `{{helpline}}` on every message. Reminders need a way to pay (`{{payment_link}}`; for IVR, "the link sent by SMS or WhatsApp"). NACH asks for sufficient balance |
| `IGNORE_IF_PAID` | T1 and pre-due reminders say to ignore the message if already paid |
| `ACCOUNT_LAST4_ONLY` | Account shown masked (`XX{{loan_last4}}`, "ending {{loan_last4}}", "अंतिम 4 अंक {{loan_last4}}"). No full-number fields, no literal runs of 5+ digits |
| `IDENTITY_STAGE` | The stage matches the purpose. Replies sent **before identity verification** (`ASK_PAYMENT_DATE`, `PTP_CONFIRM`, `CALLBACK_ACK`, `HARDSHIP_ACK`, `STOP_CONFIRM`) carry no amount, account or dates. `PAYMENT_RECEIVED` is **post-verification** and its `send_condition` says so (D7/LMS). IVR blasts name the recipient first. Voice openings mention nothing about the debt and ask to confirm `{{name}}` |
| `LENGTH_LIMITS` | SMS en: GSM-7 only and ≤ 160. SMS hi: ≤ 4 Unicode segments. WhatsApp ≤ 1024. Email subject ≤ 120. IVR ≤ 700. Voice opening ≤ 200 |
| `PLACEHOLDERS_ALLOWED` | Only the merge fields above. `variables` equals the fields used. No `[Name]`-style leftovers |
| `LANGUAGE_PARITY` | The en/hi pair exists with the same placeholders. hi is in Devanagari and en has none |
| `CHANNEL_REGISTRATION` | SMS: `dlt_template_id` + `sender_id`. WhatsApp: `wa_category` `UTILITY` + `wa_template_name`. Email: subject. IVR: `recording_status`. Voicebot: Sarvam agent name/app_id/prompt_sha + the six recorded agent controls |
| `PURPOSE_CONTENT` | T3 states days overdue. T4 states a hard date + next stage. PTP repeats the date and says reminders pause. Hardship and STOP never ask for payment. STOP says no further reminders. A receipt states the amount and has no pay link. Pre-due never says "overdue" |
| `TIER_BANDS` | T1/T2 = LOW, T3/T4 = STANDARD. T1–T3 B1–B4, T4 B2–B4. `REMINDER` never PRE_DUE. `PRE_DUE_REMINDER` only PRE_DUE. NACH is not PRE_DUE |

The checklist is deterministic keyword and structure checking. It catches the listed failure modes, but it cannot judge tone the way a compliance officer does. That is why lender sign-off is still required.

## What the selector does

`select(channel, language, purpose, intensity, band, *, intent=None, tier=None, payment_verified=False)` returns `SEND` + a template id, or `WAIT` + a reason, for **one channel**. `select_for_channels` gives one answer per channel, so a WAIT on one channel never blocks another. `tier_ladder(channel, language, band)` lists the approved T1–T4 ids. The cohort → tier → day schedule (C1 first-time bounce … C5 high-ticket) belongs to the strategy and Supervisor, not to the template. The tiers are reused across cohorts.

| Rule | Source | WAIT reason |
|---|---|---|
| Approved templates only | K01 `templates.approved_only` | `NO_APPROVED_TEMPLATE` |
| Language must be in K01 `templates.languages` (en, hi) and match exactly. Never falls back to another language | K01 | `LANGUAGE_NOT_SUPPORTED` / `NO_APPROVED_TEMPLATE` |
| Permitted intensity `WAIT` → WAIT | agent 3 | `INTENSITY_WAIT` |
| Exact intensity preferred. `LOW` never escalates. `STANDARD` may de-escalate to `LOW` (`DE_ESCALATED`) | K22 spec | `NO_APPROVED_TEMPLATE` |
| Without `tier`, the least intense tier that fits wins (T1 for LOW, T3 for STANDARD). `tier=` asks for one tier and may never exceed the permitted intensity | CN tiers | `TIER_EXCEEDS_INTENSITY` / `NO_APPROVED_TEMPLATE` |
| At `PRE_DUE`, a `REMINDER` request is served by `PRE_DUE_REMINDER` (K01: the pre-due purpose is "reminder") | K01 `dpd_bands` | `NO_APPROVED_TEMPLATE` (ivr/voicebot) |
| `PAYMENT_RECEIVED` only after D7/LMS verification (intent `PAYMENT_VERIFIED` or `payment_verified=True`). A borrower's `PAID` claim never gets a receipt | K13 / CM-03 | `PAYMENT_NOT_VERIFIED` |
| Template must list the borrower's band. Channel must be allowed in that band | K01 | `NO_APPROVED_TEMPLATE` (`NOT_IN_SCOPE` if no band) / `CHANNEL_NOT_ALLOWED` |
| Ties → (tier, lowest id) | determinism | (none) |

### Intent → purpose

| Intent (agents 1–2 / K13 event) | Purpose |
|---|---|
| `NEEDS_TIME` | `ASK_PAYMENT_DATE` |
| `PTP`, `PTP_OFFERED` | `PTP_CONFIRM` |
| `CALLBACK`, `CALLBACK_REQUEST` | `CALLBACK_ACK` |
| `HARDSHIP` | `HARDSHIP_ACK` |
| `PAYMENT_VERIFIED` | `PAYMENT_RECEIVED` |
| `PAID`, `CLAIMS_PAID`, `ALREADY_PAID` (unverified claim) | none → WAIT `PAYMENT_NOT_VERIFIED` until D7/LMS confirms |
| `ABUSIVE`, `LEGAL_THREAT`, `FRAUD_CLAIM`, `STOP`, `OPT_OUT` | none → WAIT `INTENT_NOT_AUTOMATED` |
| anything else / none | `REMINDER` (`PRE_DUE_REMINDER` at PRE_DUE) |

`STOP_CONFIRM` is never chosen from an intent. The STOP handler sends it once, on the channel the STOP arrived on, with `select(ch, lang, "STOP_CONFIRM", "LOW", band)`. All later outreach is cancelled by K01 `STOP_OPT_OUT`.

## Catalog fields

| Field | Meaning |
|---|---|
| `id` | Kairos template id (frontend naming kept: `SMS_REMIND_HI_01`, `WA_REMIND_HI_SOFT_02`, `WA_PAYLINK_*`, `SMS_DUE_HI_02`, `EM_STATEMENT_EN_01`, `IVR_DUE_*`, `VB_CALLBACK_HI_01`). Stored by the Ledger and Gate |
| `channel`, `type` | K01 channel. `type` is `sms`, `whatsapp`, `email`, `ivr_blast` or `voicebot_script` |
| `lang` | `en` or `hi` |
| `purpose` | `REMINDER`, `PRE_DUE_REMINDER`, `NACH_REPRESENTMENT`, `ASK_PAYMENT_DATE`, `PTP_CONFIRM`, `PAYMENT_RECEIVED`, `CALLBACK_ACK`, `HARDSHIP_ACK`, `STOP_CONFIRM` |
| `tier` | 1–4, REMINDER message templates only. Must match `intensity` |
| `intensity`, `dpd_bands` | `LOW`/`STANDARD`; K01 band ids |
| `family` | Pairs the en and hi versions |
| `identity_stage`, `send_condition` | `outbound_registered_contact`, `pre_verification`, `post_verification` (with its condition) or `in_call_identity_check` |
| `subject`, `body`, `opening_line` | Customer-facing text (email subject; voice opening line) |
| `variables` | The placeholders used (names without braces) |
| `approved` | Strict boolean. Must equal `approval.status == "APPROVED_FOR_DEMO"` (checked on load) |
| `approval` | `status`, `reviewed_by` ("Kairos collections compliance review (automated checklist)"), `reviewed_on` (2026-11-19), `checklist` (rule → PASS/FAIL/N/A), `failures`, `notes` |
| `version`, `provider_template_name` | Per-template version (2 = retained id with new text). Provider-registered name (placeholder) |
| SMS: `dlt_template_id`, `sender_id` · WhatsApp: `wa_category`, `wa_template_name`, `wa_language` · IVR: `recording_status` · voicebot: `sarvam_agent`, `agent_language`, `agent_variable_map`, `agent_controls` | Provider registration |

The loader validates K01 channels and bands, purposes, intensities, tiers (value, purpose and intensity), the type/channel fit, the approval/approved agreement, strict booleans and unique ids. Extra fields are kept.

## How to continue

1. **Lender compliance/legal sign-off.** Send the table above with the texts (`catalog.json`) to the lender's compliance and legal teams. Record their decision per template (e.g. `approval.lender_signoff: {by, on, ref}`) and only then treat a template as live. Keep re-running the checklist: it is the floor, not the ceiling.
2. **DLT registration (SMS).** Register each SMS body as a DLT content template under the lender's PE id and header (category *Service Implicit/Transactional* as the lender's DLT team decides), converting `{{field}}` to `{#var#}`. Put the returned id in `dlt_template_id` and the header in `sender_id`, replacing `PENDING_DLT` / `PENDING_HEADER`. The registered text must match the catalog body exactly.
3. **Meta approval (WhatsApp).** Submit each WhatsApp body as a **UTILITY** template named `wa_template_name` in `wa_language`, converting placeholders to `{{1}}`, `{{2}}`, … in order. Replace the `plh_` names with the approved names. If Meta re-categorises a template as MARKETING, do not send it for collections: fix the text and resubmit.
4. **IVR recordings.** Record T1–T4 in en and hi (TTS or voice artist), store the prompt ids, and set `recording_status`. Voicebot rows must be re-reviewed whenever a Sarvam agent's `prompt_sha` changes (re-read the agent, update `opening_line`/`prompt_sha`, re-stamp).
5. **Official D13 file.** When the registered-template file arrives, map each D13 row onto these fields, keep `purpose`/`tier`/`dpd_bands`/`family`/`identity_stage` (we own them), set `catalog_version` to the D13 version and drop `"status": "PLACEHOLDER"`. Run `python -m kairos_backend.template_compliance --stamp`, then the full suite. Call `TemplateCatalog.load().sync_to_ledger(ledger)` at startup: it upserts every template and withdraws ledger ids no longer in the catalog.
6. **Regional languages.** K01 allows only `en`/`hi` today. To add e.g. `mr`, `ta`, `bn`: add the language to K01 `templates.languages` (Product decision), add a `lang` row per family, and extend `LANGUAGE_PARITY` (pairs → every language in the family has the same placeholders, in the right script) plus the en/hi word lists in the checklist with that language's threat, third-party and consequence terms. Have a native speaker review the text. SMS in Indic scripts are Unicode, so check segments.
7. **Contact window.** The CN PRD flags 08:00–18:00 as needing regulatory validation. K01 uses 08:00–19:00. The callback acknowledgements state "8 AM to 7 PM, Monday to Saturday, except national holidays". Change both together if Product changes the window.

Code: `backend/kairos_backend/templates.py` (selector), `backend/kairos_backend/template_compliance.py` (checklist). Tests: `backend/tests/test_templates.py`, `backend/tests/test_template_compliance.py`.
