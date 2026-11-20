#!/usr/bin/env python3
"""Lay out the v4 technical film ("How Kairos works") → films/technical4.js + mix/technical4.json.

An engineering deep dive in numbered chapters. Each chapter: a title card → an animated diagram with real component
names (data flow, sequence diagram or state machine) → the real system running that case (a COMPLETE page in a glass
window on the nebula, then an eased zoom) → one or two real numbers. People appear only in 00 and 08.
"""
import json, os, subprocess

V = json.load(open("tools/lines_v4.json"))
CAPS = {"ov": "captures_hd/out_vikram", "in": "captures_hd/inbound_tall", "er": "captures_hd/engine_room", "dv": "captures_hd/deployed_voice_tall"}   # 4× captures
_dur = {}
def d(vid):
    if vid not in _dur:
        out = subprocess.run(["afinfo", "audio/v4/%s.wav" % vid], capture_output=True, text=True).stdout
        _dur[vid] = float(out.split("estimated duration:")[1].split()[0])
    return _dur[vid]
def RC(cap, label, key):
    lg = json.load(open(CAPS[cap] + "/log.json"))
    for e in lg["events"]:
        if e.get("type") == "rect" and e["name"] == "%s_%s" % (label, key):
            r = [round(v) for v in e["rect"]]
            if key == "detail": r = [r[0], r[1] - 50, r[2], r[3] + 50]          # include the card's header row
            return r
    raise SystemExit("no rect %s_%s in %s" % (label, key, cap))
def U(*rs): x0 = min(r[0] for r in rs); y0 = min(r[1] for r in rs); return [x0, y0, max(r[0] + r[2] for r in rs) - x0, max(r[1] + r[3] for r in rs) - y0]
ER_FLOW, ER_RULE = [27, 199, 582, 321], [27, 677, 582, 200]
ER_LEARN = lambda lab: U([647, 199, 606, 52 if lab == "d7" else 70], [647, 259 if lab == "d7" else 277, 606, 165])
F03_CARD = [1540, 560, 380, 500]
def lab(cap, name):
    lg = json.load(open(CAPS[cap] + "/log.json"))
    if any(f["label"] == name for f in lg["frames"]): return name
    raise SystemExit("no frame %s in %s" % (name, cap))

beats = []
def beat(shots, voice=(), at=0.3, tail=0.35, gap=0.3, name=""):
    beats.append(dict(shots=shots, voice=list(voice), at=at, tail=tail, gap=gap, name=name))
def clip(name, dur, **kw): return ("clip", dur, dict(src="comp/%s.mov" % name, **kw))
def glass(cap, label, dur, **kw): return ("glass", dur, dict(cap=cap, label=label, **kw))
def other(kind, dur, **kw): return (kind, dur, kw)
def chapter(num, title, sub): return other("title", 1.3, lines=[{"text": num, "size": 40, "cls": "kicker"}, {"text": title, "size": 104, "mt": 8}, {"text": sub, "size": 40, "cls": "soft", "mt": 14}], neb=("comp/NEB2.mov", float(num) * 1.7 if num.isdigit() else 0.0))
def num(lines, dur=3.0, **kw): return other("title", dur, lines=[dict(l) for l in lines], neb=("comp/NEB1.mov", kw.pop("nf", 3.0)), **kw)
F02, F03 = "rec/F02_approvals_supervisor_clean.mp4", "rec/F03_crm_explain_manual_preview_clean.mp4"

