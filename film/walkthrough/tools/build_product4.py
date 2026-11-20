#!/usr/bin/env python3
"""Lay out the v4 product film ("Kairos in action") → films/product4.js + mix/product4.json.

Grammar (v4): real people as b-roll (no composites), every platform shot a COMPLETE real page in a glass window on
slow nebula footage with an eased camera inside the window, the real chats as still phone frames, Aladdin as an
animated loop that returns in a corner at every act. Captions are transparent white text. Timing comes from the
measured voice files, so a different TTS pace re-times the edit by itself.
"""
import json, os, subprocess, sys

V = json.load(open("tools/lines_v4.json"))
CAPS = {"ov": "captures/out_vikram", "aa": "captures/out_aarav", "in": "captures/inbound_tall", "er": "captures/engine_room", "dv": "captures/deployed_voice"}
if not os.environ.get("OLDCAPS"):                          # release-candidate recaptures (port 8120): no vendor names; labels verified by content
    CAPS.update({k: v.replace("captures/", "captures_rc/") for k, v in CAPS.items() if os.path.exists(v.replace("captures/", "captures_rc/") + "/log.json") and k != "dv"})
_dur = {}
def d(vid):
    if vid not in _dur:
        out = subprocess.run(["afinfo", "audio/v4/%s.wav" % vid], capture_output=True, text=True).stdout
        _dur[vid] = float(out.split("estimated duration:")[1].split()[0])
    return _dur[vid]
OVL = {"@SMS": "v01", "@ALD": "v02", "@WAIT": "v08", "@WA": "v10", "@REPLY": "v16", "@PTP": "v21", "@QUIET": "v23", "@PAID": "v41"} if not os.environ.get("OLDCAPS") else \
      {"@SMS": "v02", "@ALD": "v03", "@WAIT": "v10", "@WA": "v11", "@REPLY": "v17", "@PTP": "v17", "@QUIET": "v17", "@PAID": "v41"}
def lab(cap, name):
    name = OVL.get(name, name)
    lg = json.load(open(CAPS[cap] + "/log.json"))
    for f in lg["frames"]:
        if f["label"] == name: return name
    raise SystemExit("no frame %s in %s" % (name, cap))

# ---------------------------------------------------------------- beats
# A beat = shots (kind, min dur, params) + voice lines placed one after another (gap 0.35 s) from `at`.
# The beat lasts max(sum of shot mins, end of its voice + tail); the LAST shot absorbs the slack.
beats = []
def beat(shots, voice=(), at=0.3, tail=0.35, gap=0.3, name=""):
    beats.append(dict(shots=shots, voice=list(voice), at=at, tail=tail, gap=gap, name=name))
def clip(name, dur, **kw): return ("clip", dur, dict(src="comp/%s.mov" % name, **kw))
def glass(cap, label, dur, **kw): return ("glass", dur, dict(cap=cap, label=label, **kw))
def phone(imgs, dur, **kw): return ("phone", dur, dict(imgs=imgs, **kw))
def other(kind, dur, **kw): return (kind, dur, kw)
PH = lambda s: "captures/phone_%s/f00001.jpg" % s
IN_PHONE = [10, 126, 372, 778]                  # the inbound page's own phone (whole), CSS px

# 0 · cold open (silence), then the music hit and the logo
beat([other("title", 7.0, bg="#000", y=540, maxw=1240, align="left", lines=[
        {"text": "DISCLAIMER: This product may make your collections team's life noticeably easier. Side effects include fewer angry customers and more money recovered. Use at your own risk.", "size": 40, "cls": "legal", "dur": 0.5, "t": -0.4}])],
     name="cold")
beat([other("brand", 6.2, lines=["Collections that decide who, when and how."], neb=("comp/NEB1.mov", 1.0))], name="logo")
# 1 · the problem: many different people, one identical reminder
beat([clip("X_tea", 1.4), clip("X_fruit", 1.4), clip("X_farmer", 1.4), clip("X_student", 1.4), clip("X_gig", 1.4),
      clip("X_family", 1.4), clip("X_elder", 1.4), clip("X_metro", 1.6)], voice=["p01"], at=0.4, name="problem")
beat([phone([{"t": 0, "src": PH("sms3")}], 3.6, neb=("comp/NEB2.mov", 3.0)),
      other("dots", 4.0, neb=("comp/NEB2.mov", 5.0))], voice=["p02", "p03"], at=0.3, tail=0.8, name="hook")
