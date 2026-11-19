"""Seeded synthetic labelled corpus for the K09 intent stack (SIMULATED data, no customer text).

    generate(seed=1729, per_label=200) -> [{"text", "label", "lang"}]

Templates x slot fills x noise (typos, transliteration variants, fillers, emoji, case), in English
("en"), Hindi in Devanagari ("hi"), Hinglish ("hing") and English call-summary style ("voice").
The training corpus and the held-out test corpus use DIFFERENT seeds; the hand-written gold set
(tests/data/intent_gold.jsonl) is written independently of these templates.
"""
from __future__ import annotations

import random
from typing import Dict, List, Optional, Sequence

from .intent_stack import LABELS

SLOTS: Dict[str, Dict[str, Sequence[str]]] = {
    "en": {
        "d": ["tomorrow", "on the 25th", "by Friday", "on Monday", "next Monday", "on 28th", "day after tomorrow",
              "on 1st", "by the 30th", "this Saturday", "on 5th Dec", "by month end", "in 2 days", "on 26 Nov"],
        "rel": ["father", "mother", "wife", "husband", "son", "daughter", "brother"],
        "s": ["", "", "sir", "madam", "please", "pls"],
        "lang": ["Hindi", "English", "Marathi", "Tamil", "Telugu", "Bengali", "Gujarati", "Kannada"],
    },
    "hing": {
        "d": ["kal", "parso", "25 tarikh ko", "28 ko", "monday ko", "agle somvar", "1 tareekh tak", "30 tak",
              "shukravar ko", "2 din me", "month end tak", "kal shaam tak", "5 tarikh", "aaj shaam"],
        "rel": ["papa", "mummy", "wife", "bhai", "beta", "maa", "pati", "biwi"],
        "s": ["", "", "sir", "ji", "madam", "bhai", "sir ji"],
        "lang": ["hindi", "english", "marathi", "tamil", "gujarati", "bangla", "angrezi"],
    },
    "hi": {
        "d": ["कल", "परसों", "25 तारीख को", "28 तारीख तक", "सोमवार को", "1 तारीख को", "शुक्रवार को", "2 दिन में", "३० तारीख को"],
        "rel": ["पिताजी", "माँ", "पत्नी", "बेटे", "भाई", "पति"],
        "s": ["", "", "सर", "जी", "मैडम"],
        "lang": ["हिंदी", "अंग्रेजी", "मराठी", "गुजराती"],
    },
}
SLOTS["voice"] = SLOTS["en"]

