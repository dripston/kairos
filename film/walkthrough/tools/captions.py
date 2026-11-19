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
        if best is None: raise SystemExit("caption needs an explicit split in OVERRIDES: " + text)
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
