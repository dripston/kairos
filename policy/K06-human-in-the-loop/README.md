# K06 · Human-in-the-loop approval policy — v0.1.0 (DRAFT, pending Ojas)

**Status:** DRAFT written by engineering so the backend has something to read. **Ojas (Product) must confirm every number and list below** before it is treated as policy. Environment: SIMULATED.
**File:** `approval_policy.json` · **Consumer:** `backend/kairos_backend/hitl.py` (routes under `/hitl`, see [`backend/README.md`](../../backend/README.md#human-in-the-loop))

> A person can always make outreach stricter. Nobody — operator, supervisor or policy owner — can make it looser than K01: consent, STOP, hard caps, quiet days, the contact window and the approved-template rule are checked again when a send is approved and again when it is sent.

## 1. Which sends need a person's approval

A send that passed every K01 rule is held as `PENDING_APPROVAL` when any of these match:

| Rule | When | To confirm |
|---|---|---|
| `VOICE_CHANNEL` | IVR or voicebot | Both channels? |
| `STANDARD_INTENSITY_HIGH_DPD` | Template intensity `STANDARD` and loan DPD above **60** | Threshold (Control Room says "60+ DPD") |
| `FIRST_SEND_AFTER_HARDSHIP` | First send after a hardship hold ended | Also after other holds? |
| `MANUAL_DISPATCH` | Any send from the manual scheduler | Keep maker-checker? |

A held send keeps its cap slot and budget. It **expires at the end of the contact window** on its day and is canceled (budget released). Unregistered template intensity is treated as `STANDARD`.

## 2. Who may do what

| Permission | Roles |
|---|---|
| View (masked) | viewer, operator, supervisor, policy owner |
| Approve | supervisor, policy owner |
| Reject, pause, resume own pause, hold, cancel next, hand off, assign, do-not-contact, manual schedule / cancel / reschedule, claim / resolve case | operator, supervisor, policy owner |
| Clear a hold a person didn't place (K13 abusive / legal / fraud), lift a hold when resolving a case, reopen a case, record a borrower's per-channel opt-in after DNC | supervisor, policy owner |
| Set template intensity / preview body | policy owner |

Maker-checker: whoever scheduled a manual send cannot approve it.

## 3. Locked by guardrails (refused for everyone, audited)

Override STOP · override consent · exceed a cap · bypass quiet hours · bypass the contact window · send an unregistered template · force a send · undo do-not-contact. Do-not-contact is lifted only by the borrower opting back in on **one** channel, with the consent version recorded.

## 4. Manual scheduler, cases, masking

- **Dedupe:** one live send per EMI + channel + day, automated or manual (K01 caps and the 120-minute gap apply on top).
- **Cases:** handoff SLA 4 hours; resolution codes listed in the JSON; resolving never lifts a hold unless the resolver asks for it explicitly.
- **Masking:** viewer sees initials, last 2 phone digits, no amounts or emails; other roles see last 4 phone digits. The audit never stores raw PII.