# ---------------------------------------------------------------- diagrams (world = 1920×1080, everything above the caption band, y < 880)
def N(i, x, y, w, h, title, body=(), tone="teal", tag=None): return {"id": i, "x": x, "y": y, "w": w, "h": h, "title": title, "body": list(body), "tone": tone, "tag": tag}
def E(a, b, label=None, pts=None, lx=0, ly=0, dashed=False): return {"from": a, "to": b, "label": label, "pts": pts, "lx": lx, "ly": ly, "dashed": dashed}
DIA = {
 "arch": ([N("rec", 60, 330, 330, 150, "Record", ["one per borrower"], "blue", "K03"), N("churn", 470, 330, 330, 150, "Churn", ["sorts daily"], "teal", "K27"),
           N("learn", 880, 330, 330, 150, "Learner", ["channel × hour"], "teal", "K09"), N("ald", 1290, 330, 330, 150, "Aladdin", ["the gate"], "amber", "K06"),
           N("exec", 1290, 620, 330, 130, "Executor", [], "violet"), N("chan", 880, 620, 330, 130, "Channels", [], "violet"),
           N("hook", 470, 620, 330, 130, "Webhooks", [], "violet"), N("out", 60, 620, 330, 130, "Outcomes", [], "blue"),
           N("people", 1290, 80, 330, 130, "People", [], "green"), N("intent", 1640, 330, 262, 150, "Intent", ["rules → model"], "violet")],
          [E("rec", "churn"), E("churn", "learn"), E("learn", "ald"), E("ald", "exec"), E("exec", "chan"), E("chan", "hook"), E("hook", "out"), E("out", "rec", None, [[225, 560]]),
           E("people", "ald"), E("hook", "intent", None, [[635, 790], [1775, 790]], dashed=True)]),
 "spine": ([N("bor", 70, 120, 420, 150, "Borrower", ["one record"], "blue"), N("loan", 560, 120, 360, 150, "Loans", [], "blue"), N("emi", 990, 120, 400, 150, "EMIs", ["each one tracked"], "blue"),
            N("pay", 1460, 120, 390, 150, "Payments", ["in paise"], "green"), N("con", 560, 420, 520, 170, "Consent", ["per channel, versioned"], "amber"),
            N("ev", 1150, 420, 700, 170, "Events", ["every touch, reply and decision"], "slate")],
           [E("bor", "loan"), E("loan", "emi"), E("emi", "pay"), E("bor", "con", None, [[280, 505]]), E("con", "ev")]),
 "churn": ([N("tick", 70, 330, 330, 150, "Daily tick", ["each morning"], "slate"),
            N("c", 520, 90, 400, 120, "Cohort", [], "teal"), N("r", 520, 260, 400, 120, "Risk tier", [], "teal"), N("l", 520, 430, 400, 120, "Contact layer", [], "teal"), N("s", 520, 600, 400, 120, "Status", [], "teal"),
            N("rule", 1060, 300, 380, 200, "Written rule", ["e.g. 61+ days late", "→ risk HIGH"], "amber"), N("mv", 1540, 300, 330, 200, "Move logged", ["rule + inputs"], "green")],
           [E("tick", "c", None, [[460, 405], [460, 150]]), E("tick", "r", None, [[460, 405], [460, 320]]), E("tick", "l", None, [[460, 405], [460, 490]]), E("tick", "s", None, [[460, 405], [460, 660]]),
            E("c", "rule", None, [[990, 150], [990, 400]]), E("r", "rule", None, [[990, 320], [990, 400]]), E("l", "rule", None, [[990, 490], [990, 400]]), E("s", "rule", None, [[990, 660], [990, 400]]), E("rule", "mv")]),
 "learn": ([N("touch", 70, 140, 360, 150, "A message", ["SMS · WhatsApp · call"], "violet"), N("win", 560, 140, 400, 150, "24 h window", ["then it closes"], "slate"),
            N("res", 1090, 140, 380, 150, "One result", ["reply or no reply"], "green"), N("est", 1090, 450, 780, 190, "Reply chance estimate", ["per group × channel × hour", "with its uncertainty"], "teal", "Bayesian"),
            N("dec", 70, 450, 520, 190, "Tomorrow's choice", ["best value per ₹"], "amber")],
           [E("touch", "win"), E("win", "res"), E("res", "est"), E("est", "dec", "older evidence counts for less", None, 0, -8), E("dec", "touch")]),
 "cm03": ([N("new", 60, 120, 240, 130, "NEW", [], "slate"), N("otp", 500, 120, 330, 130, "OTP_SENT", [], "amber"), N("ver", 1030, 120, 310, 130, "VERIFIED", [], "green"), N("ans", 1540, 120, 340, 130, "ANSWERED", [], "teal"),
           N("hum", 640, 440, 640, 170, "HUMAN", ["fraud · hardship · unsure"], "red"), N("opt", 1380, 440, 500, 170, "OPTED_OUT", ["STOP: every channel"], "red")],
          [E("new", "otp", "asks", None, 0, -22), E("otp", "ver", "code ok", None, 0, -22), E("ver", "ans"), E("otp", "hum", None, [[665, 525]], dashed=True), E("ver", "hum", None, [[1185, 400]], dashed=True),
           E("ans", "opt", "STOP", None, 40, 0), E("ans", "hum", None, [[1710, 360], [960, 360]], dashed=True)]),
 "voice": ([N("uc", 60, 120, 330, 150, "Use case", ["e.g. EMI reminder"], "slate"), N("reg", 460, 120, 330, 150, "Registry", ["agent · voice · language"], "teal"),
            N("agent", 860, 120, 360, 150, "AI voice agent", ["one per use case"], "violet"), N("call", 1290, 120, 330, 150, "Call", [], "violet"),
            N("hook", 1290, 420, 560, 170, "After the call", ["outcome + confidence"], "slate"), N("thr", 700, 420, 520, 170, "Hand-set threshold", ["Kairos decides"], "amber"),
            N("act", 60, 330, 420, 120, "Act on it", [], "green"), N("hold", 60, 520, 420, 120, "A person reviews", [], "red")],
           [E("uc", "reg"), E("reg", "agent"), E("agent", "call"), E("call", "hook"), E("hook", "thr"), E("thr", "act", "above"), E("thr", "hold", "below", None, 0, 48)]),
 "hitl": ([N("appr", 60, 110, 520, 170, "Approval window", ["08:00 to 09:30"], "teal"), N("hold", 60, 330, 520, 130, "Pause · resume", [], "teal"),
           N("take", 60, 510, 520, 130, "Take over a chat", [], "teal"), N("ald", 770, 280, 420, 200, "Aladdin", ["same checks for people"], "amber", "K06"),
           N("audit", 1380, 280, 460, 200, "Audit log", ["who · what · why"], "slate")],
          [E("appr", "ald", None, [[680, 195], [680, 380]]), E("hold", "ald"), E("take", "ald", None, [[680, 575], [680, 380]]), E("ald", "audit")]),
}
def fit_diagram(nodes, edges, box=(64, 80, 1856, 850), smax=1.3):
    xs = [n["x"] for n in nodes] + [n["x"] + n["w"] for n in nodes] + [p[0] for e in edges for p in (e.get("pts") or [])]
    ys = [n["y"] for n in nodes] + [n["y"] + n["h"] for n in nodes] + [p[1] for e in edges for p in (e.get("pts") or [])]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    sc = min((box[2] - box[0]) / (x1 - x0), (box[3] - box[1]) / (y1 - y0), smax)
    ox = box[0] + ((box[2] - box[0]) - (x1 - x0) * sc) / 2 - x0 * sc; oy = box[1] + ((box[3] - box[1]) - (y1 - y0) * sc) / 2 - y0 * sc
    for n in nodes: n["x"], n["y"], n["w"], n["h"] = round(n["x"] * sc + ox), round(n["y"] * sc + oy), round(n["w"] * sc), round(n["h"] * sc)
    for e in edges:
        if e.get("pts"): e["pts"] = [[round(p[0] * sc + ox), round(p[1] * sc + oy)] for p in e["pts"]]
