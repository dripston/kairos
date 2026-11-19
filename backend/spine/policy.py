"""Reads Ojas's K01 decision policy (policy/policy.json, the source of truth).

K01 README: "All numbers live in policy.json; code must read them, never hard-code them.
Every logged decision must cite policy_version." This module is the only place K03 gets
DPD bands, channel names and DPD rules from.
"""
import json
import os
from datetime import datetime
from functools import lru_cache

# One copy of the policy for the whole repo: <repo>/policy/K01-decision-policy/policy.json
# (override the folder with KAIROS_POLICY_DIR, same as the K06 backend).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
POLICY_PATH = os.path.join(os.environ.get("KAIROS_POLICY_DIR", os.path.join(_REPO_ROOT, "policy")),
                           "K01-decision-policy", "policy.json")


@lru_cache(maxsize=None)
def load_policy(path=POLICY_PATH):
    with open(path, encoding="utf-8") as f:
        policy = json.load(f)
    for key in ("policy_version", "dpd_bands", "dpd_rules", "channels"):
        if key not in policy:
            raise ValueError(f"policy.json is missing '{key}'")
    return policy


def policy_version():
    return load_policy()["policy_version"]


def channels():
    return tuple(load_policy()["channels"])


def dpd_band(dpd):
    """K01 band ID for a DPD: PRE_DUE, B1, B2, B3, B4, NOT_IN_SCOPE, or DPD_INVALID when missing."""
    if dpd is None:
        return "DPD_INVALID"
    policy = load_policy()
    if dpd < policy["dpd_rules"]["pre_due_start_dpd"]:
        return policy["dpd_rules"]["before_first_band"]          # NOT_IN_SCOPE
    for band in policy["dpd_bands"]:
        if dpd >= band["dpd_min"] and (band["dpd_max"] is None or dpd <= band["dpd_max"]):
            return band["id"]
    raise ValueError(f"No K01 band covers DPD {dpd}")


def dpd_status(dpd, dpd_as_of, now):
    """VALID, or HOLD reasons from K01: missing DPD, or DPD older than stale_after_hours (24 h)."""
    if dpd is None:
        return "DPD_MISSING"
    if dpd_as_of is None:
        return "DPD_MISSING"
    as_of = datetime.fromisoformat(dpd_as_of) if isinstance(dpd_as_of, str) else dpd_as_of
    age_hours = (now - as_of).total_seconds() / 3600
    return "DPD_STALE" if age_hours > load_policy()["dpd_rules"]["stale_after_hours"] else "VALID"
