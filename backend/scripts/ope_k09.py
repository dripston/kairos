"""Off-policy evaluation of K09 targets from an explore-mode (Thompson) log, checked against ground truth.
Everything is SIMULATED (K15 DEV simulator, synthetic portfolio, synthetic priors). From backend/:

    python scripts/ope_k09.py                 # 1,500 borrowers, 10 days, seed 42 -> reports/ope_k09.json
    python scripts/ope_k09.py --quick         # 600 borrowers, 7 days, 200 bootstrap resamples (film / CI)
    python scripts/ope_k09.py --seed 7 --draws 2000

1. LOG. The real BetaBernoulliLearner (temp ledger DB, SIMULATOR priors) picks one arm (channel x slot)
   or WAIT per eligible borrower per day by seeded Thompson sampling, and logs the propensity of the
   chosen arm (Monte Carlo over posterior draws, learner.thompson_propensities), mixed with a small
   uniform share (--epsilon 0.05, as Duolingo's 5% holdout) so that fixed schedules using arms Thompson
   almost never tries (IVR) still have overlap. Outcomes come from the
   K15 DevSimulator (same hidden borrower traits and seeded draws as every replay); the learner updates
   once a day, like production's daily batch. Spacing / 7-day cap / opt-outs as in replay/campaign.py.
2. ESTIMATE, from the log alone, the per-decision value (meaningful replies per decision, and replies
   per Rs 1,000) of target policies on the same contexts: IPS, SNIPS, direct method (q_hat = the
   learner's posterior mean at decision time) and doubly robust, with bootstrap 95% CIs.
   Targets: greedy K09 (posterior-mean ranking = PRODUCTION), the CN10 fixed calendar (stand-in cohort
   per borrower, morning slot), "always WhatsApp 17-19" (sanity), and the logger itself (weights == 1).
3. GROUND TRUTH, never shown to the estimators: in every logged context the simulator is run for the
   target's arm with the SAME seeded draws (common random numbers: exactly what would have happened),
   plus the exact expected reply probability from the simulator's formula.

Caveats: the value is per decision on the logged contexts (the contexts a target would itself create,
by contacting different people on different days, are not modelled); propensities are Monte Carlo
estimates (draws per decision, smoothed); the reward is a 0/1 meaningful reply within the day.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import tempfile
import time
from datetime import date, datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from kairos_backend.contracts import Candidate, Mode, Outcome, replies_per_rupee  # noqa: E402
from kairos_backend.learner import WAIT_ACTION, BetaBernoulliLearner, arm_key  # noqa: E402
from kairos_backend.ledger import Ledger  # noqa: E402
from kairos_backend.ope import WAIT, estimate  # noqa: E402
from kairos_backend.policy import load_policy  # noqa: E402
from kairos_backend.safeio import atomic_write_text  # noqa: E402
from replay import cn10  # noqa: E402
from replay import settings as S  # noqa: E402
from replay.policies import Decision  # noqa: E402
from replay.portfolio import ContactLog, generate_portfolio, is_eligible  # noqa: E402
from replay.simulator import BAND_EFFECT, DELIVERY_RATE, DevSimulator  # noqa: E402

assert WAIT == WAIT_ACTION
START = date(2026, 11, 23)
# K15 simulator slot <-> contract slot (as replay/k09.SLOT_MAP)
SLOTS = {"08-11": ("morning", 9), "14-17": ("afternoon", 14), "17-19": ("evening", 17)}
COHORTS = ("C1", "C2", "C3", "C4", "C5")


def q_true(sim: DevSimulator, b, ch: str, k15_slot: str, recent: int) -> float:
    """P(meaningful reply) for one touch, from DevSimulator.run_day's formula (one touch per day)."""
    i = b.index
    p = (sim.responsiveness[i] * sim.affinity[i][ch] * BAND_EFFECT[b.dpd_band]
         * (1.3 if k15_slot == sim.best_slot[i] else 0.8) * 0.8 ** recent)
    return DELIVERY_RATE[ch] * min(p, 0.95) * (1 - min(1.0, 0.04 * (1 + recent)))


