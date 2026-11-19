# Kairos Policy Pack

Policy modules for Kairos, the CreditNirvana collections agent. Each module holds machine-readable rules, test cases, a README, and a stdlib-only `reference_check.py`.

| Module | What it covers | Check |
|---|---|---|
| [K01-decision-policy](K01-decision-policy) | Outreach decision policy: DPD bands, caps, quiet hours (v1.1.1) | 38/38 |
| [K10-inbound-conversation](K10-inbound-conversation) | Kaira inbound WhatsApp conversation and identity verification | 33/33 |
| [K13-operational-exceptions](K13-operational-exceptions) | Needs time, asks for a person, hardship, route-never-decide | 25/25 |
| [K15-safety-load-replay](K15-safety-load-replay) | Safety, load and story replay at 50,000 borrowers | 12/12 |
| [K21-propensity-intensity](K21-propensity-intensity) | Propensity to pay and nudge intensity scorecard | 20/20 |
| [K24-portfolio-intake](K24-portfolio-intake) | Portfolio upload, validation, merge and scheduled start | 31/31 |
| [T01-outbound-templates](T01-outbound-templates) | 150 outbound templates × English/Hindi (300 rows) | PASS, 0 problems |

`docs/` holds the business flow, orchestration architecture and demo implementation brief (PDF).

## Run all checks

```bash
for d in K* T*; do (cd "$d" && python3 reference_check.py | tail -2); done
```
