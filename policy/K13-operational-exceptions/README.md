# K13 · Kairos Operational Exceptions — v1.0.0 (draft for sign-off)

**Owner:** Ojas (Product) · **Depends on:** K01 outreach policy v1.1.0 · **Consumers:** Rehan (Supervisor K06, tools K11), Yuval (WhatsApp conversation K10, trace K14), Varun (edge-case replays K15)
**Files:** `exceptions.json` (machine-readable rules) · `test_cases.csv` (25 expected outcomes) · `reference_check.py` (stdlib decision table; `python3 reference_check.py` → 25/25 pass)

> Covers what Kairos does when a borrower **needs time (E07)**, **asks for a person (E08)**, **reports hardship (E09)**, or raises something Kairos must **route, never decide**. Holds defined in K01 are referenced, not repeated.

## 1. Needs time (E07)
1. Push for a **payment date and time within the next 2 days**.
2. If the borrower resists, rebut. Maximum rebuttals per conversation:

| Band | Pre-due | 1–30 | 31–60 | 61–90 | 91+ |
|---|---|---|---|---|---|
| Max rebuttals | 1 | 3 | 5 | 7 | 7 |

3. **Each rebuttal gives a different, softer reason to commit** (date options, amount options, facts about consequences, offer of help). Never repeat the same ask; never threaten. A judge reading 7 identical pushes will see pressure.
4. Stop rebutting immediately on: a date given, STOP, hardship, abuse / legal threat / fraud claim, a callback request, or payment.
5. Rebuttals only **inside the contact window**. Outside it, acknowledge and capture any date offered, then wait.
6. After the last rebuttal, **capture whatever date the borrower gives**. No date at all → no hold; K01 stop-for-day applies; resume next contact day.
7. Rebuttals are replies inside a live conversation. They **don't count against outbound caps**.

## 2. Promise to pay
- Date **≤ 7 days** → accepted automatically; K01 PTP hold applies.
- Date **> 7 days** → captured, hold applies, **and a supervisor case is opened** to approve or counter.
- **After 2 broken promises in the allocation period:**
  - Kairos **does not ask** for a promise.
  - If the borrower offers one: politely say two earlier promises were missed and **push for payment now** with a payment link.
  - Paid now → payment verification (CM-03 flow).
  - Not paid → the PTP is **recorded in the backend with `NO_HOLD`**; outreach continues at band caps.

## 3. Callback (E08)
- **SLA: within 2 working hours.** Working hours = the K01 contact window on contact days; the clock pauses outside it.
- The borrower's request is logged as consent for **this callback only**.
- **While pending:** all outbound nudges pause. Repeat requests attach to the same task.
- **Unanswered:** retry once, 2 hours later. **Second miss:** send one "we tried to reach you" message with a reply option; resume normal outreach next contact day.
- **Answered:** close the task; K01 stop-for-day applies.

## 4. Hardship (E09)
- Acknowledge neutrally. No collection ask in that reply.
- Holds per K01: no date → 2 days; date within 4 days → hold until then; **beyond 4 days → supervisor**.
- A supervisor sees **only** the beyond-4-days cases (decided).

## 5. Route, never decide
| Type | Conversation | Outreach |
|---|---|---|
| Dispute · Complaint · Waiver/fee request *(required by CM-03 brief)* | Acknowledge, **no commitment** | Continues (K01 stop-for-day applies) |
| Abusive · Legal threat · Fraud claim | **End the automated conversation**, no argument | **Paused until a supervisor clears it** |

Every routed case creates an idempotent ticket: `ticket_id`, `idempotency_key`, `borrower_id`, `obligation_id`, `type`, 2-line summary, `evidence_ids`, `created_at`, `sla_due_at`, `policy_version`.

## 6. Queues
One fallback queue: **`COLLECTIONS_SUPERVISOR`**. When the organisers' routing matrix arrives, its mapping replaces this automatically (`official_routing_matrix_overrides: true`).

## 7. Decisions log and open items
**Decided (19 Nov, Ojas):** 2-day PTP target with 1/3/5/7/7 rebuttals; capture any final date; > 7 days → supervisor; 2 broken PTPs → no solicitation, push payment, record with no hold; callback within 2 working hours, 1 retry, then resume; hardship supervisor only beyond 4 days; abusive/legal/fraud routed; single supervisor queue.

**Defaults I set (confirm or change):**
1. No rebuttals outside the contact window.
2. No date after the last rebuttal → no hold.
3. A callback request counts as consent for that one call.
4. Disputes/complaints/waivers don't pause outreach; abusive/legal/fraud do.

**Not handled (accepted):** deceased or hospitalised borrowers (treated as hardship if detected). Wrong number is covered by the CM-03 OTP gate (no disclosure before verification).
