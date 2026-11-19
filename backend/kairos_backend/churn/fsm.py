"""K27 per-borrower lifecycle FSM: declared states and the only transitions allowed between them.

(`origin/k06/deterministic-states` did not exist when K27 was built, so this is a plain transition
table; port it onto kairos_backend/fsm.py when that lands.)

STATES
  ACTIVE              in an automated journey (K09 ranks, K06 gates every send)
  PAUSED_PTP          open promise-to-pay: no outbound until the date; resume on date + 1 (K01 holds.PROMISE_TO_PAY)
  PAUSED_CLAIM        "already paid" claim: pause until D7/LMS verifies it or 48 h pass (K17 payment_claim)
  PAUSED_HOLD         K13 callback pending / routed stop (abusive, legal threat, fraud claim) until cleared
  HARDSHIP_HUMAN      hardship: automation stops, a person (L4 / HITL) owns the case
  EXHAUSTED_CAMPAIGN  no-response budget spent for THIS campaign: hand to a person or the next campaign
  STOPPED_OPT_OUT     terminal: STOP on any channel (all channels); exits only on a per-channel opt-in
  PAID_EXIT           terminal: verified payment cleared the obligation; exits only on a bank reversal

Orthogonal machine-readable state per borrower: cohort, risk tier, journey + step, layer (see engine).
"""
from __future__ import annotations

STATES = ("ACTIVE", "PAUSED_PTP", "PAUSED_CLAIM", "PAUSED_HOLD", "HARDSHIP_HUMAN", "EXHAUSTED_CAMPAIGN",
          "STOPPED_OPT_OUT", "PAID_EXIT")
TERMINAL = ("STOPPED_OPT_OUT", "PAID_EXIT")
PAUSED = ("PAUSED_PTP", "PAUSED_CLAIM", "PAUSED_HOLD", "HARDSHIP_HUMAN")
AUTOMATED = ("ACTIVE",)

# from -> {to: trigger that must be present}. "*" = any trigger (a fact computed by the stop rules).
_ANY = "*"
TRANSITIONS: dict[str, dict[str, str]] = {
    "ACTIVE": {s: _ANY for s in STATES if s != "ACTIVE"},
    "PAUSED_PTP": {"ACTIVE": _ANY, "PAUSED_CLAIM": _ANY, "PAUSED_HOLD": _ANY, "HARDSHIP_HUMAN": _ANY,
                   "STOPPED_OPT_OUT": _ANY, "PAID_EXIT": _ANY, "EXHAUSTED_CAMPAIGN": _ANY},
    "PAUSED_CLAIM": {"ACTIVE": _ANY, "PAUSED_PTP": _ANY, "PAUSED_HOLD": _ANY, "HARDSHIP_HUMAN": _ANY,
                     "STOPPED_OPT_OUT": _ANY, "PAID_EXIT": _ANY, "EXHAUSTED_CAMPAIGN": _ANY},
    "PAUSED_HOLD": {"ACTIVE": _ANY, "PAUSED_PTP": _ANY, "PAUSED_CLAIM": _ANY, "HARDSHIP_HUMAN": _ANY,
                    "STOPPED_OPT_OUT": _ANY, "PAID_EXIT": _ANY, "EXHAUSTED_CAMPAIGN": _ANY},
    "HARDSHIP_HUMAN": {"ACTIVE": _ANY, "PAUSED_PTP": _ANY, "PAUSED_CLAIM": _ANY, "PAUSED_HOLD": _ANY,
                       "STOPPED_OPT_OUT": _ANY, "PAID_EXIT": _ANY, "EXHAUSTED_CAMPAIGN": _ANY},
    # exhausted is sticky for the campaign: only stronger facts, a new campaign or a person move it
    "EXHAUSTED_CAMPAIGN": {"STOPPED_OPT_OUT": _ANY, "PAID_EXIT": _ANY, "HARDSHIP_HUMAN": _ANY,
                           "PAUSED_HOLD": _ANY, "PAUSED_PTP": _ANY, "PAUSED_CLAIM": _ANY,
                           "ACTIVE": "new_campaign|human"},
    "STOPPED_OPT_OUT": {"ACTIVE": "opt_in", "PAID_EXIT": _ANY},
    "PAID_EXIT": {"ACTIVE": "payment_reversed", "STOPPED_OPT_OUT": _ANY, "PAUSED_PTP": "payment_reversed",
                  "PAUSED_CLAIM": "payment_reversed", "PAUSED_HOLD": "payment_reversed",
                  "HARDSHIP_HUMAN": "payment_reversed", "EXHAUSTED_CAMPAIGN": "payment_reversed"},
}


def allowed(frm: str, to: str, trigger: str) -> bool:
    """May the lifecycle move frm -> to on this trigger (event kind / 'payment_reversed' / 'opt_in' /
    'new_campaign' / 'human' / 'tick')?"""
    if frm == to:
        return True
    need = TRANSITIONS.get(frm, {}).get(to)
    if need is None:
        return False
    return need == _ANY or trigger in need.split("|")


def table() -> list[dict]:
    """The transition table as rows (for docs / API)."""
    return [{"from": f, "to": t, "trigger": need} for f, tos in TRANSITIONS.items() for t, need in tos.items()]
