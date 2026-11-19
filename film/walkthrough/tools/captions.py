#!/usr/bin/env python3
"""Closed captions from the mix's spoken-line timings.

    python3 tools/captions.py audio/product_cues_raw.json audio/product_cues.json out/kairos_product   (writes .srt and .vtt)

Each spoken line becomes one or more caption cues: at most 2 lines of at most 42 characters, split at phrase
boundaries, timed in proportion to their length inside the line's spoken interval. The speaker tag (Sunita:,
Kairos:, Asha:) is on the first cue of a line; the narrator has none. The burned-in captions use the same file,
so the sidecars match the picture exactly.
"""
import json, re, sys

MAXC, MAXL = 42, 2

def wrap(words, width):
    lines, cur = [], ""
    for w in words:
        if not cur: cur = w
        elif len(cur) + 1 + len(w) <= width: cur += " " + w
        else: lines.append(cur); cur = w
    if cur: lines.append(cur)
    return lines

def split_line(text, who):
    tag = (who + ": ") if who else ""
    # phrase chunks: split after sentence or clause punctuation, then pack chunks into cues of <= 2 lines
    parts = re.split(r"(?<=[.?!:;])\s+|(?<=,)\s+", text.strip())
    cues, cur = [], ""
    for ptxt in parts:
        trial = (cur + " " + ptxt).strip()
        first = not cues
        lines = wrap(((tag if first else "") + trial).split(), MAXC)
        if len(lines) <= MAXL: cur = trial
        else:
            if cur: cues.append(cur)
            cur = ptxt
            while len(wrap(((tag if not cues else "") + cur).split(), MAXC)) > MAXL:  # a single long phrase: hard-split by words
                words = cur.split(); take = []
                while words and len(wrap(((tag if not cues else "") + " ".join(take + [words[0]])).split(), MAXC)) <= MAXL: take.append(words.pop(0))
                cues.append(" ".join(take)); cur = " ".join(words)
    if cur: cues.append(cur)
    out = []
    for i, c in enumerate(cues):
        lines = wrap(((tag if i == 0 else "") + c).split(), MAXC)
        if i == 0 and tag:  # the engine renders the tag itself, bold: strip it from the first line
            lines[0] = lines[0][len(tag):]
        out.append({"text": c, "lines": lines, "who": who if i == 0 else ""})
    return out

def ts(t, sep):
    h, r = divmod(t, 3600); m, s = divmod(r, 60)
    return "%02d:%02d:%02d%s%03d" % (h, m, int(s), sep, int(round((s - int(s)) * 1000)) % 1000)

raw = json.load(open(sys.argv[1])); out = []
extra = json.load(open(sys.argv[4])) if len(sys.argv) > 4 else []
raw = sorted(raw + extra, key=lambda c: c["t0"])
for c in raw:
    pieces = split_line(c["text"], c.get("who", ""))
    total = sum(len(p["text"]) for p in pieces); t = c["t0"]; span = c["t1"] - c["t0"]
    for p in pieces:
        d = span * len(p["text"]) / total
        out.append({"t0": round(t, 3), "t1": round(t + d + (0.25 if p is pieces[-1] else 0), 3), "who": p["who"], "lines": p["lines"]})
        t += d
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
# QA: every voiced interval covered by captions (gaps < 0.1 s)
gaps = [c for c in raw if not any(o["t0"] <= c["t0"] + 0.05 and o["t1"] >= c["t0"] for o in out)]
print("cues %d from %d spoken lines; longest line %d chars; uncovered lines %d" % (len(out), len(raw), max(len(l) for c in out for l in c["lines"]), len(gaps)))
sys.exit(1 if gaps else 0)
