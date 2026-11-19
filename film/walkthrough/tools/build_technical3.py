#!/usr/bin/env python3
"""Lay out the v3 technical film ("How Kairos works") from one shot sequence → films/technical3.js + mix/technical3.json.

Grammar: people and the real-world consequence first, then ONE real component (record, posterior, rule JSON,
system trace, call log, intent ladder, agent console) large and sharp on cream, with a small "you are here"
architecture map in the corner. The full diagram appears twice: when the parts are named, and for the final pulse.
Terminal proofs are the real recorded runs, with the result line lifted out large.
"""
import json

VO = json.load(open("tools/vo_lines.json")); DLG_OLD = json.load(open("../audio/dialogue/lines.json"))
seq, voice, minimap = [], [], []
def clip(name, dur, **kw): seq.append(("clip", dur, dict(src="comp/%s.mov" % name, **kw)))
def card(cap, label, rect, dur, node=None, **kw): seq.append(("card", dur, dict(cap=cap, label=label, rect=rect, node=node, **kw)))
def other(kind, dur, **kw): seq.append((kind, dur, kw))
def say(off, vid):
    voice.append((len(seq) - 1, off, "audio/vo/%s.wav" % vid, VO[vid], ""))
def call(off, vid, who):
    d = DLG_OLD[vid]; voice.append((len(seq) - 1, off, "../audio/dialogue/%s.wav" % vid, d["en"], who))

JSON_BLOCK = [358, 372, 640, 212]
clip("T01_engineer", 3.4);                          say(0.5, "t01")
other("headline", 3.0, text="How Kairos works.", sub="One decision per borrower, per day. Every step checked.")
other("diagram_build", 5.4)
clip("T03_street", 2.6);                            say(0.3, "t02")
card("ov", "d1_churn_tech", [13, 296, 312, 317], 5.6, node="rec")
clip("T04_vikram_phone", 2.4);                      say(0.3, "t03")
card("ov", "d1_learner_tech", [349, 379, 658, 223], 4.4, node="learn")
card("ov", "d1_learner_tech_scrolled", JSON_BLOCK, 3.2, node="learn")
clip("T05_asha", 2.0);                              say(0.2, "t04")
clip("P16b_laptop_held", 2.4, push=[1.0, 2.4, [530, 115, 885, 365]])
card("aarav", "blocked", [349, 333, 658, 187], 4.0, node="ald")
card("ov", "d1_aladdin_tech_scrolled", JSON_BLOCK, 3.2, node="ald")
clip("P03_phone_lights", 1.8);                      say(0.2, "t05")
card("sys", "trace", [410, 278, 322, 296], 6.4, node="hook", play=[5, 10])
clip("T07_sunita_call", 5.2);                       call(0.3, "vc_a2", "Kairos"); call(4.4, "vc_b2", "Sunita")
card("dv", "voice", [700, 715, 700, 150], 5.6, node="chan", maxScale=2.4); say(0.3, "tv_voice")
clip("T07b_person_reviews", 3.6)
clip("P12_vikram_whatsapp", 2.4, start=3.4);        say(0.3, "tv_intent")
card("ov", "d3_intent_tech", [349, 384, 658, 218], 4.4, node="intent")
clip("T09_asha", 2.0);                              say(0.2, "t07")
clip("P20c_laptop_console", 3.0, push=[1.4, 3.0, [560, 255, 780, 500]])
card("in", "takeoverB", [972, 357, 448, 313], 3.8, node="people", maxScale=2.2)
clip("T10_engineer", 2.6);                          say(0.4, "tv_proof")
other("terminal", 16.5)
other("research", 5.4)
other("pulse", 3.4)
HARD_STOP = 108.02                                   # The_Mountain, bar 36 downbeat

