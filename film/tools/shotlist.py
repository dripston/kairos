#!/usr/bin/env python3
"""Write film/shot-list.csv (full cut) and film/shot-list-operator.csv from the film's scene table.

Timings (start/end/duration) and voice-over come from kairos_film.html, so the shot list cannot drift
from the film. Shot descriptions live below. 30 fps; every boundary is a whole frame.

    python3 film/tools/shotlist.py
    python3 -I <skill>/scripts/validate_shotlist.py film/shot-list.csv --target 216
"""
from __future__ import annotations

import csv
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "music"))
import compose  # noqa: E402

FPS = 30
COLS = ["shot_id", "start_s", "end_s", "duration_s", "purpose", "visual_action", "framing", "camera", "light_palette",
        "feature_proof", "voiceover", "on_screen_text", "sound", "transition", "method", "continuity_notes", "claim_status"]
CODE = "code-rendered HTML/CSS (kairos_film.html), recorded 1920x1080 @30 fps"
REAL = "real product screen (headless Chrome capture from origin/main, synthetic data) in CSS device frame"
NAVY = "navy #06101d stage, teal #2dd4bf accent, soft bokeh, vignette"

# scene id -> list of (local start s, purpose, visual_action, framing, camera, feature_proof, on_screen_text, sound,
#                      transition_out, method, continuity, claim_status)
SHOTS = {
    "s1": [(0.0, "hook", "Phone on a lock screen buzzes; the same overdue SMS lands five times and stacks", "medium, phone left third",
            "slow push-in + drift", "", "Meet Ravi. · 5 identical reminders · Replies: 0", "phone buzz foley x5, sparse minor pad", "cut on buzz",
            CODE, "Phone frame P1 (380x760, notch) reused in s4", "ILLUSTRATIVE"),
           (7.0, "problem", "'Seen, no reply' tag; budget bar drains, a rupee coin falls per message", "medium wide", "drift",
            "", "Lender's budget, spent on every one", "tense pad, hats tick", "eased cross-dissolve 0.9 s with blur", CODE, "", "ILLUSTRATIVE")],
    "s2": [(0.0, "problem", "10-day calendar grid; playhead fires the same message on fixed days; almost all turn grey", "wide grid",
            "slow lateral drift", "", "A fixed calendar. Same message. Everyone. Every channel.", "tense pad", "", CODE, "", "ILLUSTRATIVE"),
           (6.6, "problem->product", "Chips Costly / Annoying / Mostly ignored; product named", "wide", "drift", "", "Costly · Annoying · Mostly ignored",
            "riser into s3", "cross-dissolve + soft impact", CODE, "", "N/A")],
    "s3": [(0.0, "identity", "Dot field: every borrower, every day, teal = contact, grey = wait", "wide", "push-in", "Daily per-borrower decision (CM-02 #1)",
            "Every borrower. Every day. First question: contact, or wait?", "music lifts to major, impact on downbeat", "", CODE, "", "VERIFIED"),
           (2.6, "identity", "Ravi's card; Contact/Wait pills flicker and settle on Contact", "medium", "locked + drift", "Contact or wait", "Contact · Wait",
            "click foley", "", CODE, "Ravi card: avatar R, synthetic chip", "VERIFIED"),
           (6.4, "proof_setup", "Channel, time, message columns scan and lock", "medium wide", "drift", "Channel + time slot + template choice",
            "Channel · Time · Message", "three ticks", "", CODE, "", "VERIFIED"),
           (11.4, "proof_setup", "Decision bar: WhatsApp 7:30 pm check-in, Hindi; checked twice by Aladdin", "medium", "settle", "Supervisor checks twice (K06)",
            "Checked twice by Aladdin", "pop", "cross-dissolve", CODE, "", "VERIFIED")],
    "s4": [(0.0, "demo_journey", "Phone: SMS from the lender, read 10:02, NO REPLY", "medium, phone left", "drift", "Delivery/read is not a reply",
            "1 SMS read, no reply", "message pop, pulse groove", "", CODE, "Generic messaging UI, no WhatsApp trade dress", "VERIFIED"),
           (3.5, "demo_journey", "Evening chat 7:32 pm; Ravi replies 'need time, salary late hai' (inner voice)", "medium", "drift", "Channel + time switch; intent understood (Hinglish)",
            "2 WhatsApp, in the evening · 3 Ravi: need time", "pops, Ravi inner voice (Aman)", "", CODE, "", "VERIFIED"),
           (8.5, "demo_journey", "Voice-bot call screen, waveform; Ritu asks in Hindi, Ravi promises the 27th, Ritu reads it back", "medium", "drift",
            "Sarvam voice bot, Hindi, date read-back (K17)", "4 Voice bot agrees a date and reads it back", "ring, Ritu (Lekha) + Ravi (Aman)", "", CODE,
            "Bot name Ritu matches the Sarvam voice", "VERIFIED"),
           (22.2, "demo_journey", "Queued reminders all marked PAUSED until the 27th", "medium", "drift", "Promise pauses ordinary reminders",
            "5 Reminders pause", "click", "", CODE, "", "VERIFIED"),
           (23.6, "demo_journey", "Payment verified by the bank; Loan A reminders struck out, Loan B still scheduled", "medium", "drift",
            "Only a verified payment clears an EMI, for that EMI only", "6 Payment verified: only that loan's reminders cancel", "chime, swish",
            "shared-element morph: phone screen grows into the CRM laptop screen", CODE, "", "VERIFIED")],
    "s4b": [(0.0, "proof_real", "Real CRM borrower page B-1042 in a laptop; slow push into Loan A CLEARED / Loan B PENDING", "medium wide laptop",
             "Ken Burns push-in to loans", "CRM record with evidence (K05/K14)", "Every step, in the CRM. · REAL SCREEN", "ticks on chips",
             "cross-dissolve", REAL, "assets/real/crm_borrower.jpg", "VERIFIED")],
    "s5": [(0.0, "proof_safety", "Title + Aladdin chip", "wide", "drift", "Aladdin = Kairos Supervisor (K06)", "Safe by design. · Aladdin, our supervisor",
            "calm pad", "", CODE, "", "VERIFIED"),
           (5.9, "proof_safety", "STOP on SMS; four channel switches turn OFF; 'Blocked by Aladdin: STOP'", "medium card", "drift",
            "STOP on any channel stops all channels", "STOP means stop. Everywhere.", "four ticks", "", CODE, "", "VERIFIED"),
           (9.4, "proof_safety", "Real CM-03 console: edited payment screenshot, scan, stamp FRAUD SUSPECT", "medium card", "Ken Burns pan down the console",
            "Every seeded fake caught (127/127)", "Fake payment proof? Caught. · REAL SCREEN", "thunk on stamp", "", REAL,
            "assets/real/cm03_fake_caught.jpg", "VERIFIED"),
           (12.0, "proof_safety", "'Meri naukri chali gayi' hands over to a human officer; bots stop", "medium card", "drift", "Hardship to a human",
            "Hardship goes to a person.", "chime", "", CODE, "", "VERIFIED"),
           (14.5, "proof_safety", "Chip: nothing shared until identity is confirmed with a one-time code", "wide", "settle", "0 disclosures before OTP",
            "Nothing is shared until identity is confirmed", "", "cross-dissolve", CODE, "", "VERIFIED")],
    "s6": [(0.0, "proof_control", "Approval card; cursor clicks Approve; audit rows appear; blurred real decision log behind", "medium", "drift",
            "Approve / hold / override, audited (K06 HITL)", "People stay in charge.", "click, ticks", "cross-dissolve", CODE + " + real backdrop",
            "assets/real/journey_report.jpg (blurred)", "VERIFIED")],
    "s7": [(0.0, "proof_learning", "Reply-rate lines draw; WhatsApp dips at day 7; 'switched by itself'; send mix shifts", "medium chart", "drift",
            "In-campaign channel switch after WhatsApp drift", "It learns from every reply.", "music builds", "", CODE, "Chart is illustrative", "ILLUSTRATIVE"),
           (7.9, "proof_learning", "Dots move between four layers (SMS & email, WhatsApp, voice bot, person); region and borrower chips", "medium", "drift",
            "Auto-churn layers (K27)", "Borrowers move between layers", "build", "cross-dissolve", CODE, "", "ILLUSTRATIVE")],
    "oi": [(0.0, "hook", "Title: A day at the desk; Priya, collections head (synthetic)", "wide", "push-in", "", "A day at the desk.", "soft intro pad",
            "cross-dissolve", CODE, "", "N/A")],
    "o1": [(0.0, "identity", "9:00 am, coffee steams; laptop shows today's plan already made: 3,412 due, contact / wait / 0 to set up", "medium wide laptop",
            "drift", "Plan made automatically every day", "9:00 am. Today's plan is already done.", "chime, ticks, morning groove",
            "shared-element morph: laptop screen grows into the scoreboard", CODE, "Laptop L1 (920x580) reused in o5", "ILLUSTRATIVE")],
    "o2": [(0.0, "demo_operator", "Broadcast scoreboard: runs = rupees collected tick up; overs = time; run rate vs required", "wide board", "drift",
            "Live collections view", "The collections scoreboard. · LIVE · illustrative / SIMULATED", "match groove, crowd bed", "", CODE,
            "/demo/live not on main yet: high-fidelity rebuild", "ILLUSTRATIVE"),
           (8.0, "demo_operator", "Partnerships bar by channel; WICKETS = rule breaches = 0", "medium board", "drift", "0 rule breaches", "WICKETS 0",
            "pop", "", CODE, "", "VERIFIED"),
           (13.7, "demo_operator", "EXTRAS = money saved by not sending", "medium", "drift", "Fewer wasted messages", "EXTRAS", "", "", CODE, "", "ILLUSTRATIVE"),
           (17.6, "demo_operator", "Commentary feed: FOUR!, smart captaincy, STOP, change of bowling; Priya: 'Zero wickets, again.'", "medium", "drift",
            "Decisions explained", "LIVE COMMENTARY", "crowd swells, cheer on FOUR", "cross-dissolve", CODE, "", "ILLUSTRATIVE")],
    "o3": [(0.0, "demo_operator", "One notification: a borrower reported a job loss; suggested gentle call", "medium", "drift", "Only big calls reach a human",
            "Only the big calls come to her.", "pop", "", CODE, "Score bug top centre keeps ticking", "VERIFIED"),
           (7.5, "demo_operator", "Priya: 'Approve.' Click; Aladdin re-checks consent, quiet hours, limits, hardship; scheduled", "medium", "drift",
            "Gate re-check at send time", "Aladdin re-checks before anything goes out", "click, ticks, chime", "cross-dissolve", CODE, "", "VERIFIED")],
    "o4": [(0.0, "demo_operator", "Five cards: real CRM story, real decision log, report pack (AICI, Data Dump, Trails, FTD, Hourly), segment report, learning",
            "wide row", "cursor moves card to card", "Everything one click away (K05, K26)", "Everything, one click away.", "clicks", "cross-dissolve",
            REAL + " + code-rendered cards", "assets/real/crm_borrower.jpg, journey_report.jpg", "VERIFIED")],
    "o5": [(0.0, "payoff", "Match result: Kairos won on ₹ recovered per ₹1 spent; player of the match; wickets 0", "wide", "drift",
            "~2x money recovered per rupee", "Match result. Kairos won · about 2x", "cheer, uplift", "", CODE, "", "VERIFIED (SIMULATED)"),
           (10.3, "payoff", "Laptop lid closes; 6:02 pm, Priya heads home on time", "medium laptop", "locked", "", "6:02 pm. Priya heads home on time.",
            "laptop close foley", "cross-dissolve", CODE, "Laptop L1", "N/A")],
    "s8": [(0.0, "payoff_proof", "Proof 1: ~2x money recovered per ₹ spent; bars lender 272 vs Kairos 517", "wide", "drift",
            "K09 vs CN10, unseen seeds", "How do we know? · ~2x · SIMULATED data", "uplifting groove", "", CODE, "", "VERIFIED (SIMULATED)"),
           (6.8, "payoff_proof", "Proof 2: 0 rule breaches; 49/49 attacks blocked; 127/127 fakes caught", "medium", "drift", "Red team + CM-03 eval",
            "SAFE BY DESIGN · 0", "tick", "", CODE, "", "VERIFIED"),
           (11.0, "payoff_proof", "Proof 3: 73.2% chats resolved without a human; 50,000 decisions in about 2 minutes; 6 bots, 8 languages; footnote",
            "wide", "drift", "CM-03 eval, bench", "RUNS ITSELF, PEOPLE IN CHARGE", "music dips to silence", "silence beat then dissolve", CODE, "", "VERIFIED")],
    "s9": [(0.0, "cta", "Lockup: Kairos. Right message. Right time. Or none at all. Team and event", "wide", "settle", "", "Kairos. · Or none at all.",
            "quiet beat, then resolving cadence F-G-C", "fade out", CODE, "", "N/A")],
    "oc": [(0.0, "cta", "Lockup: Kairos. Collections on autopilot. You just watch the score.", "wide", "settle", "", "Collections on autopilot.",
            "quiet beat, resolving cadence", "fade out", CODE, "", "N/A")],
}


