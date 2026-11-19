#!/usr/bin/env python3
"""Write film/shot-list.csv from the film's scene table, the Neha VO timeline and the trailer hits.

Cuts are computed exactly as the player does: scene lengths from kairos_film.html, then any cut within
0.45 s of a hit (0.85 s for a 'big' hit) snaps onto it. Hits come from music/original/v3_hits.json when present.

    python3 film/tools/shotlist.py
    python3 -I <skill>/scripts/validate_shotlist.py film/shot-list.csv --target 139.635
"""
from __future__ import annotations

import csv
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
FILM = os.path.join(HERE, "..")
COLS = ["shot_id", "start_s", "end_s", "duration_s", "purpose", "visual_action", "framing", "camera", "light_palette",
        "feature_proof", "voiceover", "on_screen_text", "sound", "transition", "method", "continuity_notes", "claim_status"]
DEFAULT_HITS = [(0.0, "boom"), (26.28, "big"), (43.28, "hit"), (58.87, "hit"), (66.65, "stop"), (76.87, "hit"), (84.8, "soft"),
                (91.34, "hit"), (101.23, "build"), (118.09, "big"), (126.7, "hit"), (131.21, "hit"), (139.0, "boom")]
BROLL = "real footage (Pexels, graded)"
DEVICE = "original device render (CSS) with a real product screen"
BUILT = "original device render with a rebuilt product view in the product's design"
TYPE = "kinetic type on black (HTML/CSS)"
# id -> purpose, visual, framing, camera, palette, proof, on-screen text, transition, method, continuity, claim
D = {
 "n01": ("hook", "Mumbai skyline at night, then a busy street with buses and scooters", "wide", "slow push-in", "night blue then daylight, soft vignette",
         "", "", "fade in from black on the cold-open boom", BROLL, "Pexels city_aerial_mumbai_night, kolkata_buses", "N/A"),
 "n02": ("problem", "A phone lights up; three phones stack the same overdue reminder", "close-up then medium", "phone dolly-in, rig settles",
         "black stage, spotlight", "", "Same message. Same time.", "dissolve", BROLL + " + device render", "phones identical by design", "ILLUSTRATIVE"),
 "n03": ("problem", "A hand snoozes a phone; a woman reads in bed; a cost counter ticks", "medium", "slow push-in", "warm interiors",
         "", "Ignored. · Annoying. · ₹ spent on reminders (illustrative)", "dissolve", BROLL, "snoozing_phone, woman_phone_bed", "ILLUSTRATIVE"),
 "n04": ("identity", "Hands on a laptop in the dark, then our laptop opens and lights up with the Kairos wordmark and the dashboard", "medium wide",
         "lid opens on the BIG hit, push-in", "black, spotlight, floor reflection", "Kairos exists and runs collections", "Meet Kairos.",
         "dissolve; lid opens on hit 26.28", BROLL + " + " + BUILT, "laptop L1 reused across the film", "VERIFIED"),
 "n05": ("demo", "Real CRM borrower page on the laptop; push-in; three facts appear", "medium", "dolly-in to the borrower header",
         "warm studio", "Per-borrower context (CRM)", "It looks first. · 42 days · Twice, on WhatsApp · Responsive on WhatsApp", "dissolve", DEVICE,
         "assets/real/crm_borrower.jpg (synthetic B-1042)", "VERIFIED"),
 "n06": ("demo", "Two option cards; Contact, then Wait, light up; Contact is chosen", "wide", "locked", "warm studio",
         "Contact-or-wait decision per borrower per day", "Then it decides. · Contact today · Wait", "hard cut on hit 43.28", "HTML/CSS cards", "", "VERIFIED"),
 "n07": ("demo", "Four channel cards light in turn; a clock sweeps to 7:30 pm; WhatsApp is picked", "wide", "locked", "warm studio",
         "Channel + time slot choice", "It picks the channel. · SMS · WhatsApp · Voice call · Human agent · 7:30 pm", "dissolve", "HTML/CSS cards", "", "VERIFIED"),
 "n08": ("demo", "Two checklists tick: when planning, and again just before sending", "wide", "locked", "black, spotlight",
         "Aladdin (Kairos Supervisor, K06) checks twice", "Checks every message. · Twice.", "hard cut on hit 58.87", TYPE, "", "VERIFIED"),
 "n09": ("demo", "Three blocked examples; channels switch off; real STOP conversation on the laptop; still frame in the 72-73 s silence",
         "medium", "push-in, then held still", "warm studio", "Quiet hours, contact limits, STOP everywhere", "BLOCKED · ALL OFF", "hard cut on hit 66.65",
         DEVICE, "assets/real/cm03_stop.jpg", "VERIFIED"),
 "n10": ("demo", "A man on his phone at night; then a chat: he asks when his EMI is due; the agent is typing", "medium", "phone slides in",
         "night footage, then warm studio", "WhatsApp agent answers in seconds", "Answers in seconds. · In Hindi, or in English.", "hard cut on hit 76.87",
         BROLL + " + device render (generic chat UI)", "man_turban_phone_night", "VERIFIED"),
 "n11": ("demo", "The agent replies in Hindi; he says yes; a payment-link card arrives", "medium", "slight push", "warm studio",
         "Hindi/English agent", "हिंदी में बात। · English caption", "dissolve (soft hit, no drums)", "device render (generic chat UI)",
         "same chat as n10", "VERIFIED"),
 "n12": ("demo", "Identity verified by one-time code; then the real console flags an edited screenshot: no matching payment",
         "medium", "push-in to the fraud flag", "warm studio", "0 disclosures before OTP; fake proofs caught", "Identity first. · Fake proof? · FAKE CAUGHT",
         "hard cut on hit 91.34", DEVICE, "assets/real/cm03_fake_caught.jpg", "VERIFIED"),
 "n13": ("demo", "The learning panel: WhatsApp bar shrinks after day 7, SMS and voice grow", "medium", "dolly-in", "warm studio",
         "Learns from every reply", "It learns.", "hard cut on hit 101.23 (build starts)", BUILT, "", "ILLUSTRATIVE (bars)"),
 "n14": ("demo", "Live view: ₹ recovered climbs; one approval arrives on the phone and is tapped", "wide", "push-in, phone slides in",
         "black, spotlight", "Humans approve the few decisions that need them", "Watches. Approves. · Approved ✓", "dissolve; riser + silence into the proof",
         BUILT + " (real /demo/live crops)", "assets/real/live_recovered_*.png, live_breaches.png", "VERIFIED (SIMULATED)"),
 "n15": ("payoff", "Giant ~2× on the BIGGEST hit; money recovered per ₹ spent; vs a fixed schedule", "wide", "punch-in on hit", "black",
         "~2× ₹ recovered per ₹ spent", "~2× · money recovered per ₹ spent · SIMULATED", "hard cut on hit 118.09", TYPE, "", "VERIFIED (SIMULATED)"),
 "n16": ("payoff", "0 rule breaches, then 49/49 attack tests and 148/148 policy checks", "wide", "punch-in on hit", "black",
         "Safety record", "0 · 49/49 · 148/148 policy checks", "hard cut on hit 126.7", TYPE, "", "VERIFIED"),
 "n17": ("cta", "A man smiles on a call; then the wordmark, the line, team and credits", "medium then wide", "settle", "black",
         "", "Kairos · The right message, at the right time. · Or none at all.", "fade out on the final boom", BROLL + " + " + TYPE,
         "man_good_news_phone", "N/A"),
}