t, starts = 0.0, []
for kind, dur, p in seq: starts.append(t); t += dur
print("picture before lockup: %.2f s (hard stop %.2f)" % (t, HARD_STOP))
seq[-1] = (seq[-1][0], seq[-1][1] + (HARD_STOP - t), seq[-1][2])
END = HARD_STOP + 4.6

C = {"data": "#2563EB", "learn": "#0F766E", "rules": "#C2410C", "act": "#7C3AED", "understand": "#BE185D", "people": "#15803D"}
X = [60, 540, 1020, 1500, 1980]; ROWS = [40, 400, 780]
def node(i, col, row, title, sub, color, cap=None): return {"id": i, "x": X[col], "y": ROWS[row], "w": 420, "h": 180, "title": title, "sub": sub, "color": color, "t": 0, "cap": cap}
NODES = [node("rec", 0, 1, "K03 · Record", "one per borrower", C["data"]), node("churn", 1, 1, "K27 · Churn", "re-sorts daily", C["learn"]),
         node("learn", 2, 1, "K09 · Learner", "channel × hour", C["learn"]), node("ald", 3, 1, "K06 · Aladdin", "checks twice", C["rules"], "≈ safety shield · Alshiekh et al. 2018"),
         node("exec", 4, 1, "Executor", "sends on time", C["act"]), node("chan", 4, 2, "Channels", "SMS · WA · voice", C["act"]),
         node("hook", 3, 2, "Webhooks", "receipts back", C["act"]), node("intent", 2, 2, "Intent ladder", "rules → model", C["understand"], "≈ reject option · Chow 1970"),
         node("people", 3, 0, "People", "can step in", C["people"]), node("out", 1, 2, "Outcomes", "what happened", C["data"])]
EDGES = [("e1", "rec", "churn"), ("e2", "churn", "learn"), ("e3", "learn", "ald"), ("e4", "ald", "exec"), ("e5", "exec", "chan"), ("e6", "chan", "hook"),
         ("e7", "hook", "intent"), ("e8", "people", "ald"), ("e9", "intent", "out"), ("e10", "out", "learn")]
LOOP = ["e1", "e2", "e3", "e4", "e5", "e6", "e7", "e9", "e10"]
def diagram(t0, t1, appear_from=None, pulse=None, mini=None, focus=None):
    order = ["rec", "churn", "learn", "ald", "exec", "chan", "hook", "intent", "people", "out"]
    nodes = []
    for k, n in enumerate(NODES):
        n = dict(n); n["t"] = (appear_from + order.index(n["id"]) * 0.38) if appear_from is not None else t0 - 1; nodes.append(n)
    edges = [{"id": e, "from": a, "to": b, "t": max(nn["t"] for nn in nodes if nn["id"] in (a, b)) + 0.1} for e, a, b in EDGES]
    L = {"kind": "diagram", "t0": t0, "t1": t1, "ww": 2460, "wh": 1010, "nodes": nodes, "edges": edges, "enter": {"dur": 0.4}, "exit": {"dur": 0.0},
         "cam": [{"t": 0, "full": True, "z": 0.74}], "focus": focus or [{"t": 0, "nodes": ["*"]}], "bgCream": True}
    if pulse: L["pulses"] = [{"t": pulse, "path": LOOP, "speed": 1500, "dwell": 0.03}]
    if mini: L["mini"] = mini; L["enter"] = {"dur": 0.3}; L["bgCream"] = False
    return L