def run_log(n_borrowers: int, days: int, seed: int, draws: int, db_dir: str, epsilon: float = 0.0):
    portfolio = generate_portfolio(n_borrowers, seed)
    sim = DevSimulator(portfolio, seed)
    log = ContactLog()
    ledger = Ledger(load_policy(), os.path.join(db_dir, "ope.db"))
    learner = BetaBernoulliLearner(ledger, propensity_draws=draws)
    rows, greedy_mismatch, consistency_errors = [], 0, 0
    for day in range(days):
        d = START + timedelta(days=day)
        decisions, meta = [], []
        for b in portfolio:
            if not is_eligible(b):
                continue
            recent = log.previous_7_days(b.index, day)
            last = log.last_day(b.index)
            if recent + 1 > S.MAX_CONTACTS_PER_7_DAYS or (last is not None and day - last < S.MIN_DAYS_BETWEEN_CONTACTS):
                continue
            cands = [Candidate(ch, slot, datetime.combine(d, datetime.min.time()).replace(hour=h), None, S.COST_PAISE[ch])
                     for ch in sorted(b.consents) for slot, (_, h) in SLOTS.items()]
            at = datetime.combine(d, datetime.min.time()).replace(hour=8)
            r = learner.rank(b.dpd_band, cands, Mode.SIMULATOR, rng=random.Random(f"{seed}:{day}:{b.index}"),
                             borrower_id=b.borrower_id, persist=False, at=at)
            g = learner.rank(b.dpd_band, cands, Mode.SIMULATOR, rng=None, borrower_id=b.borrower_id, persist=False, at=at)
            chosen = arm_key(r.chosen) if r.chosen is not None else WAIT
            probs = dict(r.arm_probs)
            arm = r.chosen
            if epsilon > 0 and r.scores:
                # epsilon-uniform holdout over the contact arms (Yancey & Settles 2020, A.4): logger only
                keys = [arm_key(s) for s in r.scores]
                probs = {k: (1 - epsilon) * p + (epsilon / len(keys) if k != WAIT else 0.0) for k, p in probs.items()}
                u = random.Random(f"eps:{seed}:{day}:{b.index}")
                if u.random() < epsilon:
                    arm = r.scores[u.randrange(len(r.scores))]
                    chosen = arm_key(arm)
            row = {"day": day, "borrower": b.index, "band": b.dpd_band, "dpd": b.dpd, "consents": sorted(b.consents),
                   "actions": [arm_key(s) for s in r.scores],
                   "post": {arm_key(s): [round(s.alpha, 6), round(s.beta, 6)] for s in r.scores},
                   "q_hat": {arm_key(s): s.posterior_mean for s in r.scores},
                   "cost": {arm_key(s): s.est_cost_paise for s in r.scores},
                   "qualifies_mean": {arm_key(s): s.qualifies for s in g.scores},
                   "chosen": chosen, "propensity": probs[chosen], "arm_probs": probs,
                   "min_reply_prob": learner.min_reply_prob}
            if target_greedy(row) != {(arm_key(g.chosen) if g.chosen is not None else WAIT): 1.0}:
                greedy_mismatch += 1
            # ground truth for every action in this context (hidden from the estimators)
            oracle = {WAIT: (0.0, 0)}
            for s in r.scores:
                k15 = SLOTS[s.slot][0]
                out = sim.run_day(day, [Decision(b.index, s.channel, k15, S.TEMPLATES[s.channel][0])], portfolio, log)[0]
                oracle[arm_key(s)] = (q_true(sim, b, s.channel, k15, recent), int(out.meaningful_reply))
            row["_oracle"] = oracle
            rows.append(row)
            if arm is not None:
                decisions.append(Decision(b.index, arm.channel, SLOTS[arm.slot][0], S.TEMPLATES[arm.channel][0]))
                meta.append((row, arm, b))
            else:
                row["reward"] = 0
        outcomes = sim.run_day(day, decisions, portfolio, log)
        for dec in decisions:
            log.record(dec.borrower_index, day)
        for o, (row, arm, b) in zip(outcomes, meta):
            row["reward"] = int(o.meaningful_reply)
            consistency_errors += row["reward"] != row["_oracle"][row["chosen"]][1]
            if o.opted_out:
                b.opted_out = True
            if o.payment_verified:
                b.paid = True
            ex = datetime.combine(d, datetime.min.time()).replace(hour=SLOTS[arm.slot][1])
            learner.apply_outcome(Outcome(f"OPE-{seed}-{day}-{b.index}", b.borrower_id, b.dpd_band, arm.channel,
                                          arm.slot, ex, ex + timedelta(hours=24),
                                          "REPLY" if o.meaningful_reply else "NO_REPLY"))
    ledger.close()
    return rows, greedy_mismatch, consistency_errors


