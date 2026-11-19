"""Payments through the K03 spine: scope of cancellation, reversals, forged / inflated claims (E14/E15)."""
from __future__ import annotations

from spine.ledger import PaymentError

from ..core import Checks, attack
from ..world import World, at
from ._helpers import dr, must


def _paid(w: World, ob: str) -> int:
    return w.ledger._conn().execute("SELECT amount_paid_paise FROM obligations WHERE obligation_id=?", (ob,)).fetchone()[0]


def _try_pay(w: World, ob: str, amount: int, pid: str, ref: str):
    try:
        return w.ledger.record_payment_verified(ob, amount, at(9, 30), pid, ref)
    except PaymentError as e:
        return {"rejected": str(e)[:80]}


@attack("RT-PAY-01", category="payment", component="k06.ledger",
        title="Verified payment cancels only that EMI's queued touches; other EMIs/loans stay queued",
        covers=("E14", "E15", "K01-T23"))
def payment_scope(ctx):
    chk = Checks()
    w = World(ctx)
    w.borrower("B2", "A")
    w.borrower("B2", "B", emi_paise=320_000)
    w.borrower("B2", "C", dpd=40, installments=2)   # EMI C-1 40 DPD, EMI C-2 10 DPD
    ra = w.ledger.reserve(w.ins("B2", "sms", at(10), loan="A"))
    rb = w.ledger.reserve(w.ins("B2", "whatsapp", at(12), loan="B", cost=40))
    rc2 = w.ledger.reserve(w.ins("B2", "email", at(14), loan="C", n=2, cost=5))
    must(ra.reserved and rb.reserved and rc2.reserved, "queue 3 touches")
    out = w.ledger.record_payment_verified("B2-A-1", 500_000, at(9, 30), "PAY-A", "UTR-A-0001")
    chk.equal("payment on loan A EMI 1", out, {"applied": True, "obligation_cleared": True, "canceled": 1})
    chk.equal("loan A touch", w.ledger.touch(ra.instruction_id)["status"], "CANCELED")
    chk.equal("loan B touch (other loan)", w.ledger.touch(rb.instruction_id)["status"], "RESERVED")
    w.ledger.record_payment_verified("B2-C-1", 500_000, at(9, 40), "PAY-C1", "UTR-C1-0001")
    chk.equal("loan C EMI 2 touch after EMI 1 paid (same loan, other EMI)",
              w.ledger.touch(rc2.instruction_id)["status"], "RESERVED")
    part = w.ledger.record_payment_verified("B2-B-1", 100_000, at(9, 50), "PAY-B", "UTR-B-0001")
    chk.equal("partial payment on loan B keeps its touch", [part["obligation_cleared"],
              w.ledger.touch(rb.instruction_id)["status"]], [False, "RESERVED"])
    chk.equal("new touch on cleared EMI", list(dr(w.ledger.reserve(w.ins("B2", "sms", at(16), loan="A")))),
              ["CANCEL", "OBLIGATION_CLEARED"])
    return chk.result()


@attack("RT-PAY-02", category="payment", component="k06.ledger",
        title="Reversal re-opens the EMI exactly once; a repeated reversal changes nothing",
        covers=("E15",))
def reversal_reopens(ctx):
    chk = Checks()
    w = World(ctx)
    w.borrower("B1")
    w.ledger.record_payment_verified("B1-A-1", 500_000, at(9), "PAY-1", "UTR-0001")
    chk.equal("after payment", list(dr(w.ledger.reserve(w.ins(t=at(10))))), ["CANCEL", "OBLIGATION_CLEARED"])
    chk.equal("campaign collected after payment", w.ledger.campaign_state("C1")["collected_paise"], 500_000)
    chk.equal("first reversal", w.ledger.record_payment_reversed("PAY-1", at(11), "bank reversal"), True)
    chk.equal("second reversal of the same payment", w.ledger.record_payment_reversed("PAY-1", at(11, 5), "again"), False)
    chk.equal("EMI amount_paid after double reversal", _paid(w, "B1-A-1"), 0)
    chk.equal("campaign collected rolled back once", w.ledger.campaign_state("C1")["collected_paise"], 0)
    chk.equal("EMI is contactable again", list(dr(w.ledger.reserve(w.ins(t=at(12))))), ["SEND", "ELIGIBLE"])
    return chk.result()