layers, mini_focus, mini_spans = [], [], []
for i, (kind, dur, p) in enumerate(seq):
    t0, t1 = starts[i], starts[i] + dur
    if kind == "clip":
        L = {"kind": "video", "src": p["src"], "t0": round(t0, 3), "t1": round(t1, 3), "from": p.get("start", 0.0), "graded": True, "enter": {"dur": 0}, "exit": {"dur": 0}}
        if "push" in p: a, b, r = p["push"]; L["push"] = {"t0": round(t0 + a, 3), "t1": round(t0 + b, 3), "rect": r}
        layers.append(L)
    elif kind == "card":
        fr = [{"at": round(t0, 3), "label": p["label"], "play": p["play"][0], "from": p["play"][1]}] if "play" in p else [{"at": 0, "label": p["label"]}]
        layers.append({"kind": "card", "cap": p["cap"], "frames": fr, "rect": p["rect"], "t0": round(t0 - 0.05, 3), "t1": round(t1, 3), "maxw": 1500, "maxh": 640,
                       "cx": 900, "cy": 470, "maxScale": p.get("maxScale", 2.6), "enter": {"dur": 0.4}, "exit": {"dur": 0}})
        if p.get("node"): mini_focus.append({"t": round(t0, 3), "nodes": [p["node"]]}); mini_spans.append((t0, t1))
    elif kind == "headline":
        layers.append({"kind": "headline", "t0": t0, "t1": t1, "pos": "c", "text": p["text"], "sub": p["sub"], "size": 108, "stagger": 0.15, "exit": {"dur": 0}, "bgCream": True})
    elif kind == "diagram_build":
        layers.append(diagram(t0, t1, appear_from=t0 + 0.4))
    elif kind == "pulse":
        layers.append(diagram(t0, t1, pulse=t0 + 0.4))
    elif kind == "research":
        layers.append({"kind": "callout", "t0": t0, "t1": t1, "pos": "c", "bare": True, "bgCream": True, "text": "Built on research.", "size": 88, "stagger": 0.05, "sub": " ",
                       "subHtml": '<div style="display:inline-grid;grid-template-columns:auto auto;gap:14px 80px;margin-top:34px;font-size:40px;line-height:1.3;color:#57534E;text-align:left">'
                       "<div>Thompson · <i>Biometrika</i> 1933</div><div>Chapelle &amp; Li · NeurIPS 2011</div><div>Li et al. · WWW 2010</div><div>Badanidiyuru, Kleinberg &amp; Slivkins · FOCS 2013</div>"
                       "<div>Clayton &amp; Kaldor · <i>Biometrics</i> 1987</div><div>Abe et al. · KDD 2010</div><div>Yancey &amp; Settles · KDD 2020</div><div>Alshiekh et al. · AAAI 2018</div>"
                       "<div>Angelopoulos et al. · Learn then Test</div><div>Dudík, Langford &amp; Li · ICML 2011</div></div>", "exit": {"dur": 0}})
    elif kind == "terminal":
        a = t0
        layers.append({"kind": "term", "t0": a, "t1": t1, "title": "kairos/backend — zsh", "prompt": "%", "rows": 15, "maxCols": 80, "home": [160, 0, 1600],
                       "enter": {"type": "fade", "dur": 0.3}, "exit": {"type": "fade", "dur": 0.2}, "cam": [{"t": 0, "full": True}],
                       "runs": [
            {"rec": "determinism", "at": a + 0.3, "cadence": 0.004, "fit": 1.0, "keep": "^Matched|Verified payment \\(unique\\)|K01 cap breaches|K15 safety|Deterministic replay", "hi": "IDENTICAL", "badge": "638 s run · shown 600× faster"},
            {"rec": "redteam", "at": a + 3.8, "cadence": 0.012, "fit": 1.0, "clear": True, "keep": "PASS|FAIL|attacks:", "hi": "49 PASS", "badge": "3.7 s run"},
            {"rec": "bench", "at": a + 7.4, "cadence": 0.012, "fit": 0.5, "clear": True, "hi": "atomic decisions|cap breaches: 0", "badge": "ledger benchmark · 15 s run, 30× faster"},
            {"rec": "pytest", "at": a + 10.6, "cadence": 0.05, "fit": 0.7, "clear": True, "hi": "passed", "badge": "74 s run · shown 90× faster"},
            {"rec": "ope", "at": a + 13.2, "cadence": 0.012, "fit": 0.4, "clear": True, "keep": "^OPE|^replies per 1,000 decisions|^CN10.*18\\.4 +18\\.2", "hi": "^CN10", "badge": "SIMULATED · 20 s run, 50× faster", "prompt": "kairos/backend (research) %"}]})
        for o, d, k, h in [(1.6, 2.0, "Same seed, run twice", "Deterministic replay: <em>IDENTICAL</em>"), (5.2, 2.0, "Our own red-team suite", "49 attacks: <em>49 PASS</em>, 0 FAIL"),
                           (8.3, 2.1, "Ledger benchmark", "<em>50,000</em> atomic decisions in 11.1 s<br>cap breaches: <em>0</em>"), (11.6, 1.5, "Full test suite", "<em>3030 passed</em> in 74 s"),
                           (14.1, 2.3, "Off-policy evaluation · CN10 fixed schedule · SIMULATED", "From logs: <em>18.2</em>. Truth: <em>18.4</em>.")]:
            layers.append({"kind": "callout", "t0": a + o, "t1": a + o + d, "dark": True, "x": 260, "y": 560, "maxw": 1500, "kicker": k, "mono": True, "html": h})