# ---------- target policies (each sees only logged fields) ----------
def target_greedy(row):
    """K09 PRODUCTION rule: highest posterior-mean replies per rupee among qualifying arms, else WAIT."""
    best, best_v = WAIT, None
    for k in row["actions"]:
        a, b = row["post"][k]
        m, c = a / (a + b), row["cost"][k]
        if not row["qualifies_mean"].get(k, False):
            continue
        v = replies_per_rupee(m, c)
        if best_v is None or v > best_v:
            best, best_v = k, v
    return {best: 1.0}


def target_cn10(row):
    """CN10 calendar on the borrower's cycle day (DPD advances with the campaign day), first scheduled channel
    the borrower consented to, morning slot; WAIT on unscheduled days. Cohort = stand-in (borrower % 5):
    the K15 portfolio has no risk tag / bounce string."""
    cohort = COHORTS[row["borrower"] % len(COHORTS)]
    for ch, _, _ in cn10.steps_for(cohort, row["dpd"] + row["day"], "en"):
        k = f"{ch}|08-11"
        if k in row["actions"]:
            return {k: 1.0}
    return {WAIT: 1.0}


def target_whatsapp(row):
    k = "whatsapp|17-19"
    return {k: 1.0} if k in row["actions"] else {WAIT: 1.0}


def target_logger(row):
    return dict(row["arm_probs"])


TARGETS = {
    "greedy_k09": ("Greedy K09 (posterior-mean ranking, as PRODUCTION)", target_greedy),
    "cn10_fixed": ("CN10 fixed calendar (stand-in cohort, morning slot)", target_cn10),
    "always_whatsapp": ("Always WhatsApp 17-19 (sanity check)", target_whatsapp),
    "logger_on_policy": ("The Thompson logger itself (weights == 1: plumbing check)", target_logger),
}