T: Dict[str, Dict[str, List[str]]] = {
    "PAY_NOW": {
        "en": ["paying right now {s}", "ok I am making the payment now", "doing it now", "transferring the amount right away",
               "will pay immediately {s}", "paying now, give me 5 minutes", "I'm on it, paying in 10 mins", "sending the money now"],
        "hing": ["abhi pay kar raha hu {s}", "abhi bhar deta hu", "ruko abhi payment karta hu", "turant kar deta hu payment",
                 "5 min me pay kar dunga", "haan abhi jama kar raha hu", "payment kar raha hu abhi {s}", "abhi ke abhi bhej deta hu"],
        "hi": ["अभी पेमेंट कर रहा हूँ", "तुरंत भुगतान कर देता हूँ {s}", "अभी जमा कर रहा हूं", "अभी पे कर देता हूँ"],
        "voice": ["Borrower said he is paying right now while on the call", "Customer agreed to pay immediately and was making the payment"],
    },
    "PTP": {
        "en": ["I will pay {d} {s}", "will clear the dues {d}", "I promise to pay {d}", "payment will be done {d}", "I'll transfer {d}",
               "can pay {d}", "will make the payment {d} for sure", "I will pay the full amount {d}"],
        "hing": ["{d} pay kar dunga {s}", "{d} de dunga pakka", "{d} tak jama kar dunga", "{d} bhar dungi {s}", "pakka {d} payment karunga",
                 "{d} tak ho jayega payment", "{d} clear kar dunga", "aadha {d} de dunga baki baad me"],
        "hi": ["{d} भर दूंगा {s}", "{d} पेमेंट कर दूंगा", "{d} तक जमा कर दूँगा", "पक्का {d} दे दूंगा"],
        "voice": ["Borrower promised to pay {d}", "Customer agreed to pay {d}", "PTP given for {d}, customer will pay full EMI"],
    },
    "NEEDS_TIME": {
        "en": ["I need some more time {s}", "please give me a few days", "can't pay right now", "short on money this month",
               "will pay after my salary", "need an extension please", "not possible this week, give me time", "I have no money right now"],
        "hing": ["thoda time chahiye {s}", "abhi paise nahi hai", "salary ke baad dunga", "kuch din ka time do {s}", "abhi nahi ho payega",
                 "agle mahine kar dunga", "thodi mohlat de do", "is mahine nahi ho paega sir", "salary aane do fir karta hu"],
        "hi": ["थोड़ा समय चाहिए {s}", "अभी पैसे नहीं हैं", "सैलरी के बाद दूंगा", "कुछ दिन का टाइम दीजिए", "इस महीने नहीं हो पाएगा"],
        "voice": ["Borrower asked for more time, no date given", "Customer requested a few more days to arrange funds",
                  "Customer said will pay after salary, no specific date"],
    },
    "ALREADY_PAID": {
        "en": ["I already paid {s}", "payment done yesterday", "I have paid the EMI", "already cleared the dues",
               "paid it through UPI, screenshot attached", "the amount was deducted from my account", "paid last week check your system"],
        "hing": ["maine pay kar diya hai {s}", "payment ho gaya hai", "kal hi bhar diya", "emi kat gayi hai account se",
                 "jama kar diya {s} check karo", "auto debit ho gaya", "screenshot bhej diya hai", "paise transfer kar diye"],
        "hi": ["मैंने भर दिया है", "पेमेंट हो गया है {s}", "जमा कर दिया है", "भुगतान कर दिया", "पैसे कट गए हैं"],
        "voice": ["Borrower claims payment already made", "Customer said EMI was already paid via UPI"],
    },
    "CALLBACK": {
        "en": ["call me {d}", "please call me back later", "busy now, call me in the evening", "can you call after 6 pm",
               "give me a call tomorrow morning", "call back in an hour {s}"],
        "hing": ["baad me call karo {s}", "shaam ko phone karna", "abhi busy hu call kar lena", "kal call karo", "meeting me hu, baad me call kijiye",
                 "driving kar raha hu baad me phone karo"],
        "hi": ["बाद में कॉल करो", "शाम को फोन कीजिए {s}", "अभी व्यस्त हूँ, कॉल कर लेना", "कल फोन करो"],
        "voice": ["Borrower asked for a callback in the evening", "Customer was busy and requested a call back {d}"],
    },
    "HARDSHIP": {
        "en": ["my {rel} is in hospital", "I lost my job last month", "had an accident, can't work", "{rel} passed away recently",
               "going through a medical emergency", "my business shut down", "I am very sick {s}", "salary not credited for 2 months"],
        "hing": ["{rel} hospital me admit hai", "meri naukri chali gayi", "accident ho gaya tha {s}", "{rel} ki death ho gayi",
                 "tabiyat bahut kharab hai", "dhandha band ho gaya hai", "salary nahi aayi 2 mahine se", "ghar me baadh aa gayi"],
        "hi": ["{rel} अस्पताल में हैं", "मेरी नौकरी चली गई", "एक्सीडेंट हो गया है", "{rel} का देहांत हो गया", "तबीयत खराब है {s}"],
        "voice": ["Borrower reported job loss and cannot pay", "Customer said family member is hospitalised", "Customer mentioned financial hardship due to illness"],
    },
    "DISPUTE": {
        "en": ["this amount is wrong", "I never took this loan", "why so much penalty", "the loan is already closed", "this is a fraud",
               "you are charging extra interest", "I will complain to RBI", "wrong charges on my account {s}"],
        "hing": ["galat amount dikha rahe ho", "maine ye loan nahi liya", "penalty kyu lagaya", "loan pehle hi close ho gaya",
                 "extra charges kyu kaat rahe ho", "rbi me complaint karunga", "noc mil gaya hai fir bhi message", "ye fraud hai"],
        "hi": ["गलत अमाउंट है", "मैंने कोई लोन नहीं लिया", "पेनल्टी क्यों लगाई", "लोन बंद हो चुका है", "यह फ्रॉड है"],
        "voice": ["Borrower disputes the outstanding amount", "Customer denied taking the loan", "Customer contested the penalty charges"],
    },
    "STOP": {
        "en": ["STOP", "stop sending messages", "unsubscribe", "don't message me again", "please stop calling me", "remove my number",
               "no more messages {s}", "leave me alone", "opt out"],
        "hing": ["message mat bhejo", "call band karo {s}", "pareshan mat karo", "bar bar call mat karo", "sms band karo",
                 "mujhe message mat karo", "ye messages band karo"],
        "hi": ["मैसेज मत भेजो", "कॉल बंद करो", "परेशान मत करो {s}", "फोन मत करो"],
        "voice": ["Borrower asked not to be contacted again", "Customer said stop calling and hung up"],
    },
    "WRONG_NUMBER": {
        "en": ["wrong number", "you have the wrong person", "I don't know him", "this is not my number {s}", "no one by that name here",
               "I am not the person you are looking for"],
        "hing": ["galat number hai", "main wo nahi hu", "is naam ka koi nahi hai", "ye mera number nahi hai", "main inko nahi jaanta",
                 "kisi aur ka number hai {s}"],
        "hi": ["गलत नंबर है", "मैं वो नहीं हूँ", "इस नाम का कोई नहीं है", "मेरा नंबर नहीं है"],
        "voice": ["Call answered by someone else, wrong number", "Respondent said the borrower does not use this number"],
    },
    "HUMAN": {
        "en": ["I want to talk to a real person", "connect me to an agent", "are you a bot?", "let me speak with your manager",
               "I need customer care", "talk to someone from the bank {s}"],
        "hing": ["kisi insaan se baat karao", "agent se baat karni hai", "manager se baat karwao", "robot se baat nahi karni",
                 "customer care se baat karao {s}", "kisi senior se baat karni hai"],
        "hi": ["किसी इंसान से बात करवाओ", "मैनेजर से बात कराओ", "कस्टमर केयर से बात करनी है"],
        "voice": ["Borrower requested to speak with a human agent", "Customer asked for a supervisor"],
    },
    "BALANCE_QUERY": {
        "en": ["how much do I owe", "what is my outstanding amount", "how much is pending {s}", "what's the total due",
               "tell me the balance", "how much is left on my loan"],
        "hing": ["kitna baaki hai {s}", "total kitna dena hai", "emi kitni hai", "kitne paise bache hai", "balance batao",
                 "kitni emi pending hai", "due amount kya hai"],
        "hi": ["कितना बाकी है", "कुल कितना देना है {s}", "किस्त कितनी है", "बकाया कितना है"],
        "voice": ["Borrower asked about the outstanding balance", "Customer wanted to know the total due"],
    },
    "LINK_REQUEST": {
        "en": ["send me the payment link", "how can I pay", "please share UPI id", "link is not working", "resend the link {s}",
               "share QR code", "where do I pay"],
        "hing": ["link bhejo {s}", "payment link bhej do", "kaise pay karu", "link nahi khul raha", "upi id do",
                 "gpay number do", "kaha bharna hai", "link expire ho gaya"],
        "hi": ["लिंक भेजो", "पेमेंट लिंक भेजिए {s}", "कैसे भरूं", "यूपीआई आईडी दो"],
        "voice": ["Borrower asked for a payment link over WhatsApp", "Customer wanted to know how to pay"],
    },
    "STATEMENT_REQUEST": {
        "en": ["send me the account statement", "I need my loan statement {s}", "please share the repayment schedule", "send receipt for my payment",
               "need NOC", "share the interest certificate", "give me the breakup of charges"],
        "hing": ["statement bhejo", "loan ka statement chahiye", "receipt bhej do {s}", "noc chahiye", "emi schedule bhejo",
                 "charges ka breakup do"],
        "hi": ["स्टेटमेंट भेजो", "रसीद भेज दीजिए {s}", "एनओसी चाहिए"],
        "voice": ["Borrower requested the account statement", "Customer asked for a payment receipt"],
    },
    "LANGUAGE_CHANGE": {
        "en": ["please message in {lang}", "can you speak in {lang}", "I don't understand English", "reply in {lang} {s}", "change the language"],
        "hing": ["{lang} me bolo", "{lang} me message bhejo", "angrezi samajh nahi aati", "{lang} me baat karo {s}", "bhasha badlo"],
        "hi": ["{lang} में बात करो", "{lang} में लिखो", "अंग्रेजी नहीं आती"],
        "voice": ["Borrower requested the conversation in {lang}", "Customer asked to switch language to {lang}"],
    },
    "ABUSIVE": {
        "en": ["you people are idiots", "stop bothering me you bastards", "shut up", "bloody cheaters", "you are a bunch of thieves"],
        "hing": ["tum log chor ho", "saale pagal ho kya", "kamine log", "dekh lunga tumko", "harami log ho tum", "bewakoof ho kya"],
        "hi": ["कमीने लोग", "साले चोर हो तुम", "बदतमीज हो", "देख लूंगा तुम्हें"],
        "voice": ["Borrower was abusive and used foul language", "Customer threatened the agent"],
    },
    "GREETING": {
        "en": ["hi", "hello {s}", "good morning", "ok thanks", "thank you", "noted", "ok", "who is this?", "yes", "got it {s}"],
        "hing": ["namaste {s}", "theek hai", "ji", "haan ji", "acha", "shukriya", "ok sir", "aap kaun?", "dhanyavad", "hello ji"],
        "hi": ["नमस्ते", "धन्यवाद", "ठीक है {s}", "जी", "हाँ जी", "कौन बोल रहा है"],
        "voice": ["Borrower greeted and said ok", "Customer acknowledged the reminder"],
    },
    "UNKNOWN": {
        "en": ["hmm", "?", "let me see", "what", "maybe", "will see", "not sure", "I'll think about it", "hmm ok let me check",
               "what is this about", "can't say", "...", "sigh"],
        "hing": ["dekhte hai", "kya", "hmm dekhta hu", "pata nahi", "sochta hu", "kya bolu", "dekh lenge", "accha dekhte hai {s}",
                 "kuch samajh nahi aaya", "kya hai ye"],
        "hi": ["देखते हैं", "पता नहीं", "क्या", "सोचता हूँ", "हम्म"],
        "voice": ["Call ended without a clear response", "Customer was unclear about intent", "Borrower gave no clear answer"],
    },
}