# 2 · Part 1 · Reaching out (CM-02)
beat([other("title", 2.4, lines=[{"text": "Part 1", "size": 40, "cls": "kicker"}, {"text": "Reaching out", "size": 110, "mt": 10}], neb=("comp/NEB1.mov", 2.0))], name="part1")
beat([clip("X_vikas", 2.4),
      glass("dv", "control", 5.0, title="Kairos · Control Room", cam=[(2.0, [300, 120, 1140, 420], 1.8)], neb=("comp/NEB1.mov", 4.0)),
      glass("ov", "@SMS", 5.0, title="Kairos · Live journey", cam=[(1.0, [340, 320, 680, 200], 1.6)], neb=("comp/NEB1.mov", 7.0))],
     voice=["p04"], at=0.4, name="looks")
beat([glass("ov", "@SMS", 6.0, title="Kairos · Live journey", cam=[(0.0, [340, 320, 680, 200], 0.01), (2.6, [0, 100, 1440, 130], 1.6)], neb=("comp/NEB1.mov", 10.0), enter={"type": "fade", "dur": 0.3}),
      glass("dv", "learning", 7.0, title="Kairos · Learning & Proof", maxh=730, cy=500, tag="Within a fixed budget · daily report per group", neb=("comp/NEB2.mov", 8.0))],
     voice=["p05"], at=0.2, name="choice")
# 3 · Meet Aladdin
beat([other("title", 3.0, lines=[{"text": "ALADDIN", "size": 150, "track": 0.18, "dur": 0.5, "blur": True},
                                 {"text": "the safety supervisor", "size": 46, "cls": "soft", "mt": 6},
                                 {"text": "No message leaves without passing its checks.", "size": 54, "mt": 34}], stagger=0.35, neb=("comp/NEB1.mov", 6.0))], name="flash")
beat([other("loop", 24.0, neb=("comp/NEB2.mov", 0.5))], voice=["p07", "p08", "p09"], at=0.4, tail=1.2, name="aladdin")
# 4 · Vikram (CASTING.md: the Delhi man, H2_4 / H7_6)
beat([clip("X_vikram_a", 2.6),
      glass("ov", "@SMS", 4.0, title="Kairos · Live journey · Vikram", cam=[(0.6, [340, 320, 680, 200], 1.4)], neb=("comp/NEB1.mov", 12.0)),
      glass("ov", "@ALD", 4.0, title="Kairos · Live journey · Vikram", cam=[(0.0, [340, 320, 680, 260], 0.01)], neb=("comp/NEB1.mov", 14.0), mini=["aladdin"], enter={"type": "fade", "dur": 0.3}),
      clip("X_vikram_b", 2.0)], voice=["p10"], at=0.4, name="vikram1")
beat([glass("ov", "@WAIT", 4.0, title="Kairos · Live journey · Vikram", cam=[(0.4, [340, 320, 680, 280], 1.4)], neb=("comp/NEB2.mov", 2.0)),
      glass("ov", "@WA", 3.4, title="Kairos · Live journey · Vikram", cam=[(0.0, [340, 320, 680, 180], 0.01)], neb=("comp/NEB2.mov", 5.0), enter={"type": "fade", "dur": 0.3}),
      phone([{"t": 0, "src": PH("wa1")}, {"t": 1.0, "src": PH("wa1t")}, {"t": 1.9, "src": PH("wa2")}], 4.2, neb=("comp/NEB2.mov", 8.0), cc=("I'll pay by the 29th.", "Vikram", 2.0, 2.2)),
      clip("X_vikram_c", 2.0)], voice=["p11"], at=0.3, name="vikram2")
beat([glass("ov", "@PTP", 3.2, title="Kairos · Live journey · Vikram", cam=[(0.4, [340, 320, 680, 160], 1.2)], neb=("comp/NEB1.mov", 2.0)),
      glass("ov", "@QUIET", 3.0, title="Kairos · Live journey · Vikram", cam=[(0.0, [340, 320, 680, 160], 0.01)], neb=("comp/NEB1.mov", 4.0), enter={"type": "fade", "dur": 0.3}),
      glass("ov", "@PAID", 5.6, title="Kairos · Live journey · Vikram", cam=[(0.6, [0, 200, 1100, 330], 1.6)], neb=("comp/NEB1.mov", 6.0)),
      clip("X_vikram_d", 2.0)], voice=["p12"], at=0.3, name="vikram3")