def truth(rows, target):
    exp = real = cost = 0.0
    for row in rows:
        for k, p in target(row).items():
            q, r = row["_oracle"][k]
            exp += p * q
            real += p * r
            cost += p * row["cost"].get(k, 0) if k != WAIT else 0.0
    n = len(rows)
    per = lambda v: round(v / n / (cost / n / 100) * 1000, 3) if cost else 0.0  # noqa: E731
    return {"expected": round(exp / n, 5), "realized_same_draws": round(real / n, 5),
            "replies_per_1000_inr_expected": per(exp), "replies_per_1000_inr_realized": per(real)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--borrowers", type=int, default=1500)
    ap.add_argument("--days", type=int, default=10)
    ap.add_argument("--seed", type=int, default=S.SEED)
    ap.add_argument("--draws", type=int, default=1000, help="posterior draws per propensity estimate")
    ap.add_argument("--epsilon", type=float, default=0.05,
                    help="uniform-exploration share mixed into the Thompson logger (0 = pure Thompson)")
    ap.add_argument("--boot", type=int, default=500, help="bootstrap resamples")
    ap.add_argument("--quick", action="store_true", help="600 borrowers, 7 days, 200 resamples")
    ap.add_argument("--out", default=os.path.join(ROOT, "reports", "ope_k09.json"))
    a = ap.parse_args(argv)
    if a.quick:
        a.borrowers, a.days, a.boot = 600, 7, 200
    t0 = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="kairos-ope-") as tmp:
        rows, mismatch, inconsistent = run_log(a.borrowers, a.days, a.seed, a.draws, tmp, a.epsilon)
    t_log = time.perf_counter() - t0
    props = [r["propensity"] for r in rows]
    sums = [sum(r["arm_probs"].values()) for r in rows]
    results = {}
    for name, (label, fn) in TARGETS.items():
        results[name] = {"label": label, **estimate(rows, fn, n_boot=a.boot, seed=a.seed), "truth": truth(rows, fn)}
        tr = results[name]["truth"]["expected"]
        for k in ("ips", "snips", "dm", "dr"):
            lo, hi = results[name][k]["ci"]
            results[name][k]["truth_in_ci"] = lo <= tr <= hi
    report = {
        "evidence": "SIMULATED: K15 DEV simulator, synthetic portfolio, SYNTHETIC learner priors",
        "method": "IPS / SNIPS / DM / DR (Dudik, Langford & Li 2011, arXiv:1103.4601; Li et al. WSDM 2011, "
                  "arXiv:1003.5956) on a Thompson-sampling log with Monte Carlo propensities; percentile "
                  "bootstrap CIs; ground truth = same simulator, same seeded draws, target's arm",
        "params": {"borrowers": a.borrowers, "days": a.days, "seed": a.seed, "draws": a.draws, "boot": a.boot,
                   "epsilon": a.epsilon},
        "log": {"decisions": len(rows), "contacts": sum(r["chosen"] != WAIT for r in rows),
                "replies": sum(r["reward"] for r in rows),
                "propensity_min": round(min(props), 6), "propensity_max": round(max(props), 6),
                "arm_prob_sum_min": round(min(sums), 9), "arm_prob_sum_max": round(max(sums), 9),
                "greedy_target_mismatches_vs_learner": mismatch,
                "logged_reward_vs_oracle_mismatches": inconsistent,
                "log_seconds": round(t_log, 2)},
        "unit": "meaningful replies per decision (value) and per Rs 1,000 of the target's own expected spend",
        "targets": results,
        "caveats": ["per-decision value on the logged contexts; a target's own downstream contexts are not modelled",
                    "Monte Carlo propensities (smoothed); weights are capped by the smoothing, not clipped",
                    "simulator rewards; the CN10 cohort is a stand-in (borrower % 5)"],
        "runtime_seconds": None,
    }
    report["runtime_seconds"] = round(time.perf_counter() - t0, 2)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    atomic_write_text(a.out, json.dumps(report, indent=2) + "\n")

    lg = report["log"]
    print(f"OPE for K09   SIMULATED   seed {a.seed}, {a.borrowers:,} borrowers x {a.days} days, "
          f"{a.draws} draws/propensity, epsilon {a.epsilon}")
    print(f"Log: {lg['decisions']:,} Thompson decisions, {lg['contacts']:,} contacts, {lg['replies']:,} replies; "
          f"propensity in [{lg['propensity_min']}, {lg['propensity_max']}], arm probs sum to 1 "
          f"(min {lg['arm_prob_sum_min']}); consistency checks {lg['greedy_target_mismatches_vs_learner']}"
          f"/{lg['logged_reward_vs_oracle_mismatches']} mismatches")
    print(f"\n{'replies per 1,000 decisions':38}{'truth':>8}{'IPS':>22}{'SNIPS':>22}{'DR':>22}{'DM':>9}{'ESS':>8}")
    for name, r in results.items():
        cell = lambda k: f"{r[k]['value'] * 1000:6.1f} [{r[k]['ci'][0] * 1000:5.1f},{r[k]['ci'][1] * 1000:5.1f}]{'*' if r[k]['truth_in_ci'] else ' '}"  # noqa: E731,E501
        print(f"{r['label'][:37]:38}{r['truth']['expected'] * 1000:8.1f}{cell('ips'):>22}{cell('snips'):>22}"
              f"{cell('dr'):>22}{r['dm']['value'] * 1000:9.1f}{r['ess']:8.0f}")
    print("  * = ground truth inside the 95% CI.  Truth = simulator expectation for the target's arm in each logged context.")
    print(f"\n{'replies per Rs 1,000':38}{'truth':>8}{'SNIPS':>10}{'DR':>10}")
    for name, r in results.items():
        print(f"{r['label'][:37]:38}{r['truth']['replies_per_1000_inr_expected']:8.1f}"
              f"{r['snips']['replies_per_1000_inr']:10.1f}{r['dr']['replies_per_1000_inr']:10.1f}")
    print(f"\nWrote {a.out}  ({report['runtime_seconds']}s, of which logging {lg['log_seconds']}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
