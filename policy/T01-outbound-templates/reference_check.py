"""
Kairos outbound template catalogue - builder + checker (stdlib only, portable).

Builds templates.csv (5 channels x 5 DPD bands x 6 angles = 150 templates,
each in English and Hindi = 300 rows) from template_source.json, then checks:
counts, unique IDs and bodies, allowed placeholders, no forbidden words,
SMS length (English GSM <= 160 chars, Hindi <= 3 Unicode segments), pre-due
templates never mention overdue, voice openers disclose nothing before the
person is confirmed, WhatsApp has STOP opt-out, email has unsubscribe.
Usage:  python3 reference_check.py
"""
import csv, json, os, re, sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
BANDS = ["PRE_DUE", "B1", "B2", "B3", "B4"]
ANGLES = ["A", "B", "C", "D", "E", "F"]
CHANNELS = ["SMS", "WHATSAPP", "EMAIL", "IVR", "VOICEBOT"]
LANGS = [("EN", 0), ("HI", 1)]

CTA = {  # text channels
    "pay":  {"SMS": ("Pay: {link}", "भुगतान: {link}"),
             "WHATSAPP": ("Pay securely: {link}", "सुरक्षित भुगतान: {link}"),
             "EMAIL": ("Pay securely: {link}", "सुरक्षित भुगतान: {link}")},
    "help": {"SMS": ("Reply HELP for assistance.", "सहायता के लिए HELP लिखें।"),
             "WHATSAPP": ("Reply HELP here and we will assist you.", "सहायता के लिए यहाँ HELP लिखें।"),
             "EMAIL": ("Reply to this email or call {helpline} and we will assist you.", "इस ईमेल का जवाब दें या {helpline} पर कॉल करें, हम आपकी मदद करेंगे।")},
    "call": {"SMS": ("Reply CALL to speak to our team.", "टीम से बात के लिए CALL लिखें।"),
             "WHATSAPP": ("Reply CALL and our team will call you.", "CALL लिखें, हमारी टीम आपको कॉल करेगी।"),
             "EMAIL": ("Reply to this email or call {helpline} to speak to our team.", "हमारी टीम से बात के लिए इस ईमेल का जवाब दें या {helpline} पर कॉल करें।")},
    "date": {"SMS": ("Reply with your payment date.", "भुगतान की तारीख लिखकर जवाब दें।"),
             "WHATSAPP": ("Reply here with your payment date.", "भुगतान की तारीख यहाँ लिखकर जवाब दें।"),
             "EMAIL": ("Reply to this email with your payment date.", "भुगतान की तारीख के साथ इस ईमेल का जवाब दें।")},
}
VOICE_Q = {"pay": ("Would you be able to make this payment today?", "क्या आप आज यह भुगतान कर पाएँगे?"),
           "help": ("Is anything making the payment difficult right now?", "क्या इस समय भुगतान में कोई कठिनाई है?"),
           "call": ("Is anything making the payment difficult right now?", "क्या इस समय भुगतान में कोई कठिनाई है?"),
           "date": ("When will you be able to make this payment?", "आप यह भुगतान कब कर पाएँगे?")}


def spoken(text, li):
    """Adapt a written core for voice: no 'xx1234', amounts read naturally."""
    if li == 0:
        text = text.replace("loan xx{loan_last4}", "loan ending {loan_last4}")
        return re.sub(r"Rs \{(amount|emi)\}", r"{\1} rupees", text)
    return text.replace("लोन xx{loan_last4}", "लोन").replace("₹{amount}", "{amount} रुपये").replace("₹{emi}", "{emi} रुपये")