def flow_layer(key, t0, t1, appear=None, pulse=None, lit=None):
    nodes, edges = DIA[key]
    nodes = [dict(n) for n in nodes]; edges = [dict(e) for e in edges]
    fit_diagram(nodes, edges)
    for k, n in enumerate(nodes): n["t"] = (appear + k * 0.32) if appear is not None else t0 - 1
    nt = {n["id"]: n["t"] for n in nodes}
    for e in edges: e["t"] = max(nt[e["from"]], nt[e["to"]]) + 0.2
    L = {"kind": "flow", "t0": round(t0, 3), "t1": round(t1, 3), "nodes": nodes, "edges": edges, "enter": {"dur": 0.35}, "exit": {"dur": 0.3}}
    if pulse: L["pulses"] = [{"t": pulse[0], "path": pulse[1], "dur": 0.7}]
    if lit: L["lit"] = lit
    return L
def seq_layer(key, t0, t1, msgs_at):
    if key == "ald":
        actors = [{"id": "k09", "label": "K09 Learner", "x": 220}, {"id": "k06", "label": "K06 Aladdin", "x": 700, "tone": "amber"}, {"id": "led", "label": "Ledger", "x": 1150},
                  {"id": "ex", "label": "Executor", "x": 1520, "tone": "violet"}]
        msgs = [("k09", "k06", "plan: SMS at 09:30", None), ("k06", None, "consent · STOP · hours · limits · budget · holds", None),
                ("k06", "led", "reserve budget + limit", "one atomic step"), ("k06", "ex", "queued", None), ("ex", "k06", "09:30: check every rule again", None), ("k06", "ex", "allowed → send   ·   changed → nothing sent", None)]
    else:
        actors = [{"id": "ex", "label": "Executor", "x": 220, "tone": "violet"}, {"id": "ad", "label": "Adapter", "x": 700}, {"id": "ch", "label": "Channel", "x": 1150},
                  {"id": "wh", "label": "Webhook", "x": 1580, "tone": "amber"}]
        msgs = [("ex", "ad", "send SMS", None), ("ad", "ch", "deliver", "dry run in this demo"), ("ch", "wh", "delivered", "reach, not a reply"), ("ch", "wh", "delivered (again)", None),
                ("wh", None, "same key → ignored", None), ("ch", "wh", "reply", "one result for this message")]
    out = []
    for i, (a, b, label, note) in enumerate(msgs):
        m = {"t": round(msgs_at[i], 3), "from": a, "label": label}
        if b: m["to"] = b
        if note: m["note"] = note
        if key == "ald" and i == 4: m["ret"] = True
        if key == "hook" and i == 4: m["tone"] = "amber"
        out.append(m)
    return {"kind": "seq", "t0": round(t0, 3), "t1": round(t1, 3), "actors": actors, "msgs": out, "top": 120, "bottom": 860, "row": 104, "enter": {"dur": 0.35}, "exit": {"dur": 0.3}}

