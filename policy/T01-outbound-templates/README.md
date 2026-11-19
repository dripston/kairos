# Outbound Template Catalogue — v1.0.0 (draft for sign-off)

**Owner:** Ojas (sign-off on templates, per owner card) · **Consumers:** Yuval (template selector K22), Rehan (SMS/WhatsApp/email/IVR adapters), Varun (the learning engine chooses between variants)
**Files:**
- `template_source.json`: **the file to edit.** 30 message cores (5 bands × 6 angles) in English and Hindi, email subjects, forbidden words, sample values.
- `templates.csv`: **generated.** 300 rows, ready to load. Don't edit by hand.
- `reference_check.py`: rebuilds `templates.csv` and checks it (`python3 reference_check.py` → PASS, 0 problems).

## 1. What's in it
**30 templates per channel** (5 DPD bands × 6 angles) × 5 channels = **150 templates**, each in **English and Hindi** = **300 messages**.

| Angle | Idea | Call to action |
|---|---|---|
| A · Plain | Straight reminder | Pay link |
| B · Ease | Pay in a minute by UPI | Pay link |
| C · Consequence (factual) | Pre-due: keep balance for auto-debit · 1–60: late charges · 61–90: credit record · 91+: as per loan agreement | Pay link |
| D · Help | Offer of help; 61+ offers a person | HELP (CALL from 61 DPD) |
| E · Date ask | Ask for a payment date | Reply with date (IVR: press 4) |
| F · Credit / part payment | Credit-record benefit; 61–90: part payment helps | Pay link |

**Tone ladder:** pre-due friendly → 1–30 reminder → 31–60 firm (late charges) → 61–90 serious (credit record, a person) → 91+ formal notice. **No threats, no shaming, no mention of family, neighbours or employer.** A forbidden-word list (English and Hindi) is enforced by the checker.

## 2. Channel formats
| Channel | Format | Built-in compliance |
|---|---|---|
| **SMS** | `{lender_name}: <message> <CTA>` | English uses "Rs", not ₹, to stay in plain GSM text: **max 149 of 160 characters**. Hindi ≤ 3 Unicode segments. `dlt_body` column has variables as `{#var#}` for DLT registration. |
| **WhatsApp** | Greeting, message, amount/loan line, CTA, signature | **"Reply STOP to opt out"** on every template. `meta_body` has numbered `{{1}}` variables and `meta_variables` the mapping, ready for Meta submission. Category UTILITY. |
| **Email** | Subject + body | Subject per band and angle; **unsubscribe link** on every email. |
| **IVR** | `[OPENER]` then `[AFTER_CONFIRM]` | **The opener says nothing about the loan:** "This call is for {name}. If you are {name}, press 1." Amounts and loan details only after confirmation. Keypad: 1 link by SMS · 2 team · 3 already paid · 4 give a date. |
| **Voicebot** | `[OPENER]` then `[AFTER_CONFIRM]` | Same rule: "Am I speaking with {name}?" first; one open question at the end, matched to the angle. |

Voice versions read naturally: "8,500 rupees", "loan ending 4321".

## 3. Placeholders
`{name} {lender_name} {emi} {amount} {due_date} {link} {loan_last4} {helpline} {unsubscribe_link}`. Pre-due templates use `{emi}` and `{due_date}` and **never** mention an overdue amount (checked).

## 4. How the engine uses it
The template selector (K22) filters by **channel × band × language**, then the learning engine (K09) picks among the **6 angles**, measuring replies per rupee per angle. That covers the "template choice" part of the CM-02 brief. Intensity (K21) decides how many touches; the angle decides the wording.

## 5. Defaults applied (you didn't pick; change if needed)
1. **Demo lender "Nirvana Finance"** (fictional). Keeps real client names out of the hackathon; real use fills `{lender_name}`.
2. **Tone ladder** as above.
3. **Content:** amount, payment link, last 4 of the loan, reply option. Note: the amount is visible on a lock screen.

## 6. Before going live
- **WhatsApp:** submit the 60 WhatsApp templates (30 × 2 languages) to Meta **now**. Approval can take hours and blocks any live send. Start with the 12 the demo needs: B2 (borrower B-1042 is at 42 DPD), all 6 angles, English and Hindi.
- **SMS:** register on DLT with the lender's sender ID before any real send.
- **Hindi:** have a native speaker read it once. It was written for this build and hasn't been proofread.
