"""K09 tiered, deterministic intent stack: rules -> small model -> prism -> local LLM -> cloud LLM -> abstain.

    IntentStack.from_env().classify("kal de dunga", today=date(2026, 11, 19)) -> StackResult

Reads free text from borrower replies (SMS / WhatsApp / email), CM-03 WhatsApp servicing turns and
free-text voice-call summaries. It only CLASSIFIES; what a label does is decided elsewhere
(K13 exceptions, Ledger.record_stop, CM-03 flow). Each tier runs only when the previous one is unsure.

  1. rules        Deterministic regexes in English, Hindi (Devanagari) and Hinglish, with typo /
                  transliteration canonicalisation and date entities ("kal", "parso", "25 tareekh",
                  "next Monday", "salary ke baad", Devanagari digits). Safety labels (STOP, HARDSHIP
                  incl. self-harm, ABUSIVE) are checked FIRST and, when hit, the answer is final:
                  no lower tier is consulted, so nothing can override a safety hit.
  2. small_model  Multinomial Naive Bayes over word 1-2-grams + character 2-4-grams, pure Python,
                  trained on a seeded synthetic corpus (intent_corpus.py), temperature-calibrated.
                  Weights: kairos_backend/data/intent_nb_v1.json (version + training seed inside).
                  When rules found several candidates, the model only chooses AMONG them.
  2b. prism       Optional sarvam-prism decision model (kairos_backend/sarvam/prism.py), only with a Sarvam
                  key. Same masked input. Asked for the intent (choice over these labels) plus yes/no/unknown
                  on stop / hardship / self-harm / abuse and a confidence score. A prism "yes" on a safety
                  question is a review signal (needs_human); a prism-only STOP is returned as UNKNOWN +
                  safety flag "stop_unconfirmed", so nothing can auto-STOP on it. Schema violations, timeouts
                  and an open circuit breaker fall through to tier 3.
  3. local_llm    Optional Ollama (OLLAMA_HOST, KAIROS_LOCAL_LLM_MODEL). temperature 0, fixed seed,
                  format = strict JSON schema, short timeout, input with digits / names / phones
                  masked. Only when tier 2's top probability is below LOCAL_LLM_BELOW. Auto-detected
                  (GET /api/tags); off when not reachable. Schema violations fall through.
  4. cloud_llm    Optional gpt-6-luna via intent.LunaClient (reasoning none, strict JSON schema, no
                  tools, store false), same masked input. Only when tiers 1-3 are still unsure and
                  OPENAI_API_KEY is configured.
  5. abstain      UNKNOWN, needs_human=True. Never a guess.

Every result: {label, confidence, tier_used, evidence, entities, needs_human, safety_flags}; results
are cached by a hash of the normalised text + reference day, so the same input always gives the same
output within a process (LLM tiers additionally run at temperature 0 / fixed seed).
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import threading
import time
import unicodedata
import urllib.error
import urllib.request
from collections import OrderedDict
from dataclasses import asdict, dataclass, field, replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from . import intent as _legacy

# ---------------------------------------------------------------------------------------------
# Labels
# ---------------------------------------------------------------------------------------------

PAY_NOW = "PAY_NOW"
PTP = "PTP"
NEEDS_TIME = "NEEDS_TIME"
ALREADY_PAID = "ALREADY_PAID"
CALLBACK = "CALLBACK"
HARDSHIP = "HARDSHIP"
DISPUTE = "DISPUTE"
STOP = "STOP"
WRONG_NUMBER = "WRONG_NUMBER"
HUMAN = "HUMAN"
BALANCE_QUERY = "BALANCE_QUERY"
LINK_REQUEST = "LINK_REQUEST"
STATEMENT_REQUEST = "STATEMENT_REQUEST"
LANGUAGE_CHANGE = "LANGUAGE_CHANGE"
ABUSIVE = "ABUSIVE"
GREETING = "GREETING"          # greetings, thanks, "ok", small talk
SMALL_TALK = GREETING          # alias: one label covers both
UNKNOWN = "UNKNOWN"

LABELS: Tuple[str, ...] = (PAY_NOW, PTP, NEEDS_TIME, ALREADY_PAID, CALLBACK, HARDSHIP, DISPUTE, STOP,
                           WRONG_NUMBER, HUMAN, BALANCE_QUERY, LINK_REQUEST, STATEMENT_REQUEST,
                           LANGUAGE_CHANGE, ABUSIVE, GREETING, UNKNOWN)
SAFETY_LABELS: Tuple[str, ...] = (STOP, HARDSHIP, ABUSIVE)

TIERS = ("rules", "small_model", "prism", "local_llm", "cloud_llm", "abstain")

# Stack label -> legacy intent.INTENTS (what IntentClassifier / K13 already understand).
TO_LEGACY: Dict[str, str] = {
    PAY_NOW: _legacy.PTP, PTP: _legacy.PTP, NEEDS_TIME: _legacy.NEEDS_TIME, ALREADY_PAID: _legacy.CLAIMS_PAID,
    CALLBACK: _legacy.CALLBACK, HUMAN: _legacy.CALLBACK, HARDSHIP: _legacy.HARDSHIP, DISPUTE: _legacy.DISPUTE,
    STOP: _legacy.STOP, WRONG_NUMBER: _legacy.WRONG_PERSON, BALANCE_QUERY: _legacy.OTHER,
    LINK_REQUEST: _legacy.OTHER, STATEMENT_REQUEST: _legacy.OTHER, LANGUAGE_CHANGE: _legacy.OTHER,
    ABUSIVE: _legacy.OTHER, GREETING: _legacy.OTHER, UNKNOWN: _legacy.OTHER,
}
FROM_LEGACY: Dict[str, str] = {
    _legacy.PTP: PTP, _legacy.NEEDS_TIME: NEEDS_TIME, _legacy.CLAIMS_PAID: ALREADY_PAID,
    _legacy.CALLBACK: CALLBACK, _legacy.HARDSHIP: HARDSHIP, _legacy.DISPUTE: DISPUTE, _legacy.STOP: STOP,
    _legacy.WRONG_PERSON: WRONG_NUMBER, _legacy.OTHER: UNKNOWN,
}

# Labels whose date entity is kept (a promise / callback / hardship date). PAY_NOW -> today.
_DATED = (PTP, CALLBACK, HARDSHIP, PAY_NOW)

# Thresholds (fixed constants: part of the deterministic contract; change = new STACK_VERSION).
RULE_ACCEPT = 0.85         # a rule answer at or above this is final
MODEL_ACCEPT = 0.70        # small-model top probability needed to answer alone
MODEL_MARGIN = 0.25        # ... and its lead over the runner-up
MODEL_PICK_MIN = 0.20      # when rules gave candidates: the model's pick among them needs this much
LOCAL_LLM_BELOW = MODEL_ACCEPT  # tier 3 only when tier 2's top probability is below this
LLM_ACCEPT = 0.70          # local LLM self-reported confidence needed
PRISM_ACCEPT = 0.80        # prism's own probability for its intent label needed for the answer to be used
PRISM_CONF_CAP = 0.85      # a prism answer never reports more confidence than this
STACK_VERSION = "intent-stack-1"

# Ingestion default (outcomes.record_text_reply / classify_reply). Set from scripts/eval_intent.py:
# the stack beat the current rules on the gold set (see reports/intent_eval.json), so it is on unless
# KAIROS_INTENT_STACK=0. IntentClassifier (CM-03 public API) stays opt-in (use_stack=True).
STACK_DEFAULT_FOR_INGESTION = True


def stack_enabled(default: bool = STACK_DEFAULT_FOR_INGESTION, env: Optional[Dict[str, str]] = None) -> bool:
    v = (os.environ if env is None else env).get("KAIROS_INTENT_STACK")
    if v is None or not v.strip():
        return default
    return v.strip().lower() in ("1", "true", "on", "yes")


# ---------------------------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------------------------

_DEV_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
_ELONG = re.compile(r"([a-z])\1{2,}")

# Token-level typo / transliteration canonicalisation (Latin script only).
_CANON: Dict[str, str] = {}
for _canon, _variants in {
    "nahi": "nhi nai nahin nahee nahii nhin nahy nae",
    "kar": "kr", "karo": "kro", "karna": "krna", "karke": "krke",
    "kar do": "kardo krdo", "kar diya": "kardiya krdiya kardia krdia", "kar dunga": "kardunga krdunga",
    "kar dungi": "kardungi krdungi",
    "diya": "dia diyaa", "please": "pls plz plss plzz pleas plese plis pliz", "dunga": "doonga dungaa",
    "dungi": "doongi", "karunga": "krunga karuga karoonga krunga karunga", "karungi": "krungi karungee",
    "already": "alredy allready alrdy alrady aready alreay", "tomorrow": "tmrw tmr tomoro tommorow tomorow tommorrow 2moro tomarrow",
    "payment": "pymnt paymnt pmt paymt payement paymet pament", "salary": "salry sallary selary salari",
    "chahiye": "chaiye chahie chaahiye chiye chahiyee chaye", "raha": "rha", "rahi": "rhi", "rahe": "rhe",
    "me": "mein mei", "paise": "pese paisa pesa paisay pase paisee", "thoda": "thora thoda",
    "message": "msg mesage messge msj", "messages": "msgs mesages", "you": "u", "your": "ur",
    "thanks": "thx thanx thnx thnks thanq", "thank you": "thanku thankyou tq", "hello": "hlo helo hallo",
    "hi": "hii hiii", "number": "numbr nmbr nambar numbar nmber", "galat": "galt glt galath",
    "abhi": "abi abhie abhee", "bhejo": "bejo bhjo bhejoo", "bhej do": "bhejdo", "link": "linkk lnk",
    "statement": "statment stmt statemnt stetment", "balance": "balence balanc blance", "hospital": "hospitl hsptl aspatal aspataal haspatal",
    "accident": "acident accidnt axident", "call": "cal cll kol", "callback": "callbak", "tarikh": "tareekh tarik taarikh tareek tarikh",
    "baad": "bad", "bata": "btao", "batao": "btao bataoo", "kitna": "ktna kitnaa", "baat": "bat", "hai": "h hain he",
    "theek": "thik thek", "acha": "accha achha acchha", "okay": "okk okey", "sir": "sirr sirji", "kal": "kl",
    "parso": "parson parsoon parsun parsho", "karenge": "krenge", "denge": "dege", "bhar": "bhr", "jama": "jma",
    "unsubscribe": "unsubscibe unsubcribe", "emi": "e.m.i", "naukri": "nokri naukari", "bimar": "beemar bimaar",
}.items():
    for _v in _variants.split():
        _CANON[_v] = _canon
_CANON.pop("thoda", None)
# "hai/h/he" collapse would break English ("he"); keep only the safe Hinglish forms.
for _amb in ("he", "bat", "bad", "me", "u"):
    _CANON.pop(_amb, None)
_CANON["u"] = "you"
_TOKEN = re.compile(r"[a-z0-9']+|[^\sa-z0-9']")


def normalize(text: Optional[str]) -> str:
    """Cache-key normal form: NFKC, lower case, Devanagari digits -> ASCII, whitespace collapsed."""
    s = unicodedata.normalize("NFKC", text or "").translate(_DEV_DIGITS).lower()
    return " ".join(s.split())


def canonical(text: Optional[str]) -> str:
    """normalize() + elongation squash ("pleeease" -> "please") + typo / transliteration map."""
    s = _ELONG.sub(r"\1", normalize(text))
    s = re.sub(r"[a-z0-9']+", lambda m: _CANON.get(m.group(0), m.group(0)), s)
    return " ".join(s.split())


def text_hash(text: Optional[str], today: date) -> str:
    return hashlib.sha256(f"{normalize(text)}|{today.isoformat()}|{STACK_VERSION}".encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------------------------
# Tier 1: rules
# ---------------------------------------------------------------------------------------------

_SELF_HARM = "SELF_HARM"
_PROMISE = "_PROMISE"  # internal: "will pay" -> PTP with a date, NEEDS_TIME without one

# (rule id, label, pattern, weak). Patterns run on canonical() text. Devanagari patterns avoid \b
# (combining vowel signs are not \w). Weak rules drop out when another label has a strong hit.
_RAW_RULES: List[Tuple[str, str, str, bool]] = []


def _r(label: str, prefix: str, pats: Sequence[str], weak: bool = False) -> None:
    for i, p in enumerate(pats):
        _RAW_RULES.append((f"{prefix}.{i}{'w' if weak else ''}", label, p, weak))


# -- safety ---------------------------------------------------------------------------------------
_r(STOP, "stop.en", [
    r"\bstop (sending|texting|messaging|calling|contacting|harass\w*|bugging|spamming|these|this|all|it|now|the messages)\b",
    r"\b(do not|don't|dont|never) (call|message|text|contact|disturb|whatsapp|email|sms|ping)\b",
    r"\bleave me alone\b", r"\bremove (my|me|this) (number|from)\b", r"\bunsubscribe\b", r"\bopt[ -]?out\b",
    r"\bno more (messages|calls|sms|texts|emails|reminders)\b", r"\b(i will|i'll|will) block\b",
    r"^\W*stop\W*$", r"^\W*stop\b",
])
_r(STOP, "stop.hing", [
    r"\b(message|messages|call|calls|sms|whatsapp|mail|email|phone|reminder)s? (mat|na|nahi) (karo|kare|karna|bhejo|bhejna|kijiye|bhejiye|karein|karo please)\b",
    r"\b(pareshan|disturb|tang) (mat|na|nahi) (karo|kare|karna|kijiye|karein)\b",
    r"\bbar bar (call|message|phone) (mat|na|band)\b", r"\bmat bhejo\b", r"\bbhejna band\b",
    r"\b(message|messages|call|calls|sms|messaging|calling|phone) band (karo|kar do|kare|karein|kijiye|kardo)\b",
    r"\b(band karo|band kar do) (ye|yeh|message|call|sms|messages|calling)\b",
])
_r(STOP, "stop.hi", [
    r"(मैसेज|मेसेज|संदेश|कॉल|काल|फोन|फ़ोन|एसएमएस)\s*(मत|ना|न)\s*(करो|करें|भेजो|भेजें|करना|कीजिए|भेजिए)",
    r"(परेशान|तंग)\s*(मत|ना|न)\s*(करो|करें|कीजिए)",
    r"(मैसेज|मेसेज|कॉल|फोन|फ़ोन)\s*(भेजना\s*)?बंद\s*(करो|करें|कर दो|कीजिए)", r"मत भेजो",
])
_r(_SELF_HARM, "selfharm.en", [
    r"\b(kill|killing|hang|hanging) myself\b", r"\bend (my|it) (life|all)\b", r"\bsuicid\w*\b", r"\bwant to die\b",
    r"\b(rather|better) (be )?dead\b", r"\bno reason to live\b", r"\btake my (own )?life\b", r"\bend it all\b",
])
_r(_SELF_HARM, "selfharm.hing", [
    r"\b(khudkushi|khudkhushi|aatmhatya|atmahatya|atmhatya)\b",
    r"\b(mar|marr) (jaunga|jaungi|jaaunga|jaaungi|jau|jaun|jaana|jana|jaenge|jayenge|jaaenge)\b",
    r"\bjaan de (dunga|dungi|denge|du|doon)\b", r"\b(zeher|zahar|jahar) (kha|pee|pi)\w*\b", r"\b(faansi|fansi|phansi)\b",
    r"\bjeene ka (mann|man) nahi\b", r"\bmarne (ka|ke) (alawa|siva|siwa|siwaay)\b",
    # added 2026-11-19 (Sarvam-models eval: the held-out set missed this phrasing; safety rules only grow)
    r"\b(ab )?jeena nahi (chahta|chahti|chahte)\b", r"\bzindagi (khatam|khtm) kar\w*\b",
])
_r(_SELF_HARM, "selfharm.hi", [
    r"आत्महत्या|खुदकुशी|ख़ुदकुशी|मर जा(ऊं|ऊँ|उं)(गा|गी)|जान दे (दूंगा|दूँगा|दूंगी|दूँगी)|फांसी|फाँसी|(ज़हर|जहर) खा|जीने का मन नहीं|जीना नहीं चाहत",
])
_r(HARDSHIP, "hardship.en", [
    r"\baccident\b", r"\bhospital\w*\b", r"\badmit(ted)?\b", r"\bicu\b", r"\bsurgery\b", r"\boperation\b",
    r"\billness\b", r"\b(very |seriously )?(sick|ill)\b", r"\bmedical\b", r"\bcancer\b", r"\btreatment\b",
    r"\b(dialysis|chemo\w*|covid|dengue|typhoid|malaria|fracture|paralysis|paralys\w*|heart attack|stroke)\b",
    r"\blost (my )?(job|work|business)\b", r"\blaid off\b", r"\blay ?off\b", r"\bunemployed\b", r"\bjobless\b",
    r"\b(passed away|death|died|funeral)\b", r"\b(father|mother|dad|mom|husband|wife|son|daughter|brother|sister|papa|mummy) (has |had )?expired\b",
    r"\blost my (father|mother|dad|mom|husband|wife|son|daughter|brother|sister|parents?|child)\b", r"\bsalary (cut|delayed|not (paid|credited|received))\b", r"\bpay cut\b",
    r"\b(flood|fire|earthquake|cyclone)\b", r"\bemergency\b",
    r"\bfinancial (crisis|problem|problems|difficulty|difficulties|trouble|hardship)\b", r"\bhardship\b",
    r"\b(business|shop|company) (closed|shut|failed|collapsed|is closed|has closed)\b", r"\bin (huge )?loss\b",
])
_r(HARDSHIP, "hardship.hing", [
    r"\b(ilaaj|ilaj)\b", r"\btabiyat (kharab|thik nahi|theek nahi|bigad)\w*\b", r"\bbimar\w*\b", r"\bb[ae]+ma+r\w*\b",
    r"\b(naukri|job|kaam) (chali|chala|chhut|chut|gayi|gaya|gai|nahi rahi|chhoot|chhooth|lost|gone)\w*\b",
    r"\bberozgar\w*\b", r"\b(guzar|gujar) (gay[ae]|gayi)\b", r"\b(maut|nidhan|dehant|swargwas)\b", r"\bexpire ho (gaye|gayi|gaya)\b",
    r"\bsalary (nahi|nhi) (aayi|ayi|mili|aa rahi|aai|milegi)\b", r"\bsalary (ruk|band|stop|cut|kat)\w*\b",
    r"\b(baadh|aag lag)\w*\b", r"\b(business|dhandha|dhanda|dukaan|dukan|company) (band|thapp|thap|doob|dub|chaupat|manda|mandaa|loss)\w*\b",
    r"\bloss ho (gaya|gya)\b", r"\b(paise|paison) ki (tangi|kami|dikkat)\b",
    r"\b(company|boss|office|malik|seth|kampani) ne (nikaal|nikal|nikala|hata|bahar kar)\w*\b",
    r"\b(naukri|job|kaam) se (nikaal|nikal|nikala|hata)\w*\b",
])
_r(HARDSHIP, "hardship.hi", [
    r"अस्पताल|हॉस्पिटल|हास्पिटल|बीमार|इलाज|ऑपरेशन|एक्सीडेंट|दुर्घटना|नौकरी (चली|छूट|नहीं रही|गई)|(गुज़र|गुजर) (गए|गई|गया)|देहांत|निधन|मृत्यु|मौत",
    r"बाढ़|आग लग|तबीयत (खराब|ठीक नहीं)|(सैलरी|वेतन|तनख्वाह) (नहीं|रुक)|(पैसों|पैसे) की (तंगी|कमी)|आर्थिक तंगी|(कारोबार|धंधा|दुकान) बंद",
])
_r(ABUSIVE, "abuse.en", [
    r"\b(fuck\w*|f\*+k|fck|fuk|shit|bastard\w*|bitch\w*|asshole\w*|idiot\w*|stupid|moron\w*|scum|crook\w*|bloody (fool|idiot)\w*)\b",
    r"\b(cheaters|thieves|loan sharks?|blood ?suckers?|goons?)\b", r"\bi will (kill|beat|hurt|slap) you\b", r"\bshut up\b",
])
_r(ABUSIVE, "abuse.hing", [
    r"\b(bc|mc|bkl|bsdk|chutiy\w*|madarchod\w*|behenchod\w*|bhenchod\w*|bhosd\w*|gaand\w*|gandu|harami\w*|haramkhor\w*|kamin[ae]\w*|kutt[ae]|kutiya|saal[ae]|suar|ullu ke|bewakoof|bewkoof|nalayak|lafang\w*|tharki|randi\w*|lavd\w*|lawd\w*|jhaant\w*|chor log|chor ho|chor hai)\b",
    r"\b(tumko|tujhe|tumhe|tumhein|aapko|tum logon ko|tum logo ko) (dekh lunga|dekh lenge|maar (dunga|dalunga|denge)|tod dunga|thok dunga)\b",
    r"\b(dekh lunga|dekh lenge) (tumko|tujhe|tumhe|tumhein|tum logon ko|tum logo ko)\b", r"\b(maar (dunga|dalunga|denge)|jaan se maar\w*|tod dunga|thok dunga)\b",
])
_r(ABUSIVE, "abuse.hi", [r"कमीन|हरामी|कुत्त|साले|साला|गधे|उल्लू के|बदतमीज|मादरचोद|बहनचोद|भोसड|चूतिय|भड़व|चोर हो|चोर हैं|देख लूंगा|देख लूँगा"])

# -- non-safety -----------------------------------------------------------------------------------
_r(WRONG_NUMBER, "wrong.en", [
    r"\bwrong (number|no\.?|person|guy|contact|numbr)\b", r"\bnot my (number|loan account)\b",
    r"\bno one (by|named|with) (that|this) name\b", r"\b(i|we) (do not|don't|dont) know (him|her|this person|them|who)\b",
    r"\b(i am|i'm|im) not (him|her|the person|that person|mr|mrs)\b", r"\bno such person\b", r"\byou have the wrong\b",
    r"\bthis number (belongs|is not|isn't)\b", r"\b(he|she) (does not|doesn't) (use|have) this number\b",
])
_r(WRONG_NUMBER, "wrong.hing", [
    r"\bgalat (number|no\.?|insaan|aadmi|bande|banda|person|vyakti|jagah)\b", r"\b(ye|yeh|is) (mera|meri) (number|no\.?) nahi\b",
    r"\bmera number nahi\b", r"\b(main|mai|me) (wo|woh|vo|voh) nahi (hoon|hu|hun|hai)\b", r"\bis naam ka koi nahi\b",
    r"\b(koi|kisi) (aur|or|dusre) ka number\b", r"\b(main|mai|me|hum) (inko|unko|isko|usko|is aadmi ko|in ko|un ko) nahi (jaanta|janta|jaante|jante|jaanti|janti)\b",
    r"\b(ye|yeh) (number|no\.?) (unka|uska|mera) nahi\b",
])
_r(WRONG_NUMBER, "wrong.hi", [r"(गलत|ग़लत|रॉन्ग|रोंग) नंबर|मेरा नंबर नहीं|मैं (वो|वह) नहीं|इस नाम का कोई नहीं|(इन्हें|उन्हें|इसको|उसको) नहीं जानता"])
_r(DISPUTE, "dispute.en", [
    r"\bdispute\w*\b", r"\b(wrong|incorrect|extra|excess|false) (amount|charges?|emi|bill|penalty|interest|entry|fee)\b",
    r"\b(amount|charges?|emi|penalty|bill) (is )?(wrong|incorrect)\b", r"\bnever (took|taken|applied for|borrowed)\b",
    r"\bnot my loan\b", r"\bi (did not|didn't|never) (take|took|apply)\b", r"\b(fraud|scam|fake)\b", r"\bovercharg\w*\b",
    r"\bwhy (so much|extra|this|these) (charge|charges|penalty|interest|amount)\b",
    r"\b(loan|account|a/c|emi) (is )?(already )?(closed|foreclosed|settled)\b", r"\bcomplain\w* (to|in|with) (rbi|ombudsman|consumer|police|bank)\b",
    r"\b(consumer court|legal notice|lawyer|advocate|ombudsman)\b", r"\bharass\w*\b",
])
_r(DISPUTE, "dispute.hing", [
    r"\bgalat (amount|charge|charges|emi|penalty|bill|entry|interest|byaj)\b", r"\b(amount|charge|charges|emi|penalty) galat\b",
    r"\b(maine|mene|humne) (ye |yeh |koi |aisa )?loan (nahi|kabhi nahi) (liya|li|liya hai)\b",
    r"\b(penalty|late fee|charge|charges|byaj|interest|bounce charge) (kyu|kyun|kyon|kis baat)\b", r"\b(kyu|kyun|kyon) (laga|lagaya|kata|kaata|kaat)\w*\b",
    r"\b(loan|account|emi) (pehle hi |already )?(close|band|settle|foreclose|preclose) ho (gaya|gya|chuka)\b", r"\bnoc (mil gaya|mila|aa gaya|le liya|mil chuka)\b",
    r"\b(complaint|complain) (karunga|karungi|karenge|kar dunga|darj)\b", r"\brbi (me|ko) complaint\b", r"\bpolice (complaint|me|ko)\b",
])
_r(DISPUTE, "dispute.hi", [r"(मैंने|मैने) (कोई |ये |यह )?लोन (नहीं|कभी नहीं) लिया|गलत (राशि|अमाउंट|चार्ज|पेनल्टी)|फ्रॉड|धोखा|शिकायत|पेनल्टी क्यों|लोन बंद हो (गया|चुका)"])
_r(ALREADY_PAID, "paid.en", [
    r"\balready (paid|done|cleared|deposited|transferred|made the payment|made payment)\b",
    r"\b(i|we|have|has|had) (have |has |already |just )*paid\b", r"\bpaid (it|already|the|yesterday|today|on|last|this|my|full|emi|via|through|using|in full)\b",
    r"\bhas been paid\b", r"\bpayment (done|made|completed|complete|successful|cleared|is done|has been made|went through)\b",
    r"\b(auto ?debit|nach|ecs|mandate|emi) (was |got |has been )?(deducted|debited|cut|processed)\b", r"\bdeducted from (my )?account\b",
    r"\b(screenshot|receipt|utr|transaction id|reference number|ref no) (attached|sent|shared|is attached)\b",
])
_r(ALREADY_PAID, "paid.hing", [
    r"\bpayment (kar di(ya)?|ho gay[ai]|ho gya|ho chuka|ho chuki|bhej di(ya)?|kar chuka|kar chuki)\b", r"\bpaid (hai|ho gaya|ho chuka|kar diya|kiya hai)\b",
    r"\b(pay|bhar|jama|transfer|bhej|de|chuka|clear|pay kar|bhar|kar) (kar )?(diya|di|diye|chuka|chuki|chuke|diya hai)\b",
    r"\b(kar|kr) (diya|di) (hai|tha)?\s*payment\b", r"\bemi (bhar|jama|de|pay kar|kat) (di|diya|diye|gayi|gai|gaya)\b",
    r"\b(paise|paisa|emi|amount|payment) (kat|cut) (gaye|gaya|gayi|chuke|chuka)\b", r"\b(auto ?debit|nach|ecs) (ho gaya|hua|se kat)\b",
    r"\b(screenshot|receipt|utr|reference) (bheja|bhej diya|bhej di|send kiya|share kiya|bheji)\b",
])
_r(ALREADY_PAID, "paid.hi", [r"(भर|जमा कर|पेमेंट कर|भुगतान कर|चुका|ट्रांसफर कर) (दिया|दी|दिए)|(पेमेंट|भुगतान) हो (गया|गई|चुका)|पैसे भेज दिए|कट (गया|गए)"])
_r(PAY_NOW, "paynow.en", [
    r"\b(paying|pay|will pay|i'll pay|making (the )?payment|transferring|sending (the )?(money|payment|amount)|doing (it|the payment)) (it )?(right )?now\b",
    r"\b(pay|paying|payment|transfer\w*|deposit\w*)\b.{0,25}\b(right now|right away|immediately|asap|in \d+ (min|mins|minutes))\b",
    r"\b(right now|right away|immediately)\b.{0,15}\b(pay|paying|payment|transfer)\b", r"\bpaying (it )?(right )?now\b",
    r"\b(on it|doing it now)\b",
])
_r(PAY_NOW, "paynow.hing", [
    r"\babhi\b(?! (nahi|na|mat|tak))(?=.{0,30}\b(pay|payment|bhar|jama|transfer|bhej|karta|karti|deta|deti|kar raha|kar rahi|kar dunga|kar deta|kar deti|bharta|bharti)\b)",
    r"\b(turant|fauran|abhi ke abhi|isi waqt|thodi der me|\d+ (min|minute|minat) me)\b(?=.{0,30}\b(pay|payment|bhar|jama|transfer|kar|de)\w*)",
    r"\b(pay|payment|bhar|jama|transfer) kar (raha|rahi|rahe) (hu|hoon|hun|hai)\b",
])
_r(PAY_NOW, "paynow.hi", [r"(अभी|तुरंत)\s*(पेमेंट|भुगतान|भर|जमा|पे|ट्रांसफर)|(पेमेंट|भुगतान) कर (रहा|रही) (हूँ|हूं|हु)"])
_r(_PROMISE, "promise.en", [
    r"\b(i|we|he|she)? ?(will|shall|'ll|am going to|going to|gonna) (pay|clear|deposit|transfer|settle|make (the )?payment|arrange)\b",
    r"\bwill pay\b", r"\bpromise\w*\b", r"\bpaying (on|by|tomorrow|today|this|next)\b", r"\bagreed to pay\b", r"\bcan pay (on|by|tomorrow|after)\b",
    r"\b(pay|payment) (by|on|before) (the )?\d",
])
_r(_PROMISE, "promise.hing", [
    r"\b(kar|de|bhar|jama|chuka|bhej|pay kar|clear kar|transfer kar) (dunga|dungi|denge|dega|degi|dege|doonga)\b",
    r"\b(pay|payment|jama|bhugtan) (karunga|karungi|karenge|kar dunga|kar dungi|kar denge|ho jayega|ho jaega|ho jaayega)\b",
    r"\b(bharunga|bharungi|bharenge|chukaunga|chukaungi|chukayenge)\b", r"\bpakka\b", r"\bkar (paunga|paaunga|paungi)\b",
])
_r(_PROMISE, "promise.hi", [r"(दूंगा|दूँगा|दूंगी|दूँगी|दे देंगे|भर देंगे|करूंगा|करूँगा|करूंगी|करूँगी|करेंगे|पक्का)"])
_r(NEEDS_TIME, "time.en", [
    r"\b(more|some|extra|additional) time\b", r"\bneed (some |more |a little |a few |few |little )*(time|days)\b",
    r"\bgive me (some |more |a little |a few )*(time|days)\b", r"\b(some|few|couple of|a few) (more )?days\b",
    r"\b(can't|cannot|can not|unable to|not able to) pay (now|right now|today|this month|abhi|yet|at the moment|immediately)\b",
    r"\b(short|tight) (on|of) (money|cash|funds)\b", r"\bno (money|funds|cash) (now|right now|currently|at the moment)?\b",
    r"\bafter (my )?(salary|payday)\b", r"\bwhen (i get|my) salary\b", r"\b(extension|postpone|defer)\w*\b", r"\bwait (for )?(some|few|a few|couple)\b",
])
_r(NEEDS_TIME, "time.hing", [
    r"\b(thoda|thodi|kuch|aur|zyada|jyada) (time|samay|waqt|din|days|mohlat)\b", r"\b(time|samay|waqt) (chahiye|do|dijiye|de do|milega|de dijiye)\b",
    r"\bmohlat\b", r"\bhaath (tang|tight|khali)\b", r"\babhi (nahi|paise nahi|paisa nahi)\b", r"\b(abhi|filhal|abhi tak) (paise|funds) (nahi)\b", r"\bpaise (nahi) (hai|h|hain)\b",
    r"\b(aaj|kal|abhi|is mahine) (nahi|na)\b", r"\b(nahi) (ho )?(payega|paega|payegi|sakta|sakti|sakega|sakenge|hoga|paunga|paaunga)\b",
    r"\bsalary (ke baad|aane (pe|par|ke baad|do)|aayegi tab|milne (pe|par|ke baad)|milte hi|credit hone)\b", r"\bkuch din\b", r"\bfew days\b",
])
_r(NEEDS_TIME, "time.weak", [r"\bnext (week|month)\b", r"\bagl[ea] (hafte|hafta|mahine|mahina|week|month)\b", r"\bbaad me\b", r"\blater\b"], weak=True)
_r(NEEDS_TIME, "time.hi", [r"(थोड़ा|थोड़ी|कुछ) (समय|टाइम|दिन|मोहलत)|(समय|टाइम) (चाहिए|दो|दीजिए)|मोहलत|अभी पैसे नहीं|पैसे नहीं (है|हैं)|सैलरी (के बाद|आने)|नहीं हो पाएगा|नहीं कर (पाऊंगा|पाऊँगा)"])
_r(NEEDS_TIME, "time.hi.weak", [r"बाद में|अगले (हफ्ते|महीने)"], weak=True)
_r(CALLBACK, "callback.en", [
    r"\bcall (me|back)\b", r"\bcallback\b", r"\bring me\b", r"\bgive (me )?a (call|ring)\b",
    r"\b(can|could|please) (you )?call\b", r"\bcall (at|after|before|tomorrow|in the evening|in the morning|later|on)\b",
])
_r(CALLBACK, "callback.hing", [
    r"\bcall (karo|kariye|kare|karein|kar do|kijiye|kar lena|kar lo|kar lijiye|karna)\b", r"\b(phone|fone) (karo|kariye|kare|karein|kijiye|kar do|kar lena)\b",
    r"\b(shaam|subah|dopahar|raat) (ko|me)? ?(call|phone)\b",
])
_r(CALLBACK, "callback.weak", [r"\b(busy|in a meeting|meeting me|driving|gaadi chala raha)\b"], weak=True)
_r(CALLBACK, "callback.hi", [r"(कॉल|काल|फोन|फ़ोन) (करो|करें|कीजिए|कर लेना|बैक|करना)|कॉल बैक"])
_r(HUMAN, "human.en", [
    r"\b(talk|speak|chat|connect)( me)? (to|with) (a |an |your |some |the )?(real )?(agent|person|human|executive|manager|officer|someone|somebody|representative|supervisor|customer care|team leader|senior)\b",
    r"\breal (person|human)\b", r"\bnot (a |an )?(bot|robot|machine)\b", r"\b(are you|is this) (a )?(bot|robot|machine|human)\b",
    r"\bcustomer (care|support|service)\b", r"\bhuman\b",
])
_r(HUMAN, "human.hing", [
    r"\b(kisi|agent|aadmi|insaan|insan|manager|executive|officer|senior|banda|bande)\w* se baat\b",
    r"\bbaat (karni|karna|karao|karwao|karwa do|kara do|karwaiye)\b", r"\b(asli aadmi|real banda)\b", r"\b(robot|bot|machine) se\b",
])
_r(HUMAN, "human.hi", [r"(किसी|एजेंट|इंसान|मैनेजर|अधिकारी|आदमी) से बात|बात (करवाओ|कराओ|करवाइए)|कस्टमर केयर"])
_r(BALANCE_QUERY, "balance.en", [
    r"\bhow much (do i|i|is|to|should i|have to|must i)? ?(owe|pay|due|pending|outstanding|left|remaining|balance|be paid)\b",
    r"\bhow much (is )?(due|pending|outstanding|left|remaining)\b",
    r"\b(what|whats|what's|what is) (is )?(my |the )?(total |current |outstanding |pending |due |exact )?(balance|amount due|dues|outstanding|emi amount|due amount|amount)\b",
    r"\boutstanding\b", r"\btotal (due|amount|payable)\b", r"\b(foreclosure|payoff|pre-?closure) amount\b", r"\bcheck (my )?balance\b",
])
_r(BALANCE_QUERY, "balance.hing", [
    r"\b(balance|amount|dues|due) (kitna|kitni|kya hai|batao|bataiye|bata do|check)\b",
    r"\bkitna (baaki|baki|bacha|due|dena|pending|outstanding|bharna|pay karna|jama karna|hai|hua|banta)\b",
    r"\bkitne (paise|rupay|rupees|rupaye|emi) (baaki|baki|bache|dene|bharne|pending)\b", r"\b(emi|amount|due) kitni\b",
    r"\bkitni emi (baaki|baki|bachi|pending)\b", r"\btotal kitna\b",
])
_r(BALANCE_QUERY, "balance.hi", [r"कितना (बाकी|बचा|देना|भरना|पेंडिंग|है)|कितने पैसे|बकाया|बैलेंस|कुल कितना|(ईएमआई|किस्त) कितनी"])
_r(LINK_REQUEST, "link.en", [
    r"\b(send|share|give|resend|forward|need|want)( me)? (the |a |your |payment |pay )*(link|upi|qr|qr code|payment link|upi id|bank details|account details)\b",
    r"\b(payment|pay) link\b", r"\bupi id\b", r"\bqr code\b", r"\bhow (can|do|should|to|will) (i )?(pay|make (the )?payment)\b",
    r"\blink (is )?(not working|expired|broken|not opening)\b", r"\b(gpay|google pay|phonepe|paytm) (number|no|id)\b",
])
_r(LINK_REQUEST, "link.hing", [
    r"\blink (bhi |to |hi )?(bhejo|bhej do|bhejiye|do|dijiye|send karo|share karo|chahiye|nahi mila|nahi aaya|expire|kaam nahi|nahi khul|open nahi)\w*\b",
    r"\b(kaise|kahan|kaha|kidhar|kese) (pay|payment|bharu|bhare|bharein|jama|karu|bharna|kare)\b", r"\b(pay|payment|bharna|jama) (kaise|kahan|kaha|kidhar|kese)\b",
])
_r(LINK_REQUEST, "link.hi", [r"लिंक (भेजो|भेजें|दो|दीजिए|भेजिए|नहीं)|पेमेंट लिंक|यूपीआई|क्यूआर|कैसे (भरूं|भरूँ|पेमेंट|पे|करूं)|कहाँ (भरूं|भरूँ|पेमेंट)"])
_r(STATEMENT_REQUEST, "statement.en", [
    r"\bstatement\b", r"\bsoa\b", r"\b(account|loan) (summary|details)\b", r"\b(repayment|emi) schedule\b", r"\bamorti[sz]ation\b",
    r"\binterest certificate\b", r"\bno objection\b", r"\b(closure|foreclosure) letter\b", r"\bsend (me )?(the |a )?receipt\b",
    r"\breceipt (please|for|bhejo|send|chahiye|do)\b", r"\b(transaction|payment) history\b", r"\bbreak ?-?up\b",
])
_r(STATEMENT_REQUEST, "statement.weak", [r"\bnoc\b"], weak=True)
_r(STATEMENT_REQUEST, "statement.hi", [r"स्टेटमेंट|रसीद|एनओसी|ब्योरा"])
_r(LANGUAGE_CHANGE, "lang.en", [
    r"\b(in|into) (hindi|english|marathi|tamil|telugu|kannada|bengali|bangla|gujarati|malayalam|punjabi|odia|urdu)\b",
    r"\b(speak|talk|write|reply|message) (in )?(hindi|english|marathi|tamil|telugu|kannada|bengali|bangla|gujarati|malayalam|punjabi|odia|urdu)\b",
    r"\b(hindi|english|marathi|tamil|telugu|kannada|bengali|bangla|gujarati|malayalam|punjabi|odia|urdu) (please|language|lang)\b",
    r"\b(don't|dont|do not|can't|cannot) (understand|read|speak) (hindi|english)\b", r"\bchange (the )?language\b",
])
_r(LANGUAGE_CHANGE, "lang.hing", [
    r"\b(hindi|english|angrezi|angreji|marathi|tamil|telugu|kannada|bengali|bangla|gujarati|punjabi|urdu) (me|mai|bolo|boliye|likho|likhiye|bhejo|me baat|me bolo)\b",
    r"\b(hindi|english|angrezi|angreji) (nahi|samajh nahi) (aati|aata|samajh)\w*\b", r"\bbhasha (badlo|badal|change)\w*\b",
])
_r(LANGUAGE_CHANGE, "lang.hi", [r"(हिंदी|हिन्दी|अंग्रेजी|अंग्रेज़ी|इंग्लिश|मराठी|तमिल|तेलुगु|गुजराती|बंगाली) में (बात|बोलो|लिखो|भेजो|बोलिए)|(अंग्रेजी|अंग्रेज़ी|इंग्लिश) नहीं (आती|समझ)|भाषा (बदलो|बदलें)"])
_GREETING_WORDS = (
    r"hi|hello|hey|namaste|namaskar|namaskaar|pranam|good (morning|afternoon|evening|night|day)|gm|gn|ok|okay|k|kk|"
    r"thanks|thank you|thank you so much|thanks a lot|dhanyavad|dhanyavaad|dhanyawad|shukriya|theek hai|theek|ji|ji haan|haan|haan ji|"
    r"ha|han|yes|yeah|yep|noted|received|got it|sure|fine|alright|acha|acha ji|good|nice|bye|ok bye|hmm ok|ok noted|ok done|"
    r"ram ram|jai shree krishna|jai hind|sat sri akal"
)
_r(GREETING, "greet.en", [
    rf"^\W*(({_GREETING_WORDS})\W*)+( (sir|madam|maam|ma'am|ji|bhai|team|dear))?\W*$",
])
_r(GREETING, "greet.emoji", [r"^[\s\U0001F44D\U0001F64F\U0001F44C\U0001F642\U0001F60A\u263A\u2705\u2714\uFE0F\U0001F3FB-\U0001F3FF]+$"])
_r(GREETING, "greet.hi", [r"^\W*(नमस्ते|नमस्कार|धन्यवाद|शुक्रिया|ठीक है|हाँ|हां|जी|ओके|प्रणाम|राम राम|हाँ जी|जी हाँ)(\s*(जी|सर))?\W*$"])
_r(GREETING, "greet.weak", [r"\b(who is this|who are you|kaun (hai|ho|bol raha|bol rahe|bol rahi)|aap kaun)\b", r"कौन (है|हो|बोल)"], weak=True)

_COMPILED_RULES: List[Tuple[str, str, "re.Pattern[str]", bool]] = [
    (rid, label, re.compile(p, re.I), weak) for rid, label, p, weak in _RAW_RULES]
RULE_IDS: Tuple[str, ...] = tuple(rid for rid, _, _, _ in _RAW_RULES)

_HARDSHIP_KIND = [(re.compile(p, re.I), k) for p, k in [
    (r"suicid|khudkushi|aatmhatya|atmahatya|mar jaun|jaan de|आत्महत्या|खुदकुशी|मर जा|जान दे", "self-harm"),
    (r"accident|एक्सीडेंट|दुर्घटना", "accident"),
    (r"hospital|admit|icu|surgery|operation|b[aei]+ma+r|ill|sick|medical|cancer|ilaaj|ilaj|tabiyat|treatment|dialysis|chemo|"
     r"covid|dengue|typhoid|malaria|fracture|paraly|heart|stroke|अस्पताल|हॉस्पिटल|बीमार|इलाज|ऑपरेशन|तबीयत", "medical"),
    (r"job|naukri|kaam|laid|lay ?off|unemployed|jobless|berozgar|salary|pay cut|business|dhand|dukaan|dukan|company|loss|नौकरी|सैलरी|वेतन|कारोबार|धंधा|दुकान", "income loss"),
    (r"passed away|expired|death|died|funeral|guzar|gujar|maut|nidhan|dehant|swargwas|गुज़र|गुजर|देहांत|निधन|मृत्यु|मौत", "bereavement"),
    (r"flood|fire|earthquake|cyclone|baadh|aag|बाढ़|आग", "disaster"),
]]


def rule_hits(text: str) -> Dict[str, List[str]]:
    """label -> rule ids that fired on canonical(text). Internal labels: SELF_HARM, _PROMISE."""
    c = canonical(text)
    hits: Dict[str, List[str]] = {}
    for rid, label, rx, _weak in _COMPILED_RULES:
        if rx.search(c):
            hits.setdefault(label, []).append(rid)
    return hits


# ---------------------------------------------------------------------------------------------
# Entities: dates and amount type
# ---------------------------------------------------------------------------------------------

# Devanagari date words -> Hinglish tokens the legacy date parser already knows.
_DEV_DATE_WORDS = [
    ("परसों", " parso "), ("परसो", " parso "), ("कल", " kal "), ("आज", " aaj "), ("तारीख", " tarikh "), ("तारिख", " tarikh "),
    ("सोमवार", " somvar "), ("मंगलवार", " mangalvar "), ("बुधवार", " budhvar "), ("गुरुवार", " guruvar "),
    ("शुक्रवार", " shukravar "), ("शनिवार", " shanivar "), ("रविवार", " ravivar "), ("को", " ko "), ("तक", " tak "),
]
_DUR_DAYS = re.compile(r"\b(?:in\s+)?(\d{1,2}|ek|do|teen|char|chaar|paanch|panch|one|two|three|four|five)\s*(?:-\s*)?"
                       r"(din|days?|दिन)\s*(me|mein|baad|bad|later|ke andar|में|बाद)?", re.I)
_DUR_WEEK = re.compile(r"\b(ek|1|one|a)\s+(hafte|hafta|week|हफ्ते)\s*(me|mein|baad|में|बाद)?\b|\bin a week\b", re.I)
_NUM_WORDS = {"ek": 1, "one": 1, "do": 2, "two": 2, "teen": 3, "three": 3, "char": 4, "chaar": 4, "four": 4,
              "paanch": 5, "panch": 5, "five": 5}
_MONTH_END = re.compile(r"\b(month[- ]end|end of (the )?month|mahine ke (end|aakhir|akhir)|mahina khatam|month ke end)\b|महीने के (अंत|आखिर)", re.I)
_HINTS = [
    ("after_salary", re.compile(r"\bsalary\b.{0,20}\b(baad|aane|aayegi|aaegi|milte|milne|credit|aate|aa jaye|ane)\b|\bafter (my )?(salary|payday)\b|"
                                r"\bwhen (i get|my) salary\b|(सैलरी|तनख्वाह|वेतन).{0,12}(बाद|आने|मिलते|आते)", re.I)),
    ("after_festival", re.compile(r"\b(diwali|deepawali|holi|eid|festival|tyohar|tyohaar|navratri|dussehra|pongal|onam)\b.{0,12}\b(baad|after|ke baad)\b|"
                                  r"\bafter (the )?(diwali|holi|eid|festival)\b", re.I)),
    ("next_week", re.compile(r"\bnext week\b|\bagl[ea] (hafte|hafta|week)\b|अगले हफ्ते", re.I)),
    ("next_month", re.compile(r"\bnext month\b|\bagl[ea] (mahine|mahina|month)\b|अगले महीने", re.I)),
]
_AMOUNT_FULL = re.compile(r"\b(full|poora|pura|poori|puri|entire|complete|total|saara|sara|whole)\b(?! time)|पूरा|पूरी|पूरे", re.I)
_AMOUNT_PARTIAL = re.compile(r"\b(partial|part payment|part-payment|half|aadha|adha|aadhi|adhi|some amount|kuch amount|thoda (paisa|paise|amount|payment)|"
                             r"thoda thoda|installment me|kist me|token amount|minimum)\b|आधा|आधी|थोड़ा थोड़ा|कुछ पैसे", re.I)


def _date_text(text: str) -> str:
    s = canonical(text)
    for w, rep in _DEV_DATE_WORDS:
        s = s.replace(w, rep)
    return " ".join(s.split())


def extract_entities(text: str, today: date) -> Dict[str, Any]:
    """{date, date_explicit, date_hint, amount_type}. date is ISO or None; date_hint names a relative
    anchor with no calendar day ("after_salary", "after_festival", "next_week", "next_month")."""
    s = _date_text(text)
    d: Optional[date] = None
    explicit = False
    m = _DUR_DAYS.search(s)
    mw = _DUR_WEEK.search(s)
    if m:
        n = m.group(1).lower()
        days = int(n) if n.isdigit() else _NUM_WORDS.get(n, 0)
        if 0 < days <= 60:
            d = today + timedelta(days=days)
    elif mw:
        d = today + timedelta(days=7)
    elif _MONTH_END.search(s):
        nxt = (today.replace(day=28) + timedelta(days=4))
        d = nxt - timedelta(days=nxt.day)
    if d is None:
        d, explicit = _legacy.extract_date(s, today)
    hint = next((name for name, rx in _HINTS if rx.search(s)), None)
    amount = "PARTIAL" if _AMOUNT_PARTIAL.search(s) else "FULL" if _AMOUNT_FULL.search(s) else None
    return {"date": d.isoformat() if d else None, "date_explicit": bool(d and explicit), "date_hint": hint,
            "amount_type": amount}


# ---------------------------------------------------------------------------------------------
# Redaction for LLM tiers: legacy redact() + every digit masked + honorific names
# ---------------------------------------------------------------------------------------------

_RX_HONORIFIC_NAME = re.compile(r"\b(mr|mrs|ms|miss|shri|shrimati|smt|sri|dr)\.?\s+[A-Za-z]+(\s+[A-Z][a-z]+)?", re.I)
_RX_JI_NAME = re.compile(r"\b([A-Z][a-z]{2,})\s+(ji|sir|madam|bhai|sahab)\b")
_RX_DEV_PHONE = re.compile(r"[६-९6-9][०-९0-9\s-]{8,12}[०-९0-9]")


def redact_for_llm(text: str, known: Iterable[str] = (), max_chars: int = _legacy.LLM_MAX_INPUT_CHARS) -> str:
    """Mask phones, emails, UPI ids, account / reference numbers, amounts, names and then EVERY
    remaining digit ('#'). The classifier never needs a digit: dates come from rules."""
    s = unicodedata.normalize("NFKC", text or "").translate(_DEV_DIGITS)
    s = _RX_HONORIFIC_NAME.sub(lambda m: m.group(1) + " [NAME]", s)
    s = _RX_JI_NAME.sub(lambda m: "[NAME] " + m.group(2), s)
    s = _legacy.redact(s, max_chars=max_chars * 2, known=known)
    s = re.sub(r"\d", "#", s)
    return s[:max_chars]


# ---------------------------------------------------------------------------------------------
# Tier 2: small model (multinomial Naive Bayes, pure Python)
# ---------------------------------------------------------------------------------------------

MODEL_PATH = Path(__file__).resolve().parent / "data" / "intent_nb_v1.json"


def features(text: str) -> Dict[str, int]:
    """Word 1-2-grams and character 2-4-grams (within padded words) of canonical(text). Digits -> 0."""
    s = re.sub(r"\d", "0", canonical(text))
    words = re.findall(r"[\wऀ-ॿ']+", s)
    out: Dict[str, int] = {}

    def add(f: str) -> None:
        out[f] = out.get(f, 0) + 1

    for w in words:
        add("w:" + w)
        p = f" {w} "
        for n in (2, 3, 4):
            for i in range(len(p) - n + 1):
                add("c:" + p[i:i + n])
    for a, b in zip(words, words[1:]):
        add(f"b:{a} {b}")
    if not words:
        add("w:<empty>")
    return out


class NaiveBayesModel:
    """Multinomial NB with Laplace smoothing; probabilities = softmax(log-joint / temperature)."""

    def __init__(self, data: Dict[str, Any]):
        self.version: str = data["version"]
        self.seed: int = data["seed"]
        self.labels: List[str] = list(data["labels"])
        self.alpha: float = float(data["alpha"])
        self.temperature: float = float(data["temperature"])
        self.weights_sha: str = data.get("weights_sha256", "")
        k = len(self.labels)
        counts: Dict[str, List[int]] = {}
        totals = [0] * k
        for f, sparse in data["features"].items():
            row = [0] * k
            for ci, n in sparse:
                row[ci] = n
                totals[ci] += n
            counts[f] = row
        v = len(counts)
        docs = data["class_docs"]
        n_docs = sum(docs)
        self.log_prior = [math.log((docs[i] + 1) / (n_docs + k)) for i in range(k)]
        self.log_unseen = [math.log(self.alpha / (totals[i] + self.alpha * v)) for i in range(k)]
        self.log_lik: Dict[str, List[float]] = {
            f: [math.log((row[i] + self.alpha) / (totals[i] + self.alpha * v)) for i in range(k)] for f, row in counts.items()}

    @classmethod
    def load(cls, path: Path = MODEL_PATH) -> "NaiveBayesModel":
        with open(path, "r", encoding="utf-8") as fh:
            return cls(json.load(fh))

    @property
    def model_version(self) -> str:
        return f"{self.version}+seed{self.seed}+{self.weights_sha[:8]}"

    def log_joint(self, feats: Dict[str, int]) -> List[float]:
        k = len(self.labels)
        out = list(self.log_prior)
        for f, n in feats.items():
            ll = self.log_lik.get(f)
            if ll is None:
                continue  # unseen feature: identical for every class after normalisation
            for i in range(k):
                out[i] += n * ll[i]
        return out

    def predict_proba(self, text: str) -> Dict[str, float]:
        lj = self.log_joint(features(text))
        t = self.temperature
        mx = max(lj)
        ex = [math.exp((x - mx) / t) for x in lj]
        z = sum(ex)
        return {lab: ex[i] / z for i, lab in enumerate(self.labels)}

    def top_features(self, text: str, a: str, b: str, n: int = 5) -> List[str]:
        """Known features that most favour label a over label b (vocabulary comes from the synthetic corpus)."""
        ia, ib = self.labels.index(a), self.labels.index(b)
        scored = []
        for f, cnt in features(text).items():
            ll = self.log_lik.get(f)
            if ll is not None:
                scored.append((cnt * (ll[ia] - ll[ib]), f))
        scored.sort(key=lambda x: (-x[0], x[1]))
        return [f for s, f in scored[:n] if s > 0]


_DEFAULT_MODEL: Optional[NaiveBayesModel] = None
_MODEL_LOCK = threading.Lock()


def default_model() -> Optional[NaiveBayesModel]:
    global _DEFAULT_MODEL
    with _MODEL_LOCK:
        if _DEFAULT_MODEL is None and MODEL_PATH.exists():
            _DEFAULT_MODEL = NaiveBayesModel.load(MODEL_PATH)
        return _DEFAULT_MODEL


# ---------------------------------------------------------------------------------------------
# Tier 3: local LLM (Ollama), optional
# ---------------------------------------------------------------------------------------------

# transport(url, headers, body_or_None, timeout) -> (status, raw bytes); body None = GET.
HttpTransport = Callable[[str, Dict[str, str], Optional[bytes], float], Tuple[int, bytes]]

LOCAL_LLM_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "label": {"type": "string", "enum": list(LABELS)},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": ["label", "confidence"],
    "additionalProperties": False,
}
LOCAL_LLM_INSTRUCTIONS = (
    "Classify ONE borrower reply to a loan-repayment reminder in India (English, Hindi or Hinglish). "
    "Digits and personal data are masked. Return only JSON {\"label\": <one label>, \"confidence\": 0..1}. "
    "Labels: PAY_NOW (paying right now), PTP (promises a date), NEEDS_TIME (wants time, no date), ALREADY_PAID, "
    "CALLBACK (call me later), HARDSHIP (illness, accident, job loss, death, disaster), DISPUTE (contests loan / "
    "amount / fraud / complaint), STOP (opt out), WRONG_NUMBER, HUMAN (wants a person), BALANCE_QUERY, "
    "LINK_REQUEST (how / where to pay), STATEMENT_REQUEST (statement, receipt, NOC), LANGUAGE_CHANGE, ABUSIVE, "
    "GREETING (hello, thanks, ok, small talk), UNKNOWN (unclear). Use UNKNOWN and a low confidence when unsure. "
    "You only classify; you never decide actions."
)


def validate_local_output(obj: Any) -> Optional[str]:
    if not isinstance(obj, dict):
        return "not an object"
    if set(obj) != {"label", "confidence"}:
        return f"keys {sorted(obj)}"
    if obj["label"] not in LABELS:
        return f"label {obj['label']!r} not in enum"
    c = obj["confidence"]
    if isinstance(c, bool) or not isinstance(c, (int, float)) or not 0.0 <= float(c) <= 1.0 or c != c:
        return "confidence not a number in [0, 1]"
    return None


def default_http_transport(url: str, headers: Dict[str, str], body: Optional[bytes], timeout: float) -> Tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, headers=headers, method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (operator-configured host)
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read() or b""


@dataclass
class LocalReply:
    data: Optional[dict]
    error: Optional[str] = None
    latency_ms: int = 0


class OllamaClient:
    """Ollama /api/chat with temperature 0, fixed seed, format = strict JSON schema, short timeout.
    available() auto-detects the server and model once (GET /api/tags). Never raises."""

    DEFAULT_HOST = "http://127.0.0.1:11434"

    def __init__(self, model: Optional[str] = None, host: Optional[str] = None, transport: Optional[HttpTransport] = None,
                 timeout: float = 4.0, probe_timeout: float = 0.4, seed: int = 7, env: Optional[Dict[str, str]] = None):
        e = os.environ if env is None else env
        self.model = model or e.get("KAIROS_LOCAL_LLM_MODEL") or None
        host = host or e.get("OLLAMA_HOST") or self.DEFAULT_HOST
        if not host.startswith(("http://", "https://")):
            host = "http://" + host
        self.host = host.rstrip("/")
        self.transport = transport or default_http_transport
        self.timeout, self.probe_timeout, self.seed = timeout, probe_timeout, seed
        self._available: Optional[bool] = None
        self._lock = threading.Lock()

    def available(self) -> bool:
        if not self.model:
            return False
        with self._lock:
            if self._available is None:
                self._available = self._probe()
            return self._available

    def _probe(self) -> bool:
        try:
            status, raw = self.transport(self.host + "/api/tags", {}, None, self.probe_timeout)
            if status != 200:
                return False
            names = {m.get("name") or m.get("model") for m in json.loads(raw.decode("utf-8")).get("models", [])
                     if isinstance(m, dict)}
        except Exception:
            return False
        want = self.model if ":" in self.model else self.model + ":latest"
        return self.model in names or want in names

    def build_request(self, redacted_text: str) -> Dict[str, Any]:
        return {
            "model": self.model,
            "messages": [{"role": "system", "content": LOCAL_LLM_INSTRUCTIONS},
                         {"role": "user", "content": f"Borrower message (masked): {redacted_text}"}],
            "stream": False,
            "format": LOCAL_LLM_SCHEMA,
            "options": {"temperature": 0, "seed": self.seed, "top_k": 1, "top_p": 1, "num_predict": 64},
        }

    def classify(self, redacted_text: str) -> LocalReply:
        if not self.available():
            return LocalReply(None, "local LLM not available")
        body = json.dumps(self.build_request(redacted_text)).encode("utf-8")
        t0 = time.monotonic()
        try:
            status, raw = self.transport(self.host + "/api/chat", {"Content-Type": "application/json"}, body, self.timeout)
        except Exception as e:
            return LocalReply(None, f"transport error: {type(e).__name__}")
        ms = int((time.monotonic() - t0) * 1000)
        if status != 200:
            return LocalReply(None, f"HTTP {status}", ms)
        try:
            resp = json.loads(raw.decode("utf-8"))
            content = resp["message"]["content"]
            obj = json.loads(content)
        except Exception:
            return LocalReply(None, "response not the expected JSON", ms)
        err = validate_local_output(obj)
        if err:
            return LocalReply(None, f"schema: {err}", ms)
        return LocalReply({"label": obj["label"], "confidence": float(obj["confidence"])}, None, ms)


# ---------------------------------------------------------------------------------------------
# The stack
# ---------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class StackResult:
    label: str
    confidence: float
    tier_used: str                       # rules | small_model | local_llm | cloud_llm | abstain
    evidence: Dict[str, Any]
    entities: Dict[str, Any]
    needs_human: bool
    safety_flags: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["safety_flags"] = list(self.safety_flags)
        return d

    @property
    def legacy_intent(self) -> str:
        return TO_LEGACY[self.label]


@dataclass
class _RuleOutcome:
    label: Optional[str]                 # final rule label, or None
    confidence: float
    candidates: List[str]
    rule_ids: List[str]
    safety_flags: List[str] = field(default_factory=list)
    final: bool = False                  # safety hit: no lower tier may run


def _today(today: Any) -> date:
    if today is None:
        return datetime.now(_legacy.IST).date()
    if isinstance(today, datetime):
        return _legacy._local_day(today)
    return today


class IntentStack:
    """rules -> small model -> local LLM -> cloud LLM -> abstain. Classification only."""

    def __init__(self, model: Optional[NaiveBayesModel] = None, local_llm: Optional[OllamaClient] = None,
                 cloud_llm: Optional[_legacy.LunaClient] = None, use_model: bool = True, cache_size: int = 20000,
                 prism: Any = None):
        self.model = model if model is not None else (default_model() if use_model else None)
        self.prism = prism               # sarvam.prism.PrismClassifier, or None (no key: tier skipped)
        self.local_llm = local_llm
        self.cloud_llm = cloud_llm
        self.cache_size = cache_size
        self._cache: "OrderedDict[str, StackResult]" = OrderedDict()
        self._lock = threading.Lock()

    @classmethod
    def from_env(cls, env: Optional[Dict[str, str]] = None, cloud_llm: Optional[_legacy.LunaClient] = None) -> "IntentStack":
        """prism when a Sarvam key is found (sarvam/config.py) and not switched off; local LLM when
        KAIROS_LOCAL_LLM_MODEL is set (and the server answers); cloud LLM when an OPENAI_API_KEY is
        configured (or a client is passed). Nothing is required."""
        e = os.environ if env is None else env
        local = OllamaClient(env=e) if e.get("KAIROS_LOCAL_LLM_MODEL") else None
        cloud = cloud_llm if cloud_llm is not None else (_legacy.LunaClient(env=dict(e)) if e.get("OPENAI_API_KEY") else None)
        from .sarvam.prism import shared_prism
        return cls(local_llm=local, cloud_llm=cloud, prism=shared_prism(e))

    # -- public ---------------------------------------------------------------------------------
    def classify(self, text: Optional[str], today: Any = None, channel: str = "whatsapp",
                 known_names: Sequence[str] = ()) -> StackResult:
        day = _today(today)
        key = text_hash(text, day)
        with self._lock:
            hit = self._cache.get(key)
            if hit is not None:
                self._cache.move_to_end(key)
                return hit
        res = self._classify(text or "", day, channel, known_names)
        if res.label == HARDSHIP:
            res = replace(res, evidence={**res.evidence, "hardship_kind": hardship_kind(text or "") or None})
        if (res.evidence.get("prism") or {}).get("error"):
            return res      # a transient model failure is not cached: the next call may get an answer
        with self._lock:
            prev = self._cache.get(key)
            if prev is not None:     # a concurrent caller got there first: keep one answer
                return prev
            self._cache[key] = res
            while len(self._cache) > self.cache_size:
                self._cache.popitem(last=False)
        return res

    def cache_clear(self) -> None:
        with self._lock:
            self._cache.clear()

    # -- tiers ----------------------------------------------------------------------------------
    def rules(self, text: str, today: date) -> Tuple[_RuleOutcome, Dict[str, Any]]:
        ents = extract_entities(text, today)
        hits = rule_hits(text)
        ids = sorted({rid for v in hits.values() for rid in v})
        flags: List[str] = []
        stop = STOP in hits or _legacy.is_stop(text)
        if stop and STOP not in hits:
            ids.append("stop.legacy")
        if _SELF_HARM in hits:
            flags.append("self_harm")
        if ABUSIVE in hits:
            flags.append("abusive")
        if stop:
            flags.insert(0, "stop")
            return _RuleOutcome(STOP, 1.0, [STOP], ids, flags, final=True), ents
        if _SELF_HARM in hits or HARDSHIP in hits:
            flags.insert(0, "hardship")
            return _RuleOutcome(HARDSHIP, 1.0 if _SELF_HARM in hits else 0.95, [HARDSHIP], ids, flags, final=True), ents
        if ABUSIVE in hits:
            return _RuleOutcome(ABUSIVE, 0.95, [ABUSIVE], ids, flags, final=True), ents

        weak = {lab for lab, rids in hits.items() if all(r.endswith("w") for r in rids)}
        found = set(hits)
        if found - weak:
            found -= weak
        d = date.fromisoformat(ents["date"]) if ents["date"] else None
        explicit = ents["date_explicit"]
        if ALREADY_PAID in found:
            found.discard(_PROMISE)
        if PAY_NOW in found:
            found -= {_PROMISE, PTP}
        if _PROMISE in found:
            found.discard(_PROMISE)
            found.add(PTP if d else NEEDS_TIME)
        if d and NEEDS_TIME in found and (explicit or PTP in found) and not (found - {NEEDS_TIME, PTP}):
            found = {PTP}
        if NEEDS_TIME in found and PTP in found and not (found - {NEEDS_TIME, PTP}):
            found = {PTP}
        # pair resolutions (fixed, deterministic)
        if WRONG_NUMBER in found:
            found = {WRONG_NUMBER}
        if LANGUAGE_CHANGE in found:
            found -= {HUMAN, CALLBACK, GREETING}
        if HUMAN in found and CALLBACK in found:
            found.discard(HUMAN)
        if LINK_REQUEST in found:
            found -= {PAY_NOW, PTP, NEEDS_TIME, BALANCE_QUERY}
        if STATEMENT_REQUEST in found:
            found.discard(BALANCE_QUERY)
            if ALREADY_PAID in found:
                found.discard(STATEMENT_REQUEST)
        if len(found) > 1:
            found.discard(GREETING)
        cands = sorted(found, key=LABELS.index)
        if len(cands) == 1:
            lab = cands[0]
            conf = 0.75 if lab in weak else 0.92
            return _RuleOutcome(lab, conf, cands, ids, flags), ents
        if not cands and d is not None and not ents["date_hint"]:
            return _RuleOutcome(PTP, 0.75, [PTP], ids + ["date.bare"], flags), ents   # bare date reply
        return _RuleOutcome(None, 0.6 if cands else 0.0, cands, ids, flags), ents

    def _classify(self, text: str, today: date, channel: str, known: Sequence[str]) -> StackResult:
        ro, ents = self.rules(text, today)
        ev: Dict[str, Any] = {"stack_version": STACK_VERSION, "rule_ids": ro.rule_ids, "rule_candidates": ro.candidates,
                              "escalated_to": []}
        if ro.final or (ro.label is not None and ro.confidence >= RULE_ACCEPT):
            needs_human = ro.label in (HARDSHIP, ABUSIVE) or "self_harm" in ro.safety_flags
            return self._result(ro.label, ro.confidence, "rules", ev, ents, needs_human, ro.safety_flags, today)

        # tier 2: small model
        probs: Dict[str, float] = {}
        if self.model is not None:
            ev["escalated_to"].append("small_model")
            probs = self.model.predict_proba(text)
            ranked = sorted(probs.items(), key=lambda kv: (-kv[1], LABELS.index(kv[0])))
            (top, p1), (second, p2) = ranked[0], ranked[1]
            ev["model_version"] = self.model.model_version
            ev["model_top"] = [[lab, round(p, 4)] for lab, p in ranked[:3]]
            if ro.candidates:
                pick = max(ro.candidates, key=lambda lab: (probs.get(lab, 0.0), -LABELS.index(lab)))
                if pick == top or probs.get(pick, 0.0) >= MODEL_PICK_MIN:
                    ev["top_features"] = self.model.top_features(text, pick, second if pick == top else top)
                    conf = round(min(0.9, max(ro.confidence, probs[pick])), 4)
                    return self._result(pick, conf, "small_model", ev, ents, pick in SAFETY_LABELS, ro.safety_flags, today,
                                        model_safety=pick in SAFETY_LABELS)
            elif top != UNKNOWN and p1 >= MODEL_ACCEPT and p1 - p2 >= MODEL_MARGIN:
                ev["top_features"] = self.model.top_features(text, top, second)
                return self._result(top, round(p1, 4), "small_model", ev, ents, top in SAFETY_LABELS, ro.safety_flags, today,
                                    model_safety=top in SAFETY_LABELS)
        # tier 2's top probability over real labels (a confident "UNKNOWN" is still "unsure")
        top_p = max((p for lab, p in probs.items() if lab != UNKNOWN), default=0.0)

        # tier 2b: sarvam-prism, when a key is configured. Rule safety hits never reach this point.
        masked = None
        if self.prism is not None:
            ev["escalated_to"].append("prism")
            top2 = [lab for lab, _ in sorted(probs.items(), key=lambda kv: (-kv[1], LABELS.index(kv[0])))[:2]]
            out = self._prism_tier(text, known, ro, ev, ents, today, top2)
            if out is not None:
                return out

        # tier 3: local LLM, only when tier 2's top probability is below the threshold
        if self.local_llm is not None and top_p < LOCAL_LLM_BELOW and self.local_llm.available():
            ev["escalated_to"].append("local_llm")
            masked = masked or redact_for_llm(text, known)
            try:
                rep = self.local_llm.classify(masked)
            except Exception as e:  # defensive: an adapter bug never reaches the caller
                rep = LocalReply(None, f"adapter error: {type(e).__name__}")
            ev["local_llm"] = {"model": self.local_llm.model, "error": rep.error, "latency_ms": rep.latency_ms,
                               "output": rep.data}
            if rep.data and rep.data["label"] != UNKNOWN and rep.data["confidence"] >= LLM_ACCEPT:
                lab = rep.data["label"]
                return self._result(lab, round(min(0.85, rep.data["confidence"]), 4), "local_llm", ev, ents,
                                    lab in SAFETY_LABELS, ro.safety_flags, today, model_safety=lab in SAFETY_LABELS)

        # tier 4: cloud LLM (gpt-6-luna) when a key is configured
        if self.cloud_llm is not None and self.cloud_llm.api_key():
            ev["escalated_to"].append("cloud_llm")
            masked = masked or redact_for_llm(text, known)
            try:
                rep = self.cloud_llm.classify(masked, today, channel)
            except Exception as e:
                rep = _legacy.LlmReply(None, None, f"client error: {type(e).__name__}")
            ev["cloud_llm"] = {"model": _legacy.LunaClient.MODEL, "error": rep.error, "usage": rep.usage,
                               "output": None if rep.data is None else {k: rep.data[k] for k in ("intent", "ambiguous", "summary")}}
            if rep.data and rep.data["intent"] != _legacy.OTHER and not rep.data["ambiguous"]:
                lab = FROM_LEGACY[rep.data["intent"]]
                return self._result(lab, 0.8, "cloud_llm", ev, ents, lab in SAFETY_LABELS, ro.safety_flags, today,
                                    model_safety=lab in SAFETY_LABELS)

        # tier 5: abstain
        return self._result(UNKNOWN, round(top_p, 4), "abstain", ev, ents, True, ro.safety_flags, today)

    def _prism_tier(self, text: str, known: Sequence[str], ro: _RuleOutcome, ev: Dict[str, Any],
                    ents: Dict[str, Any], today: date, top2: Sequence[str] = ()) -> Optional[StackResult]:
        """Map prism's answers into a StackResult, or None to fall through. Never raises. Runs only where
        rules + small model abstained. Its label is kept only at >= PRISM_ACCEPT and only when it agrees with
        a rule candidate or the small model's top 2; a safety signal (noul yes, or a confident safety label)
        always goes to a person."""
        from .sarvam.prism import mask_for_model
        try:
            a = self.prism.classify_intent(mask_for_model(text, known))
        except Exception as e:  # defensive
            ev["prism"] = {"model": "sarvam-prism", "error": f"adapter error: {type(e).__name__}"}
            return None
        ev["prism"] = {**a.meta, "output": a.as_dict()}
        flags = list(ro.safety_flags)
        safety = list(a.safety_yes)
        sure = a.confidence is not None and a.confidence >= PRISM_ACCEPT
        if sure and a.label == STOP and "stop" not in safety:
            safety.append("stop")
        if sure and a.label == HARDSHIP and "hardship" not in safety and "self_harm" not in safety:
            safety.append("hardship")
        if sure and a.label == ABUSIVE and "abusive" not in safety:
            safety.append("abusive")
        if safety:
            # Review signal only. Precedence stop > self-harm > hardship > abuse, as in the rules.
            for s in safety:
                flags.append(f"{s}_unconfirmed")
            conf = round(min(PRISM_CONF_CAP, a.confidence if a.confidence is not None else 0.5), 4)
            if "stop" in safety:
                # never returned as STOP: no caller may auto-STOP on a model alone
                return self._result(UNKNOWN, conf, "prism", ev, ents, True, flags, today, model_safety=True)
            lab = HARDSHIP if ("self_harm" in safety or "hardship" in safety) else ABUSIVE
            return self._result(lab, conf, "prism", ev, ents, True, flags, today, model_safety=True)
        if a.error or a.label is None or a.label == UNKNOWN or a.confidence is None or a.confidence < PRISM_ACCEPT:
            return None
        allowed = set(ro.candidates) | set(top2)
        if a.label not in allowed:
            ev["prism"]["rejected"] = "label agrees with neither a rule candidate nor the small model's top 2"
            return None
        return self._result(a.label, round(min(PRISM_CONF_CAP, a.confidence), 4), "prism", ev, ents, False, flags, today)

    @staticmethod
    def _result(label: str, conf: float, tier: str, ev: Dict[str, Any], ents: Dict[str, Any], needs_human: bool,
                flags: Sequence[str], today: date, model_safety: bool = False) -> StackResult:
        ents = dict(ents)
        if label == PAY_NOW:
            ents["date"], ents["date_explicit"] = today.isoformat(), False
        elif label not in _DATED:
            ents["date"], ents["date_explicit"] = None, False
        if label == PTP and ents["date"] is None:
            label, needs_human = NEEDS_TIME, needs_human  # a promise without a date is "needs time"
        if label == HARDSHIP:
            needs_human = True
        flags = list(flags)
        if model_safety:
            ev = {**ev, "safety_unconfirmed_by_rules": True}   # a model-only safety label is sent to a human
            needs_human = True
        return StackResult(label, float(conf), tier, ev, ents, bool(needs_human), tuple(flags))


def hardship_kind(text: str) -> str:
    c = canonical(text)
    return next((k for rx, k in _HARDSHIP_KIND if rx.search(c)), "")


# ---------------------------------------------------------------------------------------------
# Ingestion helper (outcomes.record_text_reply, CM-03, voice summaries)
# ---------------------------------------------------------------------------------------------

_SHARED: Optional[IntentStack] = None
_SHARED_LOCK = threading.Lock()


def shared_stack() -> IntentStack:
    """Process-wide stack built from the environment (one cache for every caller)."""
    global _SHARED
    with _SHARED_LOCK:
        if _SHARED is None:
            _SHARED = IntentStack.from_env()
        return _SHARED


def legacy_rules_result(text: str, today: date, channel: str = "whatsapp") -> StackResult:
    """The pre-stack behaviour (intent.IntentClassifier rules, no LLM) in the stack's result shape."""
    r = _legacy.IntentClassifier(llm=None, use_stack=False).classify(
        _legacy.InboundMessage(borrower_id="-", channel=channel, received_at=datetime.combine(today, datetime.min.time()).replace(hour=12),
                               text=text))
    label = FROM_LEGACY[r.intent] if r.source != "fallback" else UNKNOWN
    if r.source == "rules" and r.intent == _legacy.OTHER:
        label = UNKNOWN
    ents = {"date": r.agreed_date, "date_explicit": False, "date_hint": None, "amount_type": None}
    return StackResult(label, r.confidence, "legacy_rules" if r.source == "rules" else "abstain",
                       {"stack_version": "legacy-rules"}, ents, r.source == "fallback" or label in (HARDSHIP,), ())


NOT_MEANINGFUL = (STOP, WRONG_NUMBER)


def is_meaningful_reply(label: str) -> bool:
    """K01 reply_attribution: any inbound text reply counts, except an opt-out (STOP) and a reply from
    someone who is not the borrower (WRONG_NUMBER)."""
    return label not in NOT_MEANINGFUL


def classify_reply(text: str, at: Any = None, channel: str = "whatsapp", use_stack: Optional[bool] = None,
                   stack: Optional[IntentStack] = None, known_names: Sequence[str] = ()) -> StackResult:
    """Free-text reply -> StackResult. use_stack None -> KAIROS_INTENT_STACK env, else
    STACK_DEFAULT_FOR_INGESTION. With the stack off, the legacy rules answer in the same shape."""
    day = _today(at)
    if use_stack is None:
        use_stack = stack_enabled()
    if not use_stack:
        return legacy_rules_result(text, day, channel)
    return (stack or shared_stack()).classify(text, day, channel, known_names)
