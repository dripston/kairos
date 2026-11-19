# K01 · Kairos Outreach Decision Policy — v1.1.0 (draft for sign-off)

**Owner:** Ojas (Product) · **Consumers:** Rehan (Supervisor/guardrails, K06), Varun (policy engine, K09/K15), Yuval (template selector K22, trace K14)
**Files:** `policy.json` (machine-readable, the source of truth) · `test_cases.csv` (38 expected outcomes) · `lender_caps.example.json` (lender cap input template) · `reference_check.py` (stdlib reference of the rule order; `python3 reference_check.py` → 38/38 pass)

> The engine decides *what is best*. This policy decides *what is allowed*. Every rule here is a hard gate — a breach fails CM-02. All numbers live in `policy.json`; code must read them, never hard-code them. Every logged decision must cite `policy_version`.

## 1. DPD bands
| Band | DPD | Purpose allowed |
|---|---|---|
| PRE_DUE | −6 to 0 (from 6 days before due date) | reminder only |
| B1 | 1–30 | reminder, collection |
| B2 | 31–60 | reminder, collection |
| B3 | 61–90 | reminder, collection |
| B4 | 91+ | reminder, collection |

DPD earlier than −6 → **not in scope**. Missing DPD, or DPD older than 24 h → **HOLD**. Collection-purpose messages need a verified overdue status.

## 2. Channels
SMS, WhatsApp, email, IVR, voicebot — **all allowed in every band**. Consent is required per channel.

## 3. When contact is allowed
- **Window:** 08:00 to before 19:00 IST (19:00 itself is outside).
- **Quiet days:** Sundays and national holidays (26 Jan, 15 Aug, 2 Oct). Outbound only — inbound servicing (CM-03) stays on.

## 4. Caps (per borrower, counted across all campaigns)
| Band | Per channel per day |
|---|---|
| PRE_DUE | **3 per day across all channels combined** |
| B1 1–30 | 3 |
| B2 31–60 | 6 |
| B3 61–90 | 9 |
| B4 91+ | 12 |

- No ceiling across channels (by decision). Budget is the practical limiter.
- **Minimum gap: 2 hours between two sends on the same channel.** Different channels may be closer together.
- **Physical ceiling:** a 2-hour gap inside 08:00–19:00 allows at most **6 sends per channel per day**. B3/B4 caps are stored as configured but bind at 6 under the current window.
- **Lender caps (optional input):** a lender can supply its own caps per allocation (`lender_caps.example.json` shows the format, loaded into `caps.lender_overrides`). Supported: per channel per day by band, per channel per week by band, all channels per day / per week by band, per-channel overrides (e.g. IVR 1/day), and a longer minimum gap. **Lender caps can only tighten, never loosen.**
- **Official caps:** organisers' D11 caps are a third source.
- **Effective cap = the strictest of policy, lender and official** for every cap; effective gap = the longest of the three.

## 5. Stop for the day
Either of these stops **all outbound on all channels** until the next contact day:
1. A call (IVR/voicebot) is answered for more than **10 seconds**.
2. Any reply or borrower-behaviour disposition is captured on any channel.

## 6. Holds
| Hold | Trigger | Effect | Ends |
|---|---|---|---|
| **Promise to pay** | PTP with a date | No activity on any channel | On the PTP date, check verified payment first. Paid → close the obligation and cancel its queued touches. Unpaid by end of day → tag `BROKEN_PTP` and resume next contact day at band caps. |
| **Hardship** | Accident, illness, job loss, etc. | Pause all outreach | No date given → 2 days, then resume. Borrower gives a date within 4 days → hold until that date/time. Date beyond 4 days → keep hold and **HANDOFF** to supervisor. (Detail in K13.) |
| **STOP / opt-out** | STOP on any channel | Suppress **all** channels from the next cycle; cancel queued sends | Borrower may opt back in per channel; consent version recorded. |

## 7. What counts as a reply (feeds learning)
- **Reply window: 24 h, all channels.** One reward per touch, finalised when the window closes.
- **Counts:** inbound text/voice reply, call answered with a captured response, PTP captured.
- **Does not count:** delivery, read, link click, email delivery. A click updates reachability only. Unknown read status stays `UNKNOWN`.

## 8. Templates
Approved templates only, in English and Hindi. No suitable approved template → **WAIT**. The catalogue is owned by K22.

## 9. Budget and stop conditions
- **Inputs (2):** `budget_per_case_inr` and `collection_target`.
- **Pool** = budget per case × allocated cases, for the allocation period (start → end date). Individual cases may spend more or less than the per-case figure; the pool is what is enforced.
- **Daily pacing (derived):** remaining pool ÷ remaining contact days, recomputed each day; unspent budget carries forward. This satisfies the brief's "daily and campaign budget" without a second budget input.
- Every send **reserves** its estimated cost before dispatch; the reservation is reconciled to the actual charge.
- **Campaign stops** when the pool is exhausted **or** the collection target is met — in **all** runs, including the official simulator run (decided 19 Nov):
  - amount % = verified ₹ collected ÷ ₹ overdue at allocation start
  - count % = accounts fully cured ÷ accounts allocated
  - either one triggers.

## 10. Rule order (Supervisor evaluates top-down, first hit wins)
STOP/opt-out → consent → DPD valid → in scope (≥ −6) → hardship hold → PTP hold → obligation cleared → quiet day → contact window → stop-for-day → channel daily cap (strictest of policy/lender/official) → all-channel daily cap → weekly caps → same-channel gap → approved template → budget → campaign stop.
Outputs: `SEND`, `WAIT`, `CANCEL` (STOP, cleared obligation), `HANDOFF` (supervisor).

## 11. Decisions log and open items
**Decided (19 Nov, Ojas):** collection-target stop applies in all runs including the official one — *accepted risk:* if the target fires early in the judged simulation, contact stops before the comparison window ends. Pre-due reminders start at DPD −6. Lender-supplied caps must be enterable (done: `caps.lender_overrides`).

**Open:**
1. **Official caps (D11) not yet received.** The strictest-wins rule protects zero-breach; check the values when the file lands.
2. **B3/B4 caps (9, 12) are unreachable** under the current window and 2-hour gap (max 6/channel/day). Stored for future window changes.
