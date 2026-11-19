#!/usr/bin/env python3
"""Markdown table of what is on screen every 5 s (from `render --describe`), for QA.md.

    python3 tools/qa_table.py qa/product_5s.jsonl
"""
import json, re, sys
rows = ["| t | On screen | Readable size at 1080p | Real product? |", "|---|---|---|---|"]
for line in open(sys.argv[1]):
    d = json.loads(line); parts, sizes, real = [], [], "—"
    for L in d["layers"]:
        k = L["kind"]
        if k in ("win", "term"):
            what = "terminal (real run)" if k == "term" else "capture `%s`%s" % (L["url"], " · focus" if L.get("focus") else " · wide")
            parts.append(what); real = "yes (real run)" if k == "term" else "yes (real capture)"
            if k == "win":
                sizes.append("UI 13 px → %d px, 16 px → %d px (zoom %.2f)" % (L["text13px"], L["text16px"], L["zoom"]))
            else:
                sizes.append("mono 28 px → %d px" % round(28 * 1.025 * L["zoom"]))
        elif k == "diagram":
            parts.append("architecture diagram"); sizes.append("titles %d px, subtitles %d px" % (L["title_px"], L["sub_px"])); real = "diagram of real components"
        elif k in ("callout", "headline"):
            parts.append("%s “%s”" % (k, re.sub(r"\s+", " ", L["text"]).strip())); sizes.append("%d px" % L["px"])
        elif k == "video":
            parts.append("Mumbai b-roll (Pexels)"); real = "establishing shot"
        elif k == "dots":
            parts.append("unit chart: 1,262 dots, 23 lit (seed-42 fixed schedule)"); real = "real counts"
        elif k == "words":
            parts.append("words “%s”" % re.sub(r"<[^>]+>", "", L["text"])); sizes.append("150 px")
        elif k == "tag":
            parts.append("tag “%s”" % L["text"]); sizes.append("32 px tag")
        elif k == "stats":
            parts.append("proof card (≈2×, 0, 49/49)"); sizes.append("150 px numbers, 40/32 px labels"); real = "sourced figures"
        elif k == "logo":
            parts.append("logo lockup + line + credits"); sizes.append("112 px logo, 56–60 px line, 32 px credits")
        elif k == "fine":
            parts.append("fine print"); sizes.append("32 px")
    rows.append("| %5.1f s | %s | %s | %s |" % (d["t"], "; ".join(parts) or "—", "; ".join(sizes) or "—", real))
print("\n".join(rows))
