import sys, json, pathlib
sys.path.insert(0, ".")
from tts import tts
out = pathlib.Path("vo"); out.mkdir(exist_ok=True)
lines = [
 ("vo01_problem","advait","en-IN",1.05,"Every day, lenders send the same reminder to every borrower."),
 ("vo02_pain","advait","en-IN",1.05,"It's expensive. It's annoying. And it gets ignored."),
 ("vo03_meet","advait","en-IN",0.95,"Meet Kairos."),
 ("vo04_decides","advait","en-IN",1.05,"It decides, for each person, each day, whether to reach out, or wait."),
 ("vo05_channels","advait","en-IN",1.1,"SMS. WhatsApp. Voice. Or a human."),
 ("vo06_aladdin","advait","en-IN",1.05,"Every decision is checked by Aladdin. Twice."),
 ("vo07_stop","advait","en-IN",1.05,"Say stop, and it stops. Everywhere."),
 ("vo08_fake","advait","en-IN",1.05,"Fake payment proof? Caught."),
 ("vo09_learns","advait","en-IN",1.05,"And it learns, live, from every reply."),
 ("vo10_proof","advait","en-IN",1.0,"Twice the money recovered for every rupee spent. Zero rule breaches."),
 ("vo11_end","advait","en-IN",0.95,"Kairos. The right message, at the right time. Or none at all."),
 ("agent01_hi","ritu","hi-IN",1.0,"नमस्ते! आपकी EMI की तारीख कल है। क्या मैं आपको पेमेंट लिंक भेज दूँ?"),
 ("agent02_en","ritu","en-IN",1.0,"Sure, sending your payment link on WhatsApp right now."),
]
res = {}
for name, spk, lang, pace, text in lines:
    r = tts(text, spk, "bulbul:v3", str(out/f"{name}.wav"), lang=lang, pace=pace)
    res[name] = {"speaker": spk, "lang": lang, "pace": pace, "text": text, "status": r[:200]}
    print(name, r[:120])
(out/"lines.json").write_text(json.dumps(res, ensure_ascii=False, indent=1))
