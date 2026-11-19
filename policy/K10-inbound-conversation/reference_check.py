"""
Kairos K10 - reference conversation gate (stdlib only, portable).

Given the conversation state and the detected intent, returns which script
key Kaira sends and whether any account data is shared. Intent detection
itself (LLM / classifier) is out of scope here; this is the deterministic
gate around it. Also validates scripts.csv covers every key in all 3 languages.
Usage:  python3 reference_check.py
"""
import csv, json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
HONORIFICS = {"mr", "mrs", "ms", "shri", "smt", "sri", "ji", "dr"}
SHARES_DATA = {"DUES", "PAYMENT_LINK", "STATEMENT", "PAY_CONFIRMED", "BROKEN_PTP_PUSH"}
PRE_VERIFICATION = {  # allowed without verification
    "STOP": "STOP_CONFIRMED", "HARDSHIP": "HARDSHIP_ACK", "CALLBACK_REQUEST": "CALLBACK_CREATED",
    "ABUSIVE": "END_CONVERSATION", "LEGAL_THREAT": "END_CONVERSATION", "FRAUD_CLAIM": "END_CONVERSATION",
    "WRONG_PERSON": "WRONG_PERSON", "INJECTION": "INJECTION_SAFE",
}


def norm_words(name):
    words = re.sub(r"[^a-z ]", " ", name.lower()).split()
    return [w for w in words if w not in HONORIFICS]


def edits(a, b):
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def name_matches(typed, record):
    t, r = norm_words(typed), norm_words(record)
    if not t or not r:
        return False
    # Typo tolerance. Short names (<= 6 letters) differ by one letter often enough
    # (Rajesh/Ramesh/Rakesh) that only ONE missing or extra letter is allowed, never a
    # substituted one. Longer words allow up to 2 edits of any kind.
    def close(x, y):
        if len(y) <= 6:
            d = abs(len(x) - len(y))
            return x == y or (d == 1 and edits(x, y) == 1)
        return edits(x, y) <= 2
    if len(t) == 1:                      # first name only
        return close(t[0], r[0])
    if len(t) == len(r):                 # full name
        return all(close(x, y) for x, y in zip(t, r))
    return close(t[0], r[0]) and close(t[-1], r[-1])   # first + last


def respond(pol, c):
    intent = c["intent"]
    verified = c["verified"] == "Y"
    if intent == "NEW_MESSAGE":
        return "ACK"
    if intent in PRE_VERIFICATION:       # safety intents never wait for verification
        return PRE_VERIFICATION[intent]
    if c["sender_registered"] != "Y":
        return "UNREGISTERED"
    if intent == "NAME_REPLY":
        if name_matches(c["name_typed"], c["name_on_record"]):
            return "VERIFIED"
        attempts = int(c["name_attempts_before"] or 0) + 1
        return "NAME_FAILED" if attempts >= pol["identity"]["name_match"]["max_attempts"] else "NAME_RETRY"
    if not verified:
        return "ASK_NAME"
    broken = int(c["broken_ptp_count"] or 0)
    if intent in ("FORECLOSURE_REQUEST", "DISPUTE", "COMPLAINT", "WAIVER_REQUEST"):
        return "ROUTED"
    if intent == "DUES_QUERY":
        return "DUES"
    if intent == "PAYMENT_LINK_REQUEST":
        return "PAYMENT_LINK"
    if intent == "STATEMENT_REQUEST":
        return "STATEMENT"
    if intent == "PAYMENT_CLAIM":
        return {"NO_PROOF": "PAY_ASK_PROOF", "PENDING": "PAY_PENDING", "SETTLED": "PAY_CONFIRMED",
                "EDITED": "PAY_NOT_MATCHED", "MISMATCH": "PAY_NOT_MATCHED"}[c["recon_status"]]
    if intent == "NEEDS_TIME":
        return "BROKEN_PTP_PUSH" if broken >= 2 else "NT_ASK_DATE"
    if intent == "PTP_OFFERED":
        if broken >= 2:
            return "BROKEN_PTP_PUSH"
        return "PTP_CAPTURED_REVIEW" if int(c["ptp_offset_days"]) > 7 else "PTP_CAPTURED"
    raise ValueError("unhandled intent " + intent)


def main():
    pol = json.load(open(os.path.join(HERE, "conversation_policy.json")))
    scripts = {r["key"]: r for r in csv.DictReader(open(os.path.join(HERE, "scripts.csv"), encoding="utf-8"))}
    rows = list(csv.DictReader(open(os.path.join(HERE, "test_cases.csv"))))
    fails = 0
    for r in rows:
        key = respond(pol, r)
        shared = "Y" if key in SHARES_DATA else "N"
        exp = (r["expected_response"], r["expected_account_data_shared"])
        problems = []
        if (key, shared) != exp:
            problems.append(f"expected {exp}")
        if key not in scripts or not all(scripts[key][l].strip() for l in ("en", "hinglish", "hi")):
            problems.append("missing script text")
        if shared == "Y" and r["verified"] != "Y":
            problems.append("DISCLOSURE BEFORE VERIFICATION")
        fails += bool(problems)
        print(f'{"FAIL" if problems else "PASS"}  {r["case_id"]:8} {key:20} shared={shared}  {r["description"]}'
              + (("   <-- " + "; ".join(problems)) if problems else ""))
    print(f"\n{len(rows) - fails}/{len(rows)} passed  (conversation {pol['policy_version']}, {len(scripts)} scripts x 3 languages)")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
