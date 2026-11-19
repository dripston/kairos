"""Runs one policy through a whole campaign and measures the funnel:
eligible -> attempted -> delivered/connected -> meaningful reply -> verified payment.
Funnel counts are UNIQUE borrowers, so 3 messages to one person count as 1 reach."""
import copy
import time
from collections import Counter

from replay.portfolio import ContactLog, generate_portfolio, is_eligible
from replay.safety import check_day
from replay.simulator import DevSimulator


def run_campaign(policy, n_borrowers, days, seed, portfolio=None):
    """portfolio: optional list of Borrowers (e.g. from the dataset). A fresh copy is used per run."""
    if portfolio is not None:
        portfolio = copy.deepcopy(portfolio)
        n_borrowers = len(portfolio)
    else:
        portfolio = generate_portfolio(n_borrowers, seed)
    simulator = DevSimulator(portfolio, seed)
    log = ContactLog()

    eligible = {b.index for b in portfolio if is_eligible(b)}
    attempted, delivered, replied, paid = set(), set(), set(), set()
    contacts = spend = replies = payments = opt_outs = fake_claims = 0
    violations = Counter()
    violation_examples = []
    plan_seconds, daily = [], []

    for day in range(days):
        start = time.perf_counter()
        decisions = policy.plan_day(day, portfolio, log)
        plan_seconds.append(time.perf_counter() - start)

        day_violations = check_day(day, decisions, portfolio, log)
        for rule, borrower_id, detail in day_violations:
            violations[rule] += 1
            if len(violation_examples) < 5:
                violation_examples.append({"day": day, "rule": rule, "borrower": borrower_id, "detail": detail})

        outcomes = simulator.run_day(day, decisions, portfolio, log)
        for d in decisions:
            log.record(d.borrower_index, day)

        day_spend = day_replies = day_payments = day_opt_outs = 0
        for o in outcomes:
            attempted.add(o.borrower_index)
            day_spend += o.cost_paise
            if o.delivered:
                delivered.add(o.borrower_index)
            if o.meaningful_reply:
                replied.add(o.borrower_index)
                day_replies += 1
            if o.opted_out:
                portfolio[o.borrower_index].opted_out = True   # must be honoured from the next day
                day_opt_outs += 1
            if o.payment_claimed and not o.payment_verified:
                fake_claims += 1
            if o.payment_verified:
                portfolio[o.borrower_index].paid = True
                paid.add(o.borrower_index)
                day_payments += 1
        policy.observe(day, outcomes, portfolio)

        contacts += len(decisions)
        spend += day_spend
        replies += day_replies
        payments += day_payments
        opt_outs += day_opt_outs
        daily.append({"day": day + 1, "contacts": len(decisions), "spend_paise": day_spend,
                      "meaningful_replies": day_replies, "verified_payments": day_payments,
                      "opt_outs": day_opt_outs, "plan_seconds": round(plan_seconds[-1], 3)})

    spend_rupees = spend / 100
    return {
        "policy": policy.name,
        "label": policy.label,
        "simulator": simulator.label,
        "borrowers": n_borrowers,
        "days": days,
        "seed": seed,
        "funnel": {
            "eligible": len(eligible),
            "attempted": len(attempted),
            "delivered_or_connected": len(delivered),
            "meaningful_reply": len(replied),
            "verified_payment": len(paid),
        },
        "contacts": contacts,
        "spend_paise": spend,
        "meaningful_replies": replies,
        "verified_payments": payments,
        "unverified_payment_claims": fake_claims,
        "opt_outs": opt_outs,
        "replies_per_1000_rupees": round(replies / spend_rupees * 1000, 2) if spend else 0.0,
        "rupees_per_verified_payment": round(spend_rupees / payments, 2) if payments else None,
        "violations": dict(violations),
        "violation_examples": violation_examples,
        "planning_seconds_max": round(max(plan_seconds), 3),
        "planning_seconds_avg": round(sum(plan_seconds) / len(plan_seconds), 3),
        "daily": daily,
    }
