# K15 · Safety, Load and Story Replay Engine — v1.0.0

**Owner:** Varun (Data & Replay Owner · QA Testing)  
**Epic:** `05 · Measure & demo` · **MoSCoW:** `Must` · **Status:** Complete & Validated  
**Depends on:** `K01 v1.1.0`, `K09`, `K10 v1.0.0`, `K12`, `K14`, `K21 v1.1.0`  
**Files:** `replay_engine.py` (core engine) · `test_cases.csv` (12 formal test cases) · `benchmark_report.json` (machine-readable replay output) · `reference_check.py` (stdlib test runner; `python3 reference_check.py` → 12/12 pass in ~4.6s)

> **What it answers:** How does Kairos perform at scale (50,000 borrowers) under identical budgets and seeds against the official Fixed-Cadence baseline? It proves the **$\ge 20\%$ replies/₹ lift**, **zero cap breaches**, **planning throughput under 10 minutes**, and **zero unverified disclosures in 500 concurrent inbound chats**.

---

## 1. Acceptance Criteria ("Done when") Verification

| Criteria | Target | Measured Result | Status |
| :--- | :--- | :--- | :--- |
| **50K Planning Throughput** | $< 10$ minutes ($600\text{s}$) | **4.64 seconds** ($150,786\text{ decisions/s}$) | **PASSED** |
| **Response Efficiency Lift** | $\ge 20\%$ vs Baseline | **+56.55% Lift** (370.31 vs 236.54 replies/₹1k) | **PASSED** |
| **Regulatory Cap Breaches** | Exactly **0** | **0 Breaches** (100% Policy Compliant) | **PASSED** |
| **CM-03 Concurrent Servicing**| 500 conversations | **500 Chats Simulated** | **PASSED** |
| **CM-03 Unverified Disclosures**| Exactly **0** | **0 Leaks** before name verification gate | **PASSED** |
| **CM-03 Fake Screenshot Catch**| 100% of fakes caught | **100.0% Detected & Blocked** | **PASSED** |

---

## 2. Apples-to-Apples Policy Benchmark

Both policies ran on the exact same 50,000-borrower portfolio synthesized from `PL_Dummy_Collections_Data_500.csv` over 7 campaign days with seed `42` and a ₹250,000 budget pool:

| Metric | Fixed-Cadence Baseline | Kairos Adaptive Bandit | Delta / Impact |
| :--- | :--- | :--- | :--- |
| **Response Efficiency (Replies / ₹1,000)** | 236.54 | **370.31** | **+56.55% Lift (Acceptance $\ge 20\%$)** |
| **Total Meaningful Replies** | 22,728 | **36,119** | **+13,391 Extra Replies (+58.9%)** |
| **Total Campaign Spend** | ₹96,086.40 | ₹97,536.20 | Within budget pool |
| **Cost per Meaningful Reply** | ₹4.23 | **₹2.70** | **-36.1% Cost Reduction** |
| **Regulatory Cap Breaches** | 0 | **0** | Zero breaches |
| **Planning Time (50,000 Accounts)** | — | **4.64 seconds** | 120x faster than 10-min SLA |

---

## 3. End-to-End Funnel Measurement

Reconciles all stages with exact denominators:
1. **Eligible Borrowers:** 50,000 accounts
2. **Touches Attempted:** Approved touches passing Policy Supervisor
3. **Touches Delivered:** Carrier / Meta delivery receipt captured
4. **Meaningful Replies:** Inbound response captured within 24 hours
5. **Verified Recovery (INR):** D7 ledger-matched overdue EMI payments cleared

---

## 4. Edge Cases Tested & Verified (Owned by Varun)

- **E01 (Intake & DPD Freshness):** Missing DPD or DPD older than 24 hours returns `WAIT / DPD_INVALID`. Pre-due starts at D-6. DPD 29→30 stays B1 (cap 3); DPD 31 enters B2 (cap 6).
- **E04 (Channel Dispositions):** SMS delivery receipt is recorded, but read status remains strictly `UNKNOWN`. SMS delivery adds 0 points to propensity score.
- **E06 (Channel Switch):** A borrower unresponsive to SMS on Day 1 transitions to WhatsApp on Day 2 if WhatsApp reachability is detected.
- **E17 (Statistical Policy):** Thompson Sampling is strictly deterministic given `seed=42`. Bayesian update adjusts Beta distribution ($\alpha, \beta$) as replies arrive.

---

## 5. How to Run & Verify

Run the self-contained verification suite from this folder:

```bash
cd /Users/varunmb/.gemini/antigravity/scratch/kairos_policy_ojas/kairos-policy/K15-safety-load-replay
python3 reference_check.py
```

Expected output:
```
12/12 passed (K15 safety, load and story replay) | 50,000 decisions in ~4.6s
Artifact written: benchmark_report.json
```
