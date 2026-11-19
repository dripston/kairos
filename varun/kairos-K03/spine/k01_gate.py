"""K03 -> K01 link: asks Ojas's own K01 reference (policy/k01_reference_check.py) whether a
queued touch for one loan may be sent, using the spine's real state (DPD, consent, cleared EMI).

This is how "clearing one loan leaves the other open" reaches outreach: the cleared loan's
queued touch is CANCELled (OBLIGATION_CLEARED) while the other loan can still be contacted.
"""
import importlib.util
import os

from spine.config import DATASET_AS_OF_DATE
from spine.ledger import is_contactable, loan_summary
from spine.policy import load_policy

_REF = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "policy", "k01_reference_check.py")


def _reference():
    spec = importlib.util.spec_from_file_location("k01_reference", _REF)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def k01_decision(conn, loan_id, channel, when_date=DATASET_AS_OF_DATE, when_time="11:00"):
    """Return (decision, reason) from Ojas's K01 reference for one queued touch on this loan."""
    loan = loan_summary(conn, loan_id, when_date)
    case = {
        "date": when_date.isoformat(), "time": when_time,
        "dpd": "" if loan["dpd_band"] == "DPD_INVALID" else str(loan["file_dpd"] if loan["dpd_band"] == "CLEARED" else loan["dpd"]),
        "dpd_age_hours": "2", "channel": channel,
        "consent": "Y" if is_contactable(conn, loan["borrower_id"], channel) else "N",
        "opted_out": "N", "hardship_until": "", "hardship_supervisor": "N", "ptp_date": "",
        "obligation_cleared": "Y" if loan["dpd_band"] == "CLEARED" else "N",
        "stop_for_day": "N", "sends_today_channel": "0", "sends_today_all": "0", "mins_since_last_same_channel": "",
        "official_cap": "", "lender": "", "sends_week_channel": "0", "sends_week_all": "0",
        "template_approved": "Y", "budget_remaining_inr": "5000", "est_cost_inr": "0.20", "campaign_target_met": "N",
    }
    return _reference().evaluate(load_policy(), case)
