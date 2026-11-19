"""K17/K18 bot registry: which Sarvam voice agent, voice and language a Kairos call uses.

    select_bot(borrower, obligation, context) -> {use_case, agent_id, voice, language, variables, reason, ...}

Deterministic: same inputs + same registry/thresholds -> same output (no wall clock when
context["as_of"] is given, no randomness; the optional A/B arm is a hash). select_bot decides only
WHICH bot; WHETHER a call may happen is the K01 Gate's job (Supervisor -> Ledger.reserve).

Inputs are plain dicts so the CRM, the Supervisor and tests can call it without a database:
  borrower    borrower_id, full_name, language (K03 spine), state (K03 spine), lender_id,
              preferred_language / preferred_voice / observed_language (voice_preferences)
  obligation  obligation_id, due_date (YYYY-MM-DD), band (K01 dpd_bands id), dpd
  context     band, dpd, as_of (date/datetime), touch_id, lender_name, hardship, human_approved,
              callback_requested, callback_reason, ptp_due, broken_ptp, promise_date,
              preferred_language, preferred_voice

Call-start variables never carry amounts, loan/account numbers, phone numbers, emails or any PII
beyond the first name; check_variables() enforces that and select_bot blanks anything that fails.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from ..policy import policy_dir

REGISTRY_PATH = Path(__file__).with_name("registry.json")
IST = timezone(timedelta(hours=5, minutes=30), "IST")
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September",
          "October", "November", "December")


# ---------- loading ----------
def load_registry(path: Optional[Path] = None) -> dict:
    with open(path or REGISTRY_PATH, encoding="utf-8") as f:
        reg = json.load(f)
    for uc, spec in reg["use_cases"].items():
        if spec["voice"] not in reg["voices"]:
            raise ValueError(f"registry use case {uc} names unknown voice {spec['voice']}")
    unsupported_default = reg["default_language"] not in reg["supported_languages"]
    if unsupported_default:
        raise ValueError("registry default_language must be a supported language")
    return reg


def load_thresholds(base: Optional[Path] = None) -> dict:
    path = Path(base or policy_dir()) / "K17-voice-dispositions" / "thresholds.json"
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ---------- small pure helpers ----------
def spoken_date(d) -> str:
    """date / 'YYYY-MM-DD' -> '5 November' (empty string when missing or invalid)."""
    if d in (None, ""):
        return ""
    if isinstance(d, datetime):
        d = d.date()
    if not isinstance(d, date):
        try:
            d = date.fromisoformat(str(d)[:10])
        except ValueError:
            return ""
    return f"{d.day} {MONTHS[d.month - 1]}"


def first_name(full_name: Optional[str]) -> str:
    """First token of the name, letters only (no titles' dots, digits or symbols)."""
    for tok in (full_name or "").split():
        clean = re.sub(r"[^A-Za-zऀ-෿'-]", "", tok)
        if clean and clean.lower().rstrip(".") not in ("mr", "mrs", "ms", "dr", "shri", "smt"):
            return clean[:40]
    return ""


def _as_day(v) -> Optional[date]:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return (v.astimezone(IST) if v.tzinfo else v).date()
    if isinstance(v, date):
        return v
    try:
        return datetime.fromisoformat(str(v)).date() if "T" in str(v) else date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def normalize_language(value, registry: dict) -> Optional[str]:
    """'mr' / 'mr-IN' / 'marathi' / 'Marathi' -> 'Marathi'. Unknown -> None. May return a language
    that is not supported (e.g. Malayalam); the caller decides the fallback."""
    if value is None:
        return None
    v = str(value).strip()
    if not v:
        return None
    codes = registry["language_codes"]
    by_name = {n.lower(): n for n in codes}
    if v.lower() in by_name:
        return by_name[v.lower()]
    code = v.split("-")[0].split("_")[0].lower()
    for name, c in codes.items():
        if c == code:
            return name
    return None


def state_code(state, registry: dict) -> Optional[str]:
    if not state:
        return None
    s = str(state).strip().upper()
    if s in registry["state_language"]:
        return s
    return registry["state_names"].get(s)


def ab_arm(borrower_id: str, experiment_id: str, arms) -> str:
    """Deterministic A/B arm: sha256(borrower_id|experiment_id) mod len(arms) (2 arms -> % 2)."""
    h = int(hashlib.sha256(f"{borrower_id}|{experiment_id}".encode()).hexdigest(), 16)
    return list(arms)[h % len(arms)]


# ---------- variable safety ----------
_TOUCH_ID = re.compile(r"^[A-Z]{2,6}-[A-Za-z0-9-]{4,64}$")
_PATTERNS = {"call_date": re.compile(r"^\d{4}-\d{2}-\d{2}$"), "overdue_days": re.compile(r"^\d{1,3}$"),
             "touch_id": _TOUCH_ID}
_FORBIDDEN = [
    (re.compile(r"\d{3,}"), "DIGIT_RUN"),                              # amounts, phone, loan / account numbers
    (re.compile(r"₹|\brs\.?\s*\d|\binr\b|\brupee", re.I), "CURRENCY"),
    (re.compile(r"@"), "EMAIL"),
    (re.compile(r"\+\s*\d"), "PHONE"),
]
_FORBIDDEN_KEYS = re.compile(r"amount|paise|inr|rupee|phone|mobile|loan|account|email|pan|aadhaar|outstanding", re.I)


def allowed_variable_keys(registry: dict) -> set:
    keys = set(registry["call_start_variables"])
    for spec in registry["use_cases"].values():
        keys.update(spec.get("extras", []))
    return keys


def check_variables(variables: dict, registry: dict) -> dict:
    """{key: reason} for every variable that must not be sent. Empty dict = safe."""
    bad = {}
    allowed = allowed_variable_keys(registry)
    for k, v in variables.items():
        if k not in allowed or _FORBIDDEN_KEYS.search(k):
            bad[k] = "KEY_NOT_ALLOWED"
            continue
        if not isinstance(v, str):
            bad[k] = "NOT_A_STRING"
            continue
        if v == "":
            continue
        if k in _PATTERNS:
            if not _PATTERNS[k].match(v):
                bad[k] = "BAD_FORMAT"
            continue
        for rx, why in _FORBIDDEN:
            if rx.search(v):
                bad[k] = why
                break
    return bad


def _neutral_phrase(text: Optional[str], default: str) -> str:
    """Short neutral phrase: letters, spaces and light punctuation only, no digits."""
    t = re.sub(r"[^A-Za-zऀ-෿ ,.'-]", " ", text or "")
    t = re.sub(r"\s+", " ", t).strip()[:80]
    return t or default


# ---------- selection ----------
def _band(obligation: dict, context: dict) -> Optional[str]:
    return context.get("band") or obligation.get("band")


def _use_case(reg: dict, band: Optional[str], ctx: dict) -> tuple[Optional[str], str]:
    ucs = reg["use_cases"]
    if ctx.get("hardship"):
        if ctx.get("human_approved"):
            return "HARDSHIP_TRIAGE", "hardship signal, human approved"
        return None, "HARDSHIP_NEEDS_HUMAN_APPROVAL"
    if ctx.get("callback_requested"):
        return "CALLBACK", "borrower asked for a call"
    if ctx.get("ptp_due") or ctx.get("broken_ptp"):
        return "PTP_FOLLOW_UP", "promise date reached" if ctx.get("ptp_due") else "broken PTP"
    if not band:
        return None, "NO_BAND"
    for name, spec in ucs.items():
        bands = spec["bands"]
        if bands != "*" and band in bands:
            return name, f"band {band}"
    return None, "NO_USE_CASE_FOR_BAND"


def _language(reg: dict, borrower: dict, ctx: dict) -> tuple[str, str]:
    supported, default = reg["supported_languages"], reg["default_language"]
    explicit = [("context preference", ctx.get("preferred_language")),
                ("borrower preference", borrower.get("preferred_language"))]
    spine = borrower.get("language")
    if spine and str(spine).strip().lower() != reg["spine_default_language_code"]:
        explicit.append(("spine language", spine))
    for src, raw in explicit:
        if raw in (None, ""):
            continue
        lang = normalize_language(raw, reg)
        if lang in supported:
            return lang, src
        return default, f"{src} '{raw}' unsupported -> {default}"
    observed = normalize_language(borrower.get("observed_language"), reg)
    if observed in supported:
        return observed, "observed on an earlier call"
    code = state_code(borrower.get("state"), reg)
    if code:
        lang = reg["state_language"][code]
        if lang in supported:
            return lang, f"state {code}"
        return default, f"state {code} -> {lang} unsupported -> {default}"
    return default, "default"


def _voice(reg: dict, use_case: str, borrower: dict, ctx: dict) -> tuple[str, str]:
    voices = reg["voices"]
    voice, src = reg["use_cases"][use_case]["voice"], "registry default"
    exp = reg.get("experiment") or {}
    if exp.get("enabled") and use_case in exp.get("use_cases", []) and borrower.get("borrower_id"):
        arm = ab_arm(str(borrower["borrower_id"]), exp["experiment_id"], exp["arms"])
        if arm in voices:
            voice, src = arm, f"experiment {exp['experiment_id']}"
    lender = (reg.get("lender_overrides") or {}).get(borrower.get("lender_id") or ctx.get("lender_id") or "", {})
    lv = (lender.get("voices") or {}).get(use_case) or lender.get("default_voice")
    if lv in voices:
        voice, src = lv, "lender override"
    pref = ctx.get("preferred_voice") or borrower.get("preferred_voice")
    if pref in voices:
        voice, src = pref, "borrower preference"
    return voice, src


def max_ptp_days(thresholds: dict, band: Optional[str], use_case: Optional[str]) -> int:
    """Max days ahead a promise may be (thresholds.json). Unknown band -> the strictest value."""
    ptp = thresholds["ptp"]
    if use_case and use_case in ptp.get("max_ptp_days_by_use_case", {}):
        return int(ptp["max_ptp_days_by_use_case"][use_case])
    by_band = ptp["max_ptp_days_by_band"]
    return int(by_band[band]) if band in by_band else min(int(v) for v in by_band.values())


def select_bot(borrower: dict, obligation: Optional[dict] = None, context: Optional[dict] = None,
               registry: Optional[dict] = None, thresholds: Optional[dict] = None) -> dict:
    reg = registry or load_registry()
    thr = thresholds or load_thresholds()
    obligation, ctx = obligation or {}, context or {}
    band = _band(obligation, ctx)
    call_day = _as_day(ctx.get("as_of")) or datetime.now(IST).date()

    use_case, uc_reason = _use_case(reg, band, ctx)
    language, lang_reason = _language(reg, borrower, ctx)
    out: dict[str, Any] = {
        "use_case": use_case, "agent_id": None, "voice": None, "voice_id": None,
        "language": language, "language_code": reg["language_codes"][language], "variables": {},
        "reason": "", "reasons": {"use_case": uc_reason, "language": lang_reason},
        "ready": False, "requires_human_approval": False, "blocked_variables": {},
        "registry_version": reg["registry_version"], "thresholds_version": thr["policy_version"],
    }
    if use_case is None:
        out["reason"] = f"no bot: {uc_reason}"
        return out

    spec = reg["use_cases"][use_case]
    voice, voice_reason = _voice(reg, use_case, borrower, ctx)
    out["reasons"]["voice"] = voice_reason
    lender = (reg.get("lender_overrides") or {}).get(borrower.get("lender_id") or ctx.get("lender_id") or "", {})
    lender_name = _neutral_phrase(ctx.get("lender_name") or lender.get("display_name"), reg["default_lender_name"])

    variables = {
        "user_name": first_name(borrower.get("full_name") or borrower.get("name")),
        "lender_name": lender_name,
        "emi_due_date": spoken_date(obligation.get("due_date")),
        "touch_id": str(ctx.get("touch_id") or ""),
        "preferred_language": language,
        "call_date": call_day.isoformat(),
        "max_ptp_date": "" if spec.get("no_max_ptp_date")
        else spoken_date(call_day + timedelta(days=max_ptp_days(thr, band, use_case))),
    }
    extras = spec.get("extras", [])
    if "overdue_days" in extras:
        dpd = ctx.get("dpd", obligation.get("dpd"))
        variables["overdue_days"] = str(int(dpd)) if isinstance(dpd, (int, float)) and dpd >= 0 else ""
    if "promise_date" in extras:
        variables["promise_date"] = spoken_date(ctx.get("promise_date") or ctx.get("ptp_date"))
    if "callback_reason" in extras:
        variables["callback_reason"] = _neutral_phrase(ctx.get("callback_reason"), spec["default_callback_reason"])

    bad = check_variables(variables, reg)
    for k in bad:  # fail closed: never send a value that could carry PII / money
        variables[k] = ""
    agent_id = spec.get("agent_id")
    out.update(agent_id=agent_id, voice=voice, voice_id=reg["voices"][voice]["internal_id"], variables=variables,
               blocked_variables=bad, requires_human_approval=bool(spec.get("requires_human_approval")),
               ready=bool(agent_id) and agent_id != "TBD")
    out["reason"] = f"{use_case} ({uc_reason}); language {language} ({lang_reason}); voice {voice} ({voice_reason})"
    return out


def agent_use_case(app_id: Optional[str], registry: Optional[dict] = None) -> Optional[str]:
    """Which use case a Sarvam agent id (app_id) belongs to; None if unknown."""
    if not app_id:
        return None
    reg = registry or load_registry()
    for name, spec in reg["use_cases"].items():
        if spec.get("agent_id") == app_id:
            return name
    return None


# ---------- inputs from the ledger (API / Supervisor) ----------
def inputs_from_ledger(ledger, borrower_id: str, at: Optional[datetime] = None,
                       obligation_id: Optional[str] = None) -> tuple[dict, dict, dict]:
    """(borrower, obligation, context) for select_bot, read from the K03 spine + K06 state + voice tables.
    Raises KeyError for an unknown borrower."""
    from .store import ensure_schema

    ensure_schema(ledger)
    at = ledger.policy.local(at) if at else ledger.policy.now()
    day = at.date()
    st = ledger.borrower_state(borrower_id, day)  # KeyError when unknown
    c = ledger._conn()
    pref = c.execute("SELECT * FROM voice_preferences WHERE borrower_id=?", (borrower_id,)).fetchone()
    row = c.execute("SELECT full_name, language, state FROM borrowers WHERE borrower_id=?", (borrower_id,)).fetchone()
    s = st["state"]  # NB: borrower_state() puts the K06 holds under "state"; the K03 home state is row["state"]
    borrower = {"borrower_id": borrower_id, "full_name": row["full_name"], "language": row["language"],
                "state": row["state"], "lender_id": s.get("lender_id"),
                "preferred_language": pref["preferred_language"] if pref else None,
                "preferred_voice": pref["preferred_voice"] if pref else None,
                "observed_language": pref["observed_language"] if pref else None}
    ob, loan = None, None
    for ln in st["loans"]:
        for o in ln["obligations"]:
            if (obligation_id and o["obligation_id"] == obligation_id) or (
                    not obligation_id and o["outstanding_paise"] > 0 and (ob is None or o["due_date"] < ob["due_date"])):
                ob, loan = o, ln
    obligation = {"obligation_id": ob["obligation_id"], "due_date": ob["due_date"],
                  "band": loan["band"], "dpd": loan["dpd"]} if ob else {}
    hardship_until = ledger._dt(s.get("hardship_until"))
    fu = c.execute("SELECT promise_date FROM voice_followups WHERE borrower_id=? AND status='PENDING' AND due_on<=?"
                   " AND use_case='PTP_FOLLOW_UP' ORDER BY due_on LIMIT 1", (borrower_id, day.isoformat())).fetchone()
    cb = c.execute("SELECT 1 FROM voice_followups WHERE borrower_id=? AND status='PENDING' AND due_on<=?"
                   " AND use_case='CALLBACK' LIMIT 1", (borrower_id, day.isoformat())).fetchone()
    ptp_date = date.fromisoformat(s["ptp_date"]) if s.get("ptp_date") else None
    context = {"as_of": at, "band": obligation.get("band"), "dpd": obligation.get("dpd"),
               "hardship": bool(hardship_until and hardship_until > at) or bool(s.get("hardship_supervisor")),
               "callback_requested": bool(s.get("callback_pending")) or bool(cb),
               "ptp_due": bool(fu) or bool(ptp_date and ptp_date <= day),
               "promise_date": fu["promise_date"] if fu else (ptp_date.isoformat() if ptp_date else None)}
    return borrower, obligation, context
