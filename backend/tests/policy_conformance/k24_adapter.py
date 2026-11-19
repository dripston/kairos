"""K24 conformance: Ojas's K24 reference checks, evaluated against OUR spine/intake.py (not his script).

The checks mirror, in order, what policy/K24-portfolio-intake/reference_check.py prints. Note: his script
prints 32 PASS lines but its summary formula counts 31 (13 + 2 + 7 + 7 + 2; the preview block has 8 checks).
We report all 32.
"""
from __future__ import annotations

import csv
from datetime import date
from pathlib import Path
from typing import Callable, List

from spine import intake as K

K24_DIR = Path(K.INTAKE_PATH).parent
UPLOAD_DATE = date(2026, 11, 20)   # DPD 42 on a 9 Oct due date -> the files were cut on 20 Nov 2026


def _csv(name: str) -> List[dict]:
    with open(K24_DIR / name, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _v(name: str) -> K.IntakeResult:
    return K.validate(K.read_upload(str(K24_DIR / name)), UPLOAD_DATE)


def run_k24() -> List[dict]:
    out: List[dict] = []

    def check(desc: str, fn: Callable[[], object], status_if_error: str = "FAIL") -> None:
        try:
            ok, detail = fn()
            status = "PASS" if ok else "FAIL"
        except NotImplementedError as e:
            status, detail = "NOT_IMPLEMENTED", str(e)
        except Exception as e:  # a crash in our code is a FAIL, with the reason
            status, detail = status_if_error, f"{type(e).__name__}: {e}"
        out.append({"case_id": f"K24-C{len(out) + 1:02d}", "description": desc, "status": status, "detail": str(detail)})

    v1 = _v("sample_upload_v1.csv")
    got = v1.status()
    # 1. validation (expected_v1.csv, one check per row)
    for e in _csv("expected_v1.csv"):
        want = (e["expected_status"], e["expected_reason"])
        check(f"{e['loan_id']} -> {want[0]} {want[1]}".strip(),
              lambda e=e, want=want: (got.get(e["loan_id"]) == want, f"got {got.get(e['loan_id'])}"))
    check("duplicate loan_id: both copies quarantined",
          lambda: (sum(1 for q in v1.quarantined if q.loan_id == "L1007" and q.reason == "DUPLICATE_LOAN_ID") == 2,
                   f"{sum(1 for q in v1.quarantined if q.loan_id == 'L1007')} copies"))
    check("mobile '+91 98765 43210' normalised",
          lambda: (next(r for r in v1.accepted if r.loan_id == "L1002").registered_mobile == "9876543210", "normalised"))

    # 2. preview / arm
    camp = K.Campaign(date(2026, 11, 22), date(2026, 12, 21), budget_per_case_paise=1000)
    pv0 = K.preview(v1, camp)
    check("budgeted set not specified: nothing budgeted, arming blocked",
          lambda: (pv0["budgeted_loans"] == 0 and not pv0["can_arm"], pv0["arm_blocked_reason"]))
    eligible_ids = [r.loan_id for r in K.budget_scope(v1.accepted)[0]]
    camp.budgeted_loan_ids = eligible_ids   # ops user confirms "all eligible"
    pv = K.preview(v1, camp)
    check("start skips Sunday 22 Nov -> Mon 23 Nov 08:00",
          lambda: (pv["first_contact"] == "2026-11-23 08:00", pv["first_contact"]))
    check("end date inclusive, no sends from 19:00",
          lambda: (pv["last_contact"] == "2026-12-21 before 19:00", pv["last_contact"]))
    check("out-of-scope L1011 (DPD -10) never eligible for budget",
          lambda: (pv["eligible_loans"] == 5 and "L1011" not in eligible_ids, f"eligible={pv['eligible_loans']}"))
    check("pool = Rs 10 x 5 budgeted loans",
          lambda: (pv["campaign_pool_inr"] == 50 and pv["can_arm"], f"pool Rs {pv['campaign_pool_inr']}"))
    camp2 = K.Campaign(date(2026, 11, 22), date(2026, 12, 21), 1000, ["L1001", "L1002", "L1011", "L1004"])
    pv2 = K.preview(v1, camp2)
    check("explicit selection: out-of-scope and quarantined loans ignored; pool = Rs 10 x 2",
          lambda: (pv2["budgeted_loans"] == 2 and pv2["campaign_pool_inr"] == 20 and pv2["customers_covered"] == 1,
                   f"budgeted={pv2['budgeted_loans']} pool Rs {pv2['campaign_pool_inr']}"))
    split = K.charge_touch(K.budget_scope(v1.accepted, ["L1001", "L1002"])[1], "C-1042", 40)
    check("Rs 0.40 touch to C-1042 split equally across its 2 budgeted loans",
          lambda: (split == {"L1001": 20, "L1002": 20}, f"paise {split}"))
    check("C-1042 grouped as one contact unit with 2 loans",
          lambda: (pv["customers"] == 5 and pv["customers_with_2plus_loans"] == 1, f"customers={pv['customers']}"))

    # 3. re-upload merge
    first = K.merge(None, v1)
    for lid, l in first.state.loans.items():
        l["journey"] = ["SMS delivered"]          # history the merge must keep
    v2 = _v("sample_upload_v2.csv")
    second = K.merge(first.state, v2)
    for e in _csv("expected_merge_v2.csv"):
        check(f"merge {e['loan_id']} -> {e['expected_change']}",
              lambda e=e: (second.changes.get(e["loan_id"]) == e["expected_change"], f"got {second.changes.get(e['loan_id'])}"))
    l1001 = second.state.loans["L1001"]
    check("updated loan keeps journey history",
          lambda: (l1001["journey"] == ["SMS delivered"] and l1001["overdue_paise"] == 0, f"journey={l1001['journey']}"))
    third = K.merge(second.state, v2)
    check("same file again: idempotent, nothing changes",
          lambda: (all(v == K.UNCHANGED for v in third.changes.values()) and third.version == second.version,
                   f"version {third.version}"))
    return out


if __name__ == "__main__":   # quick look: python -m tests.policy_conformance.k24_adapter
    rows = run_k24()
    for r in rows:
        print(f"{r['status']:15} {r['case_id']} {r['description']}  [{r['detail']}]")
    print({s: sum(1 for r in rows if r["status"] == s) for s in ("PASS", "FAIL", "NOT_IMPLEMENTED", "NOT_APPLICABLE")})