# 5 · it keeps learning
beat([glass("er", "d7", 6.0, title="Kairos · Engine Room · SIMULATED", cam=[(0.8, [640, 190, 620, 260], 1.6)], neb=("comp/NEB2.mov", 15.0)),
      glass("er", "d14", 6.0, title="Kairos · Engine Room · SIMULATED", cam=[(0.0, [640, 190, 620, 260], 0.01)], neb=("comp/NEB2.mov", 20.0), enter={"type": "fade", "dur": 0.4})],
     voice=["p13"], at=0.4, name="learning")
# 6 · Part 2 · When borrowers write in (CM-03)
beat([other("title", 2.4, lines=[{"text": "Part 2", "size": 40, "cls": "kicker"}, {"text": "When borrowers write in", "size": 110, "mt": 10}], neb=("comp/NEB1.mov", 9.0))], name="part2")
beat([clip("P17a_sunita", 2.4),
      glass("in", "tabA", 4.0, title="Kairos · Inbound WhatsApp", cam=[(1.2, IN_PHONE, 1.6)], neb=("comp/NEB1.mov", 12.0))], voice=["p15"], at=0.3, tail=0.2, name="writein")
beat([glass("in", "emi", 9.5, title="Kairos · Inbound WhatsApp", frames=[(0, "tabA"), (1.9, "emi"), (7.4, "otp")], cam=[(0.0, IN_PHONE, 0.01)], neb=("comp/NEB1.mov", 14.0), enter={"type": "fade", "dur": 0.2})],
     voice=["d1", "d2", "d3"], at=0.2, gap=0.45, tail=0.4, name="otp")
beat([glass("in", "otp", 4.2, title="Kairos · Inbound WhatsApp", cam=[(0.0, IN_PHONE, 0.01), (0.6, [380, 60, 560, 360], 1.4)], neb=("comp/NEB1.mov", 18.0))], voice=["p16"], at=0.3, name="identity")
# [slot] real WhatsApp on a real phone: a 10-15 s still/recording goes here when it lands (REAL_WA env var)
if os.environ.get("REAL_WA"):
    beat([("glass", 12.0, dict(video=os.environ["REAL_WA"], vw=1920, vh=1080, title="Real WhatsApp · Meta Cloud API test number", neb=("comp/NEB2.mov", 1.0)))], name="realwa")
beat([glass("in", "paylink", 7.6, title="Kairos · Inbound WhatsApp", frames=[(0, "paylink"), (1.2, "fake")], cam=[(0.0, IN_PHONE, 0.01)], neb=("comp/NEB2.mov", 1.0))],
     voice=["d4", "d5"], at=0.3, gap=0.45, tail=0.3, name="fake")
beat([glass("in", "fake", 7.0, title="Kairos · Inbound WhatsApp", cam=[(0.0, IN_PHONE, 0.01), (0.8, [380, 60, 560, 380], 1.6)], neb=("comp/NEB2.mov", 5.0))], voice=["p17"], at=0.3, name="fakecheck")
beat([clip("B1_borrower_b", 2.0), clip("X_callcentre", 2.0),
      glass("in", "takeoverB", 4.0, title="Kairos · Agent console", cam=[(1.0, [930, 40, 500, 560], 1.6)], neb=("comp/NEB2.mov", 9.0))], voice=["p18"], at=0.3, name="hardship")
beat([glass("in", "tabA2", 6.0, title="Kairos · Inbound WhatsApp", frames=[(0, "tabA2"), (1.4, "stop")], cam=[(0.0, IN_PHONE, 0.01)], neb=("comp/NEB1.mov", 2.0))],
     voice=["d6", "d7"], at=0.3, gap=0.5, tail=0.3, name="stop")