# ---------------------------------------------------------------- chapters (card ≈ 1.3 s, then the voice; what · how · proof)
AT = 1.35
beat([clip("T01_engineer", 2.0), other("title", 2.0, lines=[{"text": "How Kairos works", "size": 120}, {"text": "an engineering tour, part by part", "size": 44, "cls": "soft", "mt": 16}], neb=("comp/NEB2.mov", 1.0)),
      other("flow", 7.0, key="arch", neb=("comp/NEB2.mov", 3.0))], voice=["c00"], at=2.1, name="00")
beat([chapter("01", "Data spine", "K03 · one record per borrower"), other("flow", 5.0, key="spine", neb=("comp/NEB1.mov", 2.0)),
      glass("ov", "v01", 4.5, title="Kairos · Live journey · borrower record", cam=[(1.0, RC("ov", "v01", "rec"), 1.4)], neb=("comp/NEB1.mov", 6.0)),
      num([{"text": "₹7,409 = 740,900 paise", "size": 96}, {"text": "every amount is stored in paise · SIMULATED data", "size": 36, "cls": "soft", "mt": 18}], 2.2)],
     voice=["c01"], at=AT, name="01")
beat([chapter("02", "Churn engine", "K27 · re-sorts every borrower daily"), other("flow", 5.0, key="churn", neb=("comp/NEB2.mov", 3.0)),
      glass("er", "d7", 8.0, title="Kairos · Engine Room · churn · SIMULATED", cam=[(0.8, ER_FLOW, 1.4), (4.2, ER_RULE, 1.2)], neb=("comp/NEB2.mov", 6.0)),
      ],
     voice=["c02"], at=AT, name="02")
beat([chapter("03", "Learning loop", "K09 · a Bayesian learner"), other("flow", 7.0, key="learn", neb=("comp/NEB1.mov", 4.0)),
      glass("er", "d7", 10.0, title="Kairos · Engine Room · day 7 → end of day 14 · SIMULATED", frames=[(0, "d7"), (5.0, "d14")], cam=[(1.0, ER_LEARN("d7"), 1.4), (5.0, ER_LEARN("d14"), 0.8)], neb=("comp/NEB1.mov", 8.0))],
     voice=["c03"], at=AT, name="03")
beat([chapter("04", "Aladdin gate", "K06 · reserve, then re-check"), other("seq", 9.0, key="ald", neb=("comp/NEB2.mov", 2.0)),
      glass("ov", "v02", 5.0, title="Kairos · Live journey · Aladdin", cam=[(1.0, RC("ov", "v02", "detail"), 1.4)], neb=("comp/NEB2.mov", 7.0)),
      num([{"text": "50,000 decisions · 11.1 s", "size": 100}, {"text": "limit breaches: 0 · ledger benchmark (real run)", "size": 38, "cls": "soft", "mt": 18}], 2.6)],
     voice=["c04"], at=AT, name="04")
