# Extra Neha (narrator) lines: technical depth + a faster channel line. Pace 1.2 except 07 (1.4).
LINES = [
 ("07_channel", 1.4, "It picks the channel. SMS, WhatsApp, a voice call in their language, or a human. And the best time of day."),
 ("t1_record",  1.2, "Under the hood, every borrower lives in one record. Loans, payments, replies, consent."),
 ("t2_model",   1.2, "A learning model scores every channel and every hour for them, using what worked for similar borrowers in their region, and for them personally."),
 ("t3_reserve", 1.2, "Before a message is queued, Aladdin reserves the budget and the contact limit. At the moment of sending, it checks the rules again."),
 ("t4_webhooks",1.2, "Messages go out through each channel's API. Delivery, read and reply receipts come back as webhooks. Duplicates are ignored."),
 ("t5_intent",  1.2, "Replies are understood by rules first, then a small model, then Sarvam's decision model. If it's still unsure, a human decides."),
 ("t6_loop",    1.2, "Every outcome is written back, and the model updates. Same data in, same decision out. Every step, on the record."),
]
if __name__ == "__main__":
    import sys, json, pathlib
    sys.path.insert(0, ".")
    from tts import tts
    out = pathlib.Path(sys.argv[2])
    meta = json.loads((out / "lines.json").read_text())
    meta.pop("11_agent_hi", None)
    for name, pace, text in LINES:
        r = tts(text, "neha", "bulbul:v3", str(out / f"{name}.wav"), lang="en-IN", pace=pace)
        meta[name] = {"speaker": "neha", "model": "bulbul:v3", "pace": pace, "lang": "en-IN", "text": text, "ok": r.startswith("ok")}
        print(name, r[:20])
    (out / "lines.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
