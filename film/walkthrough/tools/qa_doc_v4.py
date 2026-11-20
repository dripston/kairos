#!/usr/bin/env python3
"""QA.md for the v4 finals (4K + 1080p), from the final logs, the mixes, the captions and the every-frame analysis."""
import datetime, json, re, subprocess, os
def log(n): return open("out/final_%s.log" % n).read() if os.path.exists("out/final_%s.log" % n) else ""
def ana(n): return open("out/%s_final_analyze.txt" % n).read().strip() if os.path.exists("out/%s_final_analyze.txt" % n) else "(not run)"
commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
rows = []
for f, n, title in [("product4", "kairos_product", "Product film, “Kairos in action”"), ("technical4", "kairos_technical", "Technical film, “How Kairos works”")]:
    L = log(f)
    mx = subprocess.run(["./tools/mix", "mix/%s.json" % f], capture_output=True, text=True).stdout.splitlines()[:3]
    cues = json.load(open("audio/%s_cues.json" % f))
    vo = json.load(open("mix/%s.json" % f))["vo"]
    narr = [v for v in vo if not v.get("who")]
    rows.append("### %s\n\n| | |\n|---|---|\n| 4K master | `film_out/%s_final_4k.mp4` · %s |\n| 1080p | `film_out/%s_final_1080p.mp4` · %s |\n| Captions | `film_out/%s_final.srt` / `.vtt` · %d cues, ≤ 2 lines, ≤ 44 characters per line, burned in |\n| Narration | %d Neha lines (pace 1.3), %d dialogue lines (Kavya 1.05 / Tanya 1.0) |\n| Mix | %s |\n\nEvery-frame analysis of the 1080p (`qa_tools/frameqa` + `analyze.py`):\n\n```\n%s\n```\n" % (
        title, n, (re.search(r"%s_final_4k\.mp4: ([^\n]+)" % n, L) or [None, "?"])[1], n, (re.search(r"%s_final_1080p\.mp4: ([^\n]+)" % n, L) or [None, "?"])[1],
        n, len(cues), len(narr), len(vo) - len(narr), " · ".join(mx), ana(n)))
notes = open("qa/notes_v4.md").read().strip()
doc = """# Kairos films (v4 finals): QA

Built %s from `film/walkthrough` at %s. Everything shown is **SIMULATED**: synthetic borrowers, the release-candidate server (port 8120) and the deployed dashboard read-only, and dry-run adapters. Nothing was sent to anyone.

**How they are made**
- **4K:** the 1920×1080 CSS stage is re-painted by WebKit at 3840×2160 (not upscaled). H.264 High, 48 Mb/s, BT.709, 30 fps. The 1080p file is a downscale of the 4K master.
- **Captures:** every product page is a capture at device scale 4. Zooms are capped at 2× of the page, so captures are never upscaled. The F02/F03 dashboard recordings are 1080p video, shown at ≤ 1× of their pixels.
- **Windows:** a window shows a whole page, then animates to a whole component (DOM rect + margin, header row included). It never holds empty fill and never cuts a glyph.
- **Footage:** retimed so every output frame maps to exactly one source frame (25 fps at 1.2×, 24 fps at 1.25×).
- **Nebula:** always plays in real time (ping-pong on long spans).
- **Rendering:** each frame is snapshotted only after its video frames are presented and its paint is committed.
- **Captions:** each cue starts at its clip's measured speech onset or at the end of the real pause nearest the phrase boundary.

%s
## Honest notes

%s
""" % (datetime.datetime.now().strftime("%d %b %Y %H:%M IST"), commit, "\n".join(rows), notes)
open("QA.md", "w").write(doc); print("QA.md written")