_TRANSLIT = {"nahi": ["nhi", "nai", "nahin"], "hai": ["h", "hain", "he"], "kar": ["kr"], "dunga": ["doonga", "dunga"],
             "raha": ["rha"], "payment": ["pymnt", "paymnt"], "please": ["pls", "plz"], "already": ["alredy"],
             "message": ["msg"], "chahiye": ["chaiye"], "paise": ["pese", "paisa"], "thoda": ["thora"], "baad": ["bad"],
             "tomorrow": ["tmrw"], "hu": ["hoon", "hun"], "karo": ["kro"], "statement": ["statment"], "kitna": ["ktna"]}
_EMOJI = ["🙏", "👍", "😔", "😡", "🙂", "!!", "...", "?"]
_LANG_WEIGHTS = [("hing", 0.42), ("en", 0.3), ("hi", 0.18), ("voice", 0.10)]


def _typo(rng: random.Random, w: str) -> str:
    if len(w) < 4 or not w.isascii() or not w.isalpha():
        return w
    i = rng.randrange(1, len(w) - 1)
    op = rng.randrange(4)
    if op == 0:
        return w[:i] + w[i + 1:]                       # drop
    if op == 1:
        return w[:i] + w[i] + w[i:]                    # double
    if op == 2:
        return w[:i] + w[i + 1] + w[i] + w[i + 2:]     # swap
    return w[:i] + w[i] * 3 + w[i + 1:] if w[i] in "aeiou" else w  # elongate a vowel


