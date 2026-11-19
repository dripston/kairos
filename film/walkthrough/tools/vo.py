#!/usr/bin/env python3
"""Generate the narration lines with Sarvam bulbul:v3 (Neha, pace 1.2).

    python3 -I tools/vo.py <path to .env holding the Sarvam key> [line ids…]

Reads tools/vo_lines.json, writes audio/vo/<id>.wav (44.1 kHz) and audio/vo/lines.json (text + settings).
The key is read from the file and never printed.
"""
import json, os, sys, pathlib, importlib.util
HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("sarvam_tts", HERE.parent.parent / "tools" / "sarvam_tts.py")
sys.argv = [sys.argv[0]] + sys.argv[1:]
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
lines = json.load(open(HERE / "vo_lines.json"))
want = sys.argv[2:] or list(lines)
out = HERE.parent / "audio" / "vo"; out.mkdir(parents=True, exist_ok=True)
meta_p = out / "lines.json"; meta = json.load(open(meta_p)) if meta_p.exists() else {}
for k in want:
    r = mod.tts(lines[k], "neha", "bulbul:v3", str(out / f"{k}.wav"), "en-IN", 1.2)
    meta[k] = {"speaker": "neha", "model": "bulbul:v3", "pace": 1.2, "lang": "en-IN", "text": lines[k], "ok": r.startswith("ok")}
    print(k, r[:120])
json.dump(meta, open(meta_p, "w"), ensure_ascii=False, indent=1)
