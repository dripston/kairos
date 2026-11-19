"""K24 portfolio intake: validate a lender upload, preview the campaign, merge re-uploads.

Rules come from Ojas's policy/K24-portfolio-intake/intake_rules.json (mandatory fields, column
aliases, quarantine reasons, warnings) and K01 policy.json (contact window, quiet days, DPD scope).
Nothing here talks to the database: the caller turns accepted loans into spine rows and calls
Ledger.enroll() with the budgeted set (and Ledger.unenroll() for loans the lender removed).

    rows = read_upload(path)                                 # CSV; header in row 1
    res = validate(rows, upload_date)                        # accepted / quarantined / warnings
    camp = Campaign(date(2026, 11, 22), date(2026, 12, 21), budget_per_case_paise=1000)
    preview(res, camp)                                       # can_arm False until a budgeted set is chosen
    camp.budgeted_loan_ids = [r.loan_id for r in budget_scope(res.accepted)[0]]   # "all eligible", confirmed
    state = merge(None, res)                                 # first upload = version 1
    state2 = merge(state.state, validate(read_upload(v2), upload_date))   # UPDATED / UNCHANGED / ADDED / REMOVED_BY_LENDER

Money is kept in paise (K03 convention); preview() also reports rupees for comparison with the
policy CSVs. Mobiles are kept normalised (10 digits) and are never logged by this module.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Dict, Iterable, List, Optional, Tuple

from spine.policy import POLICY_PATH, load_policy as load_k01

INTAKE_PATH = os.path.join(os.path.dirname(os.path.dirname(POLICY_PATH)), "K24-portfolio-intake", "intake_rules.json")

ACCEPTED, QUARANTINED = "ACCEPTED", "QUARANTINED"
UPDATED, UNCHANGED, ADDED, REMOVED = "UPDATED", "UNCHANGED", "ADDED", "REMOVED_BY_LENDER"
W_OUT_OF_SCOPE, W_DPD_MISMATCH, W_NO_CONSENT = "OUT_OF_SCOPE_DPD", "DPD_DUE_DATE_MISMATCH", "CONSENT_NOT_PROVIDED"
CONSENT_FIELDS = ("consent_sms", "consent_whatsapp", "consent_email", "consent_ivr", "consent_voicebot")
TRACKED = ("customer_id", "borrower_name", "registered_mobile", "emi_paise", "overdue_paise", "due_date", "dpd")


def load_rules(path: str = INTAKE_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        rules = json.load(f)
    for key in ("mandatory_fields", "file", "quarantine_reasons", "warnings_not_quarantine"):
        if key not in rules:
            raise ValueError(f"intake_rules.json is missing '{key}'")
    return rules


# ---------------------------------------------------------------- parsing helpers
def column_key(header: str) -> str:
    """'EMI Due Date' == 'emi_due_date' == 'emi-due.date' (case, spaces, hyphens, dots, underscores ignored)."""
    return re.sub(r"[\s\-._]", "", str(header or "")).lower()


def map_headers(headers: Iterable[str], rules: Optional[dict] = None) -> Dict[str, str]:
    """{file header -> canonical field}. Aliases from intake_rules.json; optional fields by their own name."""
    rules = rules or load_rules()
    lookup: Dict[str, str] = {}
    for fld, aliases in rules["file"]["column_aliases"].items():
        for a in [fld, *aliases]:
            lookup.setdefault(column_key(a), fld)
    for fld in rules.get("optional_fields", []):
        lookup.setdefault(column_key(fld), fld)
    lookup.setdefault(column_key("budget_flag"), "budget_flag")
    out = {}
    for h in headers:
        fld = lookup.get(column_key(h))
        if fld and fld not in out.values():
            out[h] = fld
    return out


def normalise_mobile(value: str) -> Optional[str]:
    """Strip spaces/hyphens, '+91' (or a bare 91 on a 12-digit number) and a leading 0: 10 digits starting 6-9."""
    s = re.sub(r"[\s\-().]", "", str(value or ""))
    if s.startswith("+91"):
        s = s[3:]
    elif len(s) == 12 and s.startswith("91"):
        s = s[2:]
    if s.startswith("0"):
        s = s[1:]
    return s if re.fullmatch(r"[6-9]\d{9}", s) else None


def parse_date(value: str) -> Optional[date]:
    s = str(value or "").strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def parse_paise(value: str) -> Optional[int]:
    s = str(value or "").strip().replace(",", "")
    try:
        d = Decimal(s)
    except InvalidOperation:
        return None
    if not d.is_finite():
        return None
    return int((d * 100).to_integral_value())


def parse_dpd(value: str) -> Optional[int]:
    s = str(value or "").strip()
    return int(s) if re.fullmatch(r"[+-]?\d+", s) else None


def read_upload(path: str) -> List[dict]:
    """CSV with the header in row 1. XLSX is listed in intake_rules.json but needs a third-party reader."""
    if path.lower().endswith(".xlsx"):
        raise NotImplementedError("XLSX upload: convert to CSV (openpyxl is not a dependency)")
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


# ---------------------------------------------------------------- validation
@dataclass
class LoanRecord:
    loan_id: str
    customer_id: str
    borrower_name: str
    registered_mobile: str
    emi_paise: int
    overdue_paise: int
    due_date: date
    dpd: int
    consents: Dict[str, bool] = field(default_factory=dict)
    optional: Dict[str, str] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    row_number: int = 0

    def tracked(self) -> dict:
        return {k: (getattr(self, k).isoformat() if isinstance(getattr(self, k), date) else getattr(self, k))
                for k in TRACKED}


@dataclass
class QuarantinedRow:
    row_number: int
    loan_id: str
    reason: str

    @property
    def reason_type(self) -> str:
        return self.reason.split(":", 1)[0]


@dataclass
class IntakeResult:
    upload_date: date
    rows_received: int
    accepted: List[LoanRecord]
    quarantined: List[QuarantinedRow]
    digest: str
    policy_version: str

    def status(self) -> Dict[str, Tuple[str, str]]:
        """{loan_id: (ACCEPTED|QUARANTINED, reason or ';'-joined warnings)} (duplicate ids collapse to one)."""
        out = {r.loan_id: (ACCEPTED, ";".join(r.warnings)) for r in self.accepted}
        for q in self.quarantined:
            out[q.loan_id] = (QUARANTINED, q.reason)
        return out

    def quarantined_by_reason(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for q in self.quarantined:
            out[q.reason_type] = out.get(q.reason_type, 0) + 1
        return out

    def warnings_by_type(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for r in self.accepted:
            for w in r.warnings:
                out[w] = out.get(w, 0) + 1
        return out

    def customers(self) -> Dict[str, List[str]]:
        """Contact units: one contact plan per customer_id; money stays per loan."""
        out: Dict[str, List[str]] = {}
        for r in self.accepted:
            out.setdefault(r.customer_id, []).append(r.loan_id)
        return out


def _row_reason(rec: dict, rules: dict) -> Optional[str]:
    for f in rules["mandatory_fields"]:
        if not str(rec.get(f) or "").strip():
            return f"MISSING_FIELD:{f}"
    if len(re.findall(r"[^\W\d_]", rec["borrower_name"])) < 2:
        return "MISSING_FIELD:borrower_name"
    if normalise_mobile(rec["registered_mobile"]) is None:
        return "INVALID_MOBILE"
    emi, overdue = parse_paise(rec["emi_amount"]), parse_paise(rec["overdue_amount"])
    if emi is None or emi <= 0 or overdue is None or overdue < 0:
        return "INVALID_AMOUNT"
    if parse_date(rec["due_date"]) is None:
        return "INVALID_DATE"
    if parse_dpd(rec["dpd"]) is None:
        return "INVALID_DPD"
    return None


def validate(rows: List[dict], upload_date: date, rules: Optional[dict] = None,
             k01: Optional[dict] = None) -> IntakeResult:
    """Quarantine invalid data only; everything else loads (with warnings). Duplicates: all copies quarantined."""
    rules = rules or load_rules()
    k01 = k01 or load_k01()
    pre_due_start = _pre_due_start(k01)
    headers = list(rows[0].keys()) if rows else []
    hmap = map_headers(headers, rules)
    consent_cols = [f for f in hmap.values() if f in CONSENT_FIELDS]
    canon = [{hmap[h]: (v or "").strip() for h, v in r.items() if h in hmap} for r in rows]
    counts: Dict[str, int] = {}
    for rec in canon:
        lid = rec.get("loan_id", "")
        if lid:
            counts[lid] = counts.get(lid, 0) + 1
    accepted: List[LoanRecord] = []
    quarantined: List[QuarantinedRow] = []
    for n, rec in enumerate(canon, start=2):   # row 1 is the header
        lid = rec.get("loan_id", "")
        reason = "DUPLICATE_LOAN_ID" if lid and counts[lid] > 1 else _row_reason(rec, rules)
        if reason:
            quarantined.append(QuarantinedRow(n, lid, reason))
            continue
        due, dpd = parse_date(rec["due_date"]), parse_dpd(rec["dpd"])
        warnings = []
        if dpd < pre_due_start:
            warnings.append(W_OUT_OF_SCOPE)
        if abs((upload_date - due).days - dpd) > 1:
            warnings.append(W_DPD_MISMATCH)
        consents = {c: rec.get(c, "").upper() in ("Y", "YES", "TRUE", "1") for c in CONSENT_FIELDS}
        if not consent_cols or not any(rec.get(c, "") for c in consent_cols):
            warnings.append(W_NO_CONSENT)
        optional = {k: v for k, v in rec.items() if k not in rules["mandatory_fields"] and k not in CONSENT_FIELDS and v}
        accepted.append(LoanRecord(lid, rec["customer_id"], rec["borrower_name"], normalise_mobile(rec["registered_mobile"]),
                                   parse_paise(rec["emi_amount"]), parse_paise(rec["overdue_amount"]), due, dpd,
                                   consents, optional, warnings, n))
    digest = hashlib.sha256(json.dumps([sorted(r.items()) for r in canon], sort_keys=True).encode()).hexdigest()
    return IntakeResult(upload_date, len(rows), accepted, quarantined, digest, rules.get("policy_version", "?"))


def _pre_due_start(k01: dict) -> int:
    """K01 dpd_rules.pre_due_start_dpd (-6): anything earlier is NOT_IN_SCOPE."""
    return int(k01["dpd_rules"]["pre_due_start_dpd"])


# ---------------------------------------------------------------- budget scope
def budget_scope(accepted: List[LoanRecord], selected: Optional[Iterable[str]] = None,
                 k01: Optional[dict] = None) -> Tuple[List[LoanRecord], List[LoanRecord]]:
    """(eligible, budgeted). Eligible = accepted and in scope (DPD >= pre-due start). Budgeted = the explicit
    selection intersected with eligible; no selection -> nothing budgeted (arming blocked)."""
    k01 = k01 or load_k01()
    start = _pre_due_start(k01)
    eligible = [r for r in accepted if r.dpd >= start]
    if selected is None:
        return eligible, []
    want = set(selected)
    return eligible, [r for r in eligible if r.loan_id in want]


def charge_touch(budgeted: List[LoanRecord], customer_id: str, cost_paise: int) -> Dict[str, int]:
    """A touch to a customer is charged across that customer's budgeted loans in equal shares (paise;
    any remainder goes to the lowest loan ids first so the shares always add up to the cost)."""
    loans = sorted(r.loan_id for r in budgeted if r.customer_id == customer_id)
    if not loans:
        return {}
    share, rest = divmod(int(cost_paise), len(loans))
    return {lid: share + (1 if i < rest else 0) for i, lid in enumerate(loans)}


# ---------------------------------------------------------------- campaign window + preview
@dataclass
class Campaign:
    allocation_start_date: date
    allocation_end_date: date
    budget_per_case_paise: int
    budgeted_loan_ids: Optional[List[str]] = None
    collection_target_amount_pct: Optional[float] = None
    collection_target_count_pct: Optional[float] = None


def _is_quiet(d: date, k01: dict) -> bool:
    qd = k01["contact_window"]["quiet_days"]
    return d.strftime("%a").upper()[:3] in qd["weekdays"] or d.isoformat() in qd["national_holidays"]


def campaign_window(start: date, end: date, k01: Optional[dict] = None) -> dict:
    """First send at window start on the first contact day on/after start; no new sends from window end on
    the last contact day on/before end (end date inclusive)."""
    k01 = k01 or load_k01()
    w = k01["contact_window"]
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    contact = [d for d in days if not _is_quiet(d, k01)]
    if not contact:
        return {"first_contact": None, "last_contact": None, "contact_days": 0}
    return {"first_contact": f"{contact[0].isoformat()} {w['start']}",
            "last_contact": f"{contact[-1].isoformat()} before {w['end_exclusive']}",
            "contact_days": len(contact)}


def preview(res: IntakeResult, camp: Campaign, k01: Optional[dict] = None) -> dict:
    """Everything the ops user must see before arming. can_arm needs >= 1 explicitly budgeted loan."""
    k01 = k01 or load_k01()
    eligible, budgeted = budget_scope(res.accepted, camp.budgeted_loan_ids, k01)
    customers = res.customers()
    pool = camp.budget_per_case_paise * len(budgeted)
    out = {
        "rows_received": res.rows_received, "accepted": len(res.accepted),
        "quarantined_by_reason": res.quarantined_by_reason(), "warnings_by_type": res.warnings_by_type(),
        "customers": len(customers), "customers_with_2plus_loans": sum(1 for v in customers.values() if len(v) > 1),
        "eligible_loans": len(eligible), "budgeted_loans": len(budgeted),
        "budgeted_loan_ids": [r.loan_id for r in budgeted],
        "eligible_not_selected": len(eligible) - len(budgeted),
        "excluded_by_reason": {**res.quarantined_by_reason(),
                               W_OUT_OF_SCOPE: len(res.accepted) - len(eligible)},
        "customers_covered": len({r.customer_id for r in budgeted}),
        "campaign_pool_paise": pool, "campaign_pool_inr": pool / 100,
        "proposed_budget_set": [r.loan_id for r in eligible] if camp.budgeted_loan_ids is None else None,
        **campaign_window(camp.allocation_start_date, camp.allocation_end_date, k01),
        "policy_version": res.policy_version,
    }
    out["can_arm"] = bool(budgeted) and camp.budget_per_case_paise > 0 and out["first_contact"] is not None
    out["arm_blocked_reason"] = None if out["can_arm"] else (
        "BUDGET_SET_NOT_SPECIFIED" if camp.budgeted_loan_ids is None else
        "NO_BUDGETED_LOANS" if not budgeted else "NO_CONTACT_DAY")
    return out


# ---------------------------------------------------------------- re-upload merge
@dataclass
class PortfolioState:
    loans: Dict[str, dict] = field(default_factory=dict)   # loan_id -> tracked fields + status, journey, holds, version
    version: int = 0
    uploads: List[dict] = field(default_factory=list)
    budgeted: List[str] = field(default_factory=list)

    def pool_paise(self, budget_per_case_paise: int) -> int:
        return budget_per_case_paise * len(self.budgeted)


@dataclass
class MergeResult:
    state: PortfolioState
    changes: Dict[str, str]
    upload_id: Optional[str]
    version: int
    duplicate_upload: bool = False
    proposed_for_budget: List[str] = field(default_factory=list)
    left_budget: List[str] = field(default_factory=list)


def merge(prev: Optional[PortfolioState], res: IntakeResult, budgeted: Optional[Iterable[str]] = None) -> MergeResult:
    """Merge and update on loan_id. Existing changed -> UPDATED (journey, holds, history kept); same -> UNCHANGED;
    new -> ADDED; missing -> REMOVED_BY_LENDER (no new sends, history kept). The same file twice changes nothing.
    The budgeted set never changes silently: new loans are only proposed; removed loans leave it."""
    import copy

    state = copy.deepcopy(prev) if prev is not None else PortfolioState()
    if budgeted is not None:
        state.budgeted = sorted(set(budgeted))
    if state.uploads and state.uploads[-1]["digest"] == res.digest:
        live = {lid: UNCHANGED for lid, l in state.loans.items() if l["status"] != REMOVED}
        return MergeResult(state, live, None, state.version, duplicate_upload=True)
    state.version += 1
    upload_id = f"UP-{state.version:04d}-{res.digest[:8]}"
    changes: Dict[str, str] = {}
    seen = set()
    for r in res.accepted:
        seen.add(r.loan_id)
        new = r.tracked()
        cur = state.loans.get(r.loan_id)
        if cur is None:
            state.loans[r.loan_id] = {**new, "status": "ACTIVE", "journey": [], "holds": [],
                                      "version": state.version, "upload_id": upload_id}
            changes[r.loan_id] = ADDED
        elif cur["status"] == REMOVED or any(cur.get(k) != v for k, v in new.items()):
            cur.update(new)
            cur.update({"status": "ACTIVE", "version": state.version, "upload_id": upload_id})
            changes[r.loan_id] = UPDATED
        else:
            changes[r.loan_id] = UNCHANGED
    left = []
    for lid, cur in state.loans.items():
        if lid not in seen and cur["status"] != REMOVED:
            cur.update({"status": REMOVED, "version": state.version, "upload_id": upload_id})
            changes[lid] = REMOVED
            if lid in state.budgeted:
                left.append(lid)
    state.budgeted = [b for b in state.budgeted if b not in left]
    eligible, _ = budget_scope(res.accepted, None)
    proposed = [r.loan_id for r in eligible if changes.get(r.loan_id) == ADDED]
    state.uploads.append({"upload_id": upload_id, "version": state.version, "digest": res.digest,
                          "rows_received": res.rows_received, "accepted": len(res.accepted),
                          "quarantined": len(res.quarantined)})
    return MergeResult(state, changes, upload_id, state.version, False, proposed, left)
