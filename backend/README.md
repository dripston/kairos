# backend/ — K06 Supervisor guardrails + executor

Python service that decides whether a touch **may** be sent, reserves it atomically, dispatches it once, and cancels queued work when the borrower's situation changes. Reads all rules from [`../policy`](../policy/README.md).

## Run

```bash
cd backend
python -m venv .venv
.venv/Scripts/activate            # Windows  (macOS/Linux: source .venv/bin/activate)
pip install -e ".[dev]"
pytest                            # 98 tests: all K01 + K13 cases, ledger, executor, API
uvicorn kairos_backend.api:app --reload    # http://127.0.0.1:8000/docs
python scripts/bench_ledger.py 50000       # throughput check (50k decisions)
```

Environment: `KAIROS_POLICY_DIR` (default `../policy`), `KAIROS_DB` (default `backend/kairos.db`).

## How it fits together

```
agent recommendation ──► Supervisor.decide() ──► Ledger.reserve()  ── one write transaction ──┐
                                                   │ facts_for(): counts, holds, budget        │
                                                   │ Gate.evaluate(): K01 rules in policy order │
                                                   └► SEND → touch RESERVED + cost reserved ◄──┘
Executor.dispatch() ──► Ledger.begin_dispatch()  (re-check at send time; else CANCEL + release)
                    ──► Provider.send()          ACCEPTED → SENT · REJECTED → FAILED (release)
                                                 no answer → UNCERTAIN → resolve_uncertain() asks the provider
borrower events (reply, call >10 s, STOP, payment, K13 exceptions) ──► Ledger cancels queued work in scope
```

| Module | Responsibility |
|---|---|
| `policy.py` | Loads K01/K13, lender and official caps; strictest-wins caps, longest gap, calendar (IST). |
| `gate.py` | Pure rule check. Follows `evaluation_order` from policy.json; first hit wins; every decision carries `policy_version`. |
| `exceptions.py` | K13 decision table (needs time, PTP, callback, hardship, routed types). |
| `ledger.py` | SQLite state + **atomic check-and-reserve**, budget pool and daily pacing, cancellations, tickets, audit log, independent cap-breach audit. |
| `executor.py` | `Supervisor` (versioned instruction with reason + expiry) and `Executor` (dispatch once, uncertain-send handling). `FakeProvider` stands in for K07/K08/K17 adapters. |
| `api.py` | FastAPI endpoints for the frontend and other agents. |

## Guarantees and how they're tested

| K06 "done when" | Mechanism | Test |
|---|---|---|
| Parallel workers stay within caps and budget | `BEGIN IMMEDIATE` around facts → Gate → insert; reserved touches count immediately | `test_parallel_workers_*` (run 30× without a flake) |
| Same instruction never dispatched twice | Stable `instruction_id`; RESERVED → DISPATCHING happens once | `test_same_instruction_reserved_once`, `test_dispatch_happens_once` |
| Check uncertain sends before retry | UNCERTAIN keeps its cap slot and cost; `resolve_uncertain` asks the provider, never resends | `test_uncertain_*` |
| STOP / verified payment cancel pending work within scope | STOP → all channels; payment → that obligation only | `test_stop_*`, `test_verified_payment_cancels_only_that_obligation` |
| Reconcile actual charge once | `reconciled` flag | `test_reconcile_exactly_once` |
| Zero cap breaches | Gate at approval + send time; independent `cap_breaches()` recount | every concurrency test + `/cap-breaches` |
| 50k decisions < 10 min | ~54 s for 50,000 full atomic decisions on a laptop | `scripts/bench_ledger.py` |

## Interpretations (flag if wrong)

- **PTP hold is borrower-wide** — K01 says "no activity on any channel".
- **K13 holds not in K01's order** (`CALLBACK_PENDING`, `SUPERVISOR_HOLD`) run right after `HARDSHIP_HOLD` until Product places them (`gate.EXTRA_RULES_AFTER`).
- **Verified payment cancels that obligation's queued touches immediately**, even during a PTP hold (event-driven, independent of rule order).
- **Daily pacing is a hard limit** (brief: stay within daily *and* campaign budget). Toggle with setting `enforce_daily_pacing`.
- **Weekly caps use a rolling 7 days** (stricter than a calendar week).
- **Any hold cancels the borrower's queued touches** so their budget returns to the pool; the Supervisor replans after the hold.
- `budget_per_case_inr` and collection targets are K01 REQUIRED_INPUTs — supplied per campaign, never defaulted.
- Money is compared at 1/100 paisa precision to avoid float drift.
