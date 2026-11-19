**Footage and composites**
- **Stock people:** every person is Pexels stock footage, cast to the story (Vikram, Asha, Sunita, borrower B, the team). They are actors, not users. Every screen they hold shows the real Kairos UI, composited in post.
- **Screen composites:** phone screens are green-screen keyed and laptop screens are white-screen tracked (`tools/screen_comp.swift`). On close inspection there is a thin edge along some phone bezels. The pale green phone in borrower B's shots (H8_5) needed a lower key threshold and a UI contrast lift. Asha's laptop is H6_3, a woman's hands over the shoulder; her sleeve colour does not match her wide shots.
- **UI inside footage:** phone and laptop screens in footage are scene detail and are not meant to be read at 1080p. The readable component always follows on a cream card.

**Captures**
- **Recorder animations:** the offscreen WKWebView recorder disables page animations. The Engine Room's one-time pulse on the 1–30 card, and its chip flip at the switch, are therefore not in the film. The stills show the final states. All motion comes from the compositor.
- **Real UI wording:** cards show the pages' own wording, for example "Locked by guardrails: no person can override this." and engine times in ms. The approval beat crops the Approvals row before and after the click, so no "presenter" wording appears on screen.
- **Mixed sources:** the Engine Room panels come from a separate local run (seed 42, 300 synthetic borrowers, 14 days). The journey cards come from the K03 demo journeys. They are different SIMULATED runs and are labelled as such.

**Timing and sound**
- **Long phrases:** two narration lines are longer than one caption cue: "Before anything goes out, …" and Kairos's OTP line. They are split at the clause boundary into two complete cues. A few line breaks fall before a conjunction rather than at a comma.
- **Tight dialogue:** in the Sunita exchange, the gap between speakers is 0.17–0.27 s, including the files' own tail silence.
- **Lockup:** the product runtime is 2:01. The music stops on the bar 68 downbeat, and the lockup holds 4.6 s in silence by design. The technical film runs 1:53, and its closing loop animation is shortened to about 2.5 s to land on the hard stop.
