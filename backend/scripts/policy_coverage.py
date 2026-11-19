"""Policy coverage: run Ojas's pack against our code and rewrite the matrix in POLICY_COVERAGE.md.

    python scripts/policy_coverage.py                      # live cases only; K15 report cases from reports/*.json
    python scripts/policy_coverage.py --k15-evidence ev.json   # add full-scale K15 numbers (see adapters.run_k15)
    python scripts/policy_coverage.py --write-snapshot     # also update tests/policy_conformance/expected_status.json

Only the part of POLICY_COVERAGE.md between the GENERATED markers is rewritten; the summary and the
OPEN_DECISIONs above it are written by people.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from tests.policy_conformance.adapters import run_all  # noqa: E402

ROOT = BACKEND.parent
DOC = ROOT / "POLICY_COVERAGE.md"
SNAPSHOT = BACKEND / "tests" / "policy_conformance" / "expected_status.json"
BEGIN, END = "<!-- BEGIN GENERATED (scripts/policy_coverage.py) -->", "<!-- END GENERATED -->"
STATUSES = ("PASS", "FAIL", "NOT_IMPLEMENTED", "NOT_APPLICABLE")


def _cell(s: str) -> str:
    return str(s).replace("|", "\\|").replace("\n", " ")


def render(results: dict) -> str:
    lines = ["## Totals", "", "| Module | Cases | PASS | FAIL | NOT_IMPLEMENTED | NOT_APPLICABLE |", "|---|---|---|---|---|---|"]
    grand = Counter()
    for m, rows in results.items():
        c = Counter(r["status"] for r in rows)
        grand.update(c)
        lines.append(f"| {m} | {len(rows)} | " + " | ".join(str(c.get(s, 0)) for s in STATUSES) + " |")
    lines.append(f"| **All** | **{sum(grand.values())}** | " + " | ".join(f"**{grand.get(s, 0)}**" for s in STATUSES) + " |")
    lines += ["", "## Matrix (module x case)", ""]
    for m, rows in results.items():
        c = Counter(r["status"] for r in rows)
        lines += [f"### {m}", "", ", ".join(f"{s} {c[s]}" for s in STATUSES if c.get(s)), ""]
        if m.startswith("T01"):
            # 300 rows: list the non-passing ones grouped by failing rules, not one table row each
            groups: dict = {}
            for r in rows:
                if r["status"] != "PASS":
                    key = r["detail"].split(";")[0][:160]
                    groups.setdefault((r["status"], key), []).append(r["case_id"])
            from tests.policy_conformance.t01_adapter import checker_flags
            lines += ["Rules our K22 checker flags in his rows (kind: FORMAT = his text is fine, our checker does not "
                      "recognise the form; POLICY = a real rule conflict for Product; NOT_BUILT = we lack the provider "
                      "object; T01_DEFECT = inside his pack):", "",
                      "| Rule | Rows | Kind | Sample ids |", "|---|---|---|---|"]
            for rule, f in sorted(checker_flags().items(), key=lambda kv: -kv[1]["count"]):
                lines.append(f"| {rule} | {f['count']} | {f['kind']} | " + ", ".join(f"`{i}`" for i in f["samples"]) + " |")
            lines += ["", "| Status | Our checker says | Rows | Template ids |", "|---|---|---|---|"]
            for (st, key), ids in sorted(groups.items(), key=lambda kv: -len(kv[1])):
                shown = ", ".join(f"`{i}`" for i in ids[:6]) + (f" … (+{len(ids) - 6})" if len(ids) > 6 else "")
                lines.append(f"| {st} | {_cell(key)} | {len(ids)} | {shown} |")
            lines.append("")
            continue
        lines += ["| Case | Status | Description | Our result |", "|---|---|---|---|"]
        for r in rows:
            src = " (report)" if r.get("source") == "report" else ""
            lines.append(f"| {r['case_id']} | {r['status']}{src} | {_cell(r['description'])} | {_cell(r['detail'][:220])} |")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--k15-evidence", default=None, help="JSON with full-scale K15 numbers")
    ap.add_argument("--write-snapshot", action="store_true")
    args = ap.parse_args()
    ev = json.loads(Path(args.k15_evidence).read_text()) if args.k15_evidence else None
    results = run_all(ev)
    body = render(results)
    doc = DOC.read_text(encoding="utf-8") if DOC.exists() else f"# Policy coverage\n\n{BEGIN}\n{END}\n"
    head, _, rest = doc.partition(BEGIN)
    _, _, tail = rest.partition(END)
    DOC.write_text(f"{head}{BEGIN}\n{body}\n{END}{tail}", encoding="utf-8")
    if args.write_snapshot:
        snap = {m: {r["case_id"]: r["status"] for r in rows if r.get("source", "live") == "live"} for m, rows in results.items()}
        SNAPSHOT.write_text(json.dumps(snap, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    for m, rows in results.items():
        c = Counter(r["status"] for r in rows)
        print(f"{m:28} " + "  ".join(f"{s} {c.get(s, 0)}" for s in STATUSES))
    return 0


if __name__ == "__main__":
    sys.exit(main())