beat([glass("in", "stop", 6.0, title="Kairos · Inbound WhatsApp", cam=[(0.0, IN_PHONE, 0.01), (0.8, [380, 60, 560, 300], 1.6)], neb=("comp/NEB1.mov", 7.0), mini=["block"])], voice=["p19"], at=0.3, name="stopall")
# 7 · the team stays in charge (the real deployed dashboard, recorded at 60 fps)
F02, F03 = "rec/F02_approvals_supervisor_clean.mp4", "rec/F03_crm_explain_manual_preview_clean.mp4"
beat([clip("P06_asha_typing", 2.0),
      ("glass", 4.4, dict(video=F02, frm=36.0, rate=1.6, vw=1920, vh=1080, title="Kairos · Control Room · approvals", cam=[(0.6, [380, 90, 1100, 230], 1.4)], neb=("comp/NEB2.mov", 12.0))),
      ("glass", 5.0, dict(video=F03, frm=50.0, rate=1.2, vw=1920, vh=1080, title="Kairos · Smart CRM · manual send", cam=[(0.6, [1440, 540, 470, 520], 1.4)], neb=("comp/NEB2.mov", 16.0), mini=["aladdin", "block"]))],
     voice=["p20"], at=0.3, name="team")
# 8 · results and ending
beat([clip("X_rajesh", 2.4),
      other("panel", 10.0, heading="In our simulated tests", items=[
          {"big": "More money recovered per rupee spent", "small": "vs a fixed reminder schedule"},
          {"html": "<em>0</em> rule breaches"},
          {"html": "<em>73%</em> of WhatsApp chats resolved without a person"},
          {"html": "Every fake payment proof caught"}],
          fine="SIMULATED · synthetic borrowers · 3 unseen seeds · 1,000 seeded test conversations", neb=("comp/NEB1.mov", 2.0))],
     voice=["p22"], at=0.4, tail=1.6, name="results")
beat([other("title", 3.2, lines=[{"text": "The right message, at the right time.", "size": 84}, {"text": "Or none at all.", "size": 84, "t_off": 0.9}], neb=("comp/NEB2.mov", 22.0)),
      other("title", 3.0, lines=[{"text": "Collections that decide", "size": 84}, {"text": "who, when and how.", "size": 84, "t_off": 0.6}], neb=("comp/NEB2.mov", 24.0)),
      other("title", 3.0, lines=[{"text": "Built to be trusted", "size": 84}, {"text": "with every rupee.", "size": 84, "t_off": 0.6}], neb=("comp/NEB2.mov", 26.0))], name="slogans")
beat([other("brand", 5.0, end=True, neb=("comp/NEB1.mov", 12.0),
            credits="Music: “Corporate Inspirational”, AtlasAudio (Pixabay licence) · Voices: AI-generated · Footage: Pexels<br>Every screen is the real Kairos software · All data SIMULATED"),
      other("title", 2.6, bg="#000", lines=[{"text": "Results may vary. Rules won't.", "size": 46, "cls": "soft"}])], name="end")

# ---------------------------------------------------------------- lay out
t, seq, voice, cc_extra = 0.0, [], [], []
for b in beats:
    vt, vends = t + b["at"], []
    for vid in b["voice"]:
        voice.append((vt, vid)); vends.append(vt + d(vid)); vt += d(vid) + b["gap"]
    need = (max(vends) - t + b["tail"]) if vends else 0
    shots = [list(s) for s in b["shots"]]
    have = sum(s[1] for s in shots)
    if need > have: shots[-1][1] += need - have
    for s in shots:
        seq.append((s[0], round(s[1], 3), s[2], round(t, 3), b["name"])); t += s[1]