def frame(x):
    return round(round(x * FPS) / FPS, 4)


def build(cut, out):
    scenes, cuts, _ = compose.parse_film(compose.FILM)
    tl, total = compose.timeline(scenes, cuts[cut], cut)
    rows, n = [], 0
    for s in tl:
        shots = SHOTS[s["id"]]
        for i, sh in enumerate(shots):
            a = s["start"] + sh[0]
            b = s["start"] + shots[i + 1][0] if i + 1 < len(shots) else s["end"]
            vo = " / ".join((f"{compose.SPEAKER_NAME.get(c['speaker'])}: " if c["speaker"] else "") + c["caption"]
                            for c in s["cues"] if sh[0] <= c["t"] < (shots[i + 1][0] if i + 1 < len(shots) else 1e9))
            n += 1
            rows.append({"shot_id": f"{n:02d}-{s['id']}", "start_s": frame(a), "end_s": frame(b), "duration_s": frame(b - a),
                         "purpose": sh[1], "visual_action": sh[2], "framing": sh[3], "camera": sh[4], "light_palette": NAVY,
                         "feature_proof": sh[5], "voiceover": vo or "NO VO", "on_screen_text": sh[6], "sound": sh[7], "transition": sh[8],
                         "method": sh[9], "continuity_notes": sh[10], "claim_status": sh[11]})
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {out}: {len(rows)} shots, {total:g} s")
    return total


if __name__ == "__main__":
    root = os.path.join(HERE, "..")
    build("full", os.path.join(root, "shot-list.csv"))
    build("operator", os.path.join(root, "shot-list-operator.csv"))