@attack("RT-PAY-03", category="payment", component="k03.spine",
        title="Reused bank reference is rejected; replaying a payment id never double-counts",
        covers=("K03:apply_payment", "CM-03:edited-screenshot"))
def reference_reuse_exact(ctx):
    chk = Checks()
    w = World(ctx)
    w.borrower("B1")
    w.borrower("B9")
    chk.equal("first payment", _try_pay(w, "B1-A-1", 100, "PAY-1", "UTR-REUSE-1").get("applied"), True)
    chk.expect("same reference, new payment id, same EMI -> rejected",
               "rejected" in _try_pay(w, "B1-A-1", 100, "PAY-2", "UTR-REUSE-1"))
    chk.expect("same reference on another borrower's EMI -> rejected",
               "rejected" in _try_pay(w, "B9-A-1", 100, "PAY-3", "UTR-REUSE-1"))
    chk.equal("replay of PAY-1", _try_pay(w, "B1-A-1", 100, "PAY-1", "UTR-REUSE-1").get("applied"), False)
    chk.equal("B1 EMI paid (paise)", _paid(w, "B1-A-1"), 100)
    chk.equal("B9 EMI paid (paise)", _paid(w, "B9-A-1"), 0)
    return chk.result()


@attack("RT-PAY-04", category="payment", component="k03.spine", finding="RT-F06",
        title="Cosmetic variants of a used bank reference (space, case, zero-width, full-width) are rejected",
        covers=("K03:apply_payment", "CM-03:edited-screenshot"))
def reference_reuse_variants(ctx):
    """An edited screenshot that re-types the same UTR with a leading space, lower case, a zero-width
    space or full-width digits is the same bank transfer and must be refused."""
    chk = Checks()
    w = World(ctx)
    w.borrower("B1")
    must(_try_pay(w, "B1-A-1", 100, "PAY-0", "UTR-7731").get("applied"), "original payment")
    variants = {"leading space": " UTR-7731", "lower case": "utr-7731", "trailing zero-width space": "UTR-7731​",
                "full-width": "ＵＴＲ-７７３１"}
    for i, (label, ref) in enumerate(variants.items(), 1):
        out = _try_pay(w, "B1-A-1", 100, f"PAY-{i}", ref)
        chk.expect(f"variant '{label}' rejected", "rejected" in out, got=out)
    chk.equal("EMI paid (paise) — only the original", _paid(w, "B1-A-1"), 100)
    return chk.result()


@attack("RT-PAY-05", category="payment", component="k03.spine",
        title="Over-payment, zero and negative amounts are rejected and change nothing",
        covers=("K03:apply_payment",))
def amount_bounds(ctx):
    chk = Checks()
    w = World(ctx)
    w.borrower("B1")
    for i, amt in enumerate((500_001, 0, -100, 10 ** 15)):
        chk.expect(f"amount {amt} rejected", "rejected" in _try_pay(w, "B1-A-1", amt, f"PAY-X{i}", f"UTR-X{i}"))
    must(_try_pay(w, "B1-A-1", 400_000, "PAY-OK", "UTR-OK").get("applied"), "partial payment")
    chk.expect("100,001 paise when 100,000 is left -> rejected",
               "rejected" in _try_pay(w, "B1-A-1", 100_001, "PAY-X9", "UTR-X9"))
    chk.equal("EMI paid (paise)", _paid(w, "B1-A-1"), 400_000)
    return chk.result()
