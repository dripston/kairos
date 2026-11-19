#!/usr/bin/env python3
"""Voice the characters' lines exactly as the real screens show them (Sarvam bulbul:v3, en-IN, pace 1.05).
    python3 -I tools/dlg.py <path to .env with the Sarvam key> [ids…]
Writes audio/dlg/<id>.wav and audio/dlg/lines.json. The key is never printed."""
import json, sys, pathlib, importlib.util
HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("sarvam_tts", HERE.parent.parent / "tools" / "sarvam_tts.py")
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
lines = json.load(open(HERE / "dlg_lines.json")); want = sys.argv[2:] or list(lines)
out = HERE.parent / "audio" / "dlg"; out.mkdir(parents=True, exist_ok=True)
meta_p = out / "lines.json"; meta = json.load(open(meta_p)) if meta_p.exists() else {}
for k in want:
    L = lines[k]
    r = mod.tts(L["text"], L["speaker"], "bulbul:v3", str(out / f"{k}.wav"), "en-IN", 1.05)
    meta[k] = dict(L, model="bulbul:v3", pace=1.05, lang="en-IN", ok=r.startswith("ok")); print(k, r[:100])
json.dump(meta, open(meta_p, "w"), ensure_ascii=False, indent=1)
