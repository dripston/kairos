#!/usr/bin/env python3
"""Closed captions from the mix's spoken-line timings.

    python3 tools/captions.py audio/product_cues_raw.json audio/product_cues.json out/kairos_product   (writes .srt and .vtt)

Each spoken line becomes one or more caption cues: at most 2 lines of about 42 characters (44 max), made of whole
sentences or explicit complete phrases (OVERRIDES), timed in proportion to their length inside the line's spoken
interval. Dialogue cues carry the speaker tag (Sunita:, Kairos:, Vikram:); the narrator has none. The burned-in captions use the same file,
so the sidecars match the picture exactly.
"""
import json, re, sys

MAXC, MAXL = 44, 2   # "about 42" characters per line; never more than 2 lines
# Lines whose sentences are too long for one cue get explicit, complete-phrase splits (never mid-clause).
OVERRIDES = {
    "Before anything goes out, Aladdin, our safety supervisor, checks it against the lender's rules.":
        ["Before anything goes out,", "Aladdin, our safety supervisor, checks it against the lender's rules."],
    "To protect your account, I have sent a 6-digit code to your registered WhatsApp number.":
        ["To protect your account,", "I have sent a 6-digit code to your registered WhatsApp number."],
}

CONJ = {"and", "but", "then", "so", "or", "with", "to", "from", "for", "when", "because", "until", "in", "on", "at"}

def lines_for(text, tag):
    """Best 1- or 2-line layout of tag+text: break after a sentence end, then a clause mark, then before a
    conjunction/preposition, balancing the two lines. Returns (lines, penalty) or None if it cannot fit."""
    words = (tag + text).split()
    if len(" ".join(words)) <= MAXC: return [" ".join(words)], 0
    best = None
    for i in range(1, len(words)):
        l1, l2 = " ".join(words[:i]), " ".join(words[i:])
        if len(l1) > MAXC or len(l2) > MAXC or (tag and i * 1 <= len(tag.split())): continue
        pen = 0 if re.search(r"[.?!]$", words[i - 1]) else 4 if re.search(r"[,;:]$", words[i - 1]) else 10 if words[i].lower() in CONJ else 25
        sc = pen + abs(len(l1) - len(l2)) * 0.5
        if best is None or sc < best[1]: best = ([l1, l2], sc, pen)
    return (best[0], best[2]) if best else None

def fits(text, tag): return lines_for(text, tag) is not None

def split_line(text, who):
    """Cues of whole sentences (or explicit complete phrases): cleanly broken cues first, then the fewest, then the most even."""
    tag = (who + ": ") if who else ""
    text = text.strip()
    if text in OVERRIDES: cues = OVERRIDES[text]
    else:
        sents = re.split(r"(?<=[.?!])\s+", text); n = len(sents); best = None
        for mask in range(1 << (n - 1)):                       # every way to group consecutive sentences
            groups, cur = [], [sents[0]]
            for i in range(1, n):
                if mask >> (i - 1) & 1: groups.append(" ".join(cur)); cur = [sents[i]]
                else: cur.append(sents[i])
            groups.append(" ".join(cur))
            lay = [lines_for(g, tag) for g in groups]
            if all(lay):
                key = (sum(1 for l in lay if l[1] >= 25), len(groups), max(len(g) for g in groups))
                if best is None or key < best[0]: best = (key, groups)
        if best is None:   # a sentence too long for one cue: split it at the clause mark nearest the middle (recursively)
            def split_long(sent):
                if lines_for(sent, tag): return [sent]
                cands = [m.end() for m in re.finditer(r"[,;:](?=\s)", sent)]
                if not cands: raise SystemExit("caption needs an explicit split in OVERRIDES: " + sent)
                k = min(cands, key=lambda c: abs(c - len(sent) / 2))
                return split_long(sent[:k].strip()) + split_long(sent[k:].strip())
            cues = []
            for g in sents:
                cues += split_long(g)
            # re-pack neighbours that fit together
            packed = []
            for c in cues:
                if packed and lines_for(packed[-1] + " " + c, tag) and lines_for(packed[-1] + " " + c, tag)[1] < 25: packed[-1] = packed[-1] + " " + c
                else: packed.append(c)
            best = (None, packed)
        cues = best[1]
    out = []
    for c in cues:
        lay = lines_for(c, tag)
        if not lay: raise SystemExit("caption cue too long: " + c)
        lines = lay[0]
        if tag: lines[0] = lines[0][len(tag):]                 # the engine renders the speaker tag itself, bold
        out.append({"text": c, "lines": lines, "who": who})     # dialogue: the tag is on every cue of the line
    return out

def ts(t, sep):
    h, r = divmod(t, 3600); m, s = divmod(r, 60)
    return "%02d:%02d:%02d%s%03d" % (h, m, int(s), sep, int(round((s - int(s)) * 1000)) % 1000)

