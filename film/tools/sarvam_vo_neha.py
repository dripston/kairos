LINES = [
 ("01_people",  "en-IN", "Every month, millions of people in India repay a loan. For a phone, a bike, a small shop."),
 ("02_same",    "en-IN", "And every month, lenders remind them the same way. Same message. Same time. To everyone."),
 ("03_cost",    "en-IN", "Most of those messages are ignored. Some annoy people. And every single one costs money."),
 ("04_meet",    "en-IN", "Meet Kairos. An AI that runs loan collections, one person at a time."),
 ("05_looks",   "en-IN", "Every morning, Kairos looks at each borrower. How late they are, how they replied before, and when they usually read their messages."),
 ("06_decides", "en-IN", "Then it decides. Contact them today, or wait."),
 ("07_channel", "en-IN", "If it reaches out, it picks the channel. SMS, WhatsApp, a voice call in their own language, or a human agent. And the best time of day."),
 ("08_aladdin", "en-IN", "Before anything is sent, Aladdin, our safety supervisor, checks it against the lender's rules. Twice."),
 ("09_rules",   "en-IN", "No messages at night. Never more than allowed. And if someone says stop, everything stops. On every channel."),
 ("10_reply",   "en-IN", "When a borrower writes back on WhatsApp, Kairos answers in seconds, in Hindi or in English."),
 ("11_agent_hi","hi-IN", "नमस्ते! आपकी EMI की तारीख कल है। क्या मैं आपको पेमेंट लिंक भेज दूँ?"),
 ("12_verify",  "en-IN", "It confirms who they are before sharing any loan details. And if someone sends a fake payment screenshot, it checks with the bank, and catches it."),
 ("13_learns",  "en-IN", "Every reply, and every payment, teaches Kairos something new. So it gets better, every single day."),
 ("14_operator","en-IN", "And the collections team? They watch the money come in, and approve the few decisions that need a human."),
 ("15_proof",   "en-IN", "In our tests, Kairos recovered about twice as much money for every rupee spent, compared to a fixed schedule."),
 ("16_safe",    "en-IN", "With zero rule breaches. And every safety test passed."),
 ("17_end",     "en-IN", "Kairos. The right message, at the right time. Or none at all."),
]
if __name__ == "__main__":
    import sys, json, pathlib
    sys.path.insert(0, ".")
    from tts import tts
    out = pathlib.Path(sys.argv[2]); out.mkdir(parents=True, exist_ok=True)
    meta = {}
    for name, lang, text in LINES:
        r = tts(text, "neha", "bulbul:v3", str(out / f"{name}.wav"), lang=lang, pace=1.2)
        meta[name] = {"speaker": "neha", "model": "bulbul:v3", "pace": 1.2, "lang": lang, "text": text, "ok": r.startswith("ok")}
        print(name, r[:40])
    (out / "lines.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
