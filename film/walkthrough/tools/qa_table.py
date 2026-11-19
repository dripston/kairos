#!/usr/bin/env python3
"""Markdown table of what is on screen every 5 s (from `render --describe`), for QA.md.

    python3 tools/qa_table.py qa/product_5s.jsonl

v3 grammar: footage (with the real UI composited onto phone and laptop screens), then ONE real component
large on cream (a "card"), with burned-in captions. Shot specs in shots/<name>.json say which screen UI was used.
"""
import json, os, re, sys

def shot(src):
    name = os.path.splitext(os.path.basename(src))[0]
    spec = {}
    p = os.path.join("shots", name + ".json")
    if os.path.exists(p):
        spec = json.load(open(p))
    clip = os.path.basename(spec.get("clip", "")).split("_")[0] if spec.get("clip") else "?"
    has_ui = spec.get("mode", "none") != "none" and spec.get("ui")
    return name, clip, bool(has_ui), spec.get("mode", "none")

rows = ["| t | On screen | Readable size at 1080p | Real product? | Caption |", "|---|---|---|---|---|"]
for line in open(sys.argv[1]):
    d = json.loads(line); parts, sizes, real, cap = [], [], "—", ""
    for L in d["layers"]:
        k = L["kind"]
        if k == "video":
            name, clip, has_ui, mode = shot(L["src"])
            if has_ui:
                parts.append("`%s`: footage %s, real UI on the %s screen%s" % (name, clip, "laptop" if mode == "white" else ("monitor" if mode == "quad" else "phone"), " (push in)" if L.get("push") else ""))
                real = "yes (real UI composited)"
            else:
                parts.append("`%s`: footage %s" % (name, clip)); real = "people (story)" if real == "—" else real
        elif k == "card":
            parts.append("card: capture `%s` frame `%s`" % (L["src"], L["label"]))
            sizes.append("UI 13 px → %d px, 16 px → %d px (zoom %.2f)" % (L["text13px"], L["text16px"], L["zoom"])); real = "yes (real capture)"
        elif k == "term":
            parts.append("terminal (real run)"); sizes.append("mono 28 px → %d px" % round(28 * 1.025 * L["zoom"])); real = "yes (real run)"
        elif k == "win":
            parts.append("capture `%s`" % L["url"]); sizes.append("UI 13 px → %d px" % L["text13px"]); real = "yes (real capture)"
        elif k == "diagram":
            parts.append("architecture diagram"); sizes.append("titles %d px, subtitles %d px" % (L["title_px"], L["sub_px"])); real = "diagram of real components" if real == "—" else real
        elif k in ("callout", "headline"):
            parts.append("%s “%s”" % (k, re.sub(r"\s+", " ", L["text"]).strip())); sizes.append("%d px" % L["px"])
        elif k == "dots":
            parts.append("unit chart: 1,262 dots, 23 lit (seed-42 fixed schedule)"); real = "real counts"
        elif k == "words":
            parts.append("words “%s”" % re.sub(r"<[^>]+>", "", L["text"])); sizes.append("150 px")
        elif k == "tag":
            parts.append("tag “%s”" % L["text"]); sizes.append("32 px tag")
        elif k == "stats":
            parts.append("proof card (≈2×, 0, 49/49)"); sizes.append("150 px numbers, 40/32 px labels"); real = "sourced figures"
        elif k == "logo":
            parts.append("logo lockup"); sizes.append("112 px logo, 56–72 px line, 32 px credits")
        elif k == "fine":
            parts.append("fine print"); sizes.append("32 px")
        elif k == "captions":
            cap = L.get("text", "")
    if cap: sizes.append("caption 46 px")
    rows.append("| %5.1f s | %s | %s | %s | %s |" % (d["t"], "; ".join(parts) or "—", "; ".join(sizes) or "—", real, cap.replace("|", "/") or "—"))
print("\n".join(rows))
