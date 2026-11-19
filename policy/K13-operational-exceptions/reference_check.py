"""
Kairos K13 - reference exception handling (stdlib only, portable).

Maps one borrower event to (action, hold, queue) using exceptions.json.
Port this decision table into the conversation agent (CM-03) and the
Supervisor (K06). Usage:  python3 reference_check.py
"""
import csv, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROUTED_CONTINUE = {"DISPUTE", "COMPLAINT", "WAIVER_REQUEST"}
ROUTED_STOP = {"ABUSIVE", "LEGAL_THREAT", "FRAUD_CLAIM"}


def handle(x, c):
    """Return (action, hold, queue) for one event. x = exceptions.json."""
    q = x["queues"]["default"]
    ev, band = c["event"], c["band"]
    num = lambda k: int(c[k]) if c.get(k, "").strip() else None
    broken = num("broken_ptp_count") or 0
    thr = x["promise_to_pay"]["broken_ptp"]["threshold"]

    if ev == "NEEDS_TIME":
        if broken >= thr:
            return "PUSH_PAYMENT_NOW", "NONE", ""
        if c["in_window"] != "Y":
            return "ACK_ONLY", "STOP_FOR_DAY", ""
        if (num("rebuttals_used") or 0) < x["needs_time"]["max_rebuttals_by_band"][band]:
            return "REBUTTAL", "NONE", ""
        return "ACCEPT_NO_DATE", "STOP_FOR_DAY", ""

    if ev == "PTP_OFFERED":
        if broken >= thr:
            if c["paid_now"] == "Y":
                return "VERIFY_PAYMENT", "NONE", ""
            return "RECORD_PTP_NO_HOLD", "STOP_FOR_DAY", ""
        far = num("date_offset_days") > x["promise_to_pay"]["auto_accept_max_days"]
        return "CAPTURE_PTP", "PTP_HOLD", (q if far else "")

    if ev == "CALLBACK_REQUEST":
        return "CREATE_CALLBACK_TASK", "CALLBACK_PENDING", q
    if ev == "CALLBACK_DUE":
        if c["callback_answered"] == "Y":
            return "CLOSE_CALLBACK_TASK", "STOP_FOR_DAY", ""
        if num("callback_attempts") < 2:  # one retry allowed
            return "RETRY_CALLBACK_2H", "CALLBACK_PENDING", q
        return "SEND_MISSED_CALLBACK_MSG", "STOP_FOR_DAY", ""

    if ev == "HARDSHIP":
        d = num("date_offset_days")
        if d is None:
            return "HOLD_2_DAYS", "HARDSHIP_HOLD", ""
        return "HOLD_UNTIL_DATE", "HARDSHIP_HOLD", (q if d > 4 else "")

    if ev in ROUTED_CONTINUE:
        return "CREATE_TICKET", "STOP_FOR_DAY", q
    if ev in ROUTED_STOP:
        return "END_CONVERSATION_AND_TICKET", "SUPERVISOR_HOLD", q
    raise ValueError("unknown event " + ev)


def main():
    x = json.load(open(os.path.join(HERE, "exceptions.json")))
    rows = list(csv.DictReader(open(os.path.join(HERE, "test_cases.csv"))))
    fails = 0
    for r in rows:
        got = handle(x, r)
        exp = (r["expected_action"], r["expected_hold"], r["expected_queue"])
        ok = got == exp
        fails += not ok
        print(f'{"PASS" if ok else "FAIL"}  {r["case_id"]:8} {got[0]:28} {got[1]:16} {got[2] or "-":22} {r["description"]}'
              + ("" if ok else f"   <-- expected {exp}"))
    print(f"\n{len(rows) - fails}/{len(rows)} passed  (exceptions {x['policy_version']})")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
