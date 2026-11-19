"""Import Ojas's T01 outbound templates (policy/T01-outbound-templates/templates.csv) into the
K22 catalog format and stamp each row with OUR collections-compliance checklist result.

    python scripts/import_t01.py            # (re)write policy/K22-template-catalog/t01_catalog.json
    python scripts/import_t01.py --check    # exit 1 if the file is missing or stale
    python scripts/import_t01.py --report   # print the checklist report for HIS rows

T01 is owned by Product (Ojas) and is the source of truth for the wording: this script never edits
his text. It only (1) converts `{x}` placeholders to our `{{x}}` syntax, renaming the fields that
mean the same thing in both catalogs (MAPPED_PLACEHOLDERS), (2) splits IVR / voicebot scripts at
his `[OPENER]` / `[AFTER_CONFIRM]` markers, and (3) adds the provider and routing fields our
checklist needs, filled honestly (placeholders still pending registration; no Sarvam agent).

The output is a SEPARATE catalog file. `kairos_backend.templates` still loads catalog.json by
default, so importing T01 changes no live template selection.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from kairos_backend.policy import policy_dir  # noqa: E402
from kairos_backend.template_compliance import (MESSAGE_TYPES, TIER_INTENSITY, approval_block,  # noqa: E402
                                                check_template)

T01_DIRNAME = "T01-outbound-templates"
OUT_NAME = "t01_catalog.json"

CHANNELS = {"SMS": "sms", "WHATSAPP": "whatsapp", "EMAIL": "email", "IVR": "ivr", "VOICEBOT": "voicebot"}
LANGS = {"EN": "en", "HI": "hi"}
# T01 field -> our field, only where both mean the same thing (our `amount` = "EMI / overdue amount").
# No T01 row uses both {emi} and {amount}, so the rename is reversible per row (t01_placeholder_map).
MAPPED_PLACEHOLDERS = {"link": "payment_link", "emi": "amount"}
# T01 has no tone tier; our tiers gate consequence wording. Mapped from T01's own tone ladder:
# 1-30 reminder (angle C already cites late charges -> T2, not the no-consequence T1),
# 31-60 firm / late charges -> T2, 61-90 serious / credit record -> T3, 91+ formal notice -> T4.
BAND_TIER = {"B1": 2, "B2": 2, "B3": 3, "B4": 4}
SEGMENTS_RE = re.compile(r"^\[OPENER\] (.*?) \[AFTER_CONFIRM\] (.*)$", re.S)
PLACEHOLDER_T01 = re.compile(r"\{(\w+)\}")

NOTES = [
    "Imported verbatim from Ojas's T01 catalogue (policy/T01-outbound-templates/templates.csv); T01 is the source "
    "of truth for wording. Regenerate with backend/scripts/import_t01.py; never edit by hand.",
    "Placeholders: T01 {x} -> {{x}}. Renamed where the meaning is identical: {link} -> {{payment_link}}, "
    "{emi} -> {{amount}} (per-row t01_placeholder_map makes it reversible). {unsubscribe_link} has no K22 "
    "equivalent and is kept as-is, so our checklist reports it.",
    "Tier: T01 has none. Mapped from T01's tone ladder: B1 and B2 -> T2 (late charges), B3 -> T3, B4 -> T4. "
    "PRE_DUE rows are purpose PRE_DUE_REMINDER (no tier). Intensity follows the tier (T2 LOW, T3/T4 STANDARD).",
    "Purpose: every T01 row is an outbound reminder (REMINDER, or PRE_DUE_REMINDER for PRE_DUE); angle E "
    "(date ask) stays REMINDER because K22's ASK_PAYMENT_DATE is a pre-verification reply, not outreach.",
    "IVR / voicebot: split at T01's [OPENER] / [AFTER_CONFIRM] markers (t01_segments). IVR body = opener + ' ' + "
    "after_confirm as spoken; voicebot opening_line = opener, body = after_confirm. T01 IVR is interactive "
    "(keypad), K22 only knows the one-way ivr_blast type.",
    "Provider fields are placeholders: SMS PENDING_DLT / PENDING_HEADER, WhatsApp UTILITY with a generated "
    "template name, IVR PENDING_RECORDING. Voicebot rows have NO Sarvam agent (none was built for T01), so our "
    "CHANNEL_REGISTRATION rule fails them by design.",
    "approved / approval come from OUR checklist only. APPROVED_FOR_DEMO is not lender, DLT or Meta approval.",
]


def t01_paths(pdir: Optional[Path] = None) -> Tuple[Path, Path]:
    pdir = pdir or policy_dir()
    return pdir / T01_DIRNAME / "templates.csv", pdir / "K22-template-catalog" / OUT_NAME


def read_t01(path: Path) -> List[dict]:
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def to_ours(text: str) -> str:
    return PLACEHOLDER_T01.sub(lambda m: "{{" + MAPPED_PLACEHOLDERS.get(m.group(1), m.group(1)) + "}}", text or "")


def to_t01(text: str, pmap: Dict[str, str]) -> str:
    """Reverse of to_ours for one row, using that row's t01_placeholder_map (ours -> T01)."""
    return re.sub(r"\{\{(\w+)\}\}", lambda m: "{" + pmap.get(m.group(1), m.group(1)) + "}", text or "")


