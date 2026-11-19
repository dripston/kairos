# policy/

The source of truth for what Kairos is **allowed** to do. Owned by Product (Ojas). Code reads these files and never hard-codes a number from them.

| Folder | What it is | Version | Consumed by |
|---|---|---|---|
| [`K01-decision-policy/`](K01-decision-policy/README.md) | Outreach rules: DPD bands, contact window, caps, holds, reply attribution, budget, rule order | 1.1.1 (draft for sign-off; from Ojas's pack `e4d4d2a`) | `backend/kairos_backend/gate.py`, `ledger.py`, `backend/spine/policy.py` (K03) |
| [`K13-operational-exceptions/`](K13-operational-exceptions/README.md) | Needs-time, promise-to-pay, callback, hardship, routed exceptions | 1.0.0 (draft for sign-off; unchanged in Ojas's pack `e4d4d2a`) | `backend/kairos_backend/exceptions.py`, `ledger.py` |
| `K17-voice-dispositions/thresholds.json` | Voice-bot disposition confidence thresholds, PTP max days, payment-claim pause (proposed by Yuval, K17/K18) | 0.1.0-draft (DRAFT) | `backend/kairos_backend/voice/ingest.py` |
| [`K06-human-in-the-loop/`](K06-human-in-the-loop/README.md) | Which sends need a person's approval, who may do what, locked actions, masking | 0.1.0 (**DRAFT, pending Ojas**) | `backend/kairos_backend/hitl.py` |
| [`K22-template-catalog/`](K22-template-catalog/README.md) | Template catalog built from CN's PL collections strategy (T1–T4 tone tiers, NACH, K13 replies; en + hi; SMS, WhatsApp, email, IVR blast, Sarvam voicebot) with a collections-compliance checklist result per template. **Synthetic placeholder until D13; "approved" = passed our checklist, not lender/DLT/Meta approval** | 0.2.0-placeholder | `backend/kairos_backend/templates.py` (→ ledger templates table → Gate `TEMPLATE_APPROVED`) |
| [`K10-inbound-conversation/`](K10-inbound-conversation/README.md) | "Kaira" inbound WhatsApp conversation: persona, language mirroring, identity (registered number + name confirmation; OTP step-up dormant), allowed-before/after-verification, intent priority, payment-claim flow, 32 scripts x en/hinglish/hi | 1.0.0 (draft for sign-off) | `backend/kairos_backend/cm03/` (`identity_mode="k10"`, see POLICY_COVERAGE.md) |
| [`K15-safety-load-replay/`](K15-safety-load-replay/README.md) | Safety, load and story replay acceptance (12 checks) with Varun's stand-alone `replay_engine.py` | 1.0.0 | Conformance: `backend/replay/`, `backend/scripts/` (our replay, not his engine) |
| [`K21-propensity-intensity/`](K21-propensity-intensity/README.md) | Propensity-to-pay points scorecard (0-100, 7-day horizon) and nudge intensity (WAIT/LOW/STANDARD/HIGH), low-evidence cap | 1.1.0 (draft for sign-off) | `backend/kairos_backend/scorecard.py` |
| [`K24-portfolio-intake/`](K24-portfolio-intake/README.md) | Portfolio upload: column matching, quarantine, warnings, grouping by customer, merge on re-upload, budget scope, scheduled start | 1.1.0 (draft for sign-off) | `backend/spine/intake.py`, `Ledger.unenroll` |
| [`T01-outbound-templates/`](T01-outbound-templates/README.md) | 150 outbound templates (5 bands x 6 angles x 5 channels) x en/hi = 300 rows | 1.0.0 (draft for sign-off) | Imported into our format as `K22-template-catalog/t01_catalog.json` by `backend/scripts/import_t01.py` (not live; see POLICY_COVERAGE.md) |
| [`docs/`](docs/) | Ojas's business flow, orchestration architecture and demo implementation brief (PDF, 64-91 KB each) | - | Reference |
| [`K27-auto-churn/`](K27-auto-churn/README.md) | Auto-churn cohorts, journeys, reassignment and risk rules (proposed by K27) | DRAFT | `backend/kairos_backend/churn/` |

**Ojas's pack.** K01, K10, K13, K15, K21, K24, T01 and `docs/` come from Ojas's policy repo (`ojas-commits/kairos-policy`, commit `e4d4d2a`) and are kept byte-for-byte (CRLF line endings included); edit them there, not here. His top-level README is kept as [`OJAS_PACK_README.md`](OJAS_PACK_README.md). K06, K17, K22 and K27 are Kairos-side packs. How our code conforms to each of his test cases: [`../POLICY_COVERAGE.md`](../POLICY_COVERAGE.md) (`backend/tests/policy_conformance/`).

Each folder has its own `reference_check.py` and `test_cases.csv`. The backend test suite runs **every** row of both CSVs against the production code (`backend/tests/test_gate_k01.py`, `test_exceptions_k13.py`), so a policy change that the code doesn't follow fails CI.

**Adding later**
- Official D13 registered-template file: it replaces `K22-template-catalog/catalog.json` (steps in that folder's README).
- `official_caps.json` (organisers' D11) — drop it here; the backend picks it up and applies strictest-wins automatically.
- Lender cap files — same format as `K01-decision-policy/lender_caps.example.json`.
