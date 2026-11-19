#!/usr/bin/env python3
"""Lay out the v3 product film ("Kairos in action") from one shot sequence, and write:
  films/product3.js     the compositor's film (layers)
  mix/product3.json     the soundtrack (music, Neha, characters) with caption text per line
Times come from the sequence, so picture, voice and captions stay locked together.

Grammar: real people → push into their screen → ONE real component, large and sharp on cream → back to people.
No text over UI. Headlines only on cream cards. Captions in the bottom band (the cards stay above it).
"""
import json

FPS_NOTE = "clips are 30 fps, graded and composited by tools/screen_comp (shots_product.py)"
VO = json.load(open("tools/vo_lines.json")); DLG = json.load(open("tools/dlg_lines.json"))
OUT = "localhost/demo/journey/outbound"

seq = []          # (kind, dur, params)
def clip(name, dur, **kw): seq.append(("clip", dur, dict(src="walkthrough/comp/%s.mov" % name, **kw)))
def card(cap, label, rect, dur, **kw): seq.append(("card", dur, dict(cap=cap, label=label, rect=rect, **kw)))
def other(kind, dur, **kw): seq.append((kind, dur, kw))
voice = []        # (seq index, offset in that shot, file, text, who)
def say(off, vid, who=""):
    if vid in VO: voice.append((len(seq) - 1, off, "audio/vo/%s.wav" % vid, VO[vid], ""))
    else: voice.append((len(seq) - 1, off, "audio/dlg/%s.wav" % vid, DLG[vid]["text"], DLG[vid]["who"]))
silent = []       # captions for on-screen text that nobody speaks (Vikram's typed Hindi reply)
def caption(off, dur, text, who): silent.append((len(seq) - 1, off, dur, text, who))

# ---------------------------------------------------------------- the film
clip("M1_flyover", 1.9);                       say(0.4, "p01")
clip("M2_rider", 1.2)
clip("M3_bazaar", 1.0)
clip("M4_annoyed", 1.2)
clip("M5_shopkeeper", 1.2)
clip("M6_breakfast", 1.1)
clip("M7_decline", 1.5)
clip("V1_vikram_reads", 2.4)
clip("P02_vikram_sms", 3.4)
clip("P04_asha_frustrated", 2.6)
other("hook", 3.4)                             # unit chart 1,262 → 23 on cream
other("logo", 3.0);                            say(0.2, "p02")
clip("P06_asha_typing", 1.8)
clip("P07_laptop_dashboard", 3.8, push=[1.8, 3.8, [560, 255, 780, 500]])
card("ov", "v02", [349, 335, 658, 163], 3.2)
card("ov", "v03", [349, 335, 658, 245], 3.6); say(0.2, "p03")
card("ov", "v10", [349, 335, 658, 263], 3.3)
card("ov", "v11", [349, 335, 658, 163], 2.6); say(0.25, "p04")
clip("V2_vikram_smiles", 1.9)
clip("P12_vikram_whatsapp", 3.6);              caption(1.7, 2.2, "I'll pay by the 29th.", "Vikram")
card("ov", "v17", [349, 335, 658, 127], 2.6)
clip("V3_vikram_pays", 2.0)
clip("P15_laptop_paid", 2.2, push=[0.7, 2.2, [560, 255, 780, 500]])
card("ov", "v41", [13, 219, 640, 301], 3.2)
clip("V4_vikram_relief", 1.8)
clip("P16a_asha_close", 1.5)
clip("P16b_laptop_held", 2.2, push=[1.0, 2.2, [530, 115, 885, 365]])
card("aarav", "blocked", [1044, 428, 368, 73], 1.8, maxScale=2.6); say(0.0, "p05")
card("aarav", "approved", [349, 335, 658, 95], 1.6)
card("aarav", "blocked", [349, 335, 658, 189], 3.6); say(0.4, "p06")
clip("P17a_sunita", 2.0)
clip("P17_sunita_otp", 8.8);                   say(0.4, "s_emi"); say(2.2, "k_otp"); say(7.6, "k_ver")
card("in", "emi", [414, 200, 523, 212], 4.2, maxScale=2.6); say(0.4, "p07")
clip("P19_sunita_fake", 4.6);                  say(1.3, "s_paid"); say(3.8, "k_fake")
card("in", "fake", [414, 200, 523, 235], 6.8, maxScale=2.6); say(4.5, "p08")
clip("B1_borrower_b", 1.8)
clip("P20a_borrower_b", 2.2)
clip("P20b_asha_side", 1.2)
clip("P20c_laptop_console", 3.2, push=[1.7, 3.2, [560, 255, 780, 500]])
card("in", "takeoverB", [972, 357, 448, 313], 2.4, maxScale=2.2)
clip("P20d_borrower_b_reply", 2.4)
clip("P21_sunita_stop", 4.2);                  say(0.8, "s_stop"); say(1.7, "k_stop")
card("in", "stop", [414, 200, 523, 181], 3.4, maxScale=2.6); say(1.15, "p09")
clip("P22_team", 2.8);                         say(0.6, "p10")
other("proof", 4.4)
HARD_STOP = 125.39                             # AtlasAudio bar 73 downbeat (the outro's last phrase)

# ---------------------------------------------------------------- layout
t, starts = 0.0, []
for kind, dur, p in seq: starts.append(t); t += dur
end_of_picture = t
print("picture before lockup: %.2f s (music hard stop at %.2f)" % (end_of_picture, HARD_STOP))
# stretch/squeeze the last proof card so the lockup lands on the hard stop
seq[-1] = (seq[-1][0], seq[-1][1] + (HARD_STOP - end_of_picture), seq[-1][2])
END = HARD_STOP + 4.6

