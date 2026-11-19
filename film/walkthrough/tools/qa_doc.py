#!/usr/bin/env python3
"""Assemble QA.md (v3) from the evidence tools/final.sh leaves in qa/, the film data and the mixes.

    python3 tools/qa_doc.py <out dir holding the masters>
"""
import datetime, json, os, re, subprocess, sys

OUTDIR = sys.argv[1] if len(sys.argv) > 1 else "../out"
def table(f): return subprocess.run([sys.executable, "tools/qa_table.py", f], capture_output=True, text=True).stdout.strip()
def log(name):
    p = "qa/%s_final.log" % name
    return open(p).read() if os.path.exists(p) else ""
def grab(text, pat, fmt="{0}", default="not measured"):
    m = re.search(pat, text)
    return fmt.format(*m.groups()) if m else default

def film(name):
    s = open("films/%s.js" % name).read()
    return json.loads(s[s.index("=") + 1:].strip().rstrip(";"))

def longest_no_person_run(name):
    """Base shots in time order: a video clip has people (or a person's hands on a real screen); everything else is a card."""
    F = film(name); shots = []
    for L in F["layers"]:
        k = L["kind"]
        if k in ("video", "card", "dots", "stats", "diagram", "term", "logo") or (k in ("callout", "headline") and L.get("bgCream")):
            if k == "diagram" and L.get("mini"): continue
            shots.append((round(L["t0"], 2), k))
    shots.sort(); seen, seq = set(), []
    for t, k in shots:
        if t in seen: continue
        seen.add(t); seq.append((t, k))
    best, cur, at = 0, 0, None
    for t, k in seq:
        if k == "video": cur = 0
        else:
            cur += 1
            if cur > best: best, at = cur, t
    return best, at

def mix_facts(name):
    m = json.load(open("mix/%s.json" % name)); vo = [v for v in m["vo"] if v["file"].startswith("audio/vo/")]
    words = sum(len(v["text"].split()) for v in vo)
    return len(vo), words

def cc_facts(name, base):
    cues = json.load(open("audio/%s_cues.json" % name))
    longest = max(len(l) + (len(c["who"]) + 2 if c["who"] and i == 0 else 0) for c in cues for i, l in enumerate(c["lines"]))
    lines = max(len(c["lines"]) for c in cues)
    side = all(os.path.exists(os.path.join(OUTDIR, base + e)) for e in (".srt", ".vtt"))
    return "✅ %d cues, 0 uncovered lines, ≤ %d lines, longest line %d chars%s" % (len(cues), lines, longest, ", sidecars written" if side else ", sidecars MISSING")

facts = {"BUILT": datetime.datetime.now().strftime("%d %b %Y %H:%M IST"),
         "COMMIT": subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip(),
         "OUTDIR": "film_out" if "film_out" in os.path.abspath(OUTDIR) else OUTDIR}
for key, name, base in (("P", "product3", "kairos_product"), ("T", "technical3", "kairos_technical")):
    L = log(base)
    facts[key + "_FINISH"] = grab(L, r"%s\.mp4: ([^\n]+)" % base, "{0}")
    facts[key + "_AFINFO"] = grab(L, r"estimated duration: ([0-9.]+)", "{0} s")
    facts[key + "_QA"] = grab(L, r"QA collisions: (\d+)", "{0} collisions")
    fr = grab(L, r"frames sampled (\d+) · empty (\d+) · identical seconds (\d+)", "{1} empty / {2} identical of {0} sampled seconds")
    facts[key + "_EMPTY"] = ("✅ " if re.search(r"empty 0 · identical seconds 0", L) else "⚠️ ") + fr
    enc = grab(L, r"encoded .*?peak ([-0-9.]+) dBFS\s+integrated ([-0-9.]+) LUFS", "after AAC {1} LUFS, sample peak {0} dBFS", "")
    mx = grab(L, r"integrated ([-0-9.]+) LUFS \(target[^)]*\)\s+true peak ([-0-9.]+) dBTP", "mix {0} LUFS, true peak {1} dBTP")
    duck = grab(L, r"music under voice ([-0-9.]+) dB vs in gaps ([-0-9.]+) dB", "; music {0} dB under voice vs {1} dB in gaps", "")
    facts[key + "_LUFS"] = "✅ " + mx + duck + ("; " + enc if enc else "")
    n, w = mix_facts(name); facts[key + "_VO"] = "%d Neha lines, %d words" % (n, w)
    facts[key + "_CC"] = cc_facts(name, base)
    run, at = longest_no_person_run(name)
    facts[key + "_RUN"] = ("✅" if run <= 2 else "⚠️") + " longest run %d (at %.1f s)" % (run, at or 0)

notes = open("qa/notes_v3.md").read().strip() if os.path.exists("qa/notes_v3.md") else "(none recorded)"
doc = open("tools/QA.template.md").read()
doc = doc.replace("{{PRODUCT_TABLE}}", table("qa/kairos_product_5s.jsonl")).replace("{{TECH_TABLE}}", table("qa/kairos_technical_5s.jsonl")).replace("{{NOTES}}", notes)
for k, v in facts.items(): doc = doc.replace("{{%s}}" % k, str(v))
left = re.findall(r"\{\{\w+\}\}", doc)
open("QA.md", "w").write(doc); print("QA.md written", "· unfilled: " + ", ".join(left) if left else "")
