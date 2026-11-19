"""Evaluate the K09 intent stack against the current rules (SIMULATED / hand-written data only).

    python scripts/eval_intent.py                     # writes reports/intent_eval.json

Datasets
  * synthetic_test : generate(TEST_SEED) -- same templates as training, DIFFERENT seed (9001 vs 1729).
                     Optimistic by construction; it checks the plumbing, not generalisation.
  * gold           : tests/data/intent_gold.jsonl, ~160 hand-written messages (Hinglish, Devanagari,
                     code-mixed, typos, sarcasm, voice-summary style, ambiguous). Written independently
                     of the templates. Reference day 2026-11-19. This is the number that matters.

Gold labelling rules: one label per message, the one the system should act on. Safety precedence is
part of the label (an opt-out mixed with abuse is STOP; self-harm is HARDSHIP). Truly ambiguous
messages (sarcasm, "hmm") are UNKNOWN: abstaining is the correct answer for them.

Offline: no local or cloud LLM is called here. A message that would go to an LLM tier abstains, and
is counted under "would_reach_llm". Reports per-tier coverage, accuracy / macro-F1, safety-label
confusion, latency p50 / p95, the legacy (intent.IntentClassifier rules) baseline, and the decision
on whether the ingestion path defaults to the stack.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kairos_backend.intent_corpus import TEST_SEED, generate  # noqa: E402
from kairos_backend.intent_stack import (  # noqa: E402
    LABELS, SAFETY_LABELS, STACK_VERSION, TO_LEGACY, UNKNOWN, IntentStack, default_model, legacy_rules_result,
)

TODAY = date(2026, 11, 19)
GOLD = ROOT / "tests" / "data" / "intent_gold.jsonl"


def load_gold(path: Path = GOLD) -> List[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def pct(x: float) -> float:
    return round(100.0 * x, 2)


def prf(gold: Sequence[str], pred: Sequence[str], labels: Sequence[str]) -> Dict[str, Any]:
    per: Dict[str, Dict[str, float]] = {}
    for lab in labels:
        tp = sum(1 for g, p in zip(gold, pred) if g == lab and p == lab)
        fp = sum(1 for g, p in zip(gold, pred) if g != lab and p == lab)
        fn = sum(1 for g, p in zip(gold, pred) if g == lab and p != lab)
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
        per[lab] = {"support": tp + fn, "precision": round(prec, 4), "recall": round(rec, 4), "f1": round(f1, 4)}
    present = [lab for lab in labels if per[lab]["support"] > 0]
    macro = sum(per[lab]["f1"] for lab in present) / len(present) if present else 0.0
    acc = sum(1 for g, p in zip(gold, pred) if g == p) / len(gold) if gold else 0.0
    return {"n": len(gold), "accuracy": round(acc, 4), "macro_f1": round(macro, 4), "per_label": per}


def safety_confusion(gold: Sequence[str], pred: Sequence[str]) -> Dict[str, Any]:
    out = {}
    for lab in SAFETY_LABELS:
        g_idx = [i for i, g in enumerate(gold) if g == lab]
        missed = Counter(pred[i] for i in g_idx if pred[i] != lab)
        fp_from = Counter(gold[i] for i, p in enumerate(pred) if p == lab and gold[i] != lab)
        tp = len(g_idx) - sum(missed.values())
        out[lab] = {"support": len(g_idx), "recall": round(tp / len(g_idx), 4) if g_idx else None,
                    "missed_as": dict(missed), "false_positives_from": dict(fp_from)}
    return out


def percentile(xs: List[float], q: float) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    k = min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))
    return round(s[k], 4)


def run_stack(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    stack = IntentStack()                    # rules + small model; no LLM tiers offline
    preds, tiers, lat, results = [], [], [], []
    for r in rows:
        t0 = time.perf_counter()
        res = stack.classify(r["text"], TODAY, r.get("lang", "whatsapp"))
        lat.append((time.perf_counter() - t0) * 1000)
        preds.append(res.label)
        tiers.append(res.tier_used)
        results.append(res)
    t0 = time.perf_counter()
    for r in rows:
        stack.classify(r["text"], TODAY)
    cached_ms = (time.perf_counter() - t0) * 1000 / max(1, len(rows))
    return {"preds": preds, "tiers": tiers, "lat": lat, "results": results, "cached_ms": cached_ms}


def run_legacy(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    preds, lat = [], []
    for r in rows:
        t0 = time.perf_counter()
        res = legacy_rules_result(r["text"], TODAY)
        lat.append((time.perf_counter() - t0) * 1000)
        preds.append(res.label)
    return {"preds": preds, "lat": lat}


def evaluate(rows: List[Dict[str, Any]], name: str) -> Dict[str, Any]:
    gold = [r["label"] for r in rows]
    st = run_stack(rows)
    lg = run_legacy(rows)
    labels = list(LABELS)
    by_tier: Dict[str, List[int]] = defaultdict(list)
    for i, t in enumerate(st["tiers"]):
        by_tier[t].append(i)
    tiers = {}
    for t in ("rules", "small_model", "local_llm", "cloud_llm", "abstain"):
        idx = by_tier.get(t, [])
        correct = sum(1 for i in idx if st["preds"][i] == gold[i])
        tiers[t] = {"n": len(idx), "coverage_pct": pct(len(idx) / len(rows)),
                    "accuracy_pct": pct(correct / len(idx)) if idx else None}
    answered = [i for i, t in enumerate(st["tiers"]) if t != "abstain"]
    would_llm = [i for i, res in enumerate(st["results"]) if res.tier_used == "abstain" and "small_model" in res.evidence.get("escalated_to", [])]
    legacy_gold = [TO_LEGACY[g] for g in gold]
    out: Dict[str, Any] = {
        "dataset": name, "n": len(rows),
        "stack": {
            **{k: v for k, v in prf(gold, st["preds"], labels).items() if k != "per_label"},
            "per_label": prf(gold, st["preds"], labels)["per_label"],
            "accuracy_on_answered": round(sum(1 for i in answered if st["preds"][i] == gold[i]) / len(answered), 4) if answered else None,
            "abstain_rate": round(1 - len(answered) / len(rows), 4),
            "tiers": tiers,
            "would_reach_llm_tier": {"n": len(would_llm), "pct": pct(len(would_llm) / len(rows))},
            "safety": safety_confusion(gold, st["preds"]),
            "needs_human_pct": pct(sum(1 for res in st["results"] if res.needs_human) / len(rows)),
            "latency_ms": {"p50": percentile(st["lat"], 0.5), "p95": percentile(st["lat"], 0.95),
                           "max": round(max(st["lat"]), 4), "cached_mean": round(st["cached_ms"], 4)},
            "legacy_projection_accuracy": round(sum(1 for g, p in zip(legacy_gold, st["preds"]) if g == TO_LEGACY[p]) / len(rows), 4),
        },
        "legacy_rules": {
            **{k: v for k, v in prf(gold, lg["preds"], labels).items() if k != "per_label"},
            "abstain_rate": round(sum(1 for p in lg["preds"] if p == UNKNOWN) / len(rows), 4),
            "safety": safety_confusion(gold, lg["preds"]),
            "latency_ms": {"p50": percentile(lg["lat"], 0.5), "p95": percentile(lg["lat"], 0.95)},
            "legacy_projection_accuracy": round(sum(1 for g, p in zip(legacy_gold, lg["preds"]) if g == TO_LEGACY[p]) / len(rows), 4),
        },
    }
    if any("date" in r for r in rows):
        dated = [i for i, r in enumerate(rows) if r.get("date")]
        ok = sum(1 for i in dated if st["results"][i].entities.get("date") == rows[i]["date"])
        out["stack"]["date_entity_accuracy"] = {"n": len(dated), "exact": ok, "pct": pct(ok / len(dated)) if dated else None}
        out["stack"]["by_lang"] = {}
        for lang in sorted({r.get("lang", "?") for r in rows}):
            idx = [i for i, r in enumerate(rows) if r.get("lang") == lang]
            out["stack"]["by_lang"][lang] = {"n": len(idx), "accuracy_pct": pct(sum(1 for i in idx if st["preds"][i] == gold[i]) / len(idx))}
        out["errors"] = [{"id": rows[i].get("id"), "text": rows[i]["text"], "gold": gold[i], "pred": st["preds"][i],
                          "tier": st["tiers"][i], "legacy": lg["preds"][i]}
                         for i in range(len(rows)) if st["preds"][i] != gold[i]]
    return out


def determinism_check(rows: List[Dict[str, Any]]) -> bool:
    a = [IntentStack().classify(r["text"], TODAY).to_dict() for r in rows]
    b = [IntentStack().classify(r["text"], TODAY).to_dict() for r in rows]
    return a == b


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "reports" / "intent_eval.json"))
    ap.add_argument("--synthetic-per-label", type=int, default=60)
    a = ap.parse_args()
    gold = load_gold()
    synth = generate(TEST_SEED, a.synthetic_per_label)
    m = default_model()
    report: Dict[str, Any] = {
        "evidence": "SIMULATED (synthetic corpus) + hand-written gold set; no customer data; offline, no LLM calls",
        "stack_version": STACK_VERSION,
        "model": {"version": m.model_version if m else None, "train_seed": m.seed if m else None,
                  "test_seed": TEST_SEED, "temperature": m.temperature if m else None},
        "reference_day": TODAY.isoformat(),
        "gold": evaluate(gold, "gold"),
        "synthetic_test": evaluate(synth, f"synthetic seed {TEST_SEED}"),
        "deterministic": determinism_check(gold),
    }
    g = report["gold"]
    st, lg = g["stack"], g["legacy_rules"]
    safety_ok = all((st["safety"][lab]["recall"] or 0) >= (lg["safety"][lab]["recall"] or 0) for lab in ("STOP", "HARDSHIP"))
    beats = st["accuracy"] > lg["accuracy"] and st["macro_f1"] > lg["macro_f1"] and \
        st["legacy_projection_accuracy"] >= lg["legacy_projection_accuracy"] and safety_ok
    report["decision"] = {
        "stack_beats_current_rules_on_gold": beats,
        "ingestion_default": "stack" if beats else "legacy rules (stack opt-in)",
        "criteria": "gold accuracy and macro-F1 above the current rules, legacy-label projection accuracy not lower, "
                    "STOP / HARDSHIP recall not lower",
    }
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2, sort_keys=False, default=str)
        fh.write("\n")

    def line(name: str, d: Dict[str, Any]) -> None:
        s, l_ = d["stack"], d["legacy_rules"]
        print(f"\n== {name} (n={d['n']}) ==")
        print(f"  stack : acc {pct(s['accuracy'])}%  macro-F1 {pct(s['macro_f1'])}%  answered-acc "
              f"{pct(s['accuracy_on_answered'] or 0)}%  abstain {pct(s['abstain_rate'])}%  legacy-projection {pct(s['legacy_projection_accuracy'])}%")
        print(f"  legacy: acc {pct(l_['accuracy'])}%  macro-F1 {pct(l_['macro_f1'])}%  legacy-projection {pct(l_['legacy_projection_accuracy'])}%")
        for t, v in s["tiers"].items():
            print(f"    tier {t:12} n={v['n']:5} coverage {v['coverage_pct']:6}%  accuracy {v['accuracy_pct']}")
        print(f"  would reach an LLM tier: {s['would_reach_llm_tier']}")
        for lab, v in s["safety"].items():
            print(f"  safety {lab:9} recall {v['recall']}  (legacy {l_['safety'][lab]['recall']})  missed_as {v['missed_as']}  fp_from {v['false_positives_from']}")
        print(f"  latency ms p50 {s['latency_ms']['p50']}  p95 {s['latency_ms']['p95']}  cached {s['latency_ms']['cached_mean']}")
        if "date_entity_accuracy" in s:
            print(f"  date entity exact: {s['date_entity_accuracy']}")

    line("gold", report["gold"])
    line("synthetic_test", report["synthetic_test"])
    print(f"\ndeterministic: {report['deterministic']}   decision: {report['decision']['ingestion_default']}")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