def _noise(rng: random.Random, text: str) -> str:
    words = text.split()
    out = []
    for w in words:
        lw = w.lower()
        if lw in _TRANSLIT and rng.random() < 0.35:
            w = rng.choice(_TRANSLIT[lw])
        elif rng.random() < 0.06:
            w = _typo(rng, w)
        out.append(w)
    s = " ".join(out)
    r = rng.random()
    if r < 0.15:
        s = s.lower()
    elif r < 0.2:
        s = s.upper() if len(s) < 40 else s
    elif r < 0.3:
        s = s.capitalize()
    if rng.random() < 0.15:
        s = s.rstrip(".?!") + " " + rng.choice(_EMOJI)
    if rng.random() < 0.08:
        s = s.replace(",", "").replace(".", "")
    return " ".join(s.split())


def _fill(rng: random.Random, tpl: str, lang: str) -> str:
    slots = SLOTS[lang]
    s = tpl
    for k, vals in slots.items():
        while "{" + k + "}" in s:
            s = s.replace("{" + k + "}", rng.choice(list(vals)), 1)
    return " ".join(s.split())


def generate(seed: int = 1729, per_label: int = 200) -> List[Dict[str, str]]:
    """per_label utterances for every label (17 labels -> 17 * per_label rows), deterministic for a seed."""
    rng = random.Random(seed)
    rows: List[Dict[str, str]] = []
    for label in LABELS:
        by_lang = T[label]
        for _ in range(per_label):
            x = rng.random()
            acc = 0.0
            lang = "hing"
            for lg, w in _LANG_WEIGHTS:
                acc += w
                if x < acc:
                    lang = lg
                    break
            if lang not in by_lang:
                lang = "en"
            ti = rng.randrange(len(by_lang[lang]))
            text = _fill(rng, by_lang[lang][ti], lang)
            if label not in ("STOP",) and rng.random() < 0.85:   # keep a share of clean STOP keywords
                text = _noise(rng, text)
            rows.append({"text": text, "label": label, "lang": lang, "tpl": f"{label}|{lang}|{ti}"})
    rng.shuffle(rows)
    return rows


