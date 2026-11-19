# policy/

The source of truth for what Kairos is **allowed** to do. Owned by Product (Ojas). Code reads these files and never hard-codes a number from them.

| Folder | What it is | Version | Consumed by |
|---|---|---|---|
| [`K01-decision-policy/`](K01-decision-policy/README.md) | Outreach rules: DPD bands, contact window, caps, holds, reply attribution, budget, rule order | 1.1.0 (draft for sign-off) | `backend/kairos_backend/gate.py`, `ledger.py`, `backend/spine/policy.py` (K03) |
| [`K13-operational-exceptions/`](K13-operational-exceptions/README.md) | Needs-time, promise-to-pay, callback, hardship, routed exceptions | 1.0.0 (draft for sign-off) | `backend/kairos_backend/exceptions.py`, `ledger.py` |
| `K17-voice-dispositions/thresholds.json` | Voice-bot disposition confidence thresholds, PTP max days, payment-claim pause (proposed by Yuval, K17/K18) | 0.1.0-draft (DRAFT) | `backend/kairos_backend/voice/ingest.py` |
| [`K06-human-in-the-loop/`](K06-human-in-the-loop/README.md) | Which sends need a person's approval, who may do what, locked actions, masking | 0.1.0 (**DRAFT, pending Ojas**) | `backend/kairos_backend/hitl.py` |
| [`K22-template-catalog/`](K22-template-catalog/README.md) | Template catalog built from CN's PL collections strategy (T1–T4 tone tiers, NACH, K13 replies; en + hi; SMS, WhatsApp, email, IVR blast, Sarvam voicebot) with a collections-compliance checklist result per template. **Synthetic placeholder until D13; "approved" = passed our checklist, not lender/DLT/Meta approval** | 0.2.0-placeholder | `backend/kairos_backend/templates.py` (→ ledger templates table → Gate `TEMPLATE_APPROVED`) |

Each folder has its own `reference_check.py` and `test_cases.csv`. The backend test suite runs **every** row of both CSVs against the production code (`backend/tests/test_gate_k01.py`, `test_exceptions_k13.py`), so a policy change that the code doesn't follow fails CI.

**Adding later**
- Official D13 registered-template file: it replaces `K22-template-catalog/catalog.json` (steps in that folder's README).
- `official_caps.json` (organisers' D11) — drop it here; the backend picks it up and applies strictest-wins automatically.
- Lender cap files — same format as `K01-decision-policy/lender_caps.example.json`.
