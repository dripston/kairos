"""Evaluate the Sarvam models where Kairos uses them (SIMULATED / hand-written data only). From backend/:

    python scripts/eval_models.py                      # offline: recorded fixtures or the STAND-IN ("LIVE NOT RUN")
    python scripts/eval_models.py --live               # real calls, only if a key is found (see sarvam/config.py)
    python scripts/eval_models.py --live --record      # ... and save the responses to tests/data/sarvam_recorded.json
    KAIROS_DOTENV=/path/to/backend/.env python scripts/eval_models.py --live

Sections (reports/models_eval.json):
  intent    gold (160) + held-out (80): current stack vs stack + prism tier. Accuracy, macro-F1, STOP / HARDSHIP
            recall (must stay 100%), abstain rate, latency, prism calls.
  voice     prism re-classification of synthetic LOW-confidence call summaries + what the voice rule did
            (upgrades, reviews, wrong upgrades, safety outcomes lowered).
  cm03      the seeded CM-03 eval with the conversational model OFF and ON: acceptance bars (sequential pass),
            0 disclosures before verification, % of model replies rejected by the validator, outcome digests.
  summaries deepseek narratives (K05, hand-off case, explain-decision, K26 daily ops) on real journey / CM-03
            facts: accepted vs fell back, independent PII + number + key-field re-check of accepted text.

Live usage is capped (default 400 prism + 100 LLM calls per run); over the cap the deterministic path answers.
The key is never printed: only where it was found ("backend/.env", "KAIROS_DOTENV", ...).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import time
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

from eval_intent import load_gold, percentile, prf, safety_confusion  # noqa: E402
from kairos_backend import pii  # noqa: E402
from kairos_backend.intent_stack import LABELS, IntentStack  # noqa: E402
from kairos_backend.sarvam.client import SarvamClient, urllib_transport  # noqa: E402
from kairos_backend.sarvam.config import load_api_key, models_switched_off  # noqa: E402
from kairos_backend.sarvam.fixtures import FixtureTransport, RecordingTransport  # noqa: E402
from kairos_backend.sarvam.narrate import Narrator  # noqa: E402
from kairos_backend.sarvam.phrasing import Phraser  # noqa: E402
from kairos_backend.sarvam.prism import PrismClassifier, mask_for_model, voice_rule  # noqa: E402

TODAY = date(2026, 11, 19)
DATA = BACKEND / "tests" / "data"


def make_client(live: bool, record: bool, max_prism: int, max_llm: int):
    caps = {"prism": max_prism, "llm": max_llm}
    if live:
        if models_switched_off():
            return None, None, "FIXTURE (LIVE NOT RUN: KAIROS_SARVAM_MODELS=off)"
        key, src = load_api_key()
        if key:
            t = RecordingTransport(urllib_transport) if record else urllib_transport
            return SarvamClient(api_key=key, transport=t, max_calls=caps), t, f"LIVE (key from {src})"
        mode = "FIXTURE (LIVE NOT RUN: no key yet)"
    else:
        mode = "FIXTURE (LIVE NOT RUN: --live not given)"
    ft = FixtureTransport()
    c = SarvamClient(api_key="fixture-no-network", transport=ft, max_calls=caps, live=False)
    return c, ft, mode


# ---------------------------------------------------------------------------------------------
# intent
# ---------------------------------------------------------------------------------------------
def _run_stack(stack: IntentStack, rows: List[dict]) -> Dict[str, Any]:
    preds, tiers, lat, safety_routed = [], [], [], []
    for r in rows:
        t0 = time.perf_counter()
        res = stack.classify(r["text"], TODAY)
        lat.append((time.perf_counter() - t0) * 1000)
        preds.append(res.label)
        tiers.append(res.tier_used)
        # a prism-only STOP comes back as UNKNOWN + stop_unconfirmed + needs_human: routed to a person
        safety_routed.append(res.label if res.label in ("STOP", "HARDSHIP") else
                             ("STOP" if "stop_unconfirmed" in res.safety_flags else res.label))
    return {"preds": preds, "tiers": tiers, "lat": lat, "safety_routed": safety_routed}


def _metrics(rows: List[dict], out: Dict[str, Any]) -> Dict[str, Any]:
    gold = [r["label"] for r in rows]
    m = prf(gold, out["preds"], list(LABELS))
    sc = safety_confusion(gold, out["preds"])
    routed = safety_confusion(gold, out["safety_routed"])
    n = len(rows)
    return {"n": n, "accuracy": m["accuracy"], "macro_f1": m["macro_f1"],
            "stop_recall": sc["STOP"]["recall"], "hardship_recall": sc["HARDSHIP"]["recall"],
            "stop_routed_to_safety_recall": routed["STOP"]["recall"],
            "abstain_rate": round(out["tiers"].count("abstain") / n, 4),
            "tiers": dict(Counter(out["tiers"])),
            "latency_ms": {"p50": percentile(out["lat"], 0.5), "p95": percentile(out["lat"], 0.95)},
            "errors": [{"text": r["text"], "gold": r["label"], "pred": p} for r, p in zip(rows, out["preds"])
                       if p != r["label"]][:40]}


def eval_intent(client: SarvamClient) -> Dict[str, Any]:
    sets = {"gold": load_gold(), "heldout": load_gold(DATA / "intent_heldout.jsonl")}
    sets["combined"] = sets["gold"] + sets["heldout"]
    out: Dict[str, Any] = {}
    for name, rows in sets.items():
        b_run = _run_stack(IntentStack(), rows)
        base = _metrics(rows, b_run)
        calls0 = sum(client.stats.calls.values())
        p_run = _run_stack(IntentStack(prism=PrismClassifier(client)), rows)
        withp = _metrics(rows, p_run)
        withp["prism_network_calls"] = sum(client.stats.calls.values()) - calls0
        gold = [r["label"] for r in rows]
        new_err = [{"text": r["text"], "gold": g, "was": b, "now": p} for r, g, b, p
                   in zip(rows, gold, b_run["preds"], p_run["preds"]) if b == g and p != g]
        fixed = [{"text": r["text"], "gold": g, "was": b} for r, g, b, p
                 in zip(rows, gold, b_run["preds"], p_run["preds"]) if b != g and p == g]
        withp["answered_by_prism"] = p_run["tiers"].count("prism")
        out[name] = {"current_stack": base, "stack_plus_prism": withp, "new_errors": new_err, "fixed": fixed,
                     "delta_accuracy_pts": round(100 * (withp["accuracy"] - base["accuracy"]), 2),
                     "safety_recall_100": all(x[k] in (None, 1.0) for x in (base, withp)
                                              for k in ("stop_recall", "hardship_recall"))}
    c = out["combined"]
    # Decision rule, fixed BEFORE the live re-run (coordinator, 2026-11-19): the prism intent tier stays on by
    # default only if combined gold + held-out accuracy rises AND it adds no new error.
    rises = c["stack_plus_prism"]["accuracy"] > c["current_stack"]["accuracy"]
    out["decision"] = {"rule": "default ON only if combined accuracy rises and new_errors == 0",
                       "combined_accuracy_rises": rises, "new_errors": len(c["new_errors"]),
                       "prism_intent_tier_default": "ON" if rises and not c["new_errors"] else "OFF"}
    return out


# ---------------------------------------------------------------------------------------------
# voice
# ---------------------------------------------------------------------------------------------
def eval_voice(client: SarvamClient) -> Dict[str, Any]:
    rows = load_gold(DATA / "voice_lowconf_summaries.jsonl")
    pc = PrismClassifier(client)
    usable = correct = upgrades = wrong_upgrades = reviews = review_on_wrong_bot = 0
    lowered = changed_disp = 0
    detail = []
    for r in rows:
        a = pc.classify_call(mask_for_model(r["call_summary"]), r["bot_disposition"])
        va = voice_rule(r["bot_disposition"], r["bot_confidence"], a)
        if a.label is not None and a.error is None:
            usable += 1
            correct += a.label == r["true_disposition"]
        if va.action == "UPGRADE":
            upgrades += 1
            wrong_upgrades += r["bot_disposition"] != r["true_disposition"]
            lowered += va.new_confidence < r["bot_confidence"]
        if va.action == "REVIEW":
            reviews += 1
            review_on_wrong_bot += r["bot_disposition"] != r["true_disposition"]
        detail.append({"id": r["id"], "true": r["true_disposition"], "bot": r["bot_disposition"],
                       "prism": a.label, "prism_conf": a.confidence, "action": va.action, "reason": va.reason})
    wrong_bot = sum(1 for r in rows if r["bot_disposition"] != r["true_disposition"])
    return {"n": len(rows), "prism_usable_answers": usable,
            "prism_reclassification_accuracy": round(correct / usable, 4) if usable else None,
            "upgrades": upgrades, "wrong_upgrades": wrong_upgrades, "reviews": reviews,
            "bot_wrong_cases": wrong_bot, "bot_wrong_cases_sent_to_review": review_on_wrong_bot,
            "confidence_lowered": lowered, "disposition_changed_by_model": changed_disp, "detail": detail}


# ---------------------------------------------------------------------------------------------
# CM-03
# ---------------------------------------------------------------------------------------------
def eval_cm03(client: SarvamClient, n: int, seed: int) -> Dict[str, Any]:
    from kairos_backend.cm03.copy import LENDER
    from kairos_backend.cm03.evaluation import build_world, run_sequential, score

    def bars(seq):
        return {"resolved_without_human_>=70%": seq["resolution_rate"] >= 0.70,
                "every_seeded_fake_caught": seq["fakes"]["caught"] == seq["fakes"]["seeded"],
                "zero_disclosure_before_otp": seq["disclosure_before_verification"] == 0,
                "first_reply_<30s": seq["first_reply_ms"]["over_30s"] == 0}

    res = {}
    w = build_world(n, seed)
    off = score(w, run_sequential(w))
    ph = Phraser(client, lender=LENDER)
    w2 = build_world(n, seed, engine_kwargs={"phraser": ph})
    on = score(w2, run_sequential(w2))
    for name, s in (("off", off), ("on", on)):
        res[name] = {"resolution_rate": s["resolution_rate"], "fakes": s["fakes"],
                     "disclosure_before_verification": s["disclosure_before_verification"],
                     "cross_talk_violations": s["cross_talk_violations"], "first_reply_ms": s["first_reply_ms"],
                     "outcome_digest": s["outcome_digest"], "acceptance": bars(s),
                     "all_bars_pass": all(bars(s).values())}
    res["on"]["phrasing"] = ph.summary()
    res["outcomes_identical_on_vs_off"] = off["outcome_digest"] == on["outcome_digest"]
    res["note"] = ("Sequential pass only; the 500-concurrency passes: scripts/run_cm03_eval.py --phrasing "
                   "{off,fixture,live}.")
    return res


# ---------------------------------------------------------------------------------------------
# summaries
# ---------------------------------------------------------------------------------------------
_NUM = re.compile(r"\d+(?:[.,]\d+)*")


def _recheck(n, facts_text: str, required: List[str]) -> List[str]:
    """Independent re-check of an ACCEPTED narrative (not the narrator's own function)."""
    probs = []
    if pii.scrub(n.text) != n.text or re.search(r"@\w", n.text):
        probs.append("pii")
    nums = {x.replace(",", "") for x in _NUM.findall(n.text)} - {x.replace(",", "") for x in _NUM.findall(facts_text)}
    if nums:
        probs.append(f"numbers_not_in_facts:{sorted(nums)}")
    for r in required:
        if r and r.lower() not in n.text.lower():
            probs.append(f"missing:{r}")
    if not n.label.startswith("AI-generated"):
        probs.append("not_labelled")
    return probs


def eval_summaries(client: SarvamClient) -> Dict[str, Any]:
    from replay.journey import Journey
    from kairos_backend import reports as k26
    from kairos_backend.cm03.evaluation import build_world, run_sequential

    nar = Narrator(client)
    samples = []

    def add(kind, n, facts_text, required):
        samples.append({"kind": kind, "accepted": n.ai_generated, "outcome": n.reason,
                        "recheck_problems": _recheck(n, facts_text, required) if n.ai_generated else [],
                        "text": n.text[:300]})

    # K26 daily ops + explain-decision on the journey ledger (kept: db_path in our own temp dir)
    with tempfile.TemporaryDirectory() as td:
        from datetime import timedelta

        from kairos_backend.ledger import Ledger
        from kairos_backend.policy import load_policy
        from replay.journey import CAMPAIGN, D0
        dbp = str(Path(td) / "journey.db")
        Journey(seed=42, quick=True, out_dir=None, echo=False, cohort_size=4, db_path=dbp).run()
        lg = Ledger(load_policy(), dbp)
        for k in range(6):
            rep = k26.daily_segment_report(lg, CAMPAIGN, D0 + timedelta(days=k))
            n = nar.daily_ops(rep)
            add("daily_ops", n, json.dumps(rep, default=str), [CAMPAIGN])
        audit = [dict(r) for r in lg._conn().execute(
            "SELECT * FROM audit WHERE decision IS NOT NULL ORDER BY id DESC LIMIT 400")]
        seen = set()
        for r in audit:
            key = (r["decision"], r["rule"])
            if key in seen or len(seen) >= 8:
                continue
            seen.add(key)
            rows = [x for x in audit if x["borrower_id"] == r["borrower_id"]][:12]
            rows = [r] + [x for x in rows if x is not r]
            n = nar.explain_decision(rows)
            add("explain_decision", n, json.dumps(rows, default=str), [x for x in (r["decision"], r["rule"]) if x])
        lg.close()
    # hand-off case summaries on a CM-03 world (hardship / dispute / fraud tickets)
    w = build_world(120, 20261120)
    run_sequential(w)
    c = w.ledger._conn()
    tickets = [dict(t) for t in c.execute("SELECT * FROM tickets ORDER BY ticket_id")]
    picked = []
    for kind in ("HARDSHIP", "DISPUTE", "FRAUD"):
        picked += [t for t in tickets if kind in (t["type"] or "")][:3]
    for t in picked:
        rows = [dict(r) for r in c.execute("SELECT * FROM audit WHERE borrower_id=? ORDER BY id DESC LIMIT 12",
                                           (t["borrower_id"],))]
        n = nar.case_summary(t, rows)
        add("case_summary", n, json.dumps({"t": t, "a": rows}, default=str), [t["ticket_id"], t["borrower_id"]])
    # K05 two-line summary on the crm/ journey, after each step
    crm = BACKEND.parent / "crm"
    sys.path.insert(0, str(crm))
    try:
        from kairos.db import connect
        from kairos.replay import STEPS, run_step
        from kairos.seed import seed
        from kairos.views import load_state, summarize
        with tempfile.TemporaryDirectory() as td:
            conn = connect(Path(td) / "k05.db")
            seed(conn)
            for i in range(len(STEPS)):
                run_step(conn, i)
                lines = summarize(load_state(conn, "B-1042"))
                n = nar.k05_summary(lines)
                ids = sorted({x.strip() for ln in lines for g in re.findall(r"\[([^\]]+)\]", ln) for x in g.split(",")})
                add("k05_summary", n, " ".join(lines), ids)
            conn.close()
    except Exception as e:  # crm/ is a separate package; report rather than fail
        samples.append({"kind": "k05_summary", "error": f"{type(e).__name__}: {e}"})
    by = {}
    for s in samples:
        b = by.setdefault(s["kind"], {"n": 0, "accepted": 0, "fell_back": Counter(), "recheck_failures": 0})
        b["n"] += 1
        if s.get("accepted"):
            b["accepted"] += 1
            b["recheck_failures"] += bool(s["recheck_problems"])
        elif "outcome" in s:
            b["fell_back"][s["outcome"]] += 1
    for b in by.values():
        b["fell_back"] = dict(b["fell_back"])
    return {"by_kind": by, "narrator_stats": nar.stats, "samples": samples[:30]}


# ---------------------------------------------------------------------------------------------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--live", action="store_true", help="real Sarvam calls when a key is configured")
    ap.add_argument("--record", action="store_true", help="with --live: save responses as fixtures")
    ap.add_argument("--max-prism", type=int, default=400)
    ap.add_argument("--max-llm", type=int, default=100)
    ap.add_argument("--cm03-n", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=20261120)
    ap.add_argument("--only", action="append", choices=["intent", "voice", "cm03", "summaries"])
    ap.add_argument("--out", default=str(BACKEND / "reports" / "models_eval.json"))
    a = ap.parse_args(argv)
    client, transport, mode = make_client(a.live, a.record, a.max_prism, a.max_llm)
    if client is None:
        print(mode)
        return 1
    only = set(a.only or ["intent", "voice", "cm03", "summaries"])
    t0 = time.perf_counter()
    rep: Dict[str, Any] = {"mode": mode, "environment": "SIMULATED (synthetic / hand-written data only)",
                           "caps": {"prism": a.max_prism, "llm": a.max_llm},
                           "note": ("FIXTURE answers come from recorded live responses when present, else a "
                                    "deterministic STAND-IN (Naive Bayes for prism, echo for phrasing, fact "
                                    "sentences for deepseek, with deliberately bad outputs injected). STAND-IN "
                                    "numbers test the plumbing and the guardrails, NOT the models.")
                           if not mode.startswith("LIVE") else "Live Sarvam responses."}
    if "intent" in only:
        rep["intent"] = eval_intent(client)
    if "voice" in only:
        rep["voice"] = eval_voice(client)
    if "cm03" in only:
        rep["cm03"] = eval_cm03(client, a.cm03_n, a.seed)
    if "summaries" in only:
        rep["summaries"] = eval_summaries(client)
    rep["client"] = client.stats.as_dict()
    if isinstance(transport, FixtureTransport):
        inj = Counter((x["kind"], x["injected_bad"]) for x in transport.log)
        rep["fixture"] = {"replayed_recorded": transport.replayed,
                          "standin_answers": {f"{k}{'_injected_bad' if b else ''}": v for (k, b), v in inj.items()}}
        if transport.replayed and not transport.log:
            rep["mode"] = mode = mode.replace("FIXTURE", "REPLAY of recorded live responses (latency not measured)")
            rep["note"] = "Every model answer replayed from tests/data/sarvam_recorded.json (recorded live)."
    if isinstance(transport, RecordingTransport):
        rep["recorded_fixtures"] = transport.save()
    rep["elapsed_s"] = round(time.perf_counter() - t0, 1)
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=2, ensure_ascii=False, default=str)

    print(f"Sarvam models eval · {rep['mode']}")
    for name in ("gold", "heldout", "combined"):
        if "intent" in rep:
            x = rep["intent"][name]
            b, p = x["current_stack"], x["stack_plus_prism"]
            print(f"  intent {name:8s} n={b['n']:3d}  acc {b['accuracy']:.3f} -> {p['accuracy']:.3f}  "
                  f"macroF1 {b['macro_f1']:.3f} -> {p['macro_f1']:.3f}  abstain {b['abstain_rate']:.3f} -> "
                  f"{p['abstain_rate']:.3f}  STOP/HARDSHIP recall {b['stop_recall']}/{b['hardship_recall']} -> "
                  f"{p['stop_recall']}/{p['hardship_recall']}  answered by prism {p['answered_by_prism']}  "
                  f"new errors {len(x['new_errors'])} fixed {len(x['fixed'])}  latency p50 {p['latency_ms']['p50']} ms")
    if "intent" in rep:
        print(f"  intent decision: {rep['intent']['decision']}")
    if "voice" in rep:
        v = rep["voice"]
        print(f"  voice  n={v['n']} prism acc {v['prism_reclassification_accuracy']} upgrades {v['upgrades']} "
              f"(wrong {v['wrong_upgrades']}) reviews {v['reviews']} (bot wrong {v['bot_wrong_cases']}, sent to review "
              f"{v['bot_wrong_cases_sent_to_review']}) confidence lowered {v['confidence_lowered']}")
    if "cm03" in rep:
        c = rep["cm03"]
        ph = c["on"]["phrasing"]
        print(f"  cm03   OFF bars {'PASS' if c['off']['all_bars_pass'] else 'FAIL'} · ON bars "
              f"{'PASS' if c['on']['all_bars_pass'] else 'FAIL'} · disclosures {c['off']['disclosure_before_verification']}"
              f"/{c['on']['disclosure_before_verification']} · outcomes identical {c['outcomes_identical_on_vs_off']} · "
              f"model replies {ph['model_answers']}, rejected {ph['rejected_by_validator']} "
              f"({ph['validator_rejection_rate']}) {ph['rejected_by_reason']}; distinct requests "
              f"{ph['unique_model_answers']}, rejected {ph['unique_rejected']} ({ph['unique_rejection_rate']}), "
              f"retries {ph['corrective_retries']} accepted after retry {ph['accepted_after_retry']}")
    if "summaries" in rep:
        for k, b in rep["summaries"]["by_kind"].items():
            print(f"  summary {k:16s} n={b['n']} accepted {b['accepted']} fell back {b['fell_back']} "
                  f"recheck failures {b['recheck_failures']}")
    if "fixture" in rep:
        print(f"  fixture: {rep['fixture']}")
    print(f"  client: {json.dumps(rep['client'])}")
    print(f"  report: {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
