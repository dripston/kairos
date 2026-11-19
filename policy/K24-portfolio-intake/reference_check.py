"""
Kairos K24 - reference portfolio intake (stdlib only, portable).

validate(file)        -> accepted rows, quarantined rows (with reason), warnings
preview(...)          -> what the ops user sees before arming the campaign
merge(state, upload)  -> re-upload: update / add / mark removed, keep journey state
first/last contact    -> campaign window from allocation dates + K01 quiet days

Usage:  python3 reference_check.py      (runs the checks against the sample files)
"""
import csv, json, math, os, re, sys
from collections import Counter
from datetime import date, datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DATE = date(2026, 11, 20)   # fixed for reproducible checks


def load_rules():
    rules = json.load(open(os.path.join(HERE, "intake_rules.json")))
    k01 = json.load(open(os.path.join(HERE, "..", "K01-decision-policy", "policy.json")))
    return rules, k01


def hkey(h):
    """Header key: case-insensitive; spaces, hyphens, dots and underscores ignored."""
    return re.sub(r"[\s_\-.]+", "", h.strip().lower())


def map_headers(rules, headers):
    alias = {hkey(a): canon for canon, al in rules["file"]["column_aliases"].items() for a in al}
    return {h: alias.get(hkey(h), h.strip().lower()) for h in headers}


def norm_mobile(m):
    m = re.sub(r"[\s\-]", "", m or "")
    if m.startswith("+91"): m = m[3:]
    elif m.startswith("91") and len(m) == 12: m = m[2:]
    if m.startswith("0"): m = m[1:]
    return m


def parse_date(s):
    for f in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(s.strip(), f).date()
        except (ValueError, AttributeError):
            pass
    return None


