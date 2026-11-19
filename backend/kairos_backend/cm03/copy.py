"""CM-03 WhatsApp copy, English and Hindi. Tone and wording follow the K22 catalog
(WA_PTPCONF_*, WA_PAIDACK_*, WA_CBACK_*, WA_HARDSHIP_*, WA_STOP_*): polite, no threats, no
collection ask in a hardship reply. Lender name and links are synthetic.

PRE_VERIFY_SAFE lists the only messages that may be sent before OTP verification. None of them
takes an account field: before verification the bot may only say who it is and ask for
verification (plus STOP / wrong-number / refusal acknowledgements, which carry no account data).
The engine refuses to send any other key while a conversation is unverified.
"""
from __future__ import annotations

from typing import Dict

LENDER = "Kairos Demo Finance"

COPY: Dict[str, Dict[str, str]] = {
    "GREET_VERIFY": {
        "en": "Hello, this is Kaira, the virtual assistant of {lender}. To protect your account, I have sent a 6-digit "
              "code to your registered WhatsApp number. Please reply with the code to continue.",
        "hi": "नमस्ते, मैं {lender} की वर्चुअल असिस्टेंट काइरा हूँ। आपके खाते की सुरक्षा के लिए हमने आपके रजिस्टर्ड "
              "WhatsApp नंबर पर 6 अंकों का कोड भेजा है। आगे बढ़ने के लिए कृपया वह कोड यहाँ लिखें।",
    },
    "ASK_LOAN_ID": {
        "en": "Hello, this is Kaira, the virtual assistant of {lender}. This number is not linked to an account I can "
              "verify. Please share your loan account number so I can guide you.",
        "hi": "नमस्ते, मैं {lender} की वर्चुअल असिस्टेंट काइरा हूँ। यह नंबर किसी ऐसे खाते से जुड़ा नहीं है जिसे मैं सत्यापित कर "
              "सकूँ। कृपया अपना लोन खाता नंबर बताएं।",
    },
    "UNREGISTERED": {
        "en": "For your security, I can verify an account only from the mobile number registered with it. If this "
              "account is registered with us, please message us from that number or use the helpline in your loan documents.",
        "hi": "आपकी सुरक्षा के लिए, मैं किसी खाते का सत्यापन केवल उसके रजिस्टर्ड मोबाइल नंबर से ही कर सकती हूँ। कृपया उसी नंबर "
              "से संदेश भेजें या अपने लोन दस्तावेज़ों में दी गई हेल्पलाइन पर संपर्क करें।",
    },
    "OTP_PROMPT": {
        "en": "Please reply with the 6-digit code sent to your registered WhatsApp number. Reply RESEND for a new code.",
        "hi": "कृपया अपने रजिस्टर्ड WhatsApp नंबर पर भेजा गया 6 अंकों का कोड लिखें। नया कोड पाने के लिए RESEND लिखें।",
    },
    "OTP_RESENT": {
        "en": "I have sent a new 6-digit code to your registered WhatsApp number. The earlier code no longer works.",
        "hi": "हमने आपके रजिस्टर्ड WhatsApp नंबर पर नया 6 अंकों का कोड भेजा है। पुराना कोड अब मान्य नहीं है।",
    },
    "OTP_WRONG": {
        "en": "That code did not match. You have {left} attempt(s) left.",
        "hi": "यह कोड मेल नहीं खाया। आपके पास {left} प्रयास बचे हैं।",
    },
    "OTP_EXPIRED": {
        "en": "That code has expired. Reply RESEND and I will send a new one to your registered number.",
        "hi": "यह कोड समाप्त हो गया है। RESEND लिखें, हम आपके रजिस्टर्ड नंबर पर नया कोड भेजेंगे।",
    },
    "OTP_LOCKED": {
        "en": "For your security, verification is locked for 30 minutes after too many attempts. A member of our team "
              "will reach out to you.",
        "hi": "आपकी सुरक्षा के लिए, कई गलत प्रयासों के बाद सत्यापन 30 मिनट के लिए रोक दिया गया है। हमारी टीम का एक सदस्य आपसे संपर्क करेगा।",
    },
    "OTP_LIMIT": {
        "en": "I cannot send more codes in this chat. A member of our team will reach out to you.",
        "hi": "इस चैट में और कोड नहीं भेजे जा सकते। हमारी टीम का एक सदस्य आपसे संपर्क करेगा।",
    },
    "WRONG_NUMBER": {
        "en": "Sorry for the trouble. We will review this number so it is not contacted again by mistake. No further "
              "action is needed from you.",
        "hi": "असुविधा के लिए खेद है। हम इस नंबर की समीक्षा करेंगे ताकि गलती से फिर संपर्क न हो। आपको कुछ और करने की ज़रूरत नहीं है।",
    },
    "STOP_ACK": {
        "en": "Hello, {lender} has noted your request. You will not receive further automated reminders from us on any "
              "channel. For any question about your loan, use the helpline in your loan documents.",
        "hi": "नमस्ते, {lender} ने आपका अनुरोध दर्ज कर लिया है। अब आपको किसी भी माध्यम पर हमसे स्वचालित रिमाइंडर नहीं मिलेंगे। "
              "किसी भी प्रश्न के लिए अपने लोन दस्तावेज़ों में दी गई हेल्पलाइन पर संपर्क करें।",
    },
    "FRAUD_REFUSE": {
        "en": "For your security, I can't help with this request in chat. A specialist from our team will review it. "
              "{lender} never asks for your OTP, PIN or password, and never sends links outside our official channels.",
        "hi": "आपकी सुरक्षा के लिए, मैं इस अनुरोध में चैट पर मदद नहीं कर सकती। हमारी टीम का विशेषज्ञ इसकी समीक्षा करेगा। "
              "{lender} कभी भी आपका OTP, PIN या पासवर्ड नहीं माँगता।",
    },
    "HANDOFF_HOLD": {
        "en": "Thank you. A member of our team has your request and will get back to you.",
        "hi": "धन्यवाद। हमारी टीम का एक सदस्य आपके अनुरोध पर आपसे संपर्क करेगा।",
    },
    "LANG_SET": {
        "en": "Sure, I will continue in English.",
        "hi": "ठीक है, अब मैं हिंदी में बात करूँगी।",
    },
    "PRE_VERIFY_NEED": {
        "en": "I can help with that once you are verified. Please reply with the 6-digit code sent to your registered "
              "WhatsApp number.",
        "hi": "सत्यापन के बाद मैं इसमें मदद कर सकती हूँ। कृपया अपने रजिस्टर्ड WhatsApp नंबर पर भेजा गया 6 अंकों का कोड लिखें।",
    },
    # ---------------- verified only ----------------
    "VERIFIED_MENU": {
        "en": "Thank you, you are verified. How can I help? Reply 1 Balance & due date, 2 Payment link, 3 I have "
              "already paid, 4 I need more time, 5 Statement / NOC, 6 Talk to a person.",
        "hi": "धन्यवाद, आपका सत्यापन हो गया है। मैं कैसे मदद करूँ? 1 बकाया और देय तिथि, 2 भुगतान लिंक, 3 मैंने भुगतान कर दिया है, "
              "4 मुझे और समय चाहिए, 5 स्टेटमेंट / NOC, 6 किसी व्यक्ति से बात करें।",
    },
    "VERIFIED_OK": {
        "en": "Thank you, you are verified.",
        "hi": "धन्यवाद, आपका सत्यापन हो गया है।",
    },
    "BALANCE": {
        "en": "Your {product} (account ending {last4}): amount overdue ₹{overdue}. Next EMI ₹{emi} due on {due_date}. "
              "Total outstanding on this loan ₹{outstanding}.",
        "hi": "आपका {product} (खाता अंतिम अंक {last4}): बकाया राशि ₹{overdue}। अगली EMI ₹{emi}, देय तिथि {due_date}। "
              "इस लोन पर कुल बकाया ₹{outstanding}।",
    },
    "NO_DUES": {
        "en": "Good news: there is nothing overdue on your loan (account ending {last4}) right now.",
        "hi": "अच्छी खबर: आपके लोन (खाता अंतिम अंक {last4}) पर अभी कुछ भी बकाया नहीं है।",
    },
    "PAY_LINK": {
        "en": "Here is your secure payment link for ₹{amount} (account ending {last4}), valid for 24 hours: {link}",
        "hi": "₹{amount} (खाता अंतिम अंक {last4}) के लिए आपका सुरक्षित भुगतान लिंक, 24 घंटे के लिए मान्य: {link}",
    },
    "ASK_UTR": {
        "en": "Thank you for letting us know. Please share the UTR / UPI reference number (or a screenshot) so I can "
              "check it with the bank.",
        "hi": "बताने के लिए धन्यवाद। कृपया UTR / UPI रेफरेंस नंबर (या स्क्रीनशॉट) भेजें ताकि मैं बैंक से इसकी जाँच कर सकूँ।",
    },
    "CLAIM_PENDING": {
        "en": "Thank you. I have recorded your payment (reference {ref}). It has not reached us from the bank yet; I am "
              "checking and will update you here. Your account is not marked paid until the bank confirms.",
        "hi": "धन्यवाद। आपका भुगतान (रेफरेंस {ref}) दर्ज कर लिया गया है। बैंक से पुष्टि अभी नहीं आई है; मैं जाँच कर रही हूँ "
              "और यहीं अपडेट दूँगी। बैंक की पुष्टि तक खाता भुगतान किया हुआ नहीं माना जाएगा।",
    },
    "CLAIM_NOREF": {
        "en": "Thank you. I have noted that you paid. Without a reference I will match it from the bank feed; it usually "
              "takes up to 2 days. I will update you here once it is confirmed.",
        "hi": "धन्यवाद। मैंने नोट कर लिया है कि आपने भुगतान किया है। बैंक फ़ीड से मिलान में 2 दिन तक लग सकते हैं; पुष्टि होते ही "
              "मैं यहीं अपडेट दूँगी।",
    },
    "RECEIPT": {
        "en": "Payment confirmed: {lender} has received ₹{amount} towards your loan (account ending {last4}). Receipt "
              "{receipt}. Reminders for this EMI are stopped.{remaining}",
        "hi": "भुगतान की पुष्टि: {lender} को आपके लोन (खाता अंतिम अंक {last4}) के लिए ₹{amount} प्राप्त हुए। रसीद {receipt}। "
              "इस EMI के रिमाइंडर बंद कर दिए गए हैं।{remaining}",
    },
    "REMAINING_DUE": {
        "en": " ₹{left} is still due on this EMI.",
        "hi": " इस EMI पर ₹{left} अभी भी बकाया है।",
    },
    "PAYMENT_REVERSED": {
        "en": "The bank shows this payment as reversed, so it has not been applied. A member of our team will contact you.",
        "hi": "बैंक के अनुसार यह भुगतान वापस हो गया है, इसलिए इसे लागू नहीं किया गया। हमारी टीम का सदस्य आपसे संपर्क करेगा।",
    },
    "ASK_PTP_DATE": {
        "en": "I understand. By which date in the next {days} days can you pay? For example, reply with a date like 25 Nov.",
        "hi": "मैं समझती हूँ। अगले {days} दिनों में आप किस तारीख तक भुगतान कर सकते हैं? जैसे: 25 तारीख।",
    },
    "PTP_TOO_FAR": {
        "en": "I can note a date up to {max_date}. Could you pay by then?",
        "hi": "मैं {max_date} तक की तारीख दर्ज कर सकती हूँ। क्या आप तब तक भुगतान कर पाएंगे?",
    },
    "PTP_CONFIRM": {
        "en": "Thank you. {lender} has noted your commitment to pay by {date}. We will pause reminders until then. If you "
              "would like to pay earlier, use this link: {link}",
        "hi": "धन्यवाद। {lender} ने {date} तक आपके भुगतान का वादा दर्ज कर लिया है। तब तक हम रिमाइंडर रोक देंगे। पहले भुगतान "
              "करना चाहें तो इस लिंक का उपयोग करें: {link}",
    },
    "PTP_REFERRED": {
        "en": "Thank you. I have noted {date}. As it is beyond {days} days, a member of our team will confirm it with you.",
        "hi": "धन्यवाद। मैंने {date} दर्ज कर लिया है। यह {days} दिनों से आगे है, इसलिए हमारी टीम का सदस्य आपसे पुष्टि करेगा।",
    },
    "PTP_NO_HOLD": {
        "en": "I have noted {date}. As earlier payment dates were missed, reminders will continue. You can pay now here: {link}",
        "hi": "मैंने {date} दर्ज कर लिया है। पिछली तारीखें चूक गई थीं, इसलिए रिमाइंडर जारी रहेंगे। अभी भुगतान करें: {link}",
    },
    "NEEDS_TIME_NOTED": {
        "en": "Understood. I have noted that you need more time. You can pay any time with this link: {link}",
        "hi": "समझ गई। मैंने नोट कर लिया है कि आपको और समय चाहिए। आप कभी भी इस लिंक से भुगतान कर सकते हैं: {link}",
    },
    "STATEMENT": {
        "en": "Here is your account statement for the loan ending {last4} (secure link, valid 24 hours): {link}",
        "hi": "लोन (अंतिम अंक {last4}) का आपका स्टेटमेंट (सुरक्षित लिंक, 24 घंटे मान्य): {link}",
    },
    "NOC_READY": {
        "en": "Your loan ending {last4} is fully closed. Your NOC is ready (secure link, valid 24 hours): {link}",
        "hi": "आपका लोन (अंतिम अंक {last4}) पूरी तरह बंद है। आपका NOC तैयार है (सुरक्षित लिंक, 24 घंटे मान्य): {link}",
    },
    "NOC_NOT_YET": {
        "en": "A NOC is issued once the loan is fully closed. Your loan ending {last4} still has ₹{outstanding} "
              "outstanding and {emis} EMI(s) left.",
        "hi": "NOC लोन पूरी तरह बंद होने पर जारी होता है। आपके लोन (अंतिम अंक {last4}) पर अभी ₹{outstanding} बकाया है "
              "और {emis} EMI बाकी हैं।",
    },
    "LOAN_DETAILS": {
        "en": "{product} (account ending {last4}): sanctioned ₹{principal}, EMI ₹{emi}, EMIs left {emis}, next due "
              "{due_date}.",
        "hi": "{product} (खाता अंतिम अंक {last4}): स्वीकृत ₹{principal}, EMI ₹{emi}, बची EMI {emis}, अगली देय तिथि {due_date}।",
    },
    "CALLBACK": {
        "en": "Thank you. I have noted your request for a call back. A member of our team will call you during working "
              "hours (8 AM to 7 PM, Monday to Saturday).",
        "hi": "धन्यवाद। आपका कॉल बैक अनुरोध दर्ज कर लिया गया है। हमारी टीम का सदस्य कामकाजी समय (सोमवार से शनिवार, सुबह 8 "
              "से शाम 7 बजे) में आपको कॉल करेगा।",
    },
    "HARDSHIP": {
        "en": "I am sorry to hear about your situation, and thank you for telling us. I have paused reminders for now, and "
              "a member of our team will reach out to discuss the options available to you.",
        "hi": "आपकी स्थिति के बारे में जानकर हमें खेद है, बताने के लिए धन्यवाद। हमने अभी के लिए रिमाइंडर रोक दिए हैं, और हमारी "
              "टीम का सदस्य आपसे उपलब्ध विकल्पों पर बात करने के लिए संपर्क करेगा।",
    },
    "ROUTED": {
        "en": "Thank you for raising this. I have registered it (ticket {ticket_ref}) and a member of our team will review it "
              "and get back to you. I cannot make any commitment on it in chat.",
        "hi": "यह बताने के लिए धन्यवाद। मैंने इसे दर्ज कर लिया है (टिकट {ticket_ref}), हमारी टीम का सदस्य इसकी समीक्षा कर आपसे "
              "संपर्क करेगा। चैट पर मैं इस पर कोई वादा नहीं कर सकती।",
    },
    "BROKEN_PTP_PUSH": {  # K13 broken_ptp >= threshold: no new date is solicited; pay now
        "en": "I see that two earlier payment dates were missed, so I would request you to pay now if you can. "
              "You can pay here: {link}",
        "hi": "मैं देख रही हूँ कि पिछली दो भुगतान तिथियाँ चूक गई थीं, इसलिए आपसे अनुरोध है कि हो सके तो अभी भुगतान करें। "
              "भुगतान लिंक: {link}",
    },
    "END_CONVERSATION": {  # K13 ABUSIVE / LEGAL_THREAT / FRAUD_CLAIM: no argument, routed to a senior team member
        "en": "I have noted your message and passed it to a senior member of our team, who will follow up with you.",
        "hi": "मैंने आपका संदेश दर्ज कर लिया है और हमारी टीम के एक वरिष्ठ सदस्य को भेज दिया है, वे आपसे संपर्क करेंगे।",
    },
    "CLARIFY": {
        "en": "Sorry, I did not get that. Reply 1 Balance & due date, 2 Payment link, 3 I have already paid, 4 I need more "
              "time, 5 Statement / NOC, 6 Talk to a person.",
        "hi": "माफ़ कीजिए, मैं समझ नहीं पाई। 1 बकाया और देय तिथि, 2 भुगतान लिंक, 3 मैंने भुगतान कर दिया है, 4 मुझे और समय "
              "चाहिए, 5 स्टेटमेंट / NOC, 6 किसी व्यक्ति से बात करें।",
    },
    "UNKNOWN_HANDOFF": {
        "en": "I want to make sure you get the right help, so I have passed this to a member of our team. They will get back to you.",
        "hi": "आपको सही मदद मिले, इसलिए मैंने यह हमारी टीम के सदस्य को भेज दिया है। वे आपसे संपर्क करेंगे।",
    },
    "ANYTHING_ELSE": {
        "en": "Is there anything else I can help with?",
        "hi": "क्या मैं और कुछ मदद कर सकती हूँ?",
    },
    "GOODBYE": {
        "en": "Thank you for contacting {lender}. Have a good day.",
        "hi": "{lender} से संपर्क करने के लिए धन्यवाद। आपका दिन शुभ हो।",
    },
    "OTP_MESSAGE": {  # delivered only to the registered number through the adapter; never logged
        "en": "{code} is your {lender} verification code. It is valid for 5 minutes. Never share it with anyone, "
              "including our staff.",
        "hi": "{code} आपका {lender} सत्यापन कोड है। यह 5 मिनट के लिए मान्य है। इसे किसी के साथ साझा न करें।",
    },
}

PRE_VERIFY_SAFE = frozenset({
    "GREET_VERIFY", "ASK_LOAN_ID", "UNREGISTERED", "OTP_PROMPT", "OTP_RESENT", "OTP_WRONG", "OTP_EXPIRED", "OTP_LOCKED",
    "OTP_LIMIT", "WRONG_NUMBER", "STOP_ACK", "FRAUD_REFUSE", "HANDOFF_HOLD", "LANG_SET", "PRE_VERIFY_NEED",
    "UNKNOWN_HANDOFF", "GOODBYE",
    # K10 allowed_before_verification (no account field in any of these)
    "HARDSHIP", "CALLBACK", "END_CONVERSATION",
    # K10 scripts used in identity_mode="k10" before verification
    "ACK", "ASK_NAME", "NAME_RETRY", "NAME_FAILED", "STOP_CONFIRMED", "HARDSHIP_ACK", "CALLBACK_CREATED",
    "WRONG_PERSON", "INJECTION_SAFE", "PAY_NOT_MATCHED",
})


def render(key: str, lang: str, **kw) -> str:
    table = COPY[key]
    text = table.get(lang) or table["en"]
    return text.format(lender=LENDER, **kw)