END = t
print("film %.2f s (%d:%02d)" % (END, END // 60, END % 60))

# ---------------------------------------------------------------- layers
layers, mini_lit = [], []
LOOP_NODES = [  # the Aladdin loop: Decide → checks → Send / Hold back → Delivered → Reply → Learn → back to Decide (all above the caption band)
    ("decide", 70, 300, 360, 170, "Decide", ["who · channel · hour"], "teal"),
    ("aladdin", 560, 110, 700, 440, "Aladdin's checks", ["consent", "stop requests", "contact hours 08:00 to 19:00", "weekly limit", "budget left", "account holds"], "amber"),
    ("send", 1440, 150, 400, 130, "Send", [], "green"),
    ("block", 1440, 400, 400, 130, "Hold back", [], "red"),
    ("deliv", 1440, 680, 400, 130, "Delivered", [], "slate"),
    ("reply", 990, 680, 330, 130, "Reply", [], "slate"),
    ("learn", 560, 680, 330, 130, "Learn", [], "violet"),
]
LOOP_EDGES = [("decide", "aladdin", None, None, 0, 0), ("aladdin", "send", "all yes", None, 0, -6), ("aladdin", "block", "any no", None, 0, 52),
              ("send", "deliv", None, [[1880, 215], [1880, 745]], 0, 0), ("deliv", "reply", None, None, 0, 0), ("reply", "learn", None, None, 0, 0),
              ("learn", "decide", "tomorrow", [[250, 745]], -70, 0)]
def loop_layer(t0, t1, built=True, mini=None, lit=None, voice_t=None):
    q = voice_t or {}
    order = ["decide", "aladdin", "send", "block", "deliv", "reply", "learn"]
    nodes = []
    for (i, x, y, w, h, title, body, tone) in LOOP_NODES:
        n = {"id": i, "x": x, "y": y, "w": w, "h": h, "title": title, "body": body, "tone": tone}
        n["t"] = (t0 - 1) if built is True else (built + order.index(i) * 0.45 if i in ("decide", "aladdin") else q.get("rest", built + 8) + order.index(i) * 0.4)
        if i == "aladdin" and q.get("checks"): n["bodyT"] = q["checks"]
        nodes.append(n)
    nt = {n["id"]: n["t"] for n in nodes}
    edges = [{"from": a, "to": b, "label": lbl, "pts": pts, "lx": lx, "ly": ly, "t": max(nt[a], nt[b]) + 0.2} for a, b, lbl, pts, lx, ly in LOOP_EDGES]
    L = {"kind": "flow", "t0": round(t0, 3), "t1": round(t1, 3), "nodes": nodes, "edges": edges, "enter": {"dur": 0.4 if not mini else 0.3}, "exit": {"dur": 0.3}}
    if mini: L["mini"] = {"x": 1920 - 34 - 1920 * 0.15, "y": 74, "s": 0.15, "h": 860}; L["z"] = 950
    if lit: L["lit"] = lit
    if q.get("pulse"): L["pulses"] = [{"t": q["pulse"], "path": ["decide", "aladdin", "send", "deliv", "reply", "learn", "decide"], "dur": 0.8}]
    return L

vstart = {vid: vt for vt, vid in voice}
neb_spans = []
for kind, dur, p, t0, bname in seq:
    t1 = t0 + dur
    if "neb" in p:
        src, frm = p["neb"]; neb_spans.append((t0, t1, src, frm))
    if kind == "clip":
        layers.append({"kind": "video", "src": p["src"], "t0": t0, "t1": round(t1, 3), "from": 0.0, "graded": True, "enter": {"dur": 0}, "exit": {"dur": 0}})
    elif kind == "glass":
        if p.get("video"): p = dict(p, cap=None, label=None, frames=None)
        fr = [] if p.get("video") else [{"at": 0, "label": lab(p["cap"], p["label"])}] if not p.get("frames") else [{"at": round(t0 + a, 3), "label": lab(p["cap"], l)} for a, l in p["frames"]]
        if p.get("frames"): fr[0]["at"] = 0
        L = {"kind": "glass", "t0": t0, "t1": round(t1, 3), "cap": p["cap"], "frames": fr, "title": p.get("title"),
             "cam": [{"t": round(t0 + a, 3), "rect": r, "dur": dd} for a, r, dd in p.get("cam", [])],
             "enter": p.get("enter", {"type": "rise", "dur": 0.6}), "exit": {"type": "fade", "dur": 0.25}}
        if p.get("cursor"): L["cursor"] = [dict(k, t=round(t0 + k["t"], 3)) for k in p["cursor"]]
        if p.get("video"):
            L.pop("cap"); L.pop("frames"); L.update({"video": p["video"], "from": p.get("frm", 0.0), "rate": p.get("rate", 1.0), "vw": p.get("vw", 1920), "vh": p.get("vh", 1080)})
        for k in ("maxh", "cy", "maxw"):
            if k in p: L[k] = p[k]
        if p.get("mini"): L["maxw"] = 1380; L["cx"] = 800
        layers.append(L)
        if p.get("tag"): layers.append({"kind": "title", "t0": round(t0 + 0.3, 3), "t1": round(t1, 3), "y": 64, "lines": [{"text": p["tag"], "size": 34, "cls": "soft"}], "enter": {"dur": 0}, "exit": {"dur": 0.25}})
        if p.get("mini"): mini_lit.append((t0, t1, p["mini"]))
    elif kind == "phone":
        layers.append({"kind": "phone", "t0": t0, "t1": round(t1, 3), "imgs": [{"t": round(t0 + i["t"], 3), "src": i["src"]} for i in p["imgs"]], "h": 860, "enter": {"dur": 0.5}, "exit": {"dur": 0.25}})
        if p.get("cc"): text, who, a, dd = p["cc"]; cc_extra.append({"t0": round(t0 + a, 3), "t1": round(t0 + a + dd, 3), "text": text, "who": who})
    elif kind == "title":
        lines = []
        for ln in p["lines"]:
            ln = dict(ln)
            if "t_off" in ln: ln["t"] = round(t0 + ln.pop("t_off"), 3)
            lines.append(ln)
        layers.append({"kind": "title", "t0": t0, "t1": round(t1, 3), "lines": lines, "bg": p.get("bg"), "y": p.get("y", 540), "maxw": p.get("maxw"), "align": p.get("align"),
                       "stagger": p.get("stagger", 0.2), "enter": {"dur": 0.0}, "exit": {"dur": 0.35 if not p.get("bg") else 0.5}})
    elif kind == "brand":
        L = {"kind": "brand", "t0": t0, "t1": round(t1, 3), "y": 470 if p.get("credits") else 520, "lines": p.get("lines", []) if not p.get("end") else [], "enter": {"dur": 0.0}, "exit": {"dur": 0.35}}
        if p.get("credits"): L["credits"] = p["credits"]; L["from"] = 0.97
        layers.append(L)
    elif kind == "dots":
        layers.append({"kind": "dots", "theme": "dark", "t0": t0, "t1": round(t1, 3), "n": 1262, "hi": 23, "tHi": round(t0 + 1.4, 3), "cols": 61, "gap": 30, "fill": 0.6, "enter": {"dur": 0}, "exit": {"dur": 0.3}})
        layers.append({"kind": "title", "t0": t0, "t1": round(t1, 3), "y": 470, "lines": [{"text": "1,262 messages", "size": 120, "t": t0}, {"html": "<span style='color:#7DF3E1'>23 replies</span>", "size": 120, "t": round(t0 + 1.4, 3)}], "enter": {"dur": 0}, "exit": {"dur": 0.3}})
        layers.append({"kind": "title", "t0": round(t0 + 0.3, 3), "t1": round(t1, 3), "y": 90, "lines": [{"text": "Fixed schedule · SIMULATED run, seed 42", "size": 32, "cls": "soft"}], "enter": {"dur": 0}, "exit": {"dur": 0.3}})
    elif kind == "panel":
        layers.append({"kind": "panel", "t0": t0, "t1": round(t1, 3), "w": 1300, "y": 120, "heading": p["heading"], "items": p["items"], "fine": p["fine"], "enter": {"dur": 0.6}, "exit": {"dur": 0.3}})
    elif kind == "loop":
        a = vstart["p07"]; b = vstart["p08"]; c = vstart["p09"]   # checks light as each question is asked (offsets scale with pace)
        tx = V["p08"]["text"]
        checks = [b + d("p08") * tx.index(q) / len(tx) for q in ("Has this person", "Have they asked", "Is it between", "Have we already", "Is there budget", "Is their account")]
        layers.append(loop_layer(t0, t1, built=t0 + 0.3, voice_t={"checks": checks, "rest": c + 0.6, "pulse": c + 4.2}))

# one nebula per contiguous span (cuts to footage hide the joins); rate keeps inside the clip
for t0, t1, src, frm in neb_spans:
    layers.insert(0, {"kind": "nebula", "t0": t0, "t1": round(t1, 3), "src": src, "from": frm, "rate": 0.35, "dim": 0.42, "enter": {"dur": 0}, "exit": {"dur": 0}})
for t0, t1, lit in mini_lit:
    layers.append(loop_layer(t0 + 0.2, t1, built=True, mini=True, lit=[{"t0": t0 + 0.6, "t1": t1, "nodes": lit}]))

film = {"duration": round(END, 3), "ccStyle": "v4", "cues": "audio/product4_cues.json", "bg": "#02040a", "captures": CAPS, "layers": layers}
import os as _os
for L in layers:
    if L.get("kind") == "video" and not _os.path.exists(L["src"]): raise SystemExit("missing clip " + L["src"])
open("films/product4.js", "w").write("/* generated by tools/build_product4.py */\nwindow.FILM = " + json.dumps(film, ensure_ascii=False, indent=1) + ";\n")

# ---------------------------------------------------------------- soundtrack: music starts with the hit after the cold open
MUSIC_AT = seq[1][3]                               # the logo shot
bar, b0 = 1.7143, 0.24                            # AtlasAudio: 140 BPM, first downbeat 0.24 s
def B(n): return b0 + n * bar
ib = [i for i, s in enumerate(seq) if s[4] == "end" and s[0] == "brand"][0]
target = seq[ib][3] + 4.5 - MUSIC_AT              # the last bar lands at least 4.5 s into the logo lockup
best = None
for n32 in range(0, 6):
    for n16 in range(0, 3):
        for n8 in range(0, 2):
            for outro in (B(68) - B(64), B(72) - B(64), B(73) - B(64)):
                tot = B(64) + n32 * (B(64) - B(32)) + n16 * (B(64) - B(48)) + n8 * (B(64) - B(56)) + outro
                if tot >= target and (best is None or tot < best[0]): best = (tot, n32, n16, n8, outro)
total, n32, n16, n8, outro = best
segs = [{"src": 0.0, "len": round(B(64), 3)}] + [{"src": round(B(32), 3), "len": round(B(64) - B(32), 3)}] * n32 \
       + [{"src": round(B(48), 3), "len": round(B(64) - B(48), 3)}] * n16 + [{"src": round(B(56), 3), "len": round(B(64) - B(56), 3)}] * n8 \
       + [{"src": round(B(64), 3), "len": round(outro, 3)}]
at = MUSIC_AT
segs = [dict(x) for x in segs]
for i, sg in enumerate(segs): sg["at"] = round(at, 3); sg["fadeIn"] = 0.02 if i else 0; sg["fadeOut"] = 0.03; at += sg["len"]
# stretch the lockup so the picture's last card starts exactly when the music stops
grow = MUSIC_AT + total - (seq[ib][3] + seq[ib][1])
seq[ib] = (seq[ib][0], round(seq[ib][1] + grow, 3), seq[ib][2], seq[ib][3], seq[ib][4])
for L in layers:
    if L.get("kind") == "brand" and abs(L["t0"] - seq[ib][3]) < 1e-3: L["t1"] = round(L["t1"] + grow, 3)
    elif L["t0"] >= seq[ib][3] + 1e-3 and L.get("kind") != "nebula": L["t0"] = round(L["t0"] + grow, 3); L["t1"] = round(L["t1"] + grow, 3)
    elif L.get("kind") == "nebula" and abs(L["t0"] - seq[ib][3]) < 1e-3: L["t1"] = round(L["t1"] + grow, 3)
END = round(END + grow, 3); film["duration"] = END
open("films/product4.js", "w").write("/* generated by tools/build_product4.py */\nwindow.FILM = " + json.dumps(film, ensure_ascii=False, indent=1) + ";\n")
print("music %.2f s (base + %d×32 + %d×16 + %d×8 bars + outro %.1f s) from %.2f; film %.2f s (%d:%02d)" % (total, n32, n16, n8, outro, MUSIC_AT, END, END // 60, END % 60))
vo = [{"file": "audio/v4/%s.wav" % vid, "at": round(vt, 3), "text": V[vid]["text"], "who": V[vid].get("who", ""), "gain": 0 if not V[vid].get("who") else -1.0} for vt, vid in voice]
mix = {"out": "audio/product4_mix.wav", "duration": round(END, 3), "lufs": -14, "ceiling": -1.0,
       "music": {"file": "../music/licensed/corporate_inspirational_atlasaudio.mp3", "segments": segs, "gain": 0},
       "duck": {"db": 18, "attack": 0.12, "release": 0.5, "pre": 0.18}, "voLevel": -20, "vo": vo, "cuesOut": "audio/product4_cues_raw.json"}
json.dump(mix, open("mix/product4.json", "w"), indent=1, ensure_ascii=False)
json.dump(cc_extra, open("audio/product4_cues_extra.json", "w"), ensure_ascii=False, indent=1)
for vt, vid in voice: print("%7.2f  %-7s %s" % (vt, V[vid].get("who") or "Neha", V[vid]["text"][:72]))
