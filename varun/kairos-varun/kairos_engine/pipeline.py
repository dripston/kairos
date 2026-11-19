"""Runs the whole evidence pipeline once and fills each task folder's outputs/.

  runs/<run_id>/                     full logs (touch log, payments, decisions) - source of truth
  K15-safety-load-replay/outputs/    benchmark, funnel, audits, timings, 500-chat load report
  K23-cohort-review/outputs/         cohort review + rollback snapshots
  K26-analytics-metrics/outputs/     joined borrower activity + KPI tables
  K27-agent-dashboard/outputs/       dashboard.html + one traced recommendation
"""
import asyncio
import hashlib
import json
import os
import shutil
import time

from analytics import build as build_analytics
from audit import audit
from build_data import build, out_dir
from chat_load import run as run_chats
from cohort import review
from common import PACKAGE, file_sha, load_all, path, policy_sources
from dashboard import write_dashboard
from engine import load_portfolio, run_campaign
from policies import FixedCadence, Kairos
from reports import write_all

TASKS = {"K15": "K15-safety-load-replay", "K23": "K23-cohort-review",
         "K26": "K26-analytics-metrics", "K27": "K27-agent-dashboard"}


def task_out(task):
    p = os.path.join(PACKAGE, TASKS[task], "outputs")
    os.makedirs(p, exist_ok=True)
    return p


def sends_hash(run):
    return hashlib.sha256(run["sends"].to_csv(index=False).encode()).hexdigest()


def run_pipeline(seed=None, borrowers=None, log=print):
    cfg, k01, k21 = load_all()
    seed = cfg["public_seed"] if seed is None else seed
    if borrowers:
        cfg["n_borrowers"] = borrowers
    t0 = time.time()
    log(f"Kairos evidence pipeline · seed {seed} · {cfg['n_borrowers']:,} borrowers · K01 {k01['policy_version']} · K21 {k21['policy_version']}")
    srcs = policy_sources()
    for k, v in srcs.items():
        log(f"  policy {k:15} <- {v['source']}")

    gen = out_dir(seed)
    man = os.path.join(gen, "manifest.json")
    if not os.path.exists(man) or json.load(open(man))["borrowers"] != cfg["n_borrowers"]:
        build(cfg, seed)
    data, templates = load_portfolio(gen)
    raw = path("data", "raw", "PL_Dummy_Collections_Data_500.csv")

    runs, audits = {}, {}
    fixed = FixedCadence(cfg, data)
    kairos = Kairos(cfg, k21, data, seed)
    pre = kairos.snapshot()
    for pol in (fixed, kairos):
        log(f"▶ {pol.name}: {cfg['campaign_days']} days x {cfg['n_borrowers']:,} borrowers")
        runs[pol.name] = run_campaign(pol, cfg, k01, data, templates, seed, None, log=lambda *a: None)
        audits[pol.name] = audit(runs[pol.name], data, templates, cfg, k01, pol.name)
        log(f"  {len(runs[pol.name]['sends']):,} sends · audited with Ojas's K01 reference · breaches "
            f"{audits[pol.name]['k01_breaches'] + audits[pol.name]['other_breaches']}")
    post = kairos.snapshot()

    log("▶ determinism: re-running Kairos with the same seed")
    again = run_campaign(Kairos(cfg, k21, data, seed), cfg, k01, data, templates, seed, None, log=lambda *a: None)
    h1, h2 = sends_hash(runs["kairos"]), sends_hash(again)
    det = {"run_1_sha256": h1, "run_2_sha256": h2, "identical": h1 == h2}
    log(f"  {'IDENTICAL' if det['identical'] else 'DIFFERENT'}")

    log("▶ 500 concurrent chats through Ojas's K10 gate")
    chats = asyncio.run(run_chats(500, seed))
    log(f"  peak {chats['peak_concurrent_conversations']} concurrent · first response p95 "
        f"{chats['first_response_seconds']['p95']}s · violations {chats['safety_violations']}")

    cfg_hash = hashlib.sha256(json.dumps([cfg, k01, k21], sort_keys=True).encode()).hexdigest()[:8]
    run_id = f"RUN-s{seed}-n{cfg['n_borrowers']}-{cfg_hash}"
    rdir = os.path.join(PACKAGE, "runs", run_id)
    log(f"▶ writing evidence for {run_id}")
    sm = write_all(rdir, runs, audits, data, templates, cfg, k01, k21, seed, run_id, time.time() - t0, det)
    rev = review(runs, data, cfg, k01, pre, post, run_id, rdir, runs["kairos"]["totals"]["drift_day"])
    build_analytics(runs, data, run_id, cfg["run_mode"], seed, cfg["baseline_source"], rdir)
    json.dump(chats, open(os.path.join(rdir, "chat_load_report.json"), "w"), indent=2)

    manifest = {"run_id": run_id, "run_mode": cfg["run_mode"], "label": cfg["run_label"], "seed": seed,
                "borrowers": cfg["n_borrowers"], "baseline": cfg["baseline_source"],
                "campaign": sm["campaign"], "budget_pool_inr": sm["budget_pool_inr"],
                "policy_files": srcs, "config_hash": cfg_hash,
                "dataset": {"file": "PL_Dummy_Collections_Data_500.csv", "sha256_12": file_sha(raw),
                            "tracker_row": "Datasets: PL_Dummy_Collections_Data_500 (Dev fallback, verified Drive file)",
                            "official_datasets_used": False,
                            "official_pending": ["D1/D2/D4", "D10/D11/D13", "Campaign simulator + fixed-cadence baseline"]},
                "run_dir": os.path.relpath(rdir, PACKAGE), "elapsed_seconds": round(time.time() - t0, 1)}
    json.dump(manifest, open(os.path.join(rdir, "run_manifest.json"), "w"), indent=2)

    # ---------- copy each task's evidence into its folder ----------
    copies = {
        "K15": ["benchmark.md", "summary.json", "funnel.csv", "segment_daily.csv", "daily_totals.csv", "decisions_daily.csv",
                "timings.csv", "channel_mix_by_period.csv", "channel_mix_daily.csv", "learning_daily.csv",
                "audit_kairos.json", "audit_fixed_cadence.json", "chat_load_report.json"],
        "K23": ["cohort_review.md", "cohort_review.json", "priors_pre_cohort.json", "priors_post_cohort.json"],
        "K26": ["kpis.csv", "kpis_by_segment.csv", "borrower_activity.csv.gz"],
        "K27": [],
    }
    for task, files in copies.items():
        od = task_out(task)
        for f in os.listdir(od):
            os.remove(os.path.join(od, f))
        for f in files:
            shutil.copy(os.path.join(rdir, f), od)
        json.dump(manifest, open(os.path.join(od, "run_manifest.json"), "w"), indent=2)
    write_dashboard(task_out("K27"), runs, data, sm, k01, k21, chats=chats, review=rev, manifest=manifest)
    json.dump(manifest, open(os.path.join(PACKAGE, "runs", "latest.json"), "w"), indent=2)
    log(f"done in {time.time() - t0:.0f}s")
    return manifest