def convert_row(r: dict) -> dict:
    tid = r["template_id"]
    channel = CHANNELS[r["channel"]]
    lang = LANGS[r["language"]]
    band = r["band"]
    t01_fields = [p for p in r["placeholders"].split(",") if p]
    pmap = {MAPPED_PLACEHOLDERS[p]: p for p in t01_fields if p in MAPPED_PLACEHOLDERS}
    variables = list(dict.fromkeys(MAPPED_PLACEHOLDERS.get(p, p) for p in t01_fields))
    pre_due = band == "PRE_DUE"
    tier = None if pre_due else BAND_TIER[band]
    row: dict = {
        "id": tid,
        "channel": channel,
        "type": MESSAGE_TYPES[channel],
        "lang": lang,
        "purpose": "PRE_DUE_REMINDER" if pre_due else "REMINDER",
    }
    if tier is not None and channel != "voicebot":
        row["tier"] = tier
    row["intensity"] = "LOW" if pre_due or channel == "voicebot" else TIER_INTENSITY[tier]
    row["dpd_bands"] = [band]
    row["family"] = f"t01.{channel}.{band}.{r['angle']}"
    row["identity_stage"] = "in_call_identity_check" if channel == "voicebot" else "outbound_registered_contact"
    if r["subject"]:
        row["subject"] = to_ours(r["subject"])
    m = SEGMENTS_RE.match(r["body"]) if channel in ("ivr", "voicebot") else None
    if channel in ("ivr", "voicebot"):
        if not m:
            raise ValueError(f"{tid}: IVR/voicebot body lacks [OPENER] ... [AFTER_CONFIRM] ...")
        opener, after = m.group(1), m.group(2)
        row["t01_segments"] = {"OPENER": to_ours(opener), "AFTER_CONFIRM": to_ours(after)}
        if channel == "voicebot":
            row["opening_line"] = to_ours(opener)
            row["body"] = to_ours(after)
        else:
            row["body"] = to_ours(opener + " " + after)
    else:
        row["body"] = to_ours(r["body"])
    row["variables"] = variables
    row["approved"] = False   # set from the checklist below
    row["version"] = 1
    slug = re.sub(r"[^a-z0-9]+", "_", tid.lower()).strip("_")
    row["provider_template_name"] = f"t01_{slug}"
    if channel == "sms":
        row.update(dlt_template_id="PENDING_DLT", sender_id="PENDING_HEADER", t01_dlt_body=r["dlt_body"],
                   t01_sms_encoding=r["sms_encoding"], t01_sms_segments=r["sms_segments"])
    elif channel == "whatsapp":
        row.update(wa_category=r["meta_category"] or None, wa_template_name=f"t01_{slug}", wa_language=lang,
                   t01_meta_body=r["meta_body"], t01_meta_variables=r["meta_variables"])
    elif channel == "ivr":
        row["recording_status"] = "PENDING_RECORDING"
    row["t01_template_id"] = tid
    row["t01_angle"] = r["angle"]
    row["t01_purpose"] = r["purpose"]
    row["t01_cta"] = r["cta"]
    if pmap:
        row["t01_placeholder_map"] = pmap
    row["source"] = "Ojas T01 outbound catalogue v1.0.0 (policy/T01-outbound-templates)"
    return row