beat([chapter("05", "Channels, webhooks and voice", "send · call back · act"), other("seq", 7.0, key="hook", neb=("comp/NEB1.mov", 3.0)),
      glass("ov", "v06", 4.0, title="Kairos · Live journey · webhooks", cam=[(1.0, RC("ov", "v06", "detail"), 1.4)], neb=("comp/NEB1.mov", 7.0)),   # "delivery receipt again: the copy is ignored"
      other("flow", 5.0, key="voice", neb=("comp/NEB2.mov", 3.0)),
      glass("dv", "voice", 5.0, title="Kairos · Voice bots · calls", page=[276, 633, 1136, 316], neb=("comp/NEB1.mov", 8.0))],
     voice=["c05"], at=AT, name="05")
beat([chapter("06", "Inbound WhatsApp", "CM-03 · a small state machine"), other("flow", 7.0, key="cm03", neb=("comp/NEB2.mov", 2.0)),
      glass("in", "fake_tech", 5.0, title="Kairos · Inbound WhatsApp · Technical", cam=[(1.0, RC("in", "fake_tech", "trace"), 1.4)], neb=("comp/NEB2.mov", 6.0)),
      other("panel", 5.0, heading="1,000 seeded test conversations", items=[
          {"html": "<em>73.2%</em> resolved without a person"}, {"html": "<em>127 / 127</em> fakes caught · 0 false flags"},
          {"html": "<em>0</em> disclosures before the code"}, {"html": "first reply p95 <em>1.6 ms</em> · engine time"}],
          fine="SIMULATED · 1,000 seeded conversations · 500 at once: 0 errors", neb=("comp/NEB2.mov", 10.0))],
     voice=["c06"], at=AT, name="06")
if os.environ.get("REAL_WA"):
    beat([("glass", 12.0, dict(video=os.environ["REAL_WA"], vw=1920, vh=1080, title="Real WhatsApp · Meta Cloud API test number", neb=("comp/NEB2.mov", 1.0)))], name="realwa")
beat([chapter("07", "Human in the loop", "people decide; Aladdin still checks"), other("flow", 4.0, key="hitl", neb=("comp/NEB2.mov", 4.0)),
      clip("P16a_asha_close", 1.6),
      ("glass", 4.4, dict(video=F02, frm=36.0, rate=2.0, vw=1920, vh=1080, title="Kairos · Control Room · approvals", neb=("comp/NEB2.mov", 9.0))),
      ("glass", 5.6, dict(video=F03, frm=50.0, rate=1.0, vw=1920, vh=1080, title="Kairos · Smart CRM · manual send at 20:00", cam=[(0.6, F03_CARD, 1.4)], kmax=1.0, neb=("comp/NEB2.mov", 14.0)))],
     voice=["c08"], at=AT, name="07")
beat([chapter("08", "Determinism and testing", "same seed, same decisions"), other("terminal", 20.0, neb=("comp/NEB1.mov", 2.0))],
     voice=["c09"], at=AT, name="08")
beat([other("research", 8.0, neb=("comp/NEB2.mov", 2.0)), other("flow", 3.0, key="arch", pulse=True, neb=("comp/NEB2.mov", 6.0))], voice=["c10"], at=1.6, name="research")
beat([other("brand", 4.0, end=True, neb=("comp/NEB1.mov", 10.0), lines=["Built to be trusted with every rupee."],
            credits="Music: “Inspiring Technology”, The_Mountain (Pixabay licence) · Voices: AI-generated · Footage: Pexels<br>Every screen and terminal run is the real Kairos software · All data SIMULATED")], name="end")

# ---------------------------------------------------------------- lay out
t, seq, voice = 0.0, [], []
for b in beats:
    vt, vends = t + b["at"], []
    for vid in b["voice"]:
        voice.append((vt, vid)); vends.append(vt + d(vid)); vt += d(vid) + b["gap"]
    need = (max(vends) - t + b["tail"]) if vends else 0
    shots = [list(s) for s in b["shots"]]
    have = sum(s[1] for s in shots)
    if need > have:   # the voice is longer: stretch the real-system shots (glass, diagrams) evenly
        g = [s for s in shots if s[0] in ("glass", "flow", "seq", "loop", "panel")] or shots[-1:]
        for s in g: s[1] += (need - have) / len(g)
    elif vends and have > need + 0.3:   # the voice is shorter: the voice is the clock, shots shrink (never below their floor)
        floor = lambda s: 1.3 if s[0] == "clip" else (1.0 if s[0] == "title" else 2.2)
        for _ in range(4):
            have = sum(s[1] for s in shots); flex = [s for s in shots if s[1] > floor(s) + 1e-3]
            if have <= need + 0.05 or not flex: break
            cut = (have - need) / len(flex)
            for s in flex: s[1] = max(floor(s), s[1] - cut)
    for s in shots:
        seq.append((s[0], round(s[1], 3), s[2], round(t, 3), b["name"])); t += s[1]
