**Length**
- The user chose about 3:30 per film. Neha stays at pace 1.3, and time was cut only from words and holds.
- With the approved trimmed lines, the voice alone is about 186 s in the product film (narration plus dialogue) and 180 s in the technical film.
- The product film lands at about 3:46. It also carries a cold open, a logo, two part cards, the Aladdin flash, three slogans and the lockup.
- The technical film lands at about 3:24.

**Footage and screens**
- **Stock people:** every person is Pexels stock, cast per `CASTING.md`: Vikram is the Delhi man (H2_4 and H7_6), and the montage and lender side use the human2 batch. They are actors, not users.
- **No tracked composites:** no UI is composited onto a moving phone or laptop. Every real screen is a still or recorded page in a glass window, or a still phone frame (`comp/phone.html` renders of the real message texts).
- **Whole pages:** pages are shown whole first. Where a browser viewport ends mid-page (the journey and inbound pages are 1440×810 and 1440×1150 viewports), the glass window shows the viewport the app shows. For the Control Room, the Engine Room and Voice bots, the page rectangle is chosen so that every card on screen is complete.
- **Engine Room animations:** the recorder freezes page animations, so the Engine Room's pulse and chip flip are not in the film; the stills show the final states.

**Data and wording**
- **Mixed runs:** the journey cards are from the release-candidate server (8120) and were matched to the story by content, not by step index (the indices drift between runs). The Engine Room run (seed 42, 300 borrowers, 14 days) and the journeys are different SIMULATED runs.
- **73% resolved without a person:** from 1,000 seeded test conversations. Counting only bank-verified payments (not recorded claims), the rate is **67.3%** (backend/README.md). That caveat is kept here, not on screen.
- **No vendor names:** the word "Sarvam" appears nowhere. The Voice bots page is shown only from the Calls card down; its header names the vendor. The deployed frontend's Control Room and Learning & Proof pages are cropped to cards without it.
- **The "presenter" wording** on one journey approval card is not used. The approval is shown from the F02 dashboard recording instead.

**Sound**
- **Music length:** both tracks are extended by repeating whole phrases (32, 16 or 8 bars, at downbeats, with 20–30 ms joins). They end on their own outro and ending, with no time-stretch.
- **Voices:** Neha (narration, pace 1.3, "Alaadin" in the TTS text), Kavya (the agent, 1.05) and Tanya (the borrower, 1.0), all AI-generated.

## Fixes after the final review (20 Nov, 13:08–13:59 IST)

- **Product** (re-spliced into the reviewed 4K master at 13:08):
  - At 1:51.57 the glass now fades in from the nebula (no empty glass frame).
  - Every glass-to-glass dissolve now eases over 0.6 s: the new window fades in over 0.4 s while the old one fades out over 0.1–0.6 s.
  - The identity, fake-proof and STOP beats hold on the result card (no phone-to-card snap).
  - The first splice at 12:47 had 2 bare-background frames at each range start. It was withdrawn. Range renders now use `--preroll 1.0`.
- **Technical** (re-spliced into the v4e 4K master at 13:57): all 21 transitions into or out of a title, chapter, big-number or brand card are sequential dips. The old element fades out over 0.25 s, then the card fades in over 0.25 s; texts never overlap. Window, diagram and panel crossfades are unchanged.
- **Still in both films:** single duplicated frames from the N1 nebula source (STUTTER flags). About 1 frame per second shows during nebula backgrounds.
  - The final2 attempt dropped those frames (`comp/NEB1c.mov`, `shots/NEB1c.json`). It was rejected: the technical final2 dropped to flat dark for one frame about every 31.3 s, which is the ping-pong period of the shorter clip.
  - The product final2 render timed out on a seek. NEB1c is not used.
