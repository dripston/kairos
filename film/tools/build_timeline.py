#!/usr/bin/env python3
"""Build film/audio/film_timeline.json: every narrator and dialogue line placed on the film's clock.

    python3 film/tools/build_timeline.py

Rule: the first line starts at 1.5 s. Narrator lines are 0.6 s apart; inside a conversation lines follow
each other 0.25 s apart, like a real chat or call; 0.6 s between the narrator and a conversation, and 0.9 s
between two different conversations. +1.2 s before 04_meet, t1_record (Under the hood) and 15_proof; +0.8 s before 08_aladdin (the channel
locks in); +0.6 s before vc_a1 (the phone rings) and 14_operator (out of Under the hood).
The end card holds 3 s after the last line. Narrator = Neha (audio/vo_neha), agent = Ritu and
borrower = Pooja (audio/dialogue).
"""
from __future__ import annotations

import json
import os
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
FILM = os.path.join(HERE, "..")
ORDER = [
    ("problem", ["01_people", "02_same", "03_cost"]),
    ("meet", ["04_meet"]),
    ("looks", ["05_looks", "06_decides"]),
    ("channels", ["07_channel"]),
    ("safety", ["08_aladdin", "09_rules", "st_b1", "st_a1"]),
    ("whatsapp", ["10_reply", "wa_b1", "wa_a1", "wa_b2", "wa_a2", "wa_b3", "wa_a3"]),
    ("call", ["vc_a1", "vc_b1", "vc_a2", "vc_b2", "vc_a3"]),
    ("fake", ["12_verify", "fp_b1", "fp_a1"]),
    ("learns", ["13_learns"]),
    ("hood", ["t1_record", "t2_model", "t3_reserve", "t4_webhooks", "t5_intent", "t6_loop"]),
    ("operator", ["14_operator"]),
    ("proof", ["15_proof", "16_safe", "17_end"]),
]
EXTRA = {"04_meet": 1.2, "08_aladdin": 0.8, "vc_a1": 0.6, "t1_record": 1.2, "14_operator": 0.6, "15_proof": 1.2}


def dur(path):
    with wave.open(path, "rb") as w:
        return w.getnframes() / w.getframerate()


def main():
    nar = json.load(open(os.path.join(FILM, "audio", "vo_neha", "lines.json"), encoding="utf-8"))
    dia = json.load(open(os.path.join(FILM, "audio", "dialogue", "lines.json"), encoding="utf-8"))
    t, prev, lines = 1.5, None, []
    for chapter, keys in ORDER:
        for k in keys:
            is_dia = k in dia
            src = f"audio/dialogue/{k}.wav" if is_dia else f"audio/vo_neha/{k}.wav"
            if prev is not None:
                if is_dia and prev["kind"] == "dialogue":
                    gap = 0.25 if k[:2] == prev["line"][:2] else 0.9
                else:
                    gap = 0.6
                t = prev["end"] + gap + EXTRA.get(k, 0.0)
            d = dur(os.path.join(FILM, src))
            meta = dia[k] if is_dia else nar[k]
            row = {"line": k, "chapter": chapter, "kind": "dialogue" if is_dia else "narrator", "src": src,
                   "speaker": meta["speaker"], "role": meta.get("role", "narrator"),
                   "start": round(t, 3), "end": round(t + d, 3), "dur": round(d, 3),
                   "text": meta["text"], "en": meta.get("en", meta["text"])}
            lines.append(row)
            prev = row
    runtime = round(prev["end"] + 3.0, 3)
    at = {r["line"]: r["start"] for r in lines}
    chapters = {}
    for r in lines:
        chapters.setdefault(r["chapter"], [r["start"], r["end"]])[1] = r["end"]
    out = {"rule": __doc__.split("Rule: ")[1].split("\n\n")[0].replace("\n", " ").strip(),
           "runtime_s": runtime, "lines": lines,
           "chapters": {k: [round(a, 3), round(b, 3)] for k, (a, b) in chapters.items()},
           "markers": {"lift_meet_kairos": round(at["04_meet"] - 0.3, 3), "stop_word": None,
                       "under_the_hood": round(at["t1_record"] - 0.3, 3),
                       "climax_proof": round(at["15_proof"] - 0.3, 3), "final_line": at["17_end"]}}
    # phrase onsets measured from the 09_rules waveform: "...says stop" at 6.0 s, "everything stops" at 6.96 s
    out["markers"]["stop_word"] = round(at["09_rules"] + 6.0, 3)
    out["markers"]["everything_stops"] = round(at["09_rules"] + 6.96, 3)
    out["markers"]["borrower_stop"] = at["st_b1"]
    p = os.path.join(FILM, "audio", "film_timeline.json")
    json.dump(out, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"wrote film/audio/film_timeline.json: {len(lines)} lines, runtime {runtime} s")
    for r in lines:
        print(f"  {r['start']:8.3f} {r['end']:8.3f}  {r['kind'][:3]} {r['speaker']:6s} {r['line']}")
    print(out["markers"])


if __name__ == "__main__":
    main()
