# K15 · Kairos vs fixed cadence — benchmark (RUN-s42-n50000-1cf8996d)

> **SIMULATED - dev fallback (PL_Dummy_Collections_Data_500 scaled to 50K; dev simulator, not the official CM-02 simulator).** Seed 42 · 50,000 borrowers · 2026-11-23 → 2026-12-20 (24 contact days) · same budget pool ₹400,000, same K01 caps, same simulator draws for both policies.

## Acceptance checks

| Check | Result |
|---|---|
| Replies per ₹ at least 20% above fixed cadence | **PASS** (+88.1%) |
| Zero cap breaches (every send re-checked with Ojas's K01 reference) | **PASS** (2,395,261 sends checked) |
| Daily decisions for all borrowers under 10 minutes | **PASS** (slowest day 0.15 s) |
| Same seed gives identical replay | **PASS** |

## Headline

| Metric | Fixed cadence | Kairos |
|---|---:|---:|
| Touches sent | 1,360,836 | 1,034,425 |
| Spend (₹) | 323,497 | 244,229 |
| Meaningful replies | 14,908 | 21,168 |
| Replies per ₹ | 0.0461 | 0.0867 |
| ₹ per reply | 21.70 | 11.54 |
| Unique borrowers reached | 49,096 | 49,069 |
| Unique responders | 13,059 | 16,943 |
| Verified payments after contact (incl. self-cure) | 15,556 | 17,338 |
| Verified payments after a reply | 6,190 | 8,653 |
| ₹ collected (all verified payments) | 305,727,918 | 339,392,452 |
| Opt-outs during campaign | 4,846 | 2,814 |

## Funnel (unique borrowers, each counted once per stage)

| Stage | Fixed cadence | Kairos |
|---|---:|---:|
| Allocated borrowers | 50,000 | 50,000 |
| Passed K01 gates at least once | 49,226 | 49,226 |
| Attempted (>=1 touch sent) | 49,154 | 49,127 |
| Delivered or connected | 49,096 | 49,069 |
| Meaningful reply (K01 definition) | 13,059 | 16,943 |
| Verified payment after replying | 6,190 | 8,653 |

## Where the lift comes from

- **Salaried** channel mix before → after the mid-campaign drift: sms 30%→50%, whatsapp 35%→24%, email 34%→25%, ivr 2%→1%, voicebot 0%→0%.
- **Self-employed** channel mix before → after the mid-campaign drift: sms 37%→35%, whatsapp 28%→40%, email 33%→24%, ivr 2%→2%, voicebot 0%→0%.
- Kairos waits when no touch clears the value threshold (0.04 replies per ₹), so it spends less on borrowers who are unlikely to respond today.
- K21 intensity: LOW borrowers get one cheap touch, voice only for STANDARD/HIGH; holds (PTP, hardship, STOP) always win.

## Caveat

This is the **dev simulator** with synthetic data. The judged number comes from the organisers' simulator on the hidden seed. The harness is built so that simulator replaces `src/world.py` and the official fixed-cadence policy file replaces `baseline.cadence` in `config.json` (blocked on K02).
