# backend/ — K06 guardrails + executor on the K03 spine

Python service that decides whether a touch **may** be sent, reserves it atomically, dispatches it once, and cancels queued work when the borrower's situation changes. Rules come from [`../policy`](../policy/README.md); borrowers, loans, EMIs, consent and payments come from the **K03 spine**.

## Run

```bash
cd backend
python -m venv .venv
.venv/Scripts/activate            # Windows  (macOS/Linux: source .venv/bin/activate)
pip install -e ".[dev]"
pytest                            # K01 + K13 cases, K06 ledger/executor/API/SupervisorFSM, K03 spine + K15 replay, K09
uvicorn kairos_backend.api:app --reload    # http://127.0.0.1:8000/docs
python scripts/bench_ledger.py 50000       # 50k decisions on K03's synthetic dataset
```

K03 / K15 scripts (Varun): `python scripts/spine_report.py`, `scripts/make_dataset.py`, `scripts/load_dataset.py`, `scripts/run_replay.py` — see [`spine/README.md`](spine/README.md).

K09 replay (SIMULATED): `python3 scripts/run_k09_replay.py --borrowers 600 --days 7` — see [K09 replay](#k09-replay-simulated).

Environment: `KAIROS_POLICY_DIR` (default `../policy`), `KAIROS_DB` (default `backend/kairos.db`).

## Layout

| Package | Task | Owner | What it does |
|---|---|---|---|
| `spine/` | K03 | Varun | Borrowers, consents, loans, EMIs (obligations), payments, events. Money in paise. Dataset importer + synthetic 50k generator. |
| `replay/` | K15 | Varun | Campaign replay: baseline vs learning policy, dev simulator, 500-chat load test. |
| `kairos_backend/` | K06 | Rehan | Gate, ledger, supervisor (`executor.Supervisor` + `supervisor.SupervisorFSM`), executor, API — reads the spine, never copies it. |
| `kairos_backend/` | K09 | Rehan / Varun | `contracts` (shared types), `learner` (Beta–Bernoulli band × channel × slot), `outcomes` (24 h reply window → one outcome per touch), `borrower_context` (engagement × recency multipliers), `intent` (rules + gpt-6-luna), `propensity` (demo). |
| `replay/k09.py`, `replay/cn10.py` | K09 | Rehan | K09 replay on the full stack; CN10 cohort-cadence baseline. |

## How K06 uses the spine

One SQLite file holds both. K03 owns *who / what is owed / what was paid*; K06 owns *campaigns, holds, reservations, audit*.

| Gate fact | Comes from |
|---|---|
| DPD + band | Live from the loan's oldest unpaid EMI due date (K03 rule). Negative = PRE_DUE. Dataset loan with no DPD → HOLD. |
| DPD fresh? | Hours since the spine was last synced (`mark_spine_synced`; `start_day` in simulations) or the loan's `dpd_as_of`. > 24 h → HOLD. |
| Consent / opt-out | K03 `consents` (per channel). STOP sets `opted_out` on every channel; opt-in is per channel. |
| Obligation cleared | That EMI's `amount_paid_paise >= amount_due_paise`. |
| Verified overdue | EMI past its due date and not fully paid. |
| Pool | `budget_per_case_paise` × loans enrolled in the campaign. |
| Collection target | Verified paise collected ÷ overdue at enrolment; loans cured ÷ loans enrolled. Reversals roll back. |

K06 writes back to the spine: `touch_sent` when a send is confirmed, `reply_received` on a reply, `consent_changed` on STOP/opt-in, and payments go through K03's `apply_payment` / `reverse_payment` (one EMI of one loan, never over the amount due, a bank reference used once).

## Flow

```
agent recommendation ──► Supervisor.decide() ──► Ledger.reserve()  ── one write transaction ──┐
                                                   │ facts from K03 spine + K06 holds/counters  │
                                                   │ Gate.evaluate(): K01 rules in policy order │
                                                   └► SEND → touch RESERVED + cost reserved ◄──┘
Executor.dispatch() ──► Ledger.begin_dispatch()  (re-check at send time; else CANCEL + release)
                    ──► Provider.send()          ACCEPTED → SENT · REJECTED → FAILED (release)
                                                 no answer → UNCERTAIN → resolve_uncertain() asks the provider
borrower events (reply, call >10 s, STOP, payment, reversal, K13 exceptions) ──► cancel queued work in scope
```

## Guarantees and how they're tested

| K06 "done when" | Mechanism | Test |
|---|---|---|
| Parallel workers stay within caps and budget | `BEGIN IMMEDIATE` around facts → Gate → insert; reserved touches count immediately | `test_parallel_workers_*` (repeated 20× without a flake) |
| Same instruction never dispatched twice | Stable `instruction_id`; RESERVED → DISPATCHING once | `test_same_instruction_reserved_once`, `test_dispatch_once_*` |
| Check uncertain sends before retry | UNCERTAIN keeps its cap slot and cost; `resolve_uncertain` asks the provider | `test_uncertain_*` |
| STOP / verified payment cancel pending work within scope | STOP → all channels; payment → that EMI only; partial payment keeps it open | `test_stop_*`, `test_verified_payment_clears_only_that_emi`, `test_partial_payment_keeps_emi_open` |
| Reconcile actual charge once | `reconciled` flag | `test_reconcile_exactly_once` |
| Zero cap breaches | Gate at approval + send time; independent `cap_breaches()` recount | concurrency tests, K03 dataset test, `/cap-breaches` |
| 50k decisions < 10 min | ~51 s for 50,000 full atomic decisions on K03's synthetic dataset | `scripts/bench_ledger.py` |

## Interpretations (flag if wrong)

- **PTP hold is borrower-wide** — K01 says "no activity on any channel".
- **K13 holds not in K01's order** (`CALLBACK_PENDING`, `SUPERVISOR_HOLD`) run right after `HARDSHIP_HOLD` until Product places them (`gate.EXTRA_RULES_AFTER`).
- **Verified payment cancels that EMI's queued touches immediately**, even during a PTP hold, and ends the PTP hold if it was for that EMI.
- **Daily pacing is a hard limit** (floored to whole paise). Toggle with setting `enforce_daily_pacing`.
- **Weekly caps use a rolling 7 days** (stricter than a calendar week).
- **Any hold cancels the borrower's queued touches** so their budget returns to the pool.
- **A case = a loan** (K01 "accounts"): pool and cure rate count enrolled loans.
- `budget_per_case` and collection targets are K01 REQUIRED_INPUTs — supplied per campaign, never defaulted.

## K09 on the spine

| Piece | What it uses from the spine / K06 |
|---|---|
| `SupervisorFSM.plan` | One plan per EMI (spine obligation). `band=None` → live band from the loan's oldest unpaid EMI. Each candidate is checked with `Ledger.preview`; the learner ranks only eligible ones; `Ledger.reserve` is atomic. CRM envelopes carry the real `loan_id` and the `obligation_id`. |
| `Ledger.preview(ins, exclude=)` | `exclude` names one live touch to leave out of caps / same-channel gap (as `begin_dispatch` does for itself) and returns its reserved paise to the budget when it is the same campaign and day — so re-planning the identical stable-id touch is an idempotent replay, never a self-block. |
| `SupervisorFSM.dispatch` | `Executor.dispatch` → `begin_dispatch` re-check; a payment (K03 `apply_payment` clears the EMI) or STOP (spine consents opted out) before dispatch → `CANCEL` with the gate rule. |
| Learner / outcomes | Costs in integer paise (`contracts.SYNTHETIC_COSTS` = `replay.settings.COST_PAISE`, one table); ranking by expected replies per rupee. Posterior mean in PRODUCTION; seeded Thompson only in SANDBOX / SIMULATOR. `decision_scores` with `model_version`. |

## K09 replay (SIMULATED)

```bash
python3 scripts/run_k09_replay.py --borrowers 600 --days 7                 # CN10 baseline vs K09
python3 scripts/run_k09_replay.py --borrowers 600 --days 7 --baseline both # + the K15 placeholder schedule
python3 scripts/run_k09_replay.py --no-explore --no-context --no-drift --check-determinism
```

Every arm runs through the same stack (spine import → SupervisorFSM → executor → K15 DEV simulator →
ledger events → outcome finalizer) on the same borrowers: K03's synthetic generator (same seed → same
borrowers as `make_dataset.py`), with Bounce Date anchored at the replay start so the spine's live
DPD equals the file DPD on day 1 (`--dataset` uses a K03 file as-is). Caps are matched (K01 + K15
placeholder caps as lender override `replay/k15_lender_caps.json` + K15's 2-day cross-channel gap as a
pre-filter), and so is money: the pool defaults to what the baseline spends in an unconstrained
calibration run. Report: `reports/k09_replay_report.json` (gitignored).

