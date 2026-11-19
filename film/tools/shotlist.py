#!/usr/bin/env python3
"""Write the shot lists for the three cuts from the film itself.

The player computes the cuts (scene per voice lines, hit snapping), so this asks it: headless Chrome loads
kairos_film.html?cut=<cut>&dump=1 from the local server (tools/serve.py on :8766) and reads the shot table.

    python3 film/tools/serve.py 8766 &
    python3 film/tools/shotlist.py            # shot-list-product.csv, shot-list-tech.csv, shot-list.csv (full)
    python3 -I <skill>/scripts/validate_shotlist.py film/shot-list-product.csv --target 119.287
"""
from __future__ import annotations

import csv
import html
import json
import os
import re
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
FILM = os.path.join(HERE, "..")
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
OUT = {"short": "shot-list-product.csv", "tech": "shot-list-tech.csv", "full": "shot-list.csv"}
COLS = ["shot_id", "start_s", "end_s", "duration_s", "purpose", "visual_action", "framing", "camera", "light_palette",
        "feature_proof", "voiceover", "on_screen_text", "sound", "transition", "method", "continuity_notes", "claim_status"]
FOOT = "real footage (Pexels, graded)"
NEB = "nebula footage (Pexels) behind a product scene"
REAL = "real product screen (live dashboard capture, 2x), focused crop"
GS = "green-screen phone footage (Pexels), keyed live, chat UI tracked onto the screen"
DIAG = "animated architecture diagram (HTML/CSS) on nebula"
# scene name -> purpose, visual, framing, camera, palette, proof, on-screen text, method, claim status
D = {
 "Millions repay a loan": ("hook", "Mumbai at dusk, then a phone, a bike, a small-shop street", "wide → medium", "slow push", "dusk, warm street", "", "A phone. A bike. A small shop.", FOOT, "N/A"),
 "Same message, same time": ("problem", "Phone on a table; identical reminders stack; three people get the same banner", "close-up → split screen", "locked", "night interiors", "", "Your EMI is due. Please pay now. · To everyone.", FOOT, "ILLUSTRATIVE"),
 "Ignored, and it costs": ("problem", "A phone lights up unread; a call is ignored; the fixed schedule's real figures", "close-up", "locked", "dim interiors", "Baseline is wasteful", "1,262 reminders → 23 replies · 98 in 100 ignored · ₹575 spent", FOOT + " + figures from the seed-42 run", "VERIFIED (SIMULATED)"),
 "Meet Kairos": ("identity", "Hands on a laptop in the dark; our laptop opens on the big hit; real Overview screen", "medium", "rig settles, push to KPI row", "nebula, spotlight", "Kairos exists", "Meet Kairos.", NEB + " + laptop render + " + REAL, "VERIFIED"),
 "It looks, then decides": ("demo", "Borrower profile builds block by block, then Contact today / Wait", "wide", "locked", "nebula", "Per-borrower context and decision", "38 days late · past touches · best time 7–9 pm · consent · Contact today", NEB + " + HTML profile", "ILLUSTRATIVE (values)"),
 "Picks the channel and the hour": ("demo", "Four channel nodes on a ring rotate to each named channel with a real example message; WhatsApp 7:30 pm locks", "wide", "ring rotation", "blurred night traffic", "Channel + time choice", "SMS · WhatsApp · Voice call · Human agent · WhatsApp · 7:30 pm", FOOT + " (soft) + HTML ring", "VERIFIED"),
 "Aladdin checks twice": ("demo", "Checklist ticks; real Rules (Aladdin) screen; two checks", "medium", "crop moves", "nebula", "Aladdin (K06) checks twice", "Aladdin · ✓ when planning · ✓ just before sending", NEB + " + " + REAL, "VERIFIED"),
 "The rules hold": ("demo", "Night train: two blocked reminders; a woman says STOP; frame freezes as every channel switches off", "medium", "freeze frame", "night", "Quiet hours, limits, STOP everywhere", "BLOCKED · STOP · ALL OFF", FOOT, "VERIFIED"),
 "STOP, confirmed": ("demo", "Real hand on a phone types STOP; the agent confirms", "close-up", "handheld", "daylight table", "STOP honoured", "STOP · all channels paused", GS, "VERIFIED"),
 "Answers on WhatsApp": ("demo", "Man on his phone at night; then a real hand-held phone: question → OTP → amount → pay link", "medium → close-up", "handheld (slowed)", "night, then outdoor", "Agent answers; identity first", "AWAIT_OTP → VERIFIED → PAY LINK SENT", FOOT + " + " + GS, "VERIFIED (SIMULATED)"),
 "A voice call in Hindi": ("demo", "Woman on a call; live call card with waveform and transcript; promise to pay Friday", "medium", "locked", "bright interior", "Sarvam voice agent, PTP", "Promise to pay · Friday ✓", FOOT + " + HTML call card", "VERIFIED"),
 "Verified; fake caught": ("demo", "Real CM-03 console: OTP → VERIFIED; bank check fails; FAKE CAUGHT", "medium", "crop to the fraud flag", "nebula", "Identity first; fakes caught", "AWAIT_OTP → VERIFIED · No matching payment · FAKE CAUGHT", NEB + " + real CM-03 screen", "VERIFIED (SIMULATED)"),
 "Fake screenshot to the team": ("demo", "Real hand-held phone: borrower sends a payment screenshot; agent says the bank has no match; real Approvals card", "close-up", "handheld", "outdoor daylight", "Bank-checked claims; human review", "UTR … no SETTLED match · Sent to the team", GS + " + " + REAL, "VERIFIED (SIMULATED)"),
 "It learns": ("demo", "Real 'What Kairos is learning' panel, then the Flow; product cut adds a 5-step technical montage", "wide", "crop moves", "nebula", "Learns from every reply", "Every reply teaches it something. · K03 · K09 · K06 · APIs · Intent", NEB + " + " + REAL, "VERIFIED (SIMULATED)"),
 "Under the hood": ("tech intro", "Server room, then the real System page", "wide", "locked", "server room → nebula", "Every component, end to end", "Under the hood.", FOOT + " + " + REAL, "VERIFIED"),
 "One record (K03)": ("tech", "Borrower record card fills: loans, payments, replies, consent, money in paise", "medium", "locked", "nebula", "K03 spine", "One record per borrower. · ₹4,250.00 = 425000 paise", DIAG, "VERIFIED"),
 "Churn engine (K27)": ("tech", "Five engine columns move one borrower: cohort, risk, journey, layer, lifecycle; exits win", "wide", "locked", "nebula", "K27 auto-churn", "Re-sorted, every day. · PAID_EXIT · STOPPED_OPT_OUT · 439 moves", DIAG, "VERIFIED (draft policy)"),
 "Learning model (K09)": ("tech", "Channel × time-slot reply-chance grid; three pooling levels light up", "wide", "locked", "nebula", "K09 learner", "Every channel. Every hour. · 9.8 in 100", DIAG, "VERIFIED (RL v2 caveat)"),
 "Reserve and re-check (K06)": ("tech", "Plan → reserve() budget and limit → queue → re-check at send ✓✓; real Rules KPI crop", "wide", "locked", "nebula", "Aladdin Gate", "Reserve first. Check again at send. · 1,324 passed both checks", DIAG + " + " + REAL, "VERIFIED"),
 "APIs and webhooks": ("tech", "Packets out to SMS / WhatsApp / Voice; receipts back; a duplicate bounces off", "wide", "locked", "nebula", "Idempotent webhooks", "Out through APIs. Back as webhooks. · IGNORED", DIAG, "VERIFIED (dry-run caveat)"),
 "Voice agents (Sarvam)": ("tech", "Woman on a call, then 8 languages and two dispositions with confidence vs threshold", "wide", "locked", "footage → nebula", "Dispositions + review", "6 Sarvam bots · 8 languages · 0.91 ≥ 0.80 · 0.62 < 0.70 → a person", FOOT + " + " + DIAG, "VERIFIED (example values)"),
 "Intent ladder": ("tech", "A reply drops through Rules → Small model → Sarvam-prism; an unclear one reaches a person", "wide", "locked", "nebula", "Intent ladder", "Cheapest first. A person last. · 95% · 100% recall", DIAG, "VERIFIED (SIMULATED)"),
 "CM-03 inbound WhatsApp": ("tech", "State machine NEW → … → HANDOFF; 0 details before OTP; bank-checked claims; real console", "wide", "crop moves", "nebula", "CM-03", "Its own state machine. · 127 / 127", DIAG + " + real CM-03 screen", "VERIFIED (SIMULATED)"),
 "People in control (HITL)": ("tech", "Office b-roll; then a Take-control panel: Pause, Approve, Reject, Hold, Take over; audit log fills", "wide", "locked", "office (soft)", "HITL through Aladdin + audit", "Any moment. Every action checked. · audit log", FOOT + " + HTML control panel + real Approvals crop", "VERIFIED (pause is global)"),
 "The loop, deterministic": ("tech", "Loop arc Record → Model → Aladdin → Channel → Outcome; two identical run hashes", "wide", "locked", "nebula", "Determinism", "Written back. Every time. · IDENTICAL ✓", DIAG, "VERIFIED"),
 "The team approves": ("demo", "Office; then the real Overview: recovered, chart, Needs you; a tap approves", "wide", "crop moves", "office → nebula", "People approve the few", "Approved ✓ · Aladdin re-checks", FOOT + " + " + REAL, "VERIFIED (SIMULATED)"),
 "~2× per rupee": ("payoff", "Giant ~2× over a dimmed city", "wide", "punch on hit", "night city", "~2× ₹ per ₹", "~2× · money recovered per ₹ spent · vs a fixed schedule", FOOT + " + type", "VERIFIED (SIMULATED)"),
 "0 breaches, tests passed": ("payoff", "0, then 49/49 and 148/148", "wide", "punch on hit", "night city", "Safety record", "0 · 49/49 · 148/148 policy checks", FOOT + " + type", "VERIFIED"),
 "50k in 2 min · 0 · 49/49": ("tech payoff", "50,000 counts up; 0 cap breaches; 49/49 red-team", "wide", "punch on hit", "dim server room", "Scale and safety", "50,000 · 2 minutes · 0 · 49/49", FOOT + " + type", "VERIFIED (SIMULATED)"),
 "End card": ("cta", "Smile on a call (or server room), then the wordmark and line over a dim street", "wide", "settle", "dusk street", "", "Kairos · tagline · credits", FOOT + " + type", "N/A"),
}


