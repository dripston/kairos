# Kairos 2-minute film — brief (context store, not started)

**Status:** deferred. Build after the product work lands. Keep adding to "Moments to capture" as features ship.

## Goal
A 2-minute commercial-style film that a layman (judges, audience, a lender's ops head) understands. It shows
what we built, why it matters, and how it actually works, without code.

## Audience takeaways (in this order)
1. Collections today = the same message to everyone on a fixed calendar → expensive, annoying, low response.
2. Kairos decides, every day for every borrower, *whether* to contact, *which channel*, *what time*, *what message*,
   and learns from every reply in real time.
3. It is safe: consent, quiet hours, caps, STOP on any channel stops all channels, hardship goes to a human,
   nothing disclosed before identity check. Humans can approve, hold or override anything.
4. It works: more replies per rupee than the lender's own schedule (headline numbers below), zero rule breaches.

## Story spine (draft, ~2:00)
- 0:00–0:15 Hook: a borrower ("Ravi", synthetic) gets the 5th identical reminder; lender pays for every one.
- 0:15–0:35 Meet Kairos: a calm voice, one screen: "Every borrower, every day: contact or wait."
- 0:35–1:10 How it works, one borrower's journey: SMS read, no reply → WhatsApp in the evening → borrower replies
  "need time" → voice bot (Ritu) agrees a date and reads it back → reminders pause → payment verified → only that
  EMI's reminders cancel. Side beats: STOP on SMS stops everything; a fake payment proof is caught on WhatsApp;
  job loss → human takes over.
- 1:10–1:35 It learns: WhatsApp gets worse mid-campaign → Kairos switches channel by itself; cohorts move live;
  per-region / per-borrower learning.
- 1:35–1:55 Proof: Kairos vs the lender's schedule on unseen data, replies per rupee, payments, zero breaches.
- 1:55–2:00 Close: "Kairos. Right message. Right time. Or none at all." + team name.

## Headline numbers (update when PRs land; always label SIMULATED)
- K09 vs CN10 (CN's own 10-day cadence), 1,500 borrowers × 14 days, matched budget and caps:
  unseen seeds +48% / +61% / +56% replies per rupee, 0 cap breaches, more verified payments on every seed (PR #17).
  Caveat to fix before filming: STOP opt-outs higher than CN10 (opt-out penalty in progress).
- 50,000 daily decisions in ~2 minutes. Deterministic: same seed, same result.
- Red-team: 49/49 guardrail attacks pass, no open findings (PRs #14, #16, #19).
- Voice: 6 bots (Ritu / Advait), 8 languages, structured outcomes with confidence thresholds.

## Moments to capture (screen recordings / audio)
- Live journey view in the demo UI ("Run the journey"), journey.html timeline (integration/all).
- Control Room live event feed; HITL approval click; learning bars moving; channel-switch moment.
- Sarvam voice bot call audio in Hindi (Ritu) — test call via Sarvam console / test number (needs set-up).
- WhatsApp conversation in CM-03 console: OTP, balance, fake UTR caught.
- Real SMS arriving on a team phone (Messages relay or SMS Gateway for Android), STOP reply.
- K09 vs CN10 replay output; report pack CSV opened.

## Production notes
- Layman language only; no jargon (no "bandit", "posterior"): say "learns", "decides", "safe".
- All borrowers synthetic; show SIMULATED labels on screen at least once.
- Tools to consider later: screen capture + voice-over (Sarvam TTS Ritu/Advait voices for narration),
  simple motion titles; 16:9 and 9:16 cuts.