**Baselines.** `cn10` (default) is CreditNirvana's own *Personal Loan — 10-Day Post-Due Digital
Collections Strategy* (HDFC project): cohorts C1–C5 from the dataset's Risk Tagging (else the bounce
string), a fixed 10-day calendar per cohort with message tiers T1–T4 (`CN10_{WA|SMS|IVR}_T{n}_{EN|HI}`
templates), repeating after day 10 (setting in `replay/cn10.py`). It is a **stand-in until the
official fixed-cadence policy file (K02) arrives**. Same-day multi-channel steps are planned one by
one and the matched 1/day cap blocks the extras, exactly as for K09. CN's IVR is a one-way blast; the
DEV simulator lets IVR produce replies and the replay follows the simulator (flagged in the report).
`k15` is the K15 placeholder schedule.


### Multi-seed result (CM-02 check, SIMULATED)

1,500 borrowers × 14 days, WhatsApp drift from day 7, budget pool matched to CN10's unconstrained
spend, K15 caps on every arm, K09 ranking by posterior mean (`--explore` adds Thompson draws; on the
tune seeds it gave +8.1%, +10.6%, −12.4%, +2.9%, so exploration stays in SANDBOX/SIMULATOR learning runs).

> **Current replay default = the PR #17 configuration below + `stop_penalty=25`** (opt-out risk priced
> into the value; see *Opt-out (STOP) risk* at the end of this section, which has its own tune / held-out /
> final table). The tables directly below are the PR #17 step (`--kairos-opts '{"stop_penalty": 0}'`).

**PR #17 configuration (k09/optimize, step 1):** posterior mean + borrower context, plus two Kairos-side options
(`replay.k09.KairosOptions`), both built only from the ledger:

* `decay=0.7` discounts arm evidence by 0.7 per day since the touch was executed, so the band ×
  channel × slot posterior follows the E17 WhatsApp drift in a few days instead of staying anchored to
  week 1.
