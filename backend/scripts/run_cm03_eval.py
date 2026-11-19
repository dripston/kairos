"""CM-03 seeded evaluation (SIMULATED). From backend/:

    python scripts/run_cm03_eval.py                       # 1,000 conversations + 500-concurrency (engine and HTTP)
    python scripts/run_cm03_eval.py --n 300 --seed 7 --concurrency 500 --skip-http
    python scripts/run_cm03_eval.py --check-determinism   # run the sequential pass twice, compare outcome digests
    python scripts/run_cm03_eval.py --phrasing fixture    # sarvam-105b-conversations phrasing ON (offline stand-in)
    python scripts/run_cm03_eval.py --phrasing live       # ... real calls when a Sarvam key is set (capped)

Writes reports/cm03_eval.json (gitignored) and prints a PASS / FAIL line per acceptance bar.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kairos_backend.cm03 import PROVENANCE  # noqa: E402
from kairos_backend.cm03.evaluation import (FAKE_MIX, LEGIT_MIX, build_world, run_concurrent_engine,  # noqa: E402
                                            run_concurrent_http, run_sequential, score)


def git_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True,
                                       cwd=Path(__file__).resolve().parent).strip()
    except Exception:
        return "unknown"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=1000, help="conversations in the main (sequential) pass")
    ap.add_argument("--seed", type=int, default=20261120)
    ap.add_argument("--concurrency", type=int, default=500, help="conversations open at once in the load passes (0 = skip)")
    ap.add_argument("--workers", type=int, default=64, help="engine thread pool for the in-process load pass")
    ap.add_argument("--skip-http", action="store_true")
    ap.add_argument("--check-determinism", action="store_true")
    ap.add_argument("--phrasing", choices=["off", "fixture", "live"], default="off",
                    help="sarvam-105b-conversations reply phrasing (validator-checked; verbatim on failure)")
    ap.add_argument("--max-llm-calls", type=int, default=100, help="cap on live phrasing calls for this run")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[1] / "reports" / "cm03_eval.json"))
    a = ap.parse_args(argv)
    phraser, phrasing_mode = None, "off"
    if a.phrasing != "off":
        from kairos_backend.cm03.copy import LENDER
        from kairos_backend.sarvam.client import SarvamClient
        from kairos_backend.sarvam.config import load_api_key
        from kairos_backend.sarvam.fixtures import FixtureTransport
        from kairos_backend.sarvam.phrasing import Phraser
        key = load_api_key()[0] if a.phrasing == "live" else None
        if key:
            client, phrasing_mode = SarvamClient(api_key=key, max_calls={"llm": a.max_llm_calls}), "LIVE"
        else:
            client = SarvamClient(api_key="fixture-no-network", transport=FixtureTransport(), live=False)
            phrasing_mode = "FIXTURE / STAND-IN (LIVE NOT RUN" + (": no key yet)" if a.phrasing == "live" else ")")
        phraser = Phraser(client, lender=LENDER)
    ekw = {"phraser": phraser} if phraser else None
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    t0 = time.perf_counter()

    world = build_world(a.n, a.seed, engine_kwargs=ekw)
    seq = score(world, run_sequential(world))
    report = {"run": {"environment": PROVENANCE, "synthetic_data_only": True, "seed": a.seed, "n": a.n,
                      "git_commit": git_commit(), "started_at": started,
                      "policy_version": world.ledger.policy.version, "exceptions_policy_version": world.ledger.policy.exceptions_version,
                      "llm": "enabled (OPENAI_API_KEY set; ambiguous free text only)" if os.environ.get("OPENAI_API_KEY") else
                             "disabled (no key): rules only, ambiguous text -> clarify / hand-off",
                      "adapter": "WhatsApp dry-run (nothing sent)", "mix_percent": {**LEGIT_MIX, **FAKE_MIX},
                      "phrasing": phrasing_mode},
              "sequential": seq}

    if a.check_determinism:
        again = score(w2 := build_world(a.n, a.seed, engine_kwargs=ekw), run_sequential(w2))
        report["determinism"] = {"digest_1": seq["outcome_digest"], "digest_2": again["outcome_digest"],
                                 "identical": seq["outcome_digest"] == again["outcome_digest"]}

    conc = {}
    if a.concurrency:
        w = build_world(a.concurrency, a.seed + 1, engine_kwargs=ekw)
        res, stats = run_concurrent_engine(w, workers=a.workers)
        sc = score(w, res)
        # same world, same seed, played one by one -> outcomes must match the concurrent run
        w_ref = build_world(a.concurrency, a.seed + 1, engine_kwargs=ekw)
        ref = score(w_ref, run_sequential(w_ref))
        conc["engine"] = {**stats, "resolution_rate": sc["resolution_rate"], "fakes": sc["fakes"],
                          "disclosure_before_verification": sc["disclosure_before_verification"],
                          "cross_talk_violations": sc["cross_talk_violations"],
                          "outcomes_match_sequential": sc["outcome_digest"] == ref["outcome_digest"]}
        if not a.skip_http:
            w = build_world(a.concurrency, a.seed + 1, engine_kwargs=ekw)
            res, stats = run_concurrent_http(w)
            sc = score(w, res)
            conc["http"] = {**stats, "resolution_rate": sc["resolution_rate"], "fakes": sc["fakes"],
                            "disclosure_before_verification": sc["disclosure_before_verification"],
                            "cross_talk_violations": sc["cross_talk_violations"],
                            "outcomes_match_sequential": sc["outcome_digest"] == ref["outcome_digest"]}
    report["concurrency"] = conc

    def ok_conc(c):
        return (c and c["error_count"] == 0 and c["cross_talk_violations"] == 0 and c["first_reply_ms"]["over_30s"] == 0
                and c["outcomes_match_sequential"] and c["disclosure_before_verification"] == 0)

    bars = {
        "resolved_without_human_>=70%": {"value": seq["resolution_rate"], "pass": seq["resolution_rate"] >= 0.70},
        "every_seeded_fake_caught": {"value": f"{seq['fakes']['caught']}/{seq['fakes']['seeded']}",
                                     "pass": seq["fakes"]["caught"] == seq["fakes"]["seeded"]},
        "zero_disclosure_before_otp": {"value": seq["disclosure_before_verification"], "pass": seq["disclosure_before_verification"] == 0},
        "first_reply_<30s": {"value": seq["first_reply_ms"], "pass": seq["first_reply_ms"]["over_30s"] == 0},
        "500_concurrent_conversations": {"value": {k: {"n": v["concurrent_conversations"], "p95_ms": v["turn_latency_ms"]["p95"],
                                                       "errors": v["error_count"], "cross_talk": v["cross_talk_violations"]}
                                                   for k, v in conc.items()},
                                         "pass": bool(conc) and all(ok_conc(v) for v in conc.values())
                                         and all(v["concurrent_conversations"] >= 500 for v in conc.values())},
    }
    report["acceptance"] = bars
    if phraser is not None:
        report["phrasing"] = {"mode": phrasing_mode, **phraser.summary()}
    report["run"]["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    report["run"]["elapsed_s"] = round(time.perf_counter() - t0, 1)
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    print(f"CM-03 eval · {PROVENANCE} · seed {a.seed} · n={a.n} · commit {report['run']['git_commit']}")
    for k, v in bars.items():
        print(f"  {'PASS' if v['pass'] else 'FAIL'}  {k}: {json.dumps(v['value'], ensure_ascii=False)}")
    print(f"  resolution excl. fakes {seq['resolution_rate_excl_fakes']:.1%} · of bot-resolvable "
          f"{seq['resolution_rate_of_bot_resolvable']:.1%} · route accuracy {seq['route_accuracy']:.1%} · "
          f"false-positive fraud flags {seq['fakes']['false_positives_on_genuine']}")
    print(f"  hand-offs: {seq['handoff_reasons']}")
    if "determinism" in report:
        print(f"  determinism: {report['determinism']}")
    if phraser is not None:
        p = report["phrasing"]
        print(f"  phrasing {phrasing_mode}: model replies {p['model_answers']}, rejected by validator "
              f"{p['rejected_by_validator']} ({p['validator_rejection_rate']}); distinct answers "
              f"{p['unique_model_answers']}, rejected {p['unique_rejected']}; reasons {p['rejected_by_reason']}")
    print(f"  report: {a.out}")
    return 0 if all(v["pass"] for v in bars.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
