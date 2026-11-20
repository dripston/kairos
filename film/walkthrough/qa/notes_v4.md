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
