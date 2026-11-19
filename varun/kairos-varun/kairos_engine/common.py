"""Shared helpers used by every other module.

Beginner note: this file has no business logic. It loads config/policy files
and provides `uniform()`, a "random number" that is really a hash of its inputs.
Same inputs -> same number, every time, on every machine. That is how the whole
replay stays deterministic for a given seed (NFR: "deterministic given a seed").
"""
import json
import os
from datetime import date, timedelta

import numpy as np

ROOT = os.path.dirname(os.path.abspath(__file__))          # kairos_engine/
PACKAGE = os.path.dirname(ROOT)                            # kairos-varun/ (holds the K folders)

# Ojas's policy packs. If his folders sit next to this package (or one level up),
# they are used directly, so a policy update by Ojas flows in with no copying.
# Otherwise the bundled copy (identical at the time of packaging) is used.
OJAS_FILES = {
    "k01_policy": ("K01-decision-policy", "policy.json", "k01_policy.json"),
    "k01_reference": ("K01-decision-policy", "reference_check.py", "k01_reference_check.py"),
    "k01_tests": ("K01-decision-policy", "test_cases.csv", "k01_test_cases.csv"),
    "k01_lender_example": ("K01-decision-policy", "lender_caps.example.json", "lender_caps.example.json"),
    "k21_scorecard": ("K21-propensity-intensity", "scorecard.json", "k21_scorecard.json"),
    "k21_tests": ("K21-propensity-intensity", "test_cases.csv", "k21_test_cases.csv"),
    "k13_exceptions": ("K13-operational-exceptions", "exceptions.json", "k13_exceptions.json"),
    "k10_policy": ("K10-inbound-conversation", "conversation_policy.json", "k10_conversation_policy.json"),
    "k10_reference": ("K10-inbound-conversation", "reference_check.py", "k10_reference_check.py"),
    "k10_scripts": ("K10-inbound-conversation", "scripts.csv", "k10_scripts.csv"),
}


def ojas_file(key):
    """Return (path, source) for one of Ojas's policy files."""
    folder, name, bundled = OJAS_FILES[key]
    for base in (PACKAGE, os.path.dirname(PACKAGE)):
        p = os.path.join(base, folder, name)
        if os.path.exists(p):
            return p, f"Ojas folder ({folder}/{name})"
    return os.path.join(ROOT, "policy_bundled", bundled), f"bundled copy (policy_bundled/{bundled})"


def file_sha(p):
    import hashlib
    return hashlib.sha256(open(p, "rb").read()).hexdigest()[:12]
CHANNELS = ["sms", "whatsapp", "email", "ivr", "voicebot"]
SMS, WA, EMAIL, IVR, VOICEBOT = range(5)
VOICE = {IVR, VOICEBOT}
BANDS = ["PRE_DUE", "B1", "B2", "B3", "B4"]
RISKS = ["C1", "C2", "C3", "C4", "C5"]
EMPS = ["SAL", "SE"]
SEGMENTS = [f"{r}-{e}" for r in RISKS for e in EMPS]  # 10 learning/reporting segments
INTENTS = ["WILLING", "SILENT", "NEEDS_TIME", "DISPUTE", "REFUSED", "HARDSHIP"]


def path(*parts):
    return os.path.join(ROOT, *parts)


def load_json(rel):
    with open(path(rel), encoding="utf-8") as f:
        return json.load(f)


def load_all():
    cfg = load_json("config.json")
    k01 = json.load(open(ojas_file("k01_policy")[0], encoding="utf-8"))
    k21 = json.load(open(ojas_file("k21_scorecard")[0], encoding="utf-8"))
    if cfg.get("lender_profile"):
        k01["caps"]["lender_overrides"] = load_json(cfg["lender_profile"])
    return cfg, k01, k21


def policy_sources():
    """Which policy files were used, from where, and their content hash (for the run record)."""
    out = {}
    for key in ("k01_policy", "k01_reference", "k21_scorecard", "k10_policy", "k10_reference"):
        p, src = ojas_file(key)
        out[key] = {"source": src, "sha256_12": file_sha(p)}
    return out


def slot_minutes(cfg):
    return np.array([int(s[:2]) * 60 + int(s[3:]) for s in cfg["slots"]], dtype=np.int64)


def campaign_dates(cfg):
    start = date.fromisoformat(cfg["campaign_start"])
    return [start + timedelta(days=d) for d in range(cfg["campaign_days"])]


def is_quiet_day(k01, d):
    q = k01["contact_window"]["quiet_days"]
    return d.strftime("%a").upper()[:3] in q["weekdays"] or d.isoformat() in q["national_holidays"]


def band_index(k01, dpd):
    """Vectorised K01 band lookup. Returns -1 for 'not in scope' (DPD < -6)."""
    dpd = np.asarray(dpd)
    out = np.full(dpd.shape, -1, dtype=np.int8)
    for i, b in enumerate(k01["dpd_bands"]):
        lo = -10**9 if b["dpd_min"] is None else b["dpd_min"]
        hi = 10**9 if b["dpd_max"] is None else b["dpd_max"]
        out[(dpd >= lo) & (dpd <= hi)] = i
    return out


# ---------- deterministic "random" numbers (counter-based, splitmix64) ----------
_G = np.uint64(0x9E3779B97F4A7C15)


def _mix(x):
    x = (x ^ (x >> np.uint64(30))) * np.uint64(0xBF58476D1CE4E5B9)
    x = (x ^ (x >> np.uint64(27))) * np.uint64(0x94D049BB133111EB)
    return x ^ (x >> np.uint64(31))


def uniform(seed, *keys):
    """Uniform [0,1) number fixed by (seed, keys). Keys may be arrays (broadcast).

    Both policies use the SAME keys for the same borrower/day/slot/channel, so if
    both send the same touch they get the same outcome ("common random numbers").
    That makes the benchmark comparison fair and low-noise.
    """
    with np.errstate(over="ignore"):
        h = _mix(np.uint64(seed) * _G + _G)
        for k in keys:
            h = _mix(h ^ (np.asarray(k).astype(np.uint64) * _G + np.uint64(0x632BE59BD9B4E019)))
        return (h >> np.uint64(11)).astype(np.float64) * (1.0 / 9007199254740992.0)