def scenes():
    src = open(os.path.join(FILM, "kairos_film.html"), encoding="utf-8").read()
    a = src.index("const ALL_SCENES = [")
    rows = re.findall(r'\{id:"(\w+)", name:"([^"]*)", secs:([\d.]+), vo:\[\["(\w+)",([\d.]+)\]\]', src[a:src.index("];", a)])
    return [(i, n, float(s), k, float(o)) for i, n, s, k, o in rows]


def hits():
    p = os.path.join(FILM, "music", "original", "v3_hits.json")
    if os.path.exists(p):
        j = json.load(open(p))
        arr = j if isinstance(j, list) else j.get("hits", [])
        out = [(float(x[0]), str(x[1])) if isinstance(x, list) else (float(x.get("t", x.get("time"))), str(x.get("type", x.get("kind", "hit")))) for x in arr]
        kind = {"cold_open": "boom", "biggest": "big", "riser": "soft", "cut": "soft", "pullback": "soft"}
        known = {"boom", "big", "stop", "build", "soft"}
        out = [(t, kind.get(ty, ty if ty in known else "hit")) for t, ty in out]
        if out:
            return sorted(out)
    return DEFAULT_HITS


def main():
    sc, H = scenes(), hits()
    starts, t = [], 0.0
    for _, _, s, _, _ in sc:
        starts.append(t)
        t += s
    total = round(t, 3)
    for i in range(1, len(starts)):
        for ht, ty in H:
            if ty != "soft" and abs(ht - starts[i]) <= (0.85 if ty == "big" else 0.45):
                starts[i] = ht
                break
    lines = json.load(open(os.path.join(FILM, "audio", "vo_neha", "lines.json"), encoding="utf-8"))
    out = []
    for i, (sid, name, _, key, _) in enumerate(sc):
        a, b = round(starts[i], 3), round(starts[i + 1] if i + 1 < len(sc) else total, 3)
        d = D[sid]
        hit = [f"{ht:g}s {ty}" for ht, ty in H if a <= ht < b]
        out.append({"shot_id": f"{i + 1:02d}-{sid}", "start_s": a, "end_s": b, "duration_s": round(b - a, 3), "purpose": d[0],
                    "visual_action": d[1], "framing": d[2], "camera": d[3], "light_palette": d[4], "feature_proof": d[5],
                    "voiceover": "Neha: " + lines[key]["text"], "on_screen_text": d[6],
                    "sound": "score " + ("hits " + ", ".join(hit) if hit else "bed") + "; ducked -11 dB under Neha",
                    "transition": d[7], "method": d[8], "continuity_notes": d[9], "claim_status": d[10]})
    path = os.path.join(FILM, "shot-list.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        w.writerows(out)
    print(f"wrote film/shot-list.csv: {len(out)} shots, {total:g} s")


if __name__ == "__main__":
    main()
