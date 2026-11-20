#!/usr/bin/env python3
"""v4 voices: Neha narrates, the Kairos agent and the borrower speak the dialogue.

    python3 -I tools/vo_v4.py <path to .env holding the Sarvam key> [line ids…]

Lines live in tools/lines_v4.json: {id: {"text": caption text, "who": "" | "Kairos" | "Sunita", "tts": optional override}}.
The TTS text is made from the caption text by tts_text(): Aladdin is spoken "Alaadin", digits stay digits, and no
hyphen or dash ever reaches the TTS ("6-digit" → "6 digit", "1–30" → "1 to 30", "49/49" → "49 out of 49",
"73%" → "73 percent", "₹7,409" → "7,409 rupees"). Writes audio/v4/<id>.wav (44.1 kHz) and audio/v4/lines.json.
The key is read from the file and never printed.
"""
import importlib.util, json, pathlib, re, sys

NARRATOR = {"speaker": "neha", "pace": 1.3}
AGENT = {"speaker": "kavya", "pace": 1.05}       # the Kairos agent talking to borrowers (user's pick)
BORROWER = {"speaker": "tanya", "pace": 1.0}     # the borrower (user's pick)
MODEL, LANG = "bulbul:v3", "en-IN"

def tts_text(s):
    s = s.replace("Aladdin", "Alaadin")
    s = re.sub(r"₹\s?([0-9][0-9,]*(?:\.[0-9]+)?)", r"\1 rupees", s)
    s = re.sub(r"([0-9]+)\s?%", r"\1 percent", s)
    s = re.sub(r"([0-9]+)/([0-9]+)", r"\1 out of \2", s)
    s = re.sub(r"([0-9])\s?[–—-]\s?([0-9])", r"\1 to \2", s)       # number ranges
    s = re.sub(r"\s[–—]\s", ", ", s)                              # dashes between words
    s = re.sub(r"(\w)-(\w)", r"\1 \2", s)                          # hyphenated words
    return s

def voice_for(who):
    return NARRATOR if not who else (AGENT if who == "Kairos" else BORROWER)

if __name__ == "__main__":
    HERE = pathlib.Path(__file__).resolve().parent
    spec = importlib.util.spec_from_file_location("sarvam_tts", HERE.parent.parent / "tools" / "sarvam_tts.py")
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    lines = json.load(open(HERE / "lines_v4.json"))
    want = sys.argv[2:] or list(lines)
    out = HERE.parent / "audio" / "v4"; out.mkdir(parents=True, exist_ok=True)
    meta_p = out / "lines.json"; meta = json.load(open(meta_p)) if meta_p.exists() else {}
    for k in want:
        L = lines[k]; v = voice_for(L.get("who", "")); t = L.get("tts") or tts_text(L["text"])
        assert not re.search(r"[–—-]", t), "hyphen in TTS text: " + t
        r = mod.tts(t, v["speaker"], MODEL, str(out / f"{k}.wav"), LANG, v["pace"])
        meta[k] = {"speaker": v["speaker"], "pace": v["pace"], "model": MODEL, "lang": LANG, "text": L["text"], "tts": t, "who": L.get("who", ""), "ok": r.startswith("ok")}
        print(k, v["speaker"], r[:3], t[:90])
    json.dump(meta, open(meta_p, "w"), ensure_ascii=False, indent=1)