def num(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def validate(rules, path):
    with open(path, newline="", encoding="utf-8") as fh:
        rd = csv.DictReader(fh)
        hmap = map_headers(rules, rd.fieldnames)
        rows = [{hmap[k]: (v or "").strip() for k, v in r.items()} for r in rd]
    counts = Counter(r.get("loan_id", "") for r in rows)
    accepted, quarantined = [], []
    for r in rows:
        reason = None
        for f in rules["mandatory_fields"]:
            if not r.get(f):
                reason = "MISSING_FIELD:" + f
                break
        if not reason and counts[r["loan_id"]] > 1:
            reason = "DUPLICATE_LOAN_ID"
        if not reason:
            r["registered_mobile"] = norm_mobile(r["registered_mobile"])
            if not re.fullmatch(r"[6-9]\d{9}", r["registered_mobile"]):
                reason = "INVALID_MOBILE"
        if not reason:
            emi, od = num(r["emi_amount"]), num(r["overdue_amount"])
            if emi is None or emi <= 0 or od is None or od < 0:
                reason = "INVALID_AMOUNT"
        if not reason and parse_date(r["due_date"]) is None:
            reason = "INVALID_DATE"
        if not reason and not re.fullmatch(r"-?\d+", r["dpd"]):
            reason = "INVALID_DPD"
        if reason:
            quarantined.append((r.get("loan_id", ""), reason))
            continue
        warn = []
        dpd, due = int(r["dpd"]), parse_date(r["due_date"])
        if dpd < -6:
            warn.append("OUT_OF_SCOPE_DPD")
        if abs((UPLOAD_DATE - due).days - dpd) > 1 and dpd >= -6:
            warn.append("DPD_DUE_DATE_MISMATCH")
        if not any(r.get(c) for c in ("consent_sms", "consent_whatsapp", "consent_email", "consent_ivr", "consent_voicebot")):
            warn.append("CONSENT_NOT_PROVIDED")
        r["_warnings"] = warn
        accepted.append(r)
    return rows, accepted, quarantined


def contact_days(k01, start, end):
    qd = k01["contact_window"]["quiet_days"]
    d = start
    while d <= end:
        if d.strftime("%a").upper()[:3] not in qd["weekdays"] and d.isoformat() not in qd["national_holidays"]:
            yield d
        d += timedelta(days=1)


def budget_scope(accepted, selected=None):
    """Eligible = accepted and in scope (DPD >= -6). Budgeted = eligible AND explicitly selected.
    selected=None means the user has not specified the set: nothing is budgeted and arming is blocked."""
    eligible = [r for r in accepted if int(r["dpd"]) >= -6]
    if selected is None:
        return eligible, []
    sel = set(selected)
    return eligible, [r for r in eligible if r["loan_id"] in sel]


def charge_touch(budgeted, customer_id, cost):
    """A touch to a customer is charged across their budgeted loans in equal shares."""
    loans = [r["loan_id"] for r in budgeted if r["customer_id"] == customer_id]
    return {lid: round(cost / len(loans), 4) for lid in loans}


def preview(k01, rows, accepted, quarantined, campaign):
    days = list(contact_days(k01, campaign["allocation_start_date"], campaign["allocation_end_date"]))
    eligible, budgeted = budget_scope(accepted, campaign.get("budgeted_loan_ids"))
    loans_per_cust = Counter(r["customer_id"] for r in accepted)
    return {
        "rows_received": len(rows),
        "accepted": len(accepted),
        "quarantined_by_reason": dict(Counter(q[1].split(":")[0] for q in quarantined)),
        "warnings_by_type": dict(Counter(w for r in accepted for w in r["_warnings"])),
        "customers": len(loans_per_cust),
        "customers_with_2plus_loans": sum(1 for c in loans_per_cust.values() if c > 1),
        "eligible_loans": len(eligible),
        "budgeted_loans": len(budgeted),
        "eligible_not_selected": len(eligible) - len(budgeted),
        "customers_covered": len({r["customer_id"] for r in budgeted}),
        "campaign_pool_inr": campaign["budget_per_case_inr"] * len(budgeted),
        "first_contact": f'{days[0]} {k01["contact_window"]["start"]}' if days else None,
        "last_contact": f'{days[-1]} before {k01["contact_window"]["end_exclusive"]}' if days else None,
        "contact_days": len(days),
        "can_arm": len(budgeted) >= 1 and bool(days),
    }


TRACKED = ("customer_id", "borrower_name", "registered_mobile", "emi_amount", "overdue_amount", "due_date", "dpd")


def merge(state, accepted):
    """state: loan_id -> record (with journey fields). Returns (new_state, changes)."""
    changes, incoming = {}, {r["loan_id"]: r for r in accepted}
    for lid, rec in state.items():
        if lid not in incoming:
            if rec.get("status") != "REMOVED_BY_LENDER":
                rec = dict(rec, status="REMOVED_BY_LENDER")
                changes[lid] = "REMOVED_BY_LENDER"
            state[lid] = rec
    for lid, r in incoming.items():
        if lid not in state:
            state[lid] = {**{k: r[k] for k in TRACKED}, "status": "ACTIVE", "journey": [], "holds": []}
            changes[lid] = "ADDED"
        else:
            old = state[lid]
            diff = {k: r[k] for k in TRACKED if old.get(k) != r[k]}
            if diff:
                state[lid] = {**old, **diff, "status": "ACTIVE"}   # journey / holds kept
                changes[lid] = "UPDATED"
            else:
                changes.setdefault(lid, "UNCHANGED")
    return state, changes


def main():
    rules, k01 = load_rules()
    fails = 0

    def check(ok, label):
        nonlocal fails
        fails += not ok
        print(("PASS  " if ok else "FAIL  ") + label)

    # 1. validation of v1
    rows, acc, quar = validate(rules, os.path.join(HERE, "sample_upload_v1.csv"))
    got = {r["loan_id"]: ("ACCEPTED", ";".join(r["_warnings"])) for r in acc}
    for lid, why in quar:
        got[lid] = ("QUARANTINED", why)
    for e in csv.DictReader(open(os.path.join(HERE, "expected_v1.csv"))):
        g = got.get(e["loan_id"])
        check(g == (e["expected_status"], e["expected_reason"]), f'{e["loan_id"]:6} {g}  expected {(e["expected_status"], e["expected_reason"])}')
    check(sum(1 for q in quar if q[0] == "L1007") == 2, "duplicate loan_id: both copies quarantined")
    check(next(r for r in acc if r["loan_id"] == "L1002")["registered_mobile"] == "9876543210", "mobile '+91 98765 43210' normalised")

    # 2. preview / arm
    camp = {"allocation_start_date": date(2026, 11, 22), "allocation_end_date": date(2026, 12, 21), "budget_per_case_inr": 10}
    pv0 = preview(k01, rows, acc, quar, camp)
    check(pv0["budgeted_loans"] == 0 and not pv0["can_arm"], "budgeted set not specified: nothing budgeted, arming blocked")
    eligible_ids = [r["loan_id"] for r in budget_scope(acc)[0]]
    camp["budgeted_loan_ids"] = eligible_ids          # user confirms 'all eligible'
    pv = preview(k01, rows, acc, quar, camp)
    print("      preview:", json.dumps(pv))
    check(pv["first_contact"] == "2026-11-23 08:00", "start skips Sunday 22 Nov -> Mon 23 Nov 08:00")
    check(pv["last_contact"] == "2026-12-21 before 19:00", "end date inclusive, no sends from 19:00")
    check(pv["eligible_loans"] == 5 and "L1011" not in eligible_ids, "out-of-scope L1011 (DPD -10) never eligible for budget")
    check(pv["campaign_pool_inr"] == 50 and pv["can_arm"], "pool = Rs 10 x 5 budgeted loans")
    camp["budgeted_loan_ids"] = ["L1001", "L1002", "L1011", "L1004"]  # L1011 out of scope, L1004 quarantined
    pv2 = preview(k01, rows, acc, quar, camp)
    check(pv2["budgeted_loans"] == 2 and pv2["campaign_pool_inr"] == 20 and pv2["customers_covered"] == 1,
          "explicit selection: out-of-scope and quarantined loans ignored; pool = Rs 10 x 2")
    check(charge_touch(budget_scope(acc, ["L1001", "L1002"])[1], "C-1042", 0.40) == {"L1001": 0.2, "L1002": 0.2},
          "Rs 0.40 touch to C-1042 split equally across its 2 budgeted loans")
    check(pv["customers"] == 5 and pv["customers_with_2plus_loans"] == 1, "C-1042 grouped as one contact unit with 2 loans")

    # 3. re-upload merge
    state = {r["loan_id"]: {**{k: r[k] for k in TRACKED}, "status": "ACTIVE", "journey": ["SMS delivered"], "holds": []} for r in acc}
    _, acc2, _ = validate(rules, os.path.join(HERE, "sample_upload_v2.csv"))
    state, ch = merge(state, acc2)
    for e in csv.DictReader(open(os.path.join(HERE, "expected_merge_v2.csv"))):
        check(ch.get(e["loan_id"]) == e["expected_change"], f'merge {e["loan_id"]:6} {ch.get(e["loan_id"])}')
    check(state["L1001"]["journey"] == ["SMS delivered"] and state["L1001"]["overdue_amount"] == "0", "updated loan keeps journey history")
    _, ch2 = merge(state, acc2)
    check(all(v == "UNCHANGED" for k, v in ch2.items()), "same file again: idempotent, nothing changes")

    total = len(open(os.path.join(HERE, "expected_v1.csv")).read().splitlines()) - 1 + 2 + 7 + 7 + 2
    print(f"\n{total - fails}/{total} passed  (intake {rules['policy_version']})")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