# ---------------------------------------------------------------------------------------------
# Training (pure Python, deterministic)
# ---------------------------------------------------------------------------------------------

TRAIN_SEED = 1729          # training corpus
CALIBRATION_SEED = 2718    # temperature fit (held out from training)
TEST_SEED = 9001           # held-out synthetic test set (scripts/eval_intent.py)
MODEL_VERSION = "nb-w12-c24-v1"


CAL_FOLDS = 4


def _fit_counts(rows: List[Dict[str, str]], feats: Optional[List[Dict[str, int]]], labels: List[str], alpha: float,
                max_features: int, min_df: int) -> Dict[str, object]:
    from .intent_stack import features

    li = {lab: i for i, lab in enumerate(labels)}
    feats = feats or [features(r["text"]) for r in rows]
    df: Dict[str, int] = {}
    for f in feats:
        for k in f:
            df[k] = df.get(k, 0) + 1
    keep_set = set(sorted((k for k, n in df.items() if n >= min_df), key=lambda k: (-df[k], k))[:max_features])
    counts: Dict[str, Dict[int, int]] = {}
    docs = [0] * len(labels)
    for r, f in zip(rows, feats):
        ci = li[r["label"]]
        docs[ci] += 1
        for k, n in f.items():
            if k in keep_set:
                row = counts.setdefault(k, {})
                row[ci] = row.get(ci, 0) + n
    return {"class_docs": docs, "features": {k: sorted(v.items()) for k, v in sorted(counts.items())}}


def train_model(seed: int = TRAIN_SEED, per_label: int = 200, alpha: float = 0.3, max_features: int = 9000,
                min_df: int = 2, calibration_seed: int = CALIBRATION_SEED) -> Dict[str, object]:
    """Fit the tier-2 Naive Bayes on generate(seed) and its temperature on generate(calibration_seed).
    Returns the JSON-ready weights dict (same inputs -> byte-identical output)."""
    import hashlib
    import json
    import math

    from .intent_stack import NaiveBayesModel, features

    rows = generate(seed, per_label)
    labels = list(LABELS)
    li = {lab: i for i, lab in enumerate(labels)}
    data: Dict[str, object] = {
        "version": MODEL_VERSION, "seed": seed, "calibration_seed": calibration_seed, "calibration_folds": CAL_FOLDS,
        "per_label": per_label, "corpus_size": len(rows), "labels": labels, "alpha": alpha, "temperature": 1.0,
    }
    data.update(_fit_counts(rows, None, labels, alpha, max_features, min_df))
    docs = data["class_docs"]
    # Temperature scaling, fitted where the model has NOT seen the phrasing: K template folds; for each
    # fold a model is trained without that fold's templates and scored on a separately seeded
    # calibration corpus drawn from the held-out templates only. In-distribution calibration would
    # make NB look (falsely) certain on real, unseen phrasing.
    cal = generate(calibration_seed, max(60, per_label // 2))
    lj = []
    for fold in range(CAL_FOLDS):
        def in_fold(r: Dict[str, str]) -> bool:
            return int(r["tpl"].rsplit("|", 1)[1]) % CAL_FOLDS == fold
        sub = _fit_counts([r for r in rows if not in_fold(r)], feats=None, labels=labels, alpha=alpha,
                          max_features=max_features, min_df=min_df)
        fm = NaiveBayesModel({**data, **sub})  # type: ignore[arg-type]
        lj += [(fm.log_joint(features(r["text"])), li[r["label"]]) for r in cal if in_fold(r)]

    def nll(t: float) -> float:
        tot = 0.0
        for v, y in lj:
            mx = max(v)
            z = sum(math.exp((x - mx) / t) for x in v)
            tot -= (v[y] - mx) / t - math.log(z)
        return tot / len(lj)

    grid = [round(0.5 + 0.25 * i, 2) for i in range(0, 240)]
    best = min(grid, key=lambda t: (round(nll(t), 9), t))
    data["temperature"] = best
    data["calibration_nll"] = round(nll(best), 6)
    body = json.dumps(data["features"], sort_keys=True, separators=(",", ":")).encode("utf-8")
    data["weights_sha256"] = hashlib.sha256(body + repr((alpha, best, docs)).encode()).hexdigest()
    return data