def dump(cut):
    url = f"http://localhost:8766/kairos_film.html?cut={cut}&dump=1"
    out = subprocess.run([CHROME, "--headless=new", "--virtual-time-budget=5000", "--dump-dom", url],
                         capture_output=True, text=True, timeout=120).stdout
    m = re.search(r'<pre id="dump">(.*?)</pre>', out, re.S)
    assert m, f"no dump for {cut}: is tools/serve.py running on :8766?"
    return json.loads(html.unescape(m.group(1)))


def main():
    for cut, name in OUT.items():
        d = dump(cut)
        tl = json.load(open(os.path.join(FILM, "audio", {"full": "film_timeline.json"}.get(cut, f"film_timeline_{cut}.json")), encoding="utf-8"))
        lines = {r["line"]: r for r in tl["lines"]}
        rows = []
        for i, s in enumerate(d["shots"]):
            p = D[s["name"]]
            vo = " / ".join(("Neha: " + lines[k]["text"]) if lines[k]["kind"] == "narrator"
                            else (("Agent (Ritu): " if lines[k]["role"] == "agent" else "Borrower (Pooja): ") + lines[k]["text"] + f" [{lines[k]['en']}]")
                            for k in s["lines"])
            hits = [f"{h[0]:g}s {h[1]}" for h in d["hits"] if s["start"] <= h[0] < s["end"] and h[1] != "soft"]
            rows.append({"shot_id": f"S{i + 1:02d}", "start_s": s["start"], "end_s": s["end"], "duration_s": round(s["end"] - s["start"], 3),
                         "purpose": p[0], "visual_action": p[1], "framing": p[2], "camera": p[3], "light_palette": p[4], "feature_proof": p[5],
                         "voiceover": vo, "on_screen_text": p[6],
                         "sound": ("score hits " + ", ".join(hits) if hits else "score bed") + "; ducked -11 dB under every voice line",
                         "transition": "hard cut on a hit" if s["hard"] else ("fade in" if i == 0 else "whip-pan 0.32 s"),
                         "method": p[7], "continuity_notes": s["name"], "claim_status": p[8]})
        with open(os.path.join(FILM, name), "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=COLS)
            w.writeheader()
            w.writerows(rows)
        print(f"wrote film/{name}: {len(rows)} shots, {d['total']:g} s")


if __name__ == "__main__":
    main()
