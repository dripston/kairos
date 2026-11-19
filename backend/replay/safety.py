"""Safety checks that run on ANY policy's decisions, before they are executed.
A policy cannot mark its own homework: these rules are checked independently."""
from collections import Counter

from replay import settings as S


def check_day(day, decisions, portfolio, log):
    """Return a list of (rule, borrower_id, detail). Empty list = safe day."""
    violations = []
    per_borrower = Counter(d.borrower_index for d in decisions)
    seen = set()
    spend = 0
    for d in decisions:
        b = portfolio[d.borrower_index]
        spend += S.COST_PAISE.get(d.channel, 0)
        if d.channel not in b.consents:
            violations.append(("no_consent", b.borrower_id, d.channel))
        if b.opted_out:
            violations.append(("contacted_after_opt_out", b.borrower_id, d.channel))
        if b.paid:
            violations.append(("contacted_after_payment", b.borrower_id, d.channel))
        if d.template_id not in S.TEMPLATES.get(d.channel, ()):
            violations.append(("unregistered_template", b.borrower_id, d.template_id))
        if d.slot not in S.SLOTS:
            violations.append(("unknown_slot", b.borrower_id, d.slot))
        if d.borrower_index in seen:
            continue  # cap rules are counted once per borrower per day
        seen.add(d.borrower_index)
        today = per_borrower[d.borrower_index]
        if today > S.MAX_CONTACTS_PER_DAY:
            violations.append(("daily_cap", b.borrower_id, f"{today} contacts in one day"))
        week = log.previous_7_days(d.borrower_index, day) + today
        if week > S.MAX_CONTACTS_PER_7_DAYS:
            violations.append(("weekly_cap", b.borrower_id, f"{week} contacts in 7 days"))
    if spend > S.DAILY_BUDGET_PAISE:
        violations.append(("daily_budget", "-", f"spent {spend} paise, limit {S.DAILY_BUDGET_PAISE}"))
    return violations
