"""K09 RL v2 sequential decisions: contact today vs wait a day, and when to stop for good.

Contact vs wait (one-step lookahead). Over-contact lowers response: the fatigue level of the hierarchy
(rl.value) learns, from finalized touches only, the reply multiplier F(r) for r = executed touches in the
previous 6 days. If one of the borrower's recent touches leaves that window tomorrow, tomorrow's touch is
worth V x F(r-1)/F(r) (and carries less STOP risk). Waiting is chosen when

    (1 - h) x (V_tomorrow - lambda x cost)  >  V_today - lambda x cost

with h = 1 / contact days left (the chance the extra day is lost to the campaign's end). No constant is
tuned: F, the STOP risk and lambda are all learned.

Optimal stopping (pause until the next trigger). A borrower's reply rate is their cohort x region rate
times a borrower multiplier m ~ Gamma(O + kappa, E + kappa) (their own observed vs expected replies,
rl.hier). One more touch both earns its value and teaches us m. The value of continuing is

    V(contact) = (v m - lambda c) + (k - 1) x [ q max(0, v m+ - lambda c) + (1 - q) max(0, v m- - lambda c) ]

with q = p m the reply chance, m+ / m- the posterior after a reply / a silence, k the contacts still
possible this campaign. When V(contact) <= 0 even at the lowest price (the ratio guard, not the daily
dual price) the borrower is paused for the rest of the campaign: the money and the STOP risk are saved,
and the case goes to the next campaign or a human (layer L4). K01 / K13 hard rules always apply on top
(the Supervisor gate re-checks every send). A reply, a payment or a new campaign clears the pause.
"""
from __future__ import annotations


def wait_is_better(v_today: float, v_tomorrow: float, cost: int, lam: float, days_left: int) -> bool:
    if days_left <= 1:
        return False
    h = 1.0 / days_left
    return (1.0 - h) * (v_tomorrow - lam * cost) > (v_today - lam * cost)


def continuation_value(v_unit: float, p_unit: float, o: float, e: float, kappa: float, cost: int, lam: float,
                       k_left: int) -> float:
    """V(contact) above. v_unit / p_unit: the best arm's value / reply chance at borrower multiplier 1."""
    m = (o + kappa) / (e + kappa)
    q = min(0.95, p_unit * m)
    now = v_unit * m - lam * cost
    if k_left <= 1:
        return now
    m_up = (o + 1 + kappa) / (e + p_unit + kappa)
    m_dn = (o + kappa) / (e + p_unit + kappa)
    later = q * max(0.0, v_unit * m_up - lam * cost) + (1 - q) * max(0.0, v_unit * m_dn - lam * cost)
    return now + (k_left - 1) * later


def contacts_left(days_left: int, gap_days: int = 2, weekly_cap: int = 3) -> int:
    """Upper bound on touches still possible for one borrower (K15 gap and 3-per-7-days cap)."""
    by_gap = (days_left + gap_days - 1) // gap_days
    by_week = (days_left * weekly_cap + 6) // 7
    return max(0, min(by_gap, by_week))
