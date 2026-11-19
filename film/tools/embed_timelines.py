#!/usr/bin/env python3
"""Copy the three cut timelines and the dialogue text into kairos_film.html, so the film also plays from file://.

    python3 film/tools/embed_timelines.py

Reads audio/film_timeline.json (full), film_timeline_short.json (product), film_timeline_tech.json (technical)
and audio/dialogue/lines.json, and rewrites the block between /*TL>*/ and /*<TL*/ in kairos_film.html.
"""
from __future__ import annotations

import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
FILM = os.path.join(HERE, "..")


def load(name):
    return json.load(open(os.path.join(FILM, "audio", name), encoding="utf-8"))


def slim(tl):
    keep = ("line", "chapter", "kind", "role", "start", "end", "text", "en")
    return {"runtime_s": tl["runtime_s"], "markers": tl.get("markers", {}),
            "lines": [{k: r[k] for k in keep if k in r} for r in tl["lines"]]}


def main():
    data = {"cuts": {"full": slim(load("film_timeline.json")), "short": slim(load("film_timeline_short.json")),
                     "tech": slim(load("film_timeline_tech.json"))},
            "dialogue": {k: {"role": v["role"], "text": v["text"], "en": v["en"]}
                         for k, v in load(os.path.join("dialogue", "lines.json")).items()}}
    p = os.path.join(FILM, "kairos_film.html")
    src = open(p, encoding="utf-8").read()
    blob = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    new, n = re.subn(r"/\*TL>\*/.*?/\*<TL\*/", lambda m: "/*TL>*/" + blob + "/*<TL*/", src, flags=re.S)
    assert n == 1, "marker /*TL>*/…/*<TL*/ not found"
    open(p, "w", encoding="utf-8").write(new)
    print("embedded:", {k: (v["runtime_s"], len(v["lines"])) for k, v in data["cuts"].items()})


if __name__ == "__main__":
    main()
