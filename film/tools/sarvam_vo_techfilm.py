# Technical film narration (Neha, pace 1.2). Each line maps to one technical scene.
LINES = [
 ("x01_intro",   "This is how Kairos works, under the hood."),
 ("t1_record",   "Every borrower lives in one record. Loans, payments, replies, consent. Money is kept to the paisa."),
 ("x02_churn",   "Every day, and on every event, the churn engine re-sorts each borrower. Their cohort, their risk, their step in the journey, and the layer of contact. Pay, or say stop, and they leave the journey at once."),
 ("t2_model",    "A learning model scores every channel and every hour for them, using what worked for similar borrowers in their region, and for them personally."),
 ("t3_reserve",  "Before a message is queued, Aladdin reserves the budget and the contact limit. At the moment of sending, it checks the rules again."),
 ("t4_webhooks", "Messages go out through each channel's API. Delivery, read and reply receipts come back as webhooks. Duplicates are ignored."),
 ("x03_voice",   "Voice calls run on Sarvam agents, in eight languages. Every call returns a disposition with a confidence score. Below the threshold, a person reviews it."),
 ("t5_intent",   "Replies are understood by rules first, then a small model, then Sarvam's decision model. If it's still unsure, a human decides."),
 ("x04_cm03",    "Inbound WhatsApp runs on its own state machine. No account details before OTP. Payment claims are checked against the bank, so doctored screenshots and reused references are caught."),
 ("x05_hitl",    "And a person can take control at any moment. Pause a campaign, approve or reject a held call, put a borrower on hold, or take over a conversation. Every action goes through Aladdin, and into the audit log."),
 ("t6_loop",     "Every outcome is written back, and the model updates. Same data in, same decision out. Every step, on the record."),
 ("x06_proof",   "Fifty thousand decisions in about two minutes. Zero cap breaches. And forty-nine out of forty-nine red-team attacks, blocked."),
 ("x07_end",     "Kairos. Built to be trusted with every rupee, and every borrower."),
]
if __name__ == "__main__":
    import sys, json, pathlib, wave, array
    sys.path.insert(0, ".")
    from tts import tts
    out = pathlib.Path(sys.argv[2]); out.mkdir(parents=True, exist_ok=True)
    meta = {}
    for name, text in LINES:
        f = out / f"{name}.wav"
        r = tts(text, "neha", "bulbul:v3", str(f), lang="en-IN", pace=1.2)
        if r.startswith("ok"):
            w = wave.open(str(f)); p = w.getparams(); a = array.array('h', w.readframes(p.nframes)); w.close()
            ch = p.nchannels; sr = p.framerate; n = len(a)//ch; win = int(sr*0.01)
            loud = lambda i: max((abs(x) for x in a[i*ch:(i+win)*ch]), default=0) > 500
            st = 0
            while st < n-win and not loud(st): st += win
            en = n-win
            while en > st and not loud(en): en -= win
            pad = int(sr*0.08); st = max(0, st-pad); en = min(n, en+win+pad)
            w = wave.open(str(f), 'wb'); w.setparams(p); w.writeframes(a[st*ch:en*ch].tobytes()); w.close()
            d = (en-st)/sr
        else:
            d = 0
        meta[name] = {"speaker": "neha", "model": "bulbul:v3", "pace": 1.2, "lang": "en-IN", "text": text, "ok": r.startswith("ok"), "dur": round(d, 3)}
        print(f"{name:12s} {d:5.1f}s {r[:3]}")
    (out / "lines.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    print("total", round(sum(v["dur"] for v in meta.values()), 1))