layers = []
for i, (kind, dur, p) in enumerate(seq):
    t0, t1 = starts[i], starts[i] + dur
    if kind == "clip":
        L = {"kind": "video", "src": "../" + p["src"].replace("walkthrough/", "walkthrough/"), "t0": round(t0, 3), "t1": round(t1, 3), "from": 0.0, "graded": True,
             "enter": {"dur": 0.0}, "exit": {"dur": 0.0}}
        L["src"] = p["src"].replace("walkthrough/", "")
        if "push" in p: a, b, r = p["push"]; L["push"] = {"t0": round(t0 + a, 3), "t1": round(t0 + b, 3), "rect": r}
        layers.append(L)
    elif kind == "card":
        layers.append({"kind": "card", "cap": p["cap"], "frames": [{"at": 0, "label": p["label"]}], "rect": p["rect"], "t0": round(t0 - 0.05, 3), "t1": round(t1, 3),
                       "maxw": 1640, "maxh": 680, "cy": 440, "maxScale": p.get("maxScale", 2.6), "enter": {"dur": 0.4}, "exit": {"dur": 0.0}})
    elif kind == "hook":
        layers.append({"kind": "dots", "t0": t0, "t1": t1, "n": 1262, "hi": 23, "tHi": t0 + 1.6, "cols": 61, "gap": 30, "fill": 0.8, "enter": {"dur": 0.2}, "exit": {"dur": 0.0}, "bgCream": True})
        layers.append({"kind": "words", "t0": t0, "t1": t1, "y": 380, "size": 150, "words": [{"t": t0, "html": "1,262 messages."}, {"t": t0 + 1.6, "html": "<em>23</em> replies."}]})
        layers.append({"kind": "tag", "t0": t0 + 0.2, "t1": t1, "pos": "t", "text": "Fixed schedule · SIMULATED run, seed 42"})
    elif kind == "logo":
        layers.append({"kind": "logo", "t0": t0, "t1": t1, "y": 360, "stagger": 0.07, "exit": {"dur": 0.0}, "bgCream": True})
        layers.append({"kind": "headline", "t0": t0 + 0.3, "t1": t1, "pos": "c", "y": 540, "text": "Collections that decide who, when and how.", "size": 60, "exit": {"dur": 0.0}})
    elif kind == "proof":
        layers.append({"kind": "stats", "t0": t0, "t1": t1, "y": 250, "stagger": 0.12, "exit": {"dur": 0.0}, "bgCream": True, "items": [
            {"pre": "≈", "post": "×", "from": 0, "to": 2, "count": 1.0, "label": "money back per ₹ spent", "fine": "vs a fixed-schedule stand-in"},
            {"from": 0, "to": 0, "label": "rule breaches", "fine": "every message checked twice"},
            {"from": 0, "to": 49, "post": "<span style='font-size:.5em;color:#57534E'>/49</span>", "count": 1.0, "label": "attacks blocked", "fine": "on our own red-team suite"}]})
        layers.append({"kind": "fine", "t0": t0 + 0.4, "t1": t1, "x": 0, "y": 760, "html": "<div style='width:1920px;text-align:center'>SIMULATED · synthetic borrowers · 3 unseen seeds · same budget and caps</div>"})
layers.append({"kind": "logo", "t0": HARD_STOP, "t1": END, "opa0": 1, "y": 330, "line": "The right message, at the right time. Or none at all.", "lineSize": 56, "stagger": 0.08, "enterDur": 0.22,
               "credits": "Music: “Corporate Inspirational” by AtlasAudio (Pixabay licence) · Voices: Sarvam bulbul:v3 (Neha, Ritu, Pooja), AI-generated<br>Footage: Pexels · Every screen is the real Kairos software · All data SIMULATED",
               "creditsY": 820, "exit": {"dur": 0}, "bgCream": True})

film = {"duration": round(END, 3), "cues": "audio/product3_cues.json",
        "captures": {"ov": "captures/out_vikram", "aarav": "captures/out_aarav", "in": "captures/inbound2"}, "layers": layers}
open("films/product3.js", "w").write("/* generated by tools/build_product3.py — edit that, not this */\nwindow.FILM = " + json.dumps(film, ensure_ascii=False, indent=1) + ";\n")

vo = []
for si, off, f, text, who in voice:
    vo.append({"file": f, "at": round(starts[si] + off, 3), "text": text, "who": who, "gain": 0 if f.startswith("audio/vo") else -1.0})
mix = {"out": "audio/product3_mix.wav", "duration": round(END, 3), "lufs": -14, "ceiling": -1.0,
       "music": {"file": "../music/licensed/corporate_inspirational_atlasaudio.mp3", "segments": [{"src": 0.0, "len": HARD_STOP, "at": 0.0, "fadeIn": 0, "fadeOut": 0.025}], "gain": 0},
       "duck": {"db": 18, "attack": 0.12, "release": 0.5, "pre": 0.18}, "voLevel": -20, "vo": vo,
       "cuesOut": "audio/product3_cues_raw.json", "clicks": "mix/product3_clicks.json", "clickDb": -30}
json.dump(mix, open("mix/product3.json", "w"), indent=1, ensure_ascii=False)
json.dump([{"t0": round(starts[si] + off, 3), "t1": round(starts[si] + off + d, 3), "text": text, "who": who} for si, off, d, text, who in silent],
          open("audio/product3_cues_extra.json", "w"), ensure_ascii=False, indent=1)
for si, off, f, text, who in voice: print("%7.2f  %-8s %s" % (starts[si] + off, who or "Neha", text[:70]))
print("end %.2f" % END)
