"""Templates: approved-only, right channel, policy language; re-checked at send time (K01 templates, E05)."""
from __future__ import annotations

from dataclasses import replace

from ..core import Checks, attack
from ..world import World, at
from ._helpers import dr, must

NO_TPL = ["WAIT", "NO_APPROVED_TEMPLATE"]


@attack("RT-TPL-01", category="templates", component="k06.ledger",
        title="Unapproved, unknown, empty or missing template id -> WAIT",
        covers=("E05", "K01-T27"))
def unapproved_or_unknown(ctx):
    chk = Checks()
    w = World(ctx)
    w.borrower("B1")
    w.ledger.upsert_template("SMS_DRAFT", "sms", "en", approved=False)
    for label, tpl in (("unapproved SMS_DRAFT", "SMS_DRAFT"), ("unknown id", "SMS_DOES_NOT_EXIST"),
                       ("empty string", ""), ("SQL-ish id", "SMS_EN' OR '1'='1")):
        chk.equal(label, list(dr(w.ledger.reserve(w.ins(tpl=tpl)))), NO_TPL)
    chk.equal("template_id None", list(dr(w.ledger.reserve(replace(w.ins(), template_id=None)))), NO_TPL)
    chk.equal("touches reserved", len(w.touches()), 0)
    return chk.result()


@attack("RT-TPL-02", category="templates", component="k06.ledger",
        title="Template for the wrong channel or a non-policy language -> WAIT",
        covers=("E05", "K01:templates.languages"))
def wrong_channel_or_language(ctx):
    chk = Checks()
    w = World(ctx)
    w.borrower("B1")
    w.ledger.upsert_template("SMS_TA", "sms", "ta")
    chk.equal("WhatsApp template on SMS", list(dr(w.ledger.reserve(w.ins(ch="sms", tpl="WA_HI")))), NO_TPL)
    chk.equal("SMS template on IVR", list(dr(w.ledger.reserve(w.ins(ch="ivr", tpl="SMS_EN")))), NO_TPL)
    chk.equal("approved SMS template in 'ta' (policy: en, hi)", list(dr(w.ledger.reserve(w.ins(tpl="SMS_TA")))), NO_TPL)
    chk.equal("control: SMS_EN on SMS", list(dr(w.ledger.reserve(w.ins(tpl="SMS_EN")))), ["SEND", "ELIGIBLE"])
    return chk.result()


@attack("RT-TPL-03", category="templates", component="k06.executor",
        title="Template revoked between reserve and dispatch -> blocked at send time, budget released",
        covers=("E05", "K06:send-time-recheck"))
def revoked_before_dispatch(ctx):
    chk = Checks()
    w = World(ctx)
    w.borrower("B1")
    r = w.ledger.reserve(w.ins())
    must(r.reserved, "reserve")
    w.ledger.upsert_template("SMS_EN", "sms", "en", approved=False)
    ex = w.executor()
    out = ex.dispatch(r.instruction_id, at(10))
    chk.equal("dispatch", [out.outcome, out.reason], ["BLOCKED", "NO_APPROVED_TEMPLATE"])
    chk.equal("provider sends", len(ex.providers["sms"].sent), 0)
    chk.equal("budget released", w.ledger.campaign_state("C1")["committed_paise"], 0)
    return chk.result()