def build(src):
    rows = []
    for band in BANDS:
        purpose = "reminder" if band == "PRE_DUE" else "collection"
        detail = (("EMI: Rs {emi} | Due: {due_date} | Loan: xx{loan_last4}", "EMI: ₹{emi} | देय तिथि: {due_date} | लोन: xx{loan_last4}")
                  if band == "PRE_DUE" else
                  ("Overdue: Rs {amount} | Loan: xx{loan_last4}", "बकाया: ₹{amount} | लोन: xx{loan_last4}"))
        for ang in ANGLES:
            cta = src["angle_cta"][ang]
            if cta == "help" and band in ("B3", "B4"):
                cta = "call"
            core = src["cores"][band][ang]
            subj_map = src["email_subjects"][ang]
            subj = subj_map.get(band, subj_map.get("*"))
            for lang, li in LANGS:
                c = core[li]
                bodies = {}
                bodies["SMS"] = ("", f"{{lender_name}}: {c} {CTA[cta]['SMS'][li]}")
                hello = ("Hi {name},", "नमस्ते {name} जी,")[li]
                sign = ("- Kaira, {lender_name} | Reply STOP to opt out.", "- काइरा, {lender_name} | संदेश बंद करने के लिए STOP लिखें।")[li]
                bodies["WHATSAPP"] = ("", f"{hello}\n\n{c}\n\n{detail[li]}\n{CTA[cta]['WHATSAPP'][li]}\n\n{sign}")
                dear = ("Dear {name},", "प्रिय {name} जी,")[li]
                regards = ("Regards,\nKaira, virtual assistant, {lender_name}\n\nUnsubscribe from these emails: {unsubscribe_link}",
                           "सादर,\nकाइरा, वर्चुअल असिस्टेंट, {lender_name}\n\nइन ईमेल से सदस्यता हटाएँ: {unsubscribe_link}")[li]
                bodies["EMAIL"] = (subj[li], f"{dear}\n\n{c}\n\n{detail[li]}\n{CTA[cta]['EMAIL'][li]}\n\n{regards}")
                sp = spoken(c, li)
                ivr_open = ("This call is for {name}. If you are {name}, press 1.",
                            "यह कॉल {name} जी के लिए है। अगर आप {name} जी हैं, तो 1 दबाएँ।")[li]
                intro = ("Hello {name}, this is Kaira, a virtual assistant calling from {lender_name}.",
                         "नमस्ते {name} जी, मैं काइरा हूँ, {lender_name} की वर्चुअल असिस्टेंट।")[li]
                menu = {"pay": ("Press 1 to receive a secure payment link by SMS. Press 2 to speak to our team. Press 3 if you have already paid.",
                                "सुरक्षित भुगतान लिंक SMS पर पाने के लिए 1 दबाएँ। हमारी टीम से बात करने के लिए 2 दबाएँ। अगर आप भुगतान कर चुके हैं तो 3 दबाएँ।"),
                        "help": ("Press 2 to speak to our team now. Press 1 to receive a secure payment link by SMS. Press 3 if you have already paid.",
                                 "हमारी टीम से अभी बात करने के लिए 2 दबाएँ। सुरक्षित भुगतान लिंक SMS पर पाने के लिए 1 दबाएँ। अगर आप भुगतान कर चुके हैं तो 3 दबाएँ।"),
                        "date": ("Press 4 to tell us your payment date. Press 1 to receive a secure payment link by SMS. Press 2 to speak to our team. Press 3 if you have already paid.",
                                 "भुगतान की तारीख बताने के लिए 4 दबाएँ। सुरक्षित भुगतान लिंक SMS पर पाने के लिए 1 दबाएँ। हमारी टीम से बात करने के लिए 2 दबाएँ। अगर आप भुगतान कर चुके हैं तो 3 दबाएँ।")}
                menu["call"] = menu["help"]
                bodies["IVR"] = ("", f"[OPENER] {ivr_open} [AFTER_CONFIRM] {intro} {sp} {menu[cta][li]}")
                vb_open = ("Hello, am I speaking with {name}? This is Kaira, a virtual assistant from {lender_name}.",
                           "नमस्ते, क्या मेरी बात {name} जी से हो रही है? मैं काइरा हूँ, {lender_name} की वर्चुअल असिस्टेंट।")[li]
                bodies["VOICEBOT"] = ("", f"[OPENER] {vb_open} [AFTER_CONFIRM] {sp} {VOICE_Q[cta][li]}")
                for ch in CHANNELS:
                    subject, body = bodies[ch]
                    rows.append({"template_id": f"{ch[:2]}-{band}-{ang}-{lang}", "channel": ch, "band": band, "angle": ang,
                                 "purpose": purpose, "language": lang, "cta": cta, "subject": subject, "body": body})
    return rows


def render(t, vals):
    return re.sub(r"\{(\w+)\}", lambda m: vals.get(m.group(1), m.group(0)), t)


GSM = set("@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà")