# the "you are here" map, top-right, over every component card
if mini_spans:
    m0, m1 = mini_spans[0][0], mini_spans[-1][1]
    layers.append(dict(diagram(m0, m1, mini={"x": 1920 - 40 - 2460 * 0.13, "y": 54, "s": 0.13}, focus=mini_focus), z=900, away=[[a, b] for a, b in
                  zip([s[1] for s in mini_spans[:-1]], [s[0] for s in mini_spans[1:]])]))
layers.append({"kind": "logo", "t0": HARD_STOP, "t1": END, "opa0": 1, "y": 330, "line": "Built to be trusted with every rupee.", "lineSize": 60, "stagger": 0.08, "enterDur": 0.22,
               "credits": "Music: “Inspiring Technology” by The_Mountain (Pixabay licence) · Voices: Sarvam bulbul:v3 (Neha, Ritu, Pooja), AI-generated<br>Footage: Pexels · Every screen and terminal run is the real Kairos software · All data SIMULATED",
               "creditsY": 820, "exit": {"dur": 0}, "bgCream": True})
film = {"duration": round(END, 3), "cues": "audio/technical3_cues.json", "bg": "#FBF8F1",
        "captures": {"ov": "captures/out_vikram", "aarav": "captures/out_aarav", "in": "captures/inbound2", "sys": "captures/live_system", "dv": "captures/deployed_voice"},
        "terms": {"redteam": "term/redteam.json", "bench": "term/bench.json", "determinism": "term/determinism.json", "pytest": "term/pytest.json", "ope": "term/ope.json"},
        "layers": layers}
open("films/technical3.js", "w").write("/* generated by tools/build_technical3.py — edit that, not this */\nwindow.FILM = " + json.dumps(film, ensure_ascii=False, indent=1) + ";\n")
vo = [{"file": f, "at": round(starts[si] + off, 3), "text": text, "who": who, "gain": 0 if f.startswith("audio/vo") else -1.0} for si, off, f, text, who in voice]
mix = {"out": "audio/technical3_mix.wav", "duration": round(END, 3), "lufs": -14, "ceiling": -1.0,
       "music": {"file": "../music/licensed/inspiring_technology_the_mountain.mp3", "segments": [{"src": 0.0, "len": HARD_STOP, "at": 0.0, "fadeIn": 0, "fadeOut": 0.025}], "gain": 0},
       "duck": {"db": 18, "attack": 0.12, "release": 0.5, "pre": 0.18}, "voLevel": -20, "vo": vo, "cuesOut": "audio/technical3_cues_raw.json"}
json.dump(mix, open("mix/technical3.json", "w"), indent=1, ensure_ascii=False)
for si, off, f, text, who in voice: print("%7.2f  %-7s %s" % (starts[si] + off, who or "Neha", text[:70]))
print("end %.2f" % END)