raw = json.load(open(sys.argv[1])); out = []
extra = json.load(open(sys.argv[4])) if len(sys.argv) > 4 and sys.argv[4] != "-" else []
MIX = json.load(open(sys.argv[5])) if len(sys.argv) > 5 else None     # the mix spec: which audio file each line is

def speech_map(path):
    """Speech onset, offset and the pauses (≥ 110 ms below the noise floor) of one voice clip, in seconds."""
    import wave, audioop
    w = wave.open(path); sr = w.getframerate(); sw = w.getsampwidth(); ch = w.getnchannels(); d = w.readframes(w.getnframes())
    hop = int(sr * 0.01) * sw * ch; rms = [audioop.rms(d[i:i + hop], sw) for i in range(0, len(d), hop)]
    thr = max(rms) * 0.06; act = [r > thr for r in rms]
    on = next(i for i, a in enumerate(act) if a); off = len(act) - 1 - next(i for i, a in enumerate(reversed(act)) if a)
    pauses, i = [], on
    while i <= off:
        if not act[i]:
            j = i
            while j <= off and not act[j]: j += 1
            if j - i >= 11: pauses.append((i * 0.01, j * 0.01))
            i = j
        else: i += 1
    return on * 0.01, off * 0.01 + 0.01, pauses

files = {}
if MIX:
    for v in MIX["vo"]: files[round(v["at"], 3)] = v["file"]
checks = []
for c in raw:
    pieces = split_line(c["text"], c.get("who", ""))
    total = sum(len(p["text"]) for p in pieces); span = c["t1"] - c["t0"]
    f = files.get(round(c["t0"], 3))
    if f:
        # cue boundaries sit on the real pauses of this clip: each cue starts when its first word is spoken
        on, off, pauses = speech_map(f)
        est, acc = [], 0
        for p in pieces[:-1]: acc += len(p["text"]); est.append(on + (off - on) * acc / total)
        bounds, used = [], set()
        for e in est:
            cand = [(abs((a + b) / 2 - e), k) for k, (a, b) in enumerate(pauses) if k not in used and abs((a + b) / 2 - e) < 1.2]
            if cand: k = min(cand)[1]; used.add(k); bounds.append(pauses[k])
            else: bounds.append((e, e))
        starts = [on] + [b for a, b in bounds]; ends = [a for a, b in bounds] + [off]
        for k, p in enumerate(pieces):
            t0 = c["t0"] + starts[k]; t1 = c["t0"] + ends[k] + (0.3 if k == len(pieces) - 1 else 0.12)
            out.append({"t0": round(t0, 3), "t1": round(t1, 3), "who": p["who"], "lines": p["lines"]})
            checks.append((round(t0, 3), " ".join(p["lines"])[:40], "onset" if k == 0 else "pause"))
    else:
        t = c["t0"]
        for p in pieces:
            d = span * len(p["text"]) / total
            out.append({"t0": round(t, 3), "t1": round(t + d + (0.25 if p is pieces[-1] else 0), 3), "who": p["who"], "lines": p["lines"]})
            t += d
out.sort(key=lambda o: o["t0"])
# never overlap the next cue
for a, b in zip(out, out[1:]):
    if a["t1"] > b["t0"] - 0.02: a["t1"] = round(b["t0"] - 0.02, 3)
json.dump(out, open(sys.argv[2], "w"), ensure_ascii=False, indent=1)
base = sys.argv[3]
def text(c): return "\n".join(([("%s: %s" % (c["who"], c["lines"][0]))] + c["lines"][1:]) if c["who"] else c["lines"])
with open(base + ".srt", "w") as f:
    for i, c in enumerate(out, 1): f.write("%d\n%s --> %s\n%s\n\n" % (i, ts(c["t0"], ","), ts(c["t1"], ","), text(c)))
with open(base + ".vtt", "w") as f:
    f.write("WEBVTT\n\n")
    for i, c in enumerate(out, 1): f.write("%d\n%s --> %s\n%s\n\n" % (i, ts(c["t0"], "."), ts(c["t1"], "."), text(c)))
if checks:
    import random; random.seed(1)
    for c in sorted(random.sample(checks, min(10, len(checks)))): print("  cue %8.3f s  %-6s %s" % (c[0], c[2], c[1]))
# QA: every voiced interval covered by captions (gaps < 0.1 s)
gaps = [c for c in raw if not any(o["t0"] <= c["t0"] + 0.35 and o["t1"] >= c["t0"] + 0.3 for o in out)]   # a cue starts at the first spoken word (the clip may open with up to ~0.3 s of breath)
print("cues %d from %d spoken lines; longest line %d chars; uncovered lines %d" % (len(out), len(raw), max(len(l) for c in out for l in c["lines"]), len(gaps)))
sys.exit(1 if gaps else 0)