END = t

layers, neb_spans = [], []
vstart = {vid: vt for vt, vid in voice}
for kind, dur, p, t0, bname in seq:
    t1 = round(t0 + dur, 3)
    if "neb" in p: neb_spans.append((t0, t1, p["neb"][0], p["neb"][1]))
    if kind == "clip":
        layers.append({"kind": "video", "src": p["src"], "t0": t0, "t1": t1, "from": 0.0, "graded": True, "enter": {"dur": 0}, "exit": {"dur": 0}})
    elif kind == "glass":
        L = {"kind": "glass", "t0": t0, "t1": t1, "title": p.get("title"), "cam": [{"t": round(t0 + a, 3), "rect": r, "dur": dd} for a, r, dd in p.get("cam", [])],
             "enter": p.get("enter", {"type": "rise", "dur": 0.6}), "exit": {"type": "fade", "dur": 0.25}}
        if p.get("video"): L.update({"video": p["video"], "from": p.get("frm", 0.0), "rate": p.get("rate", 1.0), "vw": p.get("vw", 1920), "vh": p.get("vh", 1080)})
        elif p.get("frames"): L.update({"cap": p["cap"], "frames": [{"at": 0 if i == 0 else round(t0 + a, 3), "label": lab(p["cap"], l)} for i, (a, l) in enumerate(p["frames"])]})
        else: L.update({"cap": p["cap"], "frames": [{"at": 0, "label": lab(p["cap"], p["label"])}]})
        if p.get("page"): L["page"] = p["page"]
        for kk in ("margin", "kmax"):
            if kk in p: L[kk] = p[kk]
        layers.append(L)
    elif kind == "title":
        layers.append({"kind": "title", "t0": t0, "t1": t1, "lines": p["lines"], "y": p.get("y", 470), "stagger": 0.18, "enter": {"dur": 0}, "exit": {"dur": 0.3}})
    elif kind == "flow":
        pulse = None
        if p.get("pulse"): pulse = (t0 + 0.4, ["rec", "churn", "learn", "ald", "exec", "chan", "hook", "out", "rec"])
        layers.append(flow_layer(p["key"], t0, t1, appear=None if p.get("pulse") else t0 + 0.3, pulse=pulse))
    elif kind == "seq":
        n = 6; step = max(0.9, (dur - 1.6) / n)
        layers.append(seq_layer(p["key"], t0, t1, [t0 + 0.9 + i * step for i in range(n)]))
    elif kind == "panel":
        layers.append({"kind": "panel", "t0": t0, "t1": t1, "w": 1300, "y": 110, "heading": p["heading"], "items": p["items"], "fine": p["fine"], "enter": {"dur": 0.6}, "exit": {"dur": 0.3}})
    elif kind == "research":
        cites = ["Thompson · <i>Biometrika</i> 1933", "Chapelle &amp; Li · NeurIPS 2011", "Li et al. · WWW 2010", "Badanidiyuru, Kleinberg &amp; Slivkins · FOCS 2013",
                 "Clayton &amp; Kaldor · <i>Biometrics</i> 1987", "Abe et al. · KDD 2010", "Yancey &amp; Settles · KDD 2020", "Alshiekh et al. · AAAI 2018",
                 "Angelopoulos et al. · Learn then Test", "Dudík, Langford &amp; Li · ICML 2011"]
        grid = "<div style='display:grid;grid-template-columns:auto auto;gap:14px 70px;text-align:left;font-size:34px;font-weight:500;color:rgba(226,232,240,.92);letter-spacing:0'>" + "".join("<div>%s</div>" % c for c in cites) + "</div>"
        layers.append({"kind": "panel", "t0": t0, "t1": t1, "w": 1420, "y": 120, "heading": "Built on research", "items": [{"html": grid, "t": round(t0 + 0.4, 3)}],
                       "fine": "Lineage: Aladdin ≈ a safety shield (Alshiekh 2018) · the intent ladder ≈ a reject option (Chow 1970)", "enter": {"dur": 0.5}, "exit": {"dur": 0.3}})
    elif kind == "brand":
        layers.append({"kind": "brand", "t0": t0, "t1": t1, "y": 450, "lines": p.get("lines", []), "credits": p.get("credits"), "from": 0.97, "enter": {"dur": 0}, "exit": {"dur": 0.35}})
    elif kind == "terminal":
        a = t0; ks = dur / 16.0
        layers.append({"kind": "term", "t0": a, "t1": t1, "title": "kairos/backend — zsh", "prompt": "%", "rows": 15, "maxCols": 80, "home": [160, 0, 1600],
                       "enter": {"type": "fade", "dur": 0.3}, "exit": {"type": "fade", "dur": 0.2}, "cam": [{"t": 0, "full": True}],
                       "runs": [
            {"rec": "determinism", "at": a + 0.3 * ks, "cadence": 0.004, "fit": 1.0, "keep": "^Matched|Verified payment \\(unique\\)|K01 cap breaches|K15 safety|Deterministic replay", "hi": "IDENTICAL", "badge": "638 s run · shown 600× faster"},
            {"rec": "redteam", "at": a + 3.6 * ks, "cadence": 0.012, "fit": 1.0, "clear": True, "keep": "PASS|FAIL|attacks:", "hi": "49 PASS", "badge": "3.7 s run"},
            {"rec": "bench", "at": a + 6.9 * ks, "cadence": 0.012, "fit": 0.5, "clear": True, "hi": "atomic decisions|cap breaches: 0", "badge": "ledger benchmark · 15 s run, 30× faster"},
            {"rec": "pytest", "at": a + 9.9 * ks, "cadence": 0.05, "fit": 0.7, "clear": True, "hi": "passed", "badge": "74 s run · shown 90× faster"},
            {"rec": "ope", "at": a + 12.6 * ks, "cadence": 0.012, "fit": 0.4, "clear": True, "keep": "^OPE|^replies per 1,000 decisions|^CN10.*18\\.4 +18\\.2", "hi": "^CN10", "badge": "SIMULATED · 20 s run, 50× faster", "prompt": "kairos/backend (research) %"}]})
        for o, dd, k, h in [(1.5, 2.0, "Same seed, run twice", "Deterministic replay: <em>IDENTICAL</em>"), (4.8, 1.9, "Our own red-team suite", "49 attacks: <em>49 PASS</em>, 0 FAIL"),
                            (7.8, 1.9, "Ledger benchmark", "<em>50,000</em> atomic decisions in 11.1 s<br>cap breaches: <em>0</em>"), (10.9, 1.6, "Full test suite", "<em>3030 passed</em> in 74 s"),
                            (13.5, 2.3, "Off-policy evaluation · CN10 fixed schedule · SIMULATED", "From logs: <em>18.2</em>. Truth: <em>18.4</em>.")]:
            layers.append({"kind": "callout", "t0": a + o * ks, "t1": a + o * ks + dd * ks, "dark": True, "x": 260, "y": 560, "maxw": 1500, "kicker": k, "mono": True, "html": h, "stagger": 0, "enterDur": 0.2})