* `value_floor_rel=0.5` is a marginal-value spending rule. After 200 finalized touches, a candidate
  whose borrower-adjusted expected replies per rupee is below 0.5 × the campaign's realized replies
  per rupee so far WAITs. K01 pacing carries the unspent money forward. It spends 90–95% of the pool
  (93–100% of what CN10 itself spends) instead of about 100%.

`--legacy-k09` runs the default from before k09/optimize. `--kairos-opts '{...}'` overrides single options.

**Protocol.** Options were tuned on seeds 7, 42, 99 and 2026 and validated on held-out seeds 11, 123
and 555. The final configuration was then run once on unseen seeds 31337, 8080 and 4242, next to the
previous default. The baseline, budget matching, caps and simulator are unchanged: the CN10 columns
are identical in both runs. Change = K09 replies/₹1,000 vs CN10 replies/₹1,000.

| Set | Seed | CN10 replies/₹1k | Previous default | **New default** | Replies CN10 / prev / new | Verified payments CN10 / prev / new | Spend ₹ CN10 / prev / new | K01 breaches / K15 violations |
|---|---|---|---|---|---|---|---|---|
| tune | 7 | 56.18 | 78.30 (+39.4%) | **89.21 (+58.8%)** | 163 / 236 / 242 | 35 / 60 / 59 | 2,901 / 3,014 / 2,713 | 0 / 0 |
| tune | 42 | 58.79 | 74.54 (+26.8%) | **86.50 (+47.1%)** | 169 / 228 / 242 | 53 / 62 / 62 | 2,875 / 3,059 / 2,798 | 0 / 0 |
| tune | 99 | 65.27 | 77.85 (+19.3%) | **88.03 (+34.9%)** | 191 / 237 / 249 | 45 / 62 / 65 | 2,926 / 3,044 / 2,829 | 0 / 0 |
| tune | 2026 | 64.87 | 87.91 (+35.5%) | **92.72 (+42.9%)** | 186 / 265 / 265 | 53 / 69 / 69 | 2,867 / 3,014 / 2,858 | 0 / 0 |
| held-out | 11 | 55.17 | 66.06 (+19.7%) | **82.12 (+48.8%)** | 163 / 207 / 231 | 39 / 48 / 56 | 2,954 / 3,134 / 2,813 | 0 / 0 |
| held-out | 123 | 67.38 | 89.24 (+32.4%) | **97.18 (+44.2%)** | 197 / 269 / 276 | 61 / 72 / 76 | 2,924 / 3,014 / 2,840 | 0 / 0 |
| held-out | 555 | 66.42 | 76.95 (+15.9%) | **86.67 (+30.5%)** | 192 / 232 / 245 | 48 / 65 / 72 | 2,891 / 3,015 / 2,827 | 0 / 0 |
| final | 31337 | 60.93 | 75.67 (+24.2%) | **90.10 (+47.9%)** | 178 / 227 / 245 | 50 / 52 / 57 | 2,921 / 3,000 / 2,719 | 0 / 0 |
| final | 8080 | 60.95 | 89.69 (+47.2%) | **98.23 (+61.2%)** | 179 / 273 / 276 | 41 / 67 / 65 | 2,937 / 3,044 / 2,810 | 0 / 0 |
| final | 4242 | 61.95 | 86.18 (+39.1%) | **96.67 (+56.0%)** | 182 / 265 / 282 | 43 / 76 / 81 | 2,938 / 3,075 / 2,917 | 0 / 0 |

Mean / minimum change vs CN10, previous → new default: tune +30.2% / +19.3% → +45.9% / +34.9%;
held-out +22.7% / +15.9% → +41.2% / +30.5%; final +36.8% / +24.2% → +55.0% / +47.9%. On every
seed the new default gets at least as many replies as the previous one and more than CN10. Verified
payments are higher than CN10 on every seed, but on seeds 7 and 8080 they are slightly lower than
the previous default (60 → 59 and 67 → 65). The new default spends 5–10% less than the previous
default. The ratio still rises when spend is held fixed: `decay` alone uses the whole pool and
reaches +36.1% / +25.3% on the tune seeds. STOP opt-outs are higher than CN10 for both K09 defaults
(e.g. final seeds CN10 10 / 18 / 18, previous 22 / 25 / 23, new 22 / 30 / 27). The replies/₹ bar
does not count this cost.

**What was tried.** Each option was run alone on the tune seeds, change vs CN10 in replies/₹. Spend is
about the same as the previous default unless a spend figure is given:

| Option (alone unless noted) | 7 | 42 | 99 | 2026 | Mean / min | Note |
|---|---|---|---|---|---|---|
| previous default | +39.4 | +26.8 | +19.3 | +35.5 | +30.2 / +19.3 | |
| `decay` 0.9 | +44.1 | +31.8 | +22.3 | +37.6 | +34.0 / +22.3 | |
| `decay` 0.8 | +42.3 | +32.9 | +22.8 | +37.6 | +33.9 / +22.8 | |
| `decay` 0.7 | +44.1 | +39.6 | +25.3 | +35.5 | +36.1 / +25.3 | held-out +29.1 / +15.9 |
| `decay` 0.6 | +46.5 | +37.2 | +31.1 | +45.1 | +40.0 / +31.1 | combined with the floor it did worse than 0.7 (below) |
| `recency: gap` (finer fatigue feature) | +41.7 | +28.4 | +22.8 | +38.1 | +32.8 / +22.8 | adds nothing on top of decay 0.7 (+36.9 / +25.3) |
| `affinity_strength` 10 (borrower × channel) | +40.5 | +27.9 | +19.8 | – | – | within noise; stopped after 3 seeds |
| `hier_strength` 50 (slots pooled across bands) | +34.6 | +20.7 | +18.8 | – | – | worse on all 3; stopped |
| `payment_lambda` 0.1 (demo propensity rule) | – | +38.4 | +0.2 | – | – | unstable; stopped |
| `min_reply_prob` 0.02 (lets email in) | – | +29.6 | +8.7 | – | – | unstable; stopped |
| `min_replies_per_inr` 0.15 (absolute floor) | – | +84.8 | +66.5 | – | – | spends 65% of CN10, payments 51 / 53 (below previous default); rejected |
| `value_floor_rel` 0.5 | +70.8 | +53.1 | +33.6 | +51.2 | +52.2 / +33.6 | spends 83–94% of CN10; 42: replies 216 vs 228 |
| `value_floor_rel` 0.75 / 1.0 | – | +58.5 / +66.2 | +37.2 / +46.1 | +61.2 / – | – | spend falls further (74–91% of CN10) |
| **decay 0.7 + value_floor_rel 0.5** | +58.8 | +47.1 | +34.9 | +42.9 | **+45.9 / +34.9** | chosen; held-out +41.2 / +30.5 |
| decay 0.7 + value_floor_rel 0.75 | +56.2 | +48.3 | +33.7 | +43.7 | +45.5 / +33.7 | held-out +41.1 / +30.5 |
| decay 0.6 + value_floor_rel 0.5 | +53.4 | +40.5 | +34.0 | +46.5 | +43.6 / +34.0 | held-out +40.5 / +25.1 |

Selection rule, set on the tune seeds: best tune mean and minimum replies/₹, with verified payments
and replies at or above CN10 on every tune seed. The held-out seeds only validated candidates. The
two floor levels with decay 0.7 tied there, and 0.5 was kept for its better tune mean. Absolute floors raise replies/₹ mainly by
not spending: about a third of the pool goes unspent and payments fall. So they stay behind a flag.