def sms_segments(text):
    if all(ch in GSM for ch in text):
        return (1 if len(text) <= 160 else -(-len(text) // 153)), "GSM"
    return (1 if len(text) <= 70 else -(-len(text) // 67)), "UNICODE"


def main():
    src = json.load(open(os.path.join(HERE, "template_source.json"), encoding="utf-8"))
    rows = build(src)
    vals = src["sample_values"]
    meta_ix = {}
    for r in rows:
        r["placeholders"] = ",".join(dict.fromkeys(re.findall(r"\{(\w+)\}", r["subject"] + r["body"])))
        rendered = render(r["body"], vals)
        r["rendered_sample"] = rendered
        r["rendered_length"] = len(rendered)
        if r["channel"] == "SMS":
            segs, enc = sms_segments(rendered)
            r["sms_segments"], r["sms_encoding"] = segs, enc
            r["dlt_body"] = re.sub(r"\{\w+\}", "{#var#}", r["body"])
        if r["channel"] == "WHATSAPP":
            order = list(dict.fromkeys(re.findall(r"\{(\w+)\}", r["body"])))
            r["meta_body"] = re.sub(r"\{(\w+)\}", lambda m: "{{%d}}" % (order.index(m.group(1)) + 1), r["body"])
            r["meta_variables"] = ";".join(f"{{{{{i+1}}}}}={p}" for i, p in enumerate(order))
            r["meta_category"] = "UTILITY"
    fields = ["template_id", "channel", "band", "angle", "purpose", "language", "cta", "subject", "body", "placeholders",
              "rendered_length", "sms_segments", "sms_encoding", "dlt_body", "meta_body", "meta_variables", "meta_category", "rendered_sample"]
    with open(os.path.join(HERE, "templates.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    fails = []
    chk = lambda ok, msg: ok or fails.append(msg)
    per = Counter((r["channel"], r["language"]) for r in rows)
    for ch in CHANNELS:
        for lang, _ in LANGS:
            chk(per[(ch, lang)] == 30, f"{ch}/{lang} has {per[(ch, lang)]} templates, expected 30")
    chk(len({r["template_id"] for r in rows}) == len(rows), "duplicate template IDs")
    bodies = Counter((r["channel"], r["language"], r["body"]) for r in rows)
    chk(all(v == 1 for v in bodies.values()), "duplicate bodies within a channel/language")
    allowed = set(src["allowed_placeholders"])
    bad_words = [w.lower() for w in src["forbidden_words"]]
    for r in rows:
        tid, text = r["template_id"], (r["subject"] + " " + r["body"])
        chk(set(r["placeholders"].split(",")) <= allowed, f"{tid}: unknown placeholder {r['placeholders']}")
        low = text.lower()
        for w in bad_words:
            if re.search(r"(?<![a-z])" + re.escape(w) + r"(?![a-z])", low):
                fails.append(f"{tid}: forbidden word '{w}'")
        if r["band"] == "PRE_DUE":
            chk("{amount}" not in text and "overdue" not in low and "बकाया" not in text, f"{tid}: pre-due mentions overdue")
        if r["channel"] == "SMS":
            if r["language"] == "EN":
                chk(r["sms_encoding"] == "GSM" and r["rendered_length"] <= 160, f"{tid}: EN SMS {r['rendered_length']} chars / {r['sms_encoding']}")
            else:
                chk(r["sms_segments"] <= 3, f"{tid}: HI SMS {r['sms_segments']} segments")
        if r["channel"] in ("IVR", "VOICEBOT"):
            opener = r["body"].split("[AFTER_CONFIRM]")[0]
            chk(not re.search(r"\{(amount|emi|loan_last4|due_date)\}|loan|लोन|EMI", opener), f"{tid}: opener discloses before confirmation")
        if r["channel"] == "WHATSAPP":
            chk("STOP" in r["body"], f"{tid}: no STOP opt-out")
        if r["channel"] == "EMAIL":
            chk("{unsubscribe_link}" in r["body"] and r["subject"], f"{tid}: missing unsubscribe or subject")

    en_sms = [r["rendered_length"] for r in rows if r["channel"] == "SMS" and r["language"] == "EN"]
    hi_sms = [r["sms_segments"] for r in rows if r["channel"] == "SMS" and r["language"] == "HI"]
    print(f"Built {len(rows)} rows = {len(rows)//2} templates x 2 languages -> templates.csv")
    print(f"EN SMS length: max {max(en_sms)} chars (limit 160, GSM)  |  HI SMS: max {max(hi_sms)} segments (limit 3)")
    for f in fails[:40]:
        print("FAIL ", f)
    print(f"\n{'PASS' if not fails else 'FAIL'}: {len(fails)} problems  (catalog {src['catalog_version']})")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
