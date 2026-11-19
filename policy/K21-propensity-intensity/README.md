# K21 · Propensity to Pay + Nudge Intensity — v1.1.0 (draft for sign-off)

**Owner:** Ojas (Product) · **Depends on:** K01 v1.1.0, K13 v1.0.0 · **Consumers:** Varun (policy engine K09, replays K15), Yuval (template selector K22, trace K14), Rehan (Supervisor K06)
**Files:** `scorecard.json` (points, cut-offs, intensity levels) · `test_cases.csv` (19 profiles) · `reference_check.py` (stdlib scorer; `python3 reference_check.py` → 20/20 pass; scores 50,000 accounts in ~0.3 s)

> **What it answers:** how likely is a **verified payment in the next 7 days** (0–100), and how hard should Kaira work on this borrower today? **Holds always win.** K09's learning still picks the channel, the time and whether a touch is worth its cost; K01 caps remain the hard ceiling.

## 1. Strategy: persuadables first (decided)
Spend effort where it changes the outcome. That is what maximises **replies per rupee**, the CM-02 score.

| Score | Intensity | Meaning |
|---|---|---|
| 70–100 | **LOW** | Likely to pay anyway |
| 60–69 | **STANDARD** | Leaning to pay |
| **40–59** | **HIGH** | Most persuadable |
| 25–39 | **STANDARD** | Leaning not to pay |
| 0–24 | **LOW** | Unlikely to pay regardless |

## 2. Intensity levels
| Level | Allowance per day | Channels |
|---|---|---|
| WAIT | 0 | — |
| LOW | 1 touch in total | Cheapest consented channel with real evidence of reach (read, click, answer, reply); no voice |
| STANDARD | Half the K01 band cap per channel (rounded up) | All consented; IVR yes, voicebot no |
| HIGH | Full K01 band cap per channel | All consented, including IVR and voicebot |

**Low-evidence cap (decided):** if data exists for 2 or fewer of the 4 signal groups (`evidence_strength` ≤ 0.5), HIGH drops to STANDARD.

**Final allowance = the strictest of this, the K01 policy cap, the lender cap and the official cap.**

## 3. Scorecard (start at 50, clamp 0–100)
| Group | Signal | Points |
|---|---|---|
| **Payment history** | DPD band: pre-due / 1–30 / 31–60 / 61–90 / 91+ | +15 / +5 / −5 / −12 / −20 |
| | On-time EMIs in the last 6 months | (count − 3) × 4, range −12 to +12 |
| | Broken promises this period | −12 each, max −24 |
| | Paid in the last 30 days · part payment this cycle | +8 · +6 |
| **Engagement** | Borrower responded in the last 7 days (any channel) | **+15** |
| | Responded on 2 or more channels | +5 |
| | WhatsApp read · link clicked · call answered over 10 s (last 7 days) | +4 · +6 · +6 |
| | **SMS delivered** | **0** (by decision: delivery isn't engagement) |
| | Unreachable on every channel for 14 days | −10 |
| **Intent** (Summarizer) | Willing / silent / needs time / dispute / refused / hardship | +15 / 0 / −5 / −10 / −15 / −15 |
| **Bounce + AA** | NACH bounce: technical (balance was sufficient) / insufficient funds | **+12** / −8 |
| | AA net cash flow last month: positive / negative | +8 / −8 |
| | Debt-to-income above 0.65 / below 0.40 | −8 / +4 |
| | 3 or more AA risk flags | −6 |
| | **No AA consent** | **0** (neutral, never a penalty) |

## 4. Overrides → WAIT
STOP, hardship hold, PTP hold, callback pending, supervisor hold, obligation cleared, campaign stop. **The score is still computed and logged** for analytics.

## 5. Output (per borrower, per obligation, per day)
`score`, `horizon_days` (7), `intensity`, `override`, **`top_reasons`** (the 3 largest signed contributions, e.g. "bounce:TECHNICAL +12"), `evidence_strength` (share of the 4 signal groups with data; SMS delivery doesn't count), `model_version`, `policy_version`, `computed_at`. Score, evidence strength and intensity are separate fields, as the architecture requires.

## 6. Calibration
These weights are **synthetic**, for the demo. When the official 12-month history (D1/D2) arrives: fit a logistic regression on the same features to "verified payment within 7 days", convert the coefficients back to points, and keep the cut-offs unless the 40–59 band ends up holding less than 30% of accounts.

## 7. Decisions log and open items
**Decided (19 Nov, Ojas):** persuadables first; 7-day horizon; signals = DPD + payment history, engagement, intent, bounce + AA, connectivity per channel (SMS delivery ≈ 0), responses weighted highest; points scorecard now, calibrate later; HIGH level added.

**Decided (19 Nov, Ojas):** low-evidence cap applied — HIGH becomes STANDARD when `evidence_strength` ≤ 0.5. HIGH band at 40–59 taken as accepted.
