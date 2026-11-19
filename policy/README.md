# policy/

The source of truth for what Kairos is **allowed** to do. Owned by Product (Ojas). Code reads these files and never hard-codes a number from them.

| Folder | What it is | Version | Consumed by |
|---|---|---|---|
| [`K01-decision-policy/`](K01-decision-policy/README.md) | Outreach rules: DPD bands, contact window, caps, holds, reply attribution, budget, rule order | 1.1.0 (draft for sign-off) | `backend/kairos_backend/gate.py`, `ledger.py`, `backend/spine/policy.py` (K03) |
| [`K13-operational-exceptions/`](K13-operational-exceptions/README.md) | Needs-time, promise-to-pay, callback, hardship, routed exceptions | 1.0.0 (draft for sign-off) | `backend/kairos_backend/exceptions.py`, `ledger.py` |

Each folder has its own `reference_check.py` and `test_cases.csv`. The backend test suite runs **every** row of both CSVs against the production code (`backend/tests/test_gate_k01.py`, `test_exceptions_k13.py`), so a policy change that the code doesn't follow fails CI.

**Adding later**
- `official_caps.json` (organisers' D11) — drop it here; the backend picks it up and applies strictest-wins automatically.
- Lender cap files — same format as `K01-decision-policy/lender_caps.example.json`.
