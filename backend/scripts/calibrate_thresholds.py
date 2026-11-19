"""Data-driven human-handoff threshold for the intent stack (Learn-then-Test). SIMULATED / hand-written data only.

    python scripts/calibrate_thresholds.py                 # alpha 0.05, delta 0.1, seed 7 -> reports/threshold_calibration.json
    python scripts/calibrate_thresholds.py --quick         # smaller synthetic share (a few seconds)
    python scripts/calibrate_thresholds.py --alpha 0.02 --pvalue hoeffding
    KAIROS_INTENT_THRESHOLDS=calibrated ...                # the stack then reads the chosen threshold (default: hand-set)

What is calibrated: MODEL_ACCEPT, the small model's top probability needed to answer WITHOUT a human
(hand-set 0.70). Rule thresholds stay hand-set, and STOP / HARDSHIP / ABUSIVE are rule-final, so they
cannot depend on this number (checked below on every row).

Method: Learn-then-Test (Angelopoulos et al., arXiv:2110.01052) with fixed-sequence testing from the
most conservative threshold downwards and exact binomial p-values (or Hoeffding). Risk = error rate
among the messages the stack handles alone (tier != abstain and not sent to a human). Guarantee: with
probability >= 1 - delta over the calibration draw, the chosen threshold has risk <= alpha on data
exchangeable with the calibration data. On a SYNTHETIC + hand-written corpus this is a synthetic
guarantee; it has to be re-run on real labelled borrower messages before anyone relies on it.

Data: hand-written gold (160) + held-out (80) messages, plus a synthetic corpus from a seed that is
neither the training (1729), temperature (2718) nor test (9001) seed. Split 50/50 into calibration and
test, stratified by source, deterministically from --seed. Offline: no LLM tier is called.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kairos_backend.calibration import grid, ltt_fixed_sequence  # noqa: E402
from kairos_backend.intent_corpus import CALIBRATION_SEED, TEST_SEED, TRAIN_SEED, generate  # noqa: E402
from kairos_backend.intent_stack import (  # noqa: E402
    CALIBRATION_PATH, HARDSHIP, MODEL_ACCEPT, MODEL_NEVER_ALONE, RULE_ACCEPT, SAFETY_LABELS, STACK_VERSION, STOP, IntentStack,
    Thresholds, default_model,
)
from kairos_backend.safeio import atomic_write_text  # noqa: E402

TODAY = date(2026, 11, 19)
DATA = ROOT / "tests" / "data"
CORPUS_SEED = 31337   # synthetic calibration corpus; must differ from TRAIN / CALIBRATION / TEST seeds
assert CORPUS_SEED not in (TRAIN_SEED, CALIBRATION_SEED, TEST_SEED)
SAFETY = (STOP, HARDSHIP)


def load_rows(per_label: int, data: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if data in ("handwritten", "both"):
        for name in ("intent_gold.jsonl", "intent_heldout.jsonl"):
            with open(DATA / name, encoding="utf-8") as fh:
                rows += [{"text": r["text"], "label": r["label"], "source": "handwritten"}
                         for r in (json.loads(x) for x in fh if x.strip())]
    if data in ("synthetic", "both") and per_label > 0:
        rows += [{"text": r["text"], "label": r["label"], "source": "synthetic"} for r in generate(CORPUS_SEED, per_label)]
    return rows


def split(rows: List[Dict[str, Any]], seed: int):
    cal, test = [], []
    for src in sorted({r["source"] for r in rows}):
        part = [r for r in rows if r["source"] == src]
        random.Random(f"{seed}:{src}").shuffle(part)
        h = len(part) // 2
        cal += part[:h]
        test += part[h:]
    return cal, test


class Traced:
    """Each row classified once with the small model allowed to answer at any probability (A) and once
    never alone (B). At threshold λ the stack returns A when A came from that branch and p1 >= λ, else B
    (exact: the threshold only gates that one branch; tests/test_threshold_calibration.py checks it
    against a real stack at several λ)."""

    def __init__(self, rows: List[Dict[str, Any]]):
        model = default_model()
        a = IntentStack(model=model, thresholds=Thresholds(model_accept=0.0, source="calibration-probe"))
        b = IntentStack(model=model, thresholds=Thresholds(model_accept=MODEL_NEVER_ALONE, source="calibration-probe"))
        self.rows = rows
        self.items = []
        for r in rows:
            ra, rb = a.classify(r["text"], TODAY), b.classify(r["text"], TODAY)
            gated = ra.tier_used == "small_model" and rb.tier_used == "abstain"
            p1 = max(model.predict_proba(r["text"]).values()) if gated else 0.0
            self.items.append((r["label"], ra, rb, gated, p1))

    def at(self, lam: float):
        # a model safety label is never held above the hand-set bar (Thresholds.model_safety_accept)
        return [(g, ra if (gated and p1 >= (min(lam, MODEL_ACCEPT) if ra.label in SAFETY_LABELS else lam)) else rb)
                for g, ra, rb, gated, p1 in self.items]

    def stats(self, lam: float):
        n = k = 0
        for g, res in self.at(lam):
            if res.tier_used != "abstain" and not res.needs_human:
                n += 1
                k += res.label != g
        return n, k

    def metrics(self, lam: float) -> Dict[str, Any]:
        out = self.at(lam)
        n_auto, k = self.stats(lam)
        rec = {}
        for lab in SAFETY:
            sup = [res for g, res in out if g == lab]
            rec[lab] = {"support": len(sup), "recall": round(sum(r.label == lab for r in sup) / len(sup), 4) if sup else None}
        return {"n": len(out), "auto_handled": n_auto, "automation_rate": round(n_auto / len(out), 4) if out else None,
                "errors_among_auto": k, "error_rate_among_auto": round(k / n_auto, 4) if n_auto else None,
                "accuracy_all": round(sum(res.label == g for g, res in out) / len(out), 4) if out else None,
                "to_human": len(out) - n_auto, "safety_recall": rec}

    def safety_invariant(self, lambdas) -> Dict[str, Any]:
        """For gold STOP / HARDSHIP rows, over every threshold in the grid: recall never drops, and such a
        row is never handled without a human under a non-safety label."""
        rows = [i for i, it in enumerate(self.items) if it[0] in SAFETY]
        worst = {lab: 1.0 for lab in SAFETY}
        leaked = set()
        rule_final = sum(1 for i in rows if self.items[i][2].tier_used == "rules")
        for lam in lambdas:
            out = self.at(lam)
            for lab in SAFETY:
                sup = [out[i][1] for i in rows if out[i][0] == lab]
                if sup:
                    worst[lab] = min(worst[lab], sum(r.label == lab for r in sup) / len(sup))
            for i in rows:
                res = out[i][1]
                if res.label not in SAFETY and res.tier_used != "abstain" and not res.needs_human:
                    leaked.add(i)
        return {"safety_rows": len(rows), "decided_by_rules": rule_final,
                "min_recall_over_grid": {k: round(v, 4) for k, v in worst.items()},
                "auto_handled_as_non_safety_at_any_threshold": len(leaked)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--alpha", type=float, default=0.05, help="max error rate among auto-handled messages")
    ap.add_argument("--delta", type=float, default=0.10, help="allowed probability the guarantee fails")
    ap.add_argument("--seed", type=int, default=7, help="calibration / test split seed")
    ap.add_argument("--per-label", type=int, default=120, help="synthetic rows per label (17 labels)")
    ap.add_argument("--data", choices=("both", "handwritten", "synthetic"), default="both")
    ap.add_argument("--pvalue", choices=("binomial", "hoeffding"), default="binomial")
    ap.add_argument("--floor", type=float, default=0.50,
                    help="policy floor: the small model never answers alone below this, whatever the data says")
    ap.add_argument("--quick", action="store_true", help="--per-label 40 (film / CI)")
    ap.add_argument("--out", default=str(CALIBRATION_PATH))
    a = ap.parse_args(argv)
    if a.quick:
        a.per_label = 40
    t0 = time.perf_counter()
    rows = load_rows(a.per_label, a.data)
    cal_rows, test_rows = split(rows, a.seed)
    cal, test = Traced(cal_rows), Traced(test_rows)
    lam_grid = grid(0.99, a.floor, 0.01)
    lam, trace = ltt_fixed_sequence(lam_grid, cal.stats, a.alpha, a.delta, a.pvalue)
    chosen = lam if lam is not None else MODEL_NEVER_ALONE
    by_src = {}
    for src in sorted({r["source"] for r in test_rows}):
        t = Traced([r for r in test_rows if r["source"] == src])
        by_src[src] = {"hand_set": t.metrics(MODEL_ACCEPT), "calibrated": t.metrics(chosen)}
    report = {
        "evidence": "SIMULATED (synthetic corpus) + hand-written messages; no customer data; offline, no LLM calls",
        "method": "Learn-then-Test (Angelopoulos et al. 2021, arXiv:2110.01052): fixed-sequence test from "
                  "lambda=0.99 down to the policy floor, " + a.pvalue + " p-values; risk = error rate among messages handled "
                  "without a human. Related: conformal risk control, arXiv:2208.02814.",
        "guarantee": f"P(risk(chosen) <= {a.alpha}) >= {1 - a.delta} for data exchangeable with the calibration "
                     "split. SYNTHETIC guarantee: re-run on real labelled borrower messages before relying on it.",
        "stack_version": STACK_VERSION,
        "params": {"alpha": a.alpha, "delta": a.delta, "seed": a.seed, "pvalue": a.pvalue, "per_label": a.per_label,
                   "data": a.data, "synthetic_corpus_seed": CORPUS_SEED, "grid": [lam_grid[0], lam_grid[-1], 0.01]},
        "data": {"calibration": len(cal_rows), "test": len(test_rows),
                 "sources": {s: sum(r["source"] == s for r in rows) for s in sorted({r["source"] for r in rows})}},
        "hand_set": {"rule_accept": RULE_ACCEPT, "model_accept": MODEL_ACCEPT},
        "chosen": {"model_accept": chosen, "rule_accept": RULE_ACCEPT, "certified": lam is not None,
                   "note": ("smallest certified threshold" if lam is not None else
                            "no threshold certified: fail closed (small model never answers alone)")},
        "calibration_split": {"hand_set": cal.metrics(MODEL_ACCEPT), "calibrated": cal.metrics(chosen)},
        "test_split": {"hand_set": test.metrics(MODEL_ACCEPT), "calibrated": test.metrics(chosen), "by_source": by_src},
        "safety": {"calibration": cal.safety_invariant(lam_grid + [MODEL_NEVER_ALONE]),
                   "test": test.safety_invariant(lam_grid + [MODEL_NEVER_ALONE]),
                   "note": "STOP / HARDSHIP / ABUSIVE are rule-final; the calibrated threshold only gates the "
                           "small model's stand-alone answers"},
        "ltt_trace": trace,
        "flag": "KAIROS_INTENT_THRESHOLDS=calibrated reads chosen.model_accept; default is hand-set",
        "runtime_seconds": None,
    }
    report["runtime_seconds"] = round(time.perf_counter() - t0, 2)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    atomic_write_text(a.out, json.dumps(report, indent=2, ensure_ascii=False) + "\n")

    def line(name, m):
        sr = m["safety_recall"]
        return (f"  {name:22} automation {m['automation_rate'] * 100:5.1f}%   error among auto "
                f"{(m['error_rate_among_auto'] or 0) * 100:4.1f}% ({m['errors_among_auto']}/{m['auto_handled']})   "
                f"STOP recall {sr[STOP]['recall']}  HARDSHIP recall {sr[HARDSHIP]['recall']}")

    print(f"Learn-then-Test  alpha={a.alpha}  delta={a.delta}  ({a.pvalue})  seed={a.seed}   SIMULATED + hand-written")
    print(f"Data: {len(cal_rows)} calibration / {len(test_rows)} test  {report['data']['sources']}")
    last = trace[-1]
    print(f"MODEL_ACCEPT hand-set {MODEL_ACCEPT:.2f}  ->  calibrated {chosen:.2f}   "
          f"({'certified' if lam is not None else 'NOT certified: fail closed'}; walk stopped at "
          f"lambda={last['lambda']} p={last['p_value']})")
    print("Held-out test split:")
    print(line("hand-set " + f"{MODEL_ACCEPT:.2f}", report["test_split"]["hand_set"]))
    print(line("calibrated " + f"{chosen:.2f}", report["test_split"]["calibrated"]))
    for src, d in by_src.items():
        print(f" {src}:")
        print(line("hand-set", d["hand_set"]))
        print(line("calibrated", d["calibrated"]))
    s = report["safety"]["test"]
    print(f"Safety (test): {s['safety_rows']} STOP/HARDSHIP rows, {s['decided_by_rules']} decided by rules; lowest recall "
          f"over every threshold {lam_grid[0]}..{lam_grid[-1]} and fail-closed: {s['min_recall_over_grid']}; "
          f"auto-handled as non-safety at any threshold: {s['auto_handled_as_non_safety_at_any_threshold']}")
    print(f"Wrote {a.out}  ({report['runtime_seconds']}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
