"""T01 import: Ojas's 300 outbound templates in the K22 catalog format, checked by OUR checklist,
without touching his wording or the live catalog.json selection."""
from __future__ import annotations

import csv
import json
import re
from collections import Counter

import pytest

from kairos_backend.templates import TemplateCatalog, default_catalog_path
from scripts.import_t01 import (BAND_TIER, MAPPED_PLACEHOLDERS, build, dumps, t01_paths, to_ours,
                                to_t01)
from tests.policy_conformance.t01_adapter import RULE_KIND, checker_flags, run_t01

SRC, OUT = t01_paths()


@pytest.fixture(scope="module")
def t01():
    with open(SRC, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


@pytest.fixture(scope="module")
def imported():
    return json.loads(OUT.read_text(encoding="utf-8"))


def test_generated_file_is_up_to_date(imported):
    assert OUT.read_text(encoding="utf-8") == dumps(build()), "run backend/scripts/import_t01.py"


def test_import_is_deterministic():
    assert dumps(build()) == dumps(build())


def test_300_rows_150_ids_by_two_languages(t01, imported):
    rows = imported["templates"]
    assert len(t01) == len(rows) == 300
    ids = [r["t01_template_id"] for r in rows]
    assert sorted(ids) == sorted(r["template_id"] for r in t01)
    assert all(n == 1 for n in Counter(ids).values())
    stems = Counter(re.sub(r"-(EN|HI)$", "", i) for i in ids)
    assert len(stems) == 150 and set(stems.values()) == {2}
    assert Counter(r["lang"] for r in rows) == {"en": 150, "hi": 150}


def test_channel_band_language_and_placeholders_preserved(t01, imported):
    ours = {r["t01_template_id"]: r for r in imported["templates"]}
    for t in t01:
        r = ours[t["template_id"]]
        assert r["channel"] == t["channel"].lower()
        assert r["dpd_bands"] == [t["band"]] and r["lang"] == t["language"].lower()
        want = list(dict.fromkeys(MAPPED_PLACEHOLDERS.get(p, p) for p in t["placeholders"].split(",") if p))
        assert r["variables"] == want, t["template_id"]


def test_his_text_is_unchanged_after_reverse_mapping(t01, imported):
    ours = {r["t01_template_id"]: r for r in imported["templates"]}
    for t in t01:
        r = ours[t["template_id"]]
        back = {v: k for k, v in MAPPED_PLACEHOLDERS.items()}
        pmap = r.get("t01_placeholder_map", {})
        assert set(pmap.items()) <= set(back.items())
        if "t01_segments" in r:
            seg = r["t01_segments"]
            body = f"[OPENER] {to_t01(seg['OPENER'], pmap)} [AFTER_CONFIRM] {to_t01(seg['AFTER_CONFIRM'], pmap)}"
            if r["channel"] == "voicebot":
                assert (r["opening_line"], r["body"]) == (seg["OPENER"], seg["AFTER_CONFIRM"])
            else:
                assert r["body"] == seg["OPENER"] + " " + seg["AFTER_CONFIRM"]
        else:
            body = to_t01(r["body"], pmap)
        assert body == t["body"], t["template_id"]
        assert to_t01(r.get("subject", ""), pmap) == t["subject"]


def test_placeholder_mapping_round_trips():
    assert to_ours("Pay {link} Rs {emi} {unsubscribe_link}") == "Pay {{payment_link}} Rs {{amount}} {{unsubscribe_link}}"
    assert to_t01("{{payment_link}} {{amount}}", {"payment_link": "link", "amount": "emi"}) == "{link} {emi}"
    assert set(BAND_TIER) == {"B1", "B2", "B3", "B4"}


def test_approval_comes_from_our_checklist_only(imported):
    for r in imported["templates"]:
        assert r["approved"] == (r["approval"]["status"] == "APPROVED_FOR_DEMO")
        assert r["approved"] == all(v != "FAIL" for v in r["approval"]["checklist"].values())


def test_live_catalog_selection_unaffected():
    assert default_catalog_path().name == "catalog.json"
    cat = TemplateCatalog.load()
    assert not any(t.id.startswith(("SM-", "WH-", "EM-", "IV-", "VO-")) for t in cat.templates)


def test_conformance_adapter_covers_every_row(t01):
    res = run_t01()
    assert [r["case_id"] for r in res] == [t["template_id"] for t in t01]
    assert {r["status"] for r in res} <= {"PASS", "FAIL", "NOT_IMPLEMENTED"}
    assert not [r for r in res if r["status"] == "NOT_IMPLEMENTED"]


def test_his_text_has_no_threat_or_third_party_wording():
    """Our checker finds no threat, legal-action or third-party wording in any T01 row."""
    flags = checker_flags()
    for rule in ("NO_THREATS_OR_ABUSE", "NO_THIRD_PARTY_DISCLOSURE", "LENGTH_LIMITS", "LENDER_IDENTIFIED"):
        assert rule not in flags, flags.get(rule)
    rows = json.loads(OUT.read_text(encoding="utf-8"))["templates"]
    misleading = [r["id"] for r in rows for x in r["approval"]["failures"]
                  if x.startswith("CONSEQUENCES_PERMITTED: consequence not permitted")]   # legal action, court, ...
    assert not misleading
    assert all(rule in RULE_KIND for rule in flags)