def build(pdir: Optional[Path] = None) -> dict:
    src, _ = t01_paths(pdir)
    t01 = read_t01(src)
    rows = [convert_row(r) for r in t01]
    for row in rows:
        res = check_template(row, rows)
        row["approval"] = approval_block(res, row)
        row["approved"] = res.passed
    srcmeta = json.loads((src.parent / "template_source.json").read_text(encoding="utf-8"))
    return {
        "catalog_id": "kairos-t01-imported",
        "catalog_version": f"t01-{srcmeta.get('catalog_version', '?')}-import-1",
        "status": "IMPORTED_FROM_T01",
        "source": f"policy/{T01_DIRNAME}/templates.csv ({srcmeta.get('catalog_id')} {srcmeta.get('catalog_version')}, "
                  f"{srcmeta.get('status')}, owner {srcmeta.get('owner')})",
        "owner": "Ojas (Product) - wording; K22 (Yuval) - import and checklist",
        "approval_meaning": "APPROVED_FOR_DEMO = passed the Kairos collections-compliance checklist "
                            "(backend/kairos_backend/template_compliance.py) for the synthetic demo. Not lender, "
                            "DLT or Meta approval. FAILED_CHECKLIST rows are reported to Product, not edited.",
        "mapped_placeholders": dict(MAPPED_PLACEHOLDERS),
        "band_tier": dict(BAND_TIER),
        "notes": NOTES,
        "templates": rows,
    }


def dumps(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def flag_summary(data: dict, samples: int = 3) -> Dict[str, dict]:
    by_rule: Dict[str, List[str]] = defaultdict(list)
    for row in data["templates"]:
        for rule, v in row["approval"]["checklist"].items():
            if v == "FAIL":
                by_rule[rule].append(row["id"])
    return {rule: {"count": len(ids), "samples": ids[:samples]} for rule, ids in sorted(by_rule.items())}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Import T01 templates into the K22 catalog format.")
    ap.add_argument("--check", action="store_true", help="exit 1 if t01_catalog.json is missing or stale")
    ap.add_argument("--report", action="store_true", help="print the per-rule checklist summary")
    a = ap.parse_args(argv)
    _, out = t01_paths()
    data = build()
    text = dumps(data)
    if a.check:
        if not out.exists() or out.read_text(encoding="utf-8") != text:
            print(f"STALE: {out} differs from a fresh import; run scripts/import_t01.py", file=sys.stderr)
            return 1
        print(f"OK: {out.name} is up to date ({len(data['templates'])} rows)")
    else:
        out.write_text(text, encoding="utf-8")
        print(f"wrote {out} ({len(data['templates'])} rows)")
    rows = data["templates"]
    ok = sum(r["approved"] for r in rows)
    print(f"our checklist: {ok}/{len(rows)} T01 rows pass, {len(rows) - ok} flagged")
    if a.report:
        for rule, s in flag_summary(data).items():
            print(f"  {rule:28s} {s['count']:4d}  e.g. {', '.join(s['samples'])}")
        print("  by channel:", dict(Counter((r["channel"], r["approved"]) for r in rows)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
