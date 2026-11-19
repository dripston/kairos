"""K09 RL v2 sweep harness: CN10 baseline cached per seed, K09 arm only per config, seeds in parallel.
SIMULATED. Final numbers are re-checked with scripts/run_k09_replay.py. Run from backend/:

    python3 scripts/rl2_sweep.py --seeds 7 42 99 2026 --config '{"policy": "rl2", "rl2": {"pacing": "dual"}}'
    python3 scripts/rl2_sweep.py --seeds 7 --config '{"policy": "default"}'
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from replay import k09  # noqa: E402
from replay.portfolio import portfolio_from_csv  # noqa: E402

CACHE = os.environ.get("RL2_CACHE", os.path.join(tempfile.gettempdir(), "rl2-cn10-cache"))


def summary(run) -> dict:
    r = run.report()
    return {"replies": r["meaningful_replies"], "spend_inr": r["spend_inr"], "rpk": r["replies_per_1000_inr"],
            "payments": r["verified_payments"], "recovered_inr": r["recovered_paise"] / 100,
            "recovered_per_inr": round(r["recovered_paise"] / r["spend_paise"], 2) if r["spend_paise"] else 0,
            "stops": r["opt_outs"], "cap_breaches": r["cap_breaches_k01"],
            "k15_violations": sum(r["k15_safety_violations"].values()), "dps": r["decisions_per_second"]}


def setup(seed, n, days, workdir):
    csv = k09.write_replay_dataset(os.path.join(workdir, f"syn_{n}_{seed}.csv"), n, seed, k09.START)
    return csv, portfolio_from_csv(csv), k09.load_loans(csv)


def cn10(seed, n, days, drift):
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, f"cn10_{seed}_{n}_{days}_{drift}.json")
    if os.path.exists(path):
        return json.load(open(path))
    wd = tempfile.mkdtemp(prefix="rl2-cn10-")
    csv, pf, loans = setup(seed, n, days, wd)
    cal = k09.StackRun("cn10", csv, seed, days, os.path.join(wd, "cal.db"), max(k09.COST_PAISE.values()) * days,
                       drift, explore=False, portfolio=pf, loans=loans)
    cal.run()
    per_case = max(1, math.ceil(cal.spend_paise / n))
    run = k09.StackRun("cn10", csv, seed, days, os.path.join(wd, "cn10.db"), per_case, drift, explore=False,
                       portfolio=pf, loans=loans)
    run.run()
    out = {"per_case": per_case, "pool_inr": per_case * n / 100, **summary(run)}
    json.dump(out, open(path, "w"))
    return out


def kairos(seed, n, days, drift, config) -> dict:
    base = cn10(seed, n, days, drift)
    wd = tempfile.mkdtemp(prefix="rl2-k09-")
    csv, pf, loans = setup(seed, n, days, wd)
    warm = None
    if config.get("warm_from") is not None:   # cohort A (another seed) first, export, then this cohort
        a_seed = config["warm_from"]
        a_base = cn10(a_seed, n, days, drift)
        a_csv, a_pf, a_loans = setup(a_seed, n, days, wd)
        a = k09.StackRun("kairos", a_csv, a_seed, days, os.path.join(wd, "a.db"), a_base["per_case"], drift,
                         explore=False, portfolio=a_pf, loans=a_loans, policy="rl2", rl2_opts=config.get("rl2"))
        a.run()
        from kairos_backend.rl.warmstart import export_posteriors
        warm = json.loads(json.dumps(export_posteriors(a.rl2.model, **config.get("warm_caps", {}))))
        a.ledger.close()
    run = k09.StackRun("kairos", csv, seed, days, os.path.join(wd, "k.db"), base["per_case"], drift, explore=False,
                       portfolio=pf, loans=loans, kairos_opts=config.get("kairos"),
                       policy=config.get("policy", "default"), rl2_opts=config.get("rl2"), warm_start=warm)
    run.run()
    out = summary(run)
    out["spend_vs_pool"] = round(out["spend_inr"] / base["pool_inr"], 3)
    out["vs_cn10"] = round((out["rpk"] / base["rpk"] - 1) * 100, 1)
    out["cn10"] = base
    if run.rl2 is not None:
        rep = run.rl2.report()
        out["rl2_stats"] = rep["stats"]
        out["rl2_bps"] = rep["borrowers_evaluated_per_second"]
        if config.get("keep_report"):
            out["rl2_report"] = rep
    run.ledger.close()
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--seeds", type=int, nargs="+", default=[7, 42, 99, 2026])
    p.add_argument("--borrowers", type=int, default=1500)
    p.add_argument("--days", type=int, default=14)
    p.add_argument("--drift-day", type=int, default=7)
    p.add_argument("--config", default="{}")
    p.add_argument("--label", default="")
    p.add_argument("--jobs", type=int, default=4)
    p.add_argument("--out", default=None)
    a = p.parse_args()
    cfg = json.loads(a.config)
    drift = a.drift_day - 1
    with ProcessPoolExecutor(a.jobs) as ex:
        res = list(ex.map(kairos, a.seeds, [a.borrowers] * len(a.seeds), [a.days] * len(a.seeds),
                          [drift] * len(a.seeds), [cfg] * len(a.seeds)))
    chg = [r["vs_cn10"] for r in res]
    line = (f"{a.label or a.config:60} mean {sum(chg) / len(chg):+.1f} min {min(chg):+.1f} | " +
            " ".join(f"{s}:{r['vs_cn10']:+.1f}/R{r['replies']}/P{r['payments']}/S{r['stops']}/"
                     f"rec{r['recovered_per_inr']}/sp{r['spend_vs_pool']}" for s, r in zip(a.seeds, res)))
    print(line, flush=True)
    if a.out:
        with open(a.out, "a") as f:
            f.write(json.dumps({"label": a.label, "config": cfg, "seeds": a.seeds,
                                "results": [{k: v for k, v in r.items() if k != "rl2_report"} for r in res]}) + "\n")
    return res


if __name__ == "__main__":
    main()
