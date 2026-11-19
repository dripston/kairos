# Two-voice conversations for the film. Agent = Sarvam "ritu" (the Kairos agents' real voice), borrower = "pooja".
# Narrator (Neha) never voices a character. Text in Devanagari for natural Hindi; English subtitles in "en".
AGENT, BORROWER = "ritu", "pooja"
LINES = [
 # WhatsApp: borrower writes in, agent verifies with OTP before sharing anything, then sends a payment link
 ("wa_b1", BORROWER, "hi-IN", "नमस्ते, मेरी EMI कब ड्यू है?", "Hi, when is my EMI due?"),
 ("wa_a1", AGENT,    "hi-IN", "नमस्ते सुनीता जी! पहले आपकी पहचान कन्फर्म कर लूँ। आपके रजिस्टर्ड नंबर पर एक OTP भेजा है।", "Hello Sunita ji! First let me confirm it's you. I've sent an OTP to your registered number."),
 ("wa_b2", BORROWER, "hi-IN", "OTP है चार आठ दो नौ।", "The OTP is 4829."),
 ("wa_a2", AGENT,    "hi-IN", "धन्यवाद! आपकी EMI, चार हज़ार दो सौ पचास रुपये, कल ड्यू है। क्या मैं पेमेंट लिंक भेज दूँ?", "Thank you! Your EMI of ₹4,250 is due tomorrow. Shall I send a payment link?"),
 ("wa_b3", BORROWER, "hi-IN", "हाँ, भेज दीजिए।", "Yes, please send it."),
 ("wa_a3", AGENT,    "hi-IN", "लिंक भेज दिया है। पेमेंट होते ही मैं कन्फर्म कर दूँगी।", "Link sent. I'll confirm as soon as the payment lands."),
 # Fake payment proof
 ("fp_b1", BORROWER, "hi-IN", "मैंने पेमेंट कर दिया है, ये रहा स्क्रीनशॉट।", "I've paid, here's the screenshot."),
 ("fp_a1", AGENT,    "hi-IN", "शुक्रिया! बैंक में अभी ये पेमेंट नहीं दिख रहा। मैंने इसे वेरिफिकेशन के लिए टीम को भेज दिया है।", "Thanks! The bank doesn't show this payment yet. I've sent it to the team to verify."),
 # Voice bot call
 ("vc_a1", AGENT,    "hi-IN", "नमस्ते, मैं डेमो फाइनेंस की तरफ़ से बोल रही हूँ। क्या मेरी बात सुनीता जी से हो रही है?", "Hello, I'm calling from Demo Finance. Am I speaking with Sunita ji?"),
 ("vc_b1", BORROWER, "hi-IN", "हाँ, बोलिए।", "Yes, go ahead."),
 ("vc_a2", AGENT,    "hi-IN", "आपकी EMI पिछले हफ़्ते ड्यू थी। क्या आप शुक्रवार तक पेमेंट कर पाएँगी?", "Your EMI was due last week. Could you pay by Friday?"),
 ("vc_b2", BORROWER, "hi-IN", "हाँ, शुक्रवार को कर दूँगी।", "Yes, I'll pay on Friday."),
 ("vc_a3", AGENT,    "hi-IN", "बहुत अच्छा। मैंने शुक्रवार का वादा नोट कर लिया है, और आपको एक रिमाइंडर भेज दूँगी।", "Great. I've noted your promise for Friday, and I'll send you a reminder."),
 # STOP
 ("st_b1", BORROWER, "en-IN", "Please stop messaging me.", "Please stop messaging me."),
 ("st_a1", AGENT,    "en-IN", "Understood. You won't get any more messages from us.", "Understood. You won't get any more messages from us."),
]
if __name__ == "__main__":
    import sys, json, pathlib
    sys.path.insert(0, ".")
    from tts import tts
    out = pathlib.Path(sys.argv[2]); out.mkdir(parents=True, exist_ok=True)
    meta = {}
    for name, spk, lang, text, en in LINES:
        r = tts(text, spk, "bulbul:v3", str(out / f"{name}.wav"), lang=lang, pace=1.05)
        meta[name] = {"role": "agent" if spk == AGENT else "borrower", "speaker": spk, "model": "bulbul:v3", "pace": 1.05, "lang": lang, "text": text, "en": en, "ok": r.startswith("ok")}
        print(name, r[:30])
    (out / "lines.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
