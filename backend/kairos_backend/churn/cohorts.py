"""K27 cohort DSL: named, reusable, priority-ordered cohorts (CN PRD v3 §5.3).

    CohortSet.load() / CohortSet(doc)        policy/K27-auto-churn/cohorts.json
    cs.assign(feats, campaign, region)       -> (cohort_id, explanation)   first match wins, else UNASSIGNED
    cs.coverage(features_by_borrower)        per-cohort counts (first match), overlaps, orphans (UNASSIGNED)
    cs.snapshot_orphans(name, ids)           PRD "one click": orphans -> a FROZEN snapshot cohort (never recomputes)

Cohorts are keyed by a stable `id` (the PRD warns name-keyed journeys break on rename).
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from ..policy import policy_dir
from .dsl import evaluate, fields_of, in_scope, validate
from .state import known_feature

UNASSIGNED = "UNASSIGNED"


def k27_dir(base: Optional[Path] = None) -> Path:
    return Path(base or policy_dir()) / "K27-auto-churn"


def load_json(name: str, base: Optional[Path] = None) -> dict:
    with open(k27_dir(base) / name, encoding="utf-8") as f:
        return json.load(f)


class CohortSet:
    def __init__(self, doc: Mapping[str, Any]):
        self.doc = copy.deepcopy(dict(doc))
        self.validate()
        self.cohorts = self.doc["cohorts"]
        self.by_id = {c["id"]: c for c in self.cohorts}
        self.order = {c["id"]: i for i, c in enumerate(self.cohorts)}
        self.order[UNASSIGNED] = len(self.cohorts)

    @classmethod
    def load(cls, base: Optional[Path] = None) -> "CohortSet":
        return cls(load_json("cohorts.json", base))

    @property
    def version(self) -> str:
        return self.doc.get("policy_version", "unversioned")

    def validate(self) -> None:
        cs = self.doc.get("cohorts")
        if not isinstance(cs, list) or not cs:
            raise ValueError("cohorts.json needs a non-empty 'cohorts' list")
        seen = set()
        for i, c in enumerate(cs):
            cid = c.get("id")
            if not cid or cid in seen or cid == UNASSIGNED:
                raise ValueError(f"cohort[{i}]: id missing, duplicate or reserved ({cid!r})")
            seen.add(cid)
            if c.get("kind") == "snapshot":
                if not isinstance(c.get("borrower_ids"), list):
                    raise ValueError(f"snapshot cohort {cid} needs borrower_ids")
                continue
            if "condition" not in c:
                raise ValueError(f"cohort {cid} has no condition")
            validate(c["condition"], f"cohorts.{cid}")
            bad = [f for f in fields_of(c["condition"]) if not known_feature(f)]
            if bad:
                raise ValueError(f"cohort {cid}: unknown fields {bad}")

    def matches(self, cohort: Mapping[str, Any], feats: Mapping[str, Any], borrower_id: Optional[str] = None,
                campaign: Optional[str] = None, region: Optional[str] = None) -> tuple[bool, list[str]]:
        if not in_scope(cohort.get("scope"), campaign, None, region):
            return False, [f"out of scope {cohort.get('scope')}"]
        if cohort.get("kind") == "snapshot":
            ok = borrower_id in cohort["borrower_ids"]
            return ok, [f"snapshot membership (frozen {cohort.get('frozen_at')}) -> {ok}"]
        return evaluate(cohort["condition"], feats)

    def assign(self, feats: Mapping[str, Any], borrower_id: Optional[str] = None, campaign: Optional[str] = None,
               region: Optional[str] = None) -> tuple[str, dict]:
        """First matching cohort in priority order; explanation lists the cohorts tried before it."""
        tried = []
        for c in self.cohorts:
            ok, why = self.matches(c, feats, borrower_id, campaign, region)
            if ok:
                return c["id"], {"cohort": c["id"], "matched": why, "skipped": tried,
                                 "cohorts_version": self.version}
            tried.append(c["id"])
        return UNASSIGNED, {"cohort": UNASSIGNED, "matched": ["no cohort matched (live orphan)"], "skipped": tried,
                            "cohorts_version": self.version}

    def journey_of(self, cohort_id: str, journeys_doc: Optional[Mapping] = None) -> str:
        if cohort_id == UNASSIGNED:
            return (journeys_doc or {}).get("cohort_journey_override", {}).get(UNASSIGNED, "DEFAULT_LIGHT")
        return self.by_id[cohort_id].get("journey", "NONE")

    def coverage(self, rows: Iterable[tuple[str, Mapping[str, Any], Optional[str], Optional[str]]]) -> dict:
        """rows: (borrower_id, features, campaign, region). PRD §5.3: case count per cohort (first match),
        how many match none, plus overlap (loans matching several cohorts, resolved by priority)."""
        counts = {c["id"]: 0 for c in self.cohorts}
        counts[UNASSIGNED] = 0
        any_match = {c["id"]: 0 for c in self.cohorts}
        orphans, overlap, total = [], 0, 0
        for bid, feats, campaign, region in rows:
            total += 1
            hits = [c["id"] for c in self.cohorts if self.matches(c, feats, bid, campaign, region)[0]]
            for h in hits:
                any_match[h] += 1
            if len(hits) > 1:
                overlap += 1
            if hits:
                counts[hits[0]] += 1
            else:
                counts[UNASSIGNED] += 1
                orphans.append(bid)
        return {"total": total, "covered": total - len(orphans), "orphans": len(orphans),
                "orphan_ids": sorted(orphans)[:500], "by_cohort": counts, "matching_any_priority": any_match,
                "overlapping_borrowers": overlap, "cohorts_version": self.version,
                "orphan_mode": self.doc.get("orphan_mode", "live")}

    def snapshot_orphans(self, cohort_id: str, name: str, borrower_ids: list[str], frozen_at: str,
                         journey: str = "DEFAULT_LIGHT") -> dict:
        """Return a NEW cohorts doc with a frozen snapshot cohort inserted just before the last risk cohort
        fallthrough (i.e. at the end; it only catches what nothing else catches). PRD WATCH OUT: it never
        recomputes; it is labelled kind 'snapshot' with its freeze time."""
        doc = copy.deepcopy(self.doc)
        doc["cohorts"].append({"id": cohort_id, "name": name, "kind": "snapshot", "journey": journey,
                               "borrower_ids": sorted(borrower_ids), "frozen_at": frozen_at,
                               "description": "Frozen snapshot of orphan borrowers (does not recompute)."})
        return doc