NEB_LEN = {"comp/NEB1.mov": 16.4, "comp/NEB1c.mov": 15.75, "comp/NEB2.mov": 30.4}
NEB1 = os.environ.get("KAIROS_NEB1", "comp/NEB1.mov")   # comp/NEB1c.mov = N1 with its built-in duplicate frames dropped (final2)
merged = []
for t0, t1, src, frm in sorted(neb_spans):
    if merged and abs(merged[-1][1] - t0) < 2e-2: merged[-1][1] = t1           # adjacent platform shots share one background
    else: merged.append([t0, t1, src, frm])
for t0, t1, src, frm in merged:
    src = NEB1; n, dur = NEB_LEN[src], t1 - t0
    frm = frm % max(1.0, n - 1.0)
    layers.insert(0, {"kind": "nebula", "t0": max(0, t0 - 0.05), "t1": round(t1 + 0.05, 3), "src": src, "from": round(frm, 3), "rate": 1.0, "len": n - 0.1, "dim": 0.38, "tint": "8,26,56", "zoom": [1.04, 1.04 + 0.004 * dur], "enter": {"dur": 0}, "exit": {"dur": 0}})
# a glass window that cuts to another glass window crossfades (the old one stays under the new one), no dip to the background
gl = [L for L in layers if L.get("kind") == "glass"]
for a, b in zip(gl, gl[1:]):
    if abs(a["t1"] - b["t0"]) < 1e-3:
        a["t1"] = round(a["t1"] + 0.35, 3); a["exit"] = {"type": "fade", "dur": 0.0}; b["enter"] = {"type": "fade", "dur": 0.35}