Reproduce one seed (current default, the PR #17 step, then the default from before k09/optimize):

```bash
python3 scripts/run_k09_replay.py --borrowers 1500 --days 14 --seed 42 --baseline both
python3 scripts/run_k09_replay.py --borrowers 1500 --days 14 --seed 42 --kairos-opts '{"stop_penalty": 0}'
python3 scripts/run_k09_replay.py --borrowers 1500 --days 14 --seed 42 --legacy-k09
```

#### Opt-out (STOP) risk (k09/optimize, step 2: the current default)

The PR #17 configuration drew more STOPs than CN10: 89 vs 61 on the tune seeds. Every STOP was charged
to the touch whose 24 h reply window held it.

**Where the STOPs come from.** Totals over the 4 tune seeds, per touch:

| | CN10 STOPs / touches | K09 (PR #17) STOPs / touches | STOPs per 100 replies, CN10 / K09 |
|---|---|---|---|
| WhatsApp | 37 / 13,662 (2.7‰) | 69 / 22,420 (3.1‰) | 7.8 / 8.6 |
| SMS | 22 / 9,065 (2.4‰) | 19 / 8,425 (2.3‰) | 11.2 / 9.9 |
| IVR | 2 / 2,559 (0.8‰) | 1 / 82 | 5.4 / – |
| borrower's 1st touch | 9 / 5,825 | 15 / 5,840 | 3.2 / 4.1 |
| 2nd touch | 21 / 5,646 | 27 / 5,723 | 10.7 / 11.2 |
| 3rd+ touch | 31 / 13,815 | 47 / 19,364 | 13.5 / 12.0 |
| days 1–6 (before drift) | 35 / 12,709 | 63 / 16,604 | – |
| days 8–13 | 26 / 12,577 | 26 / 14,323 | – |

Two patterns stand out:

* Per reply, CN10 and K09 draw about the same STOPs (8.6 vs 8.9 per 100 replies). K09's excess comes
  from sending about 22% more touches, mostly cheap WhatsApp in week 1.
* A borrower's 2nd and later touches draw about 3× more STOPs per reply than the first.

So STOPs cannot be cut for free. The lever is fewer repeat touches to people who have not replied.

**What was added.** `kairos_backend/stop_risk.py` (`StopRiskModel`) learns P(STOP | touch) per channel ×
contact number (n0, n1, n2, n3+) from the ledger. Its inputs are the spine `events` STOP records
(`record_stop`) and `touch_outcomes`. It uses hierarchical Beta shrinkage (pooled → channel → segment)
and counts only touches whose reply window has closed. `ContextualLearner(stop_penalty=μ)` values each
arm at (p_reply − μ·P(STOP)) per rupee, and the budget order and the relative value floor both use that
value. `stop_max` is a hard WAIT above a risk cap.

**Frontier on the tune seeds** (sums over 7 / 42 / 99 / 2026; CN10: 61 STOPs, 709 replies, 186
payments). Change is the replies/₹ change vs CN10:

| K09 configuration | STOPs | Replies | Payments | Spend vs CN10 | Change mean / min |
|---|---|---|---|---|---|
| PR #17 (μ = 0) | 89 | 998 | 255 | 94–100% | +45.9% / +34.9% |
| μ = 10 | 86 | 978 | 252 | 83–99% | +53.4% / +29.2% |
| μ = 15 | 75 | 896 | 218 | 67–79% | +73.1% / +42.2% |
| μ = 20 | 64 | 845 | 207 | 50–83% | +81.3% / +38.2% |
| **μ = 25 (chosen)** | **61** | **795** | **193** | **49–68%** | **+93.9% / +58.8%** |
| μ = 30 | 53 | 732 | 180 | 49–62% | +95.5% / +74.7% |
| μ = 40 | 44 | 602 | 145 | 33–46% | +110.8% / +74.7% |
| μ = 20, shrinkage 500 / 5000 | 66 / 65 | 850 / 816 | 211 / 202 | 49–83% | +84.5% / +42.1%, +83.2% / +49.4% |
| control: absolute floor 0.15 replies/₹, no STOP model | 78 | 970 | 245 | 79–90% | +65.0% / +40.4% |
| control: absolute floor 0.25 replies/₹, no STOP model | 74 | 877 | 223 | 57–68% | +90.6% / +75.0% |
| `stop_max` 0.004 (seeds 42, 99 only) | 39 (CN10 31) | 488 | 129 | 95–100% | +39.5% / +36.0% |
| `stop_max` 0.003 (seeds 42, 99 only) | 13 (CN10 31) | 180 | 54 | 17–27% | – (replies far below CN10) |

The controls cut spend without pricing STOPs. At similar replies, μ = 20–25 draws fewer STOPs than a
blunt cut (7.7 vs 8.4 STOPs per 100 replies), so the risk model adds something beyond spending less.
Most of the STOP reduction, though, comes from contacting less.

The selection rule, set on the tune seeds, was: the smallest μ whose tune STOPs are ≤ CN10's, with
replies ≥ CN10 on every tune seed. That gives μ = 25. The default rule (held-out mean STOPs must fall,
and every held-out and final seed must stay ≥ +20% replies/₹) passed, so `stop_penalty=25` is now the
replay default.

**Result** (PR #17 step vs current default, per seed). Breaches and K15 violations were 0 / 0 for every arm on every seed:

| Set | Seed | CN10 replies/₹1k | PR #17 | **Current (μ = 25)** | STOPs CN10 / #17 / now | Replies CN10 / #17 / now | Payments CN10 / #17 / now | Spend ₹ CN10 / #17 / now |
|---|---|---|---|---|---|---|---|---|
| tune | 7 | 56.18 | 89.21 (+58.8%) | **110.32 (+96.4%)** | 11 / 23 / 14 | 163 / 242 / 219 | 35 / 59 / 46 | 2,901 / 2,713 / 1,985 |
| tune | 42 | 58.79 | 86.50 (+47.1%) | **116.02 (+97.3%)** | 15 / 21 / 12 | 169 / 242 / 180 | 53 / 62 / 46 | 2,875 / 2,798 / 1,552 |
| tune | 99 | 65.27 | 88.03 (+34.9%) | **103.68 (+58.8%)** | 16 / 18 / 14 | 191 / 249 / 194 | 45 / 65 / 50 | 2,926 / 2,829 / 1,871 |
| tune | 2026 | 64.87 | 92.72 (+42.9%) | **144.86 (+123.3%)** | 19 / 27 / 21 | 186 / 265 / 202 | 53 / 69 / 51 | 2,867 / 2,858 / 1,394 |
| held-out | 11 | 55.17 | 82.12 (+48.8%) | **95.64 (+73.4%)** | 16 / 28 / 18 | 163 / 231 / 180 | 39 / 56 / 39 | 2,954 / 2,813 / 1,882 |
| held-out | 123 | 67.38 | 97.18 (+44.2%) | **141.99 (+110.7%)** | 23 / 30 / 22 | 197 / 276 / 203 | 61 / 76 / 56 | 2,924 / 2,840 / 1,430 |
| held-out | 555 | 66.42 | 86.67 (+30.5%) | **101.31 (+52.5%)** | 15 / 19 / 13 | 192 / 245 / 202 | 48 / 72 / 62 | 2,891 / 2,827 / 1,994 |
| final | 2718 | 66.56 | 80.16 (+20.4%) | **109.86 (+65.1%)** | 11 / 14 / 12 | 194 / 231 / 207 | 58 / 63 / 52 | 2,915 / 2,882 / 1,884 |
| final | 1618 | 53.76 | 76.16 (+41.7%) | **109.10 (+102.9%)** | 11 / 19 / 12 | 156 / 214 / 172 | 43 / 61 / 54 | 2,902 / 2,810 / 1,577 |
| final | 9001 | 59.15 | 82.45 (+39.4%) | **101.43 (+71.5%)** | 9 / 25 / 16 | 176 / 238 / 202 | 48 / 56 / 50 | 2,976 / 2,887 / 1,992 |

**STOPs**, CN10 / PR #17 / now:

* tune: 61 / 89 / 61 (parity)
* held-out: 54 / 77 / 53 (parity)
* final (unseen): 31 / 58 / 40. That is 31% below PR #17 but still 29% above CN10, so parity was not
  reached on the final seeds.

**Replies/₹ change vs CN10**, mean / min, PR #17 → now: held-out +41.2 / +30.5 → +78.9 / +52.5;
final +33.8 / +20.4 → +79.8 / +65.1.

**The cost:**

* Spend falls to 49–69% of CN10's, because the penalty mostly withholds repeat touches.
* Replies fall 15–22% vs PR #17, but stay above CN10 on every seed.
* Verified payments fall 13–24% vs PR #17. They are below CN10 on 42, 2026, 123 and 2718 (e.g. 46
  vs 53) and equal on 11.

If payments matter more than opt-outs, `{"stop_penalty": 20}` or `{"stop_penalty": 0}` (PR #17) are
the points to use. The trade-off is set by μ, and the frontier above is the menu.

## K09 RL v2 (cost-optimal collections policy)

`--policy rl2` (`kairos_backend/rl/`). SIMULATED like everything above: DEV simulator, synthetic dataset,
placeholder costs and caps. The CN10 baseline, budget matching, caps, simulator and Supervisor gate are
unchanged; the policy only proposes, and the K01 gate re-checks every send.

### In plain words (for judges)

Each morning Kairos asks, for every borrower it may contact: *how likely is a reply on each channel and
time, how likely is a payment after a reply, how big is the EMI, and how likely is an opt-out?* It turns
that into one value in "reply units", divides by the channel's cost, and spends money only where value
per rupee beats a learned **price of money**. Everything it knows comes from the ledger: who replied,
who paid (verified, inside the touch's reply window), who sent STOP, plus spine facts (DPD band, CN10
cohort, state, EMI) and channel costs.

The estimates come from one **hierarchical Bayesian model**. Every level is shrunk toward the one above
it, and the data decides how much (empirical Bayes, below):

```
global channel x slot -> DPD band                       (decayed 0.7/day: follows the WhatsApp drift)
  x fatigue (touches in the previous 6 days)            (over-contact lowers response)
  x channel transition (previous layer + outcome -> this channel) -> per cohort
  x campaign -> cohort (CN10 C1-C5) -> region (cohort x state)
  x borrower -> borrower x channel -> borrower x slot   (the borrower's own counts, shrunk to the above)
```

`P(pay | reply)` (global → campaign → cohort → region → band) and `P(STOP)` (reply propensity × stop
share → fatigue → cohort → region) use the same machinery. Every finalized outcome updates the counts
the moment it is finalized (`apply_outcome`); the levels are refit lazily before the next decision.

### Method

* **Empirical-Bayes levels** (`rl/hier.py`). For each level, the levels above give every finalized touch
  an expected reply count E; the group's multiplier is `m = (O + κ) / (E + κ)` (Gamma(κ, κ) prior, mean 1).
  κ is estimated per level by the method of moments from the between-group spread of O/E beyond Poisson
  noise (decay-weighted counts use Σw²·E as the noise term). A level whose groups do not differ beyond
  noise gets κ → ∞ and collapses onto its parent: that is how the model says "no regional effect".
  Levels are backfitted twice, so the fatigue effect is not confounded with the borrower effect (repeat
  touches go to people who did not reply).
* **Collections value** (`rl/value.py`): `V = P(reply) × (1 + α × P(pay | reply) × EMI / mean EMI) − μ × P(STOP)`.
* **Price of money** (`rl/pacing.py`, bandits with knapsacks). `pacing="dual"`: λ starts at the
  value/cost where day 1's K01 allowance would run out, then `λ ← λ·exp(η·(spent/target − 1))` daily
  (target = ρ × K01 allowance); never below the ratio guard φ × realized replies per paisa (the previous
  relative floor, `pacing="floor"`); on the last contact day only the guard applies (spend-down). Touches
  with `V − λ·cost ≥ 0` go to the Supervisor in order of V / cost, so K01's daily allowance funds the
  best value per rupee first, with the borrower's top 3 arms as fallbacks for the gate.
* **Contact vs wait** (`rl/lookahead.py`): wait a day when a recent touch leaves the 6-day window
  tomorrow and `(1 − 1/days_left)·(V_tomorrow − λc) > V_today − λc`, with the learned fatigue effect.
* **Optimal stopping**: pause a borrower for the rest of the campaign (hand to the next campaign or a
  human, layer L4) when the one-step value-of-information continuation value is ≤ 0 even at the guard
  price: `V = (v·m − λc) + (k−1)·[q·max(0, v·m⁺ − λc) + (1−q)·max(0, v·m⁻ − λc)]`, with m the borrower's
  Gamma posterior, m⁺/m⁻ after a reply / a silence, k the touches still possible. No fixed "k misses"
  constant: the threshold comes from the learned price and posterior. A reply resumes the borrower.
* **Layers** (`rl/report.py`): L1 cheap digital (SMS, email), L2 rich digital (WhatsApp), L3 voice (IVR,
  voicebot), L4 human. The learned rule per cohort is "after a no-reply / undelivered touch at layer L,
  the next channel with the best transition-adjusted replies per ₹"; printed as a table and a mermaid
  diagram by `--report-levels`.
* **Warm start** (`rl/warmstart.py`): at cohort close, export population posteriors only (arm means with
  effective sample size ≤ 200, level counts with E ≤ 30 per group; never borrower level).

Every quantity is deterministic (no rng, sorted iteration); `--check-determinism` compares two runs.

### Protocol and constraints

1,500 borrowers × 14 days, WhatsApp drift from day 7, pool matched to CN10's unconstrained spend, K15
caps and the 2-day gap on every arm (as above). Tuned on seeds 7 / 42 / 99 / 2026, validated on held-out
11 / 123 / 555, then run once on unseen seeds 271828 / 161803 / 141421. The CN10 arm is identical in every
run (`scripts/rl2_sweep.py` caches it per seed; the final seed was re-run with the real script).

The target, per seed: replies/₹ ≥ +20% vs CN10, STOPs ≤ CN10, verified payments ≥ CN10, spend close to the
pool. **Selection rule, fixed on the tune seeds before held-out was run:** among configurations with
replies/₹ ≥ +20% and payments ≥ CN10 on every tune seed, take the one with the smallest total STOP excess
over CN10 (Σ max(0, STOPs − CN10)). No configuration met the spend target, so spend is reported, not used.

### Isolated effects and the frontier (tune seeds, sums over 7 / 42 / 99 / 2026)

Change = replies/₹ vs CN10. "Failing" lists every seed that misses a constraint (r/₹ < +20%, pay < CN10,
STOP above CN10, spend < 85% of pool). `rl2` rows add one option to "R2 base" (hierarchy + relative floor
0.5, α = μ = 0).

| Configuration | STOPs (CN10) | Replies (CN10) | Payments (CN10) | ₹ recovered / ₹ spent (CN10) | Spend vs pool | Change mean / min | Seeds failing a constraint |
|---|---|---|---|---|---|---|---|
| current default (`stop_penalty` 25) | 61 (61) | 795 (709) | 193 (186) | 373 (223) | 46–66% | +94.0% / +58.8% | 7: STOP+3; 42: pay; 2026: pay, STOP+2 |
| R2 base | 69 (61) | 901 (709) | 227 (186) | 315 (223) | 72–85% | +57.5% / +31.8% | 7: STOP+6; 42: STOP+1; 2026: STOP+2 |
| R2 without cohort / region / transition levels | 80 (61) | 957 (709) | 231 (186) | 309 (223) | 79–82% | +60.3% / +42.7% | STOP excess on all 4 |
| R2 ratio guard φ 0.3 (spend more) | 72 (61) | 926 (709) | 232 (186) | 285 (223) | 84–95% | +43.8% / +17.1% | 99: r/₹; STOP excess on 3 |
| **R2 dual price (`pacing: dual`) — selected** | **58 (61)** | **816 (709)** | **219 (186)** | **347 (223)** | **61–70%** | **+68.3% / +56.1%** | **7: STOP+1; 42: STOP+1** |
| R2 μ 5 (STOP term) | 66 (61) | 894 (709) | 223 (186) | 316 (223) | 70–84% | +61.5% / +31.9% | 7: STOP+4; 42, 2026: STOP+1 |
| R2 lookahead (contact vs wait) | 70 (61) | 899 (709) | 226 (186) | 314 (223) | 72–85% | +57.3% / +31.8% | as base (rarely triggers) |
| R2 optimal stopping | 68 (61) | 868 (709) | 219 (186) | 321 (223) | 71–76% | +60.2% / +38.7% | 42: pay; STOP excess on 3 |
| R2 α 1 (payment × EMI term) | 74 (61) | 890 (709) | 240 (186) | 317 (223) | 72–80% | +54.9% / +22.8% | STOP excess on 3 |
| R2 α 1 + μ 5 + lookahead + stopping | 72 (61) | 839 (709) | 225 (186) | 337 (223) | 66–69% | +66.7% / +33.3% | STOP excess on 3 |
| … + dual price | 57 (61) | 715 (709) | 180 (186) | 367 (223) | 52–59% | +73.3% / +37.3% | pay on 3 seeds |
| … + φ 0.3 | 74 (61) | 854 (709) | 231 (186) | 323 (223) | 71–80% | +55.8% / +26.0% | STOP excess on 3 |
| R2 base, warm start from another tune seed | 62 (61) | 745 (709) | 166 (186) | 226 (223) | 60–100% | +33.2% / −12.4% | 99, 2026: r/₹ and pay |
| R2 α1 μ5 look stop, warm start | 66 (61) | 708 (709) | 189 (186) | 341 (223) | 41–73% | +71.7% / +14.5% | 42: r/₹ and pay |

What each method did, alone:

* **Hierarchy.** It learns what is really there and switches off what is not. Fitted κ on a full run:
  band × channel × slot ≈ 5–15 (B3/B4 WhatsApp ×0.8–0.9), fatigue ≈ 5–9 (1 prior touch ×0.43–0.58,
  2+ ×0.31–0.34, relative to a first touch ×0.64), channel transition ≈ 8 when present, borrower ≈ 0.5–0.7
  (borrowers differ a lot). Cohort, region (cohort × state), cohort × transition, borrower × channel and
  borrower × slot collapse onto their parents (κ → ∞) on every seed. That is correct for this simulator,
  whose hidden traits do not depend on cohort or state. The drill-down sentences ("WhatsApp morning works
  1.6× in Bihar B1") come out shrunk toward 1 (κ ≈ 2–5), and on most runs they collapse entirely. Turning
  the population segment and transition levels off gave more replies and more STOPs. Keeping them is the
  safer default, because they cost nothing when they collapse.
* **Pacing.** The dual price spends less in week 1, when the most repeat touches and STOPs happen. It
  starts at the price where day 1's allowance runs out, about 0.6 replies/₹ on the WhatsApp prior, and
  decays toward the realized ratio by day 10. It was the only single change that cut STOPs below CN10 on
  the tune total while keeping payments ≥ CN10 on every tune seed. Spending more by lowering the guard
  (φ 0.3, or no price at all) bought almost no replies. In round 1, with no price, spend went from 79% to
  100% and replies went from 232 to 235. The binding resources here are the contact caps and STOP risk,
  not money.
* **Collections value.** α = 1 raised payments (240 vs 227) and STOPs (74 vs 69). α = 3 (round 1) cost
  replies/₹ without more payments. P(pay | reply) is flat at about 0.24 in this simulator (one rate for
  every band), so the α term mostly moves money toward larger EMIs.
* **μ (STOP term)** lowers STOPs a little at μ = 5. In round 1, μ = 10 cut spend to 61–79%.
* **Lookahead** almost never fires. The learned fatigue effect is real, but under the 2-day gap and the
  3-per-7-days cap, waiting a day rarely beats contacting today once the end-of-campaign hazard is priced.
* **Optimal stopping** paused only a few borrowers (0–40 per run). It raised replies/₹, cost payments
  on seed 42, and did not cut STOPs.
* **Warm start** hurt. The exported week-2 posteriors include the drift, which makes WhatsApp look poor
  on the next cohort's day 1. On seed 99 it also overspent early (100% of the pool, −12.4%). It stays
  behind `warm_start=` as a "second campaign onwards" experiment and is not claimed.
* **Not done:** a cheaper-channel ladder when confidence bounds overlap (6), and per-borrower channel
  affinity beyond the hierarchy's borrower × channel level (5, which collapses).

### Held-out and final seeds

Selected configuration: `--policy rl2 --rl2-opts '{"pacing": "dual"}'`. Held-out results:

| Seed | CN10 r/₹1k | Current default | **RL v2 (dual)** | STOPs CN10 / default / rl2 | Replies | Payments | ₹ recovered per ₹ spent | Spend ₹ CN10 / default / rl2 |
|---|---|---|---|---|---|---|---|---|
| 555 | 66.42 | 101.31 (+52.5%) | **84.39 (+27.1%)** | 15 / 13 / **10** | 192 / 202 / 184 | 48 / 62 / 55 | 246 / 448 / 358 | 2,891 / 1,994 / 2,180 |
| 11 | 55.17 | 95.64 (+73.4%) | **91.80 (+66.4%)** | 16 / 18 / **16** | 163 / 180 / 154 | 39 / 39 / 40 | 186 / 302 / 356 | 2,954 / 1,882 / 1,678 |
| 123 | 67.38 | 141.99 (+110.7%) | **110.74 (+64.4%)** | 23 / 22 / **17** | 197 / 203 / 204 | 61 / 56 / 56 | 246 / 531 / 402 | 2,924 / 1,430 / 1,842 |

On held-out, RL v2 (dual) had STOPs ≤ CN10 on all three seeds (43 vs CN10 54, current default 53), and
replies/₹ ≥ +27%. Payments were ≥ CN10 on 555 and 11 but below on 123 (56 vs 61).

**Final unseen seeds, run once** (CN10 / current default / RL v2 dual). K01 breaches and K15 violations were 0 / 0 everywhere:

| Seed | CN10 r/₹1k | Current default | **RL v2 (dual)** | STOPs | Replies | Payments | ₹ recovered per ₹ spent | Spend ₹ |
|---|---|---|---|---|---|---|---|---|
| 271828 | 66.27 | 135.77 (+104.9%) | **106.78 (+61.1%)** | 10 / 15 / 19 | 190 / 224 / 223 | 59 / 63 / 52 | 272 / 517 / 313 | 2,867 / 1,650 / 2,088 |
| 161803 | 62.23 | 122.21 (+96.4%) | **97.14 (+56.1%)** | 22 / 17 / 15 | 185 / 216 / 191 | 45 / 69 / 54 | 229 / 530 / 425 | 2,973 / 1,767 / 1,966 |
| 141421 | 68.40 | 123.34 (+80.3%) | **99.15 (+45.0%)** | 14 / 12 / 16 | 194 / 219 / 247 | 56 / 68 / 84 | 268 / 459 / 411 | 2,836 / 1,776 / 2,491 |
| 42 (reference, tune) | 58.79 | 116.02 (+97.3%) | 103.16 (+75.5%) | 15 / 12 / 16 | 169 / 180 / 221 | 53 / 46 / 59 | 253 / 381 / 365 | 2,875 / 1,552 / 2,142 |

Not selected, for the frontier: R2 μ 5 on the final seeds got +61.2 / +66.6 / +37.3%, STOPs 16 / 22 / 13,
and payments 74 / 77 / 70 (the most payments of any arm).

**Decision: RL v2 is not the replay default.** The rule was: beat the current default on held-out mean
replies/₹ and minimum, without more STOPs. On held-out, RL v2 got +52.6% / +27.1% against the current
default's +78.9% / +52.5%. It is available as `--policy rl2`.

* **Where it is better than the current default:** STOPs on held-out (43 vs 53). It spends more of the
  pool (54–84% vs 47–66%).
* **Where it is worse:** replies/₹ on every held-out and final seed, and payments on most seeds.
* **Against the four constraints, on the 6 held-out + final seeds:**
  * replies/₹ ≥ +20%: 6 of 6.
  * STOPs ≤ CN10: 4 of 6 (271828: 19 vs 10; 141421: 16 vs 14).
  * payments ≥ CN10: 4 of 6 (123: 56 vs 61; 271828: 52 vs 59).
  * ₹ recovered per ₹ spent above CN10: 6 of 6.
  * spend: 54–84% of the pool.

Determinism: `--check-determinism` at 1,500 × 14 on seed 271828 with `--policy rl2 --rl2-opts
'{"pacing": "dual"}'` → IDENTICAL (CRM envelope hash 1bc7d4a67666f318 both runs). The real script gave
the same numbers as the sweep harness on that seed.
