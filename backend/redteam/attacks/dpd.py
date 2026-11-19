"""DPD data quality: missing, stale, not-yet-overdue and future-dated data must HOLD (K01 dpd_rules, E01)."""
from __future__ import annotations

from datetime import datetime, timedelta

from kairos_backend.ledger import Instruction
from spine.config import DATASET_AS_OF_DATE
from spine.synthetic_dataset import MISSING_DPD_EVERY, write_dataset

from ..core import Checks, attack
from ..world import D, World, at
from ._helpers import dr, must


@attack("RT-DPD-01", category="dpd", component="k06.ledger",
        title="Loan with missing DPD (real K03 importer, synthetic CSV) is held, not guessed",
        covers=("E01", "K01-T08"))
def missing_dpd(ctx):
    chk = Checks()
    w = World(ctx, start=DATASET_AS_OF_DATE, days=10)
    csv_path = ctx.path("synthetic.csv")
    write_dataset(str(csv_path), MISSING_DPD_EVERY, seed=ctx.seed)
    report = w.ledger.import_dataset(csv_path)
    must(report["loaded"] == MISSING_DPD_EVERY, f"import loaded {report.get('loaded')}")
    missing = f"SL{100001 + MISSING_DPD_EVERY - 1}"   # every 1,000th synthetic row has no DPD
    control = "SL100001"                                # boundary row: DPD 1 -> B1
    conn = w.ledger._spine()
    with conn:  # make consent explicit so the DPD rule (not consent) is what decides
        for loan in (missing, control):
            conn.execute("INSERT INTO consents (borrower_id, channel, consented, opted_out, updated_at)"
                         " VALUES (?, 'sms', 1, 0, ?) ON CONFLICT (borrower_id, channel) DO UPDATE SET consented=1,"
                         " opted_out=0", (f"BRW-{loan}", DATASET_AS_OF_DATE.isoformat()))
    file_dpd = conn.execute("SELECT file_dpd FROM loans WHERE loan_id=?", (missing,)).fetchone()[0]
    chk.equal(f"{missing} file_dpd in K03", file_dpd, None)
    w.ledger.enroll("C1", [missing, control])
    t = at(10, day=DATASET_AS_OF_DATE)
    for loan, want in ((missing, ["WAIT", "DPD_INVALID"]), (control, ["SEND", "ELIGIBLE"])):
        r = w.ledger.reserve(Instruction(f"BRW-{loan}", f"{loan}-OD1", "C1", "sms", "SMS_EN", t, 20))
        chk.equal(f"{loan}", list(dr(r)), want)
    return chk.result()


@attack("RT-DPD-02", category="dpd", component="k06.ledger",
        title="Stale LMS data (> 24 h, or never synced) holds; exactly 24 h still sends",
        covers=("E01", "K01-T09"))
def stale_dpd(ctx):
    chk = Checks()
    w = World(ctx)
    for b in ("S1", "S2", "S3"):
        w.borrower(b)
    w.ledger.mark_spine_synced(at(10) - timedelta(hours=24, minutes=1))
    chk.equal("synced 24h01m ago", list(dr(w.ledger.reserve(w.ins("S1", t=at(10))))), ["WAIT", "DPD_INVALID"])
    w.ledger.mark_spine_synced(at(10) - timedelta(hours=24))
    chk.equal("synced exactly 24h ago", list(dr(w.ledger.reserve(w.ins("S2", t=at(10))))), ["SEND", "ELIGIBLE"])
    w.ledger._conn().execute("DELETE FROM settings WHERE key='spine_synced_at'")
    chk.equal("never synced", list(dr(w.ledger.reserve(w.ins("S3", t=at(12))))), ["WAIT", "DPD_INVALID"])
    return chk.result()


@attack("RT-DPD-03", category="dpd", component="k06.ledger",
        title="DPD 0 / not-yet-due EMI cannot be sent a collection message; DPD -7 is out of scope",
        covers=("E01", "K01-T31", "K01-T32", "K01:collection_purpose_requires_verified_overdue"))
def not_verified_overdue(ctx):
    chk = Checks()
    w = World(ctx)
    w.borrower("Z0", dpd=0)
    r = w.ledger.reserve(w.ins("Z0", purpose="collection"))
    chk.expect("DPD 0, purpose=collection -> HOLD (WAIT, not reserved)", r.decision.decision == "WAIT" and not r.reserved,
               got=list(dr(r)))
    chk.equal("DPD 0, purpose=reminder -> pre-due reminder allowed", list(dr(w.ledger.reserve(
        w.ins("Z0", t=at(14), purpose="reminder")))), ["SEND", "ELIGIBLE"])
    w.borrower("N2", dpd=12, installments=2)  # loan is B1 because EMI 1 is late; EMI 2 is due in 18 days
    chk.equal("B1 loan, collection on the not-yet-due EMI 2", list(dr(w.ledger.reserve(
        w.ins("N2", n=2, purpose="collection")))), ["WAIT", "OVERDUE_NOT_VERIFIED"])
    w.borrower("M7", dpd=-7)
    chk.equal("DPD -7 (before pre-due start)", list(dr(w.ledger.reserve(w.ins("M7")))), ["WAIT", "NOT_IN_SCOPE"])
    w.borrower("M6", dpd=-6)
    chk.equal("DPD -6 (first pre-due day)", list(dr(w.ledger.reserve(w.ins("M6")))), ["SEND", "ELIGIBLE"])
    return chk.result()


@attack("RT-DPD-04", category="dpd", component="k06.ledger",
        title="A future-dated LMS sync stamp must not make 5-day-old data look fresh",
        covers=("E01", "K01-T09"))
def future_sync_stamp(ctx):
    """One bad call (POST /spine/synced?at=2030-01-01, or a feed with a skewed clock) stamps the spine as
    synced in the future. Five days later, with no refresh at all, DPD must be stale -> HOLD."""
    chk = Checks()
    w = World(ctx)
    w.borrower("B1")
    w.ledger.mark_spine_synced(datetime(2030, 1, 1))
    later = at(10, day=D + timedelta(days=5))
    chk.equal("decision 5 days after the last real sync", list(dr(w.ledger.reserve(w.ins(t=later)))),
              ["WAIT", "DPD_INVALID"])
    return chk.result()