XF = 0.4
base = [L for L in layers if L["kind"] in ("video", "glass", "title", "flow", "seq", "panel", "brand", "term") and abs(L.get("t1", 0) - L.get("t0", 0)) > 0.5]
base.sort(key=lambda L: L["t0"])
for a, b in zip(base, base[1:]):
    if abs(a["t1"] - b["t0"]) < 0.06:
        a["t1"] = round(a["t1"] + XF, 3); a["exit"] = dict(a.get("exit") or {}, dur=XF, type="fade")
        b["enter"] = dict(b.get("enter") or {}, dur=XF, type="fade")
film = {"duration": round(END, 3), "ccStyle": "v4", "cues": "audio/technical4_cues.json", "bg": "#02040a", "captures": CAPS,
        "terms": {"redteam": "term/redteam.json", "bench": "term/bench.json", "determinism": "term/determinism.json", "pytest": "term/pytest.json", "ope": "term/ope.json"}, "layers": layers}
for L in layers:
    if L.get("kind") == "video" and not os.path.exists(L["src"]): raise SystemExit("missing clip " + L["src"])

# ---------------------------------------------------------------- music: The_Mountain, 80 BPM, bars of 3.0 s from 0.02 s; phrase-aligned repeats, then the ending
def B(n): return 0.02 + 3.0 * n
ib = len(seq) - 1
target = seq[ib][3] + 3.4                                   # the ending bar lands at least 4 s into the lockup
best = None
for n32 in range(0, 4):
    for n16 in range(0, 3):
        for n8 in range(0, 3):
            for n4 in range(0, 2):
                tot = B(40) + n32 * (B(40) - B(8)) + n16 * (B(40) - B(24)) + n8 * (B(40) - B(32)) + n4 * (B(40) - B(36)) + (B(44) - B(40))
                if tot >= target and (best is None or tot < best[0]): best = (tot, n32, n16, n8, n4)
total, n32, n16, n8, n4 = best
segs = [{"src": 0.0, "len": round(B(40), 3)}] + [{"src": round(B(8), 3), "len": 96.0}] * n32 + [{"src": round(B(24), 3), "len": 48.0}] * n16 + [{"src": round(B(32), 3), "len": 24.0}] * n8 + [{"src": round(B(36), 3), "len": 12.0}] * n4 + [{"src": round(B(40), 3), "len": 12.0}]
segs = [dict(x) for x in segs]; at = 0.0
for i, sg in enumerate(segs): sg["at"] = round(at, 3); sg["fadeIn"] = 0.02 if i else 0; sg["fadeOut"] = 0.03 if i < len(segs) - 1 else 1.5; at += sg["len"]
grow = total - (seq[ib][3] + seq[ib][1])
for L in layers:
    if abs(L["t1"] - END) < 1e-3: L["t1"] = round(L["t1"] + grow, 3)
END = round(END + grow, 3); film["duration"] = END
open("films/" + os.environ.get("KAIROS_FILM", "technical4") + ".js", "w").write("/* generated by tools/build_technical4.py */\nwindow.FILM = " + json.dumps(film, ensure_ascii=False, indent=1) + ";\n")
vo = [{"file": "audio/v4/%s.wav" % vid, "at": round(vt, 3), "text": V[vid]["text"], "who": "", "gain": 0} for vt, vid in voice]
mix = {"out": "audio/technical4_mix.wav", "duration": END, "lufs": -14, "ceiling": -1.0,
       "music": {"file": "../music/licensed/inspiring_technology_the_mountain.mp3", "segments": segs, "gain": 0},
       "duck": {"db": 18, "attack": 0.12, "release": 0.5, "pre": 0.18}, "voLevel": -20, "vo": vo, "cuesOut": "audio/technical4_cues_raw.json"}
json.dump(mix, open("mix/technical4.json", "w"), indent=1, ensure_ascii=False)
print("film %.2f s (%d:%02d); music %.1f s (40 bars + %d×32 + %d×16 + %d×8 + ending)" % (END, END // 60, END % 60, total, n32, n16, n8))
for vt, vid in voice: print("%7.2f  %s" % (vt, V[vid]["text"][:80]))
