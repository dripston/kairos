"""T01 conformance: every row of Ojas's T01 templates.csv against OUR K22 pipeline.

For each T01 row (case_id = his template_id, e.g. SM-PRE_DUE-A-EN):
  PASS            the row is in our imported catalog (policy/K22-template-catalog/t01_catalog.json, built by
                  scripts/import_t01.py) with the same channel, band, language and placeholders, AND our
                  collections-compliance checklist passes it;
  FAIL            our checklist flags it (detail = failing rules, each tagged with its kind, + first reason);
  NOT_IMPLEMENTED our pipeline cannot represent the row (missing from the import, or channel/band/language/
                  placeholders do not survive the import).

The checklist is run fresh here (not read from the stamped file), so a stale import cannot hide a result.
"""
from __future__ import annotations

import csv
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List

BACKEND = Path(__file__).resolve().parents[2]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from kairos_backend.policy import policy_dir  # noqa: E402
from kairos_backend.template_compliance import FAIL, check_template  # noqa: E402

PASS_S, FAIL_S, NOT_IMPL = "PASS", "FAIL", "NOT_IMPLEMENTED"

# Why our checklist flags HIS rows, per rule (from the import on 19 Nov; see POLICY_COVERAGE.md):
#   FORMAT         T01 says the same thing in a form our checker does not recognise
#   POLICY         T01 and K22 disagree on what a message must / may say (Product decides)
#   NOT_BUILT      the provider side does not exist yet for T01 (no Sarvam agent)
#   T01_DEFECT     inconsistency inside T01 itself
RULE_KIND = {
    "ACCOUNT_LAST4_ONLY": "FORMAT",        # T01 masks as 'xx1234'; checker looks for 'XX{{loan_last4}}' / 'ending'
    "PLACEHOLDERS_ALLOWED": "FORMAT",      # {unsubscribe_link} is not a K22 merge field
    "IDENTITY_STAGE": "FORMAT",            # IVR 'This call is for {name}' vs checker's 'This message is for {{name}}'
    "CHANNEL_REGISTRATION": "NOT_BUILT",   # T01 voicebot rows have no Sarvam agent / recorded controls
    "PAY_AND_HELP": "POLICY",              # K22 wants a helpline on every message; T01 uses HELP/CALL replies
    "IGNORE_IF_PAID": "POLICY",            # K22 wants 'ignore if already paid' on pre-due reminders
    "CONSEQUENCES_PERMITTED": "POLICY",    # T01 cites credit bureau from B1/B2; K22 only from tier 3
    "PURPOSE_CONTENT": "POLICY",           # K22 T3 needs days overdue, T4 a final date + next stage
    "LANGUAGE_PARITY": "T01_DEFECT",       # angle-A IVR/voicebot: EN names loan last-4, HI does not
    "NO_THREATS_OR_ABUSE": "POLICY",
    "NO_THIRD_PARTY_DISCLOSURE": "POLICY",
    "LENGTH_LIMITS": "POLICY",
    "LENDER_IDENTIFIED": "POLICY",
    "TIER_BANDS": "FORMAT",
}
_T01_PH = re.compile(r"\{(\w+)\}")


def _paths():
    pdir = policy_dir()
    return pdir / "T01-outbound-templates" / "templates.csv", pdir / "K22-template-catalog" / "t01_catalog.json"


def _load():
    src, cat = _paths()
    with open(src, encoding="utf-8", newline="") as f:
        t01 = list(csv.DictReader(f))
    rows = json.loads(cat.read_text(encoding="utf-8"))["templates"] if cat.exists() else []
    return t01, rows


def _results(rows: List[dict]) -> Dict[str, object]:
    return {r["id"]: check_template(r, rows) for r in rows}


def run_t01() -> List[dict]:
    from scripts.import_t01 import CHANNELS, LANGS, MAPPED_PLACEHOLDERS  # noqa: E402
    t01, rows = _load()
    ours = {r.get("t01_template_id"): r for r in rows}
    results = _results(rows)
    out = []
    for t in t01:
        cid = t["template_id"]
        desc = f"{t['channel']} {t['band']} angle {t['angle']} {t['language']} ({t['purpose']}, cta {t['cta']})"
        r = ours.get(cid)
        if r is None:
            out.append({"case_id": cid, "description": desc, "status": NOT_IMPL, "detail": "not in t01_catalog.json"})
            continue
        want_ph = sorted({MAPPED_PLACEHOLDERS.get(p, p) for p in t["placeholders"].split(",") if p})
        mismatch = []
        if r["channel"] != CHANNELS[t["channel"]]:
            mismatch.append("channel")
        if r["dpd_bands"] != [t["band"]]:
            mismatch.append("band")
        if r["lang"] != LANGS[t["language"]]:
            mismatch.append("language")
        if sorted(r["variables"]) != want_ph:
            mismatch.append("placeholders")
        if mismatch:
            out.append({"case_id": cid, "description": desc, "status": NOT_IMPL,
                        "detail": "import does not preserve " + ", ".join(mismatch)})
            continue
        res = results[r["id"]]
        if res.passed:
            out.append({"case_id": cid, "description": desc, "status": PASS_S, "detail": "in catalog; checklist PASS"})
        else:
            rules = [f"{x}[{RULE_KIND.get(x, '?')}]" for x in res.failed_rules]
            out.append({"case_id": cid, "description": desc, "status": FAIL_S,
                        "detail": "checklist: " + ", ".join(rules) + " | " + res.reasons[0][:120]})
    return out


def checker_flags(samples: int = 3) -> Dict[str, dict]:
    """Which of OUR rules fail how many of HIS rows: {rule: {count, kind, samples}}."""
    _, rows = _load()
    by_rule: Dict[str, List[str]] = defaultdict(list)
    for tid, res in _results(rows).items():
        for rule, v in res.checklist.items():
            if v == FAIL:
                by_rule[rule].append(tid)
    return {rule: {"count": len(ids), "kind": RULE_KIND.get(rule, "?"), "samples": sorted(ids)[:samples]}
            for rule, ids in sorted(by_rule.items())}
