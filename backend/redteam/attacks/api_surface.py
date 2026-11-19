"""API surface: every mutating route on kairos_backend/api.py rejects malformed or negative paise and
unknown ids with a 4xx — never a 500, never a silent 200 that writes state."""
from __future__ import annotations

import csv

from kairos_backend.policy import policy_dir
from spine.synthetic_dataset import COLUMNS

from ..core import Checks, attack
from ..world import ApiWorld
from ._helpers import is_4xx, must

T = "2026-11-20T{}:00"
BAD_PAISE = [-1, -100_000, "abc", 1.5, None, 2 ** 63, 10 ** 30]


def _world(ctx) -> ApiWorld:
    w = ApiWorld(ctx)
    w.borrower("B1")
    w.borrower("B2")
    return w


def _record(chk: Checks, label: str, r: dict) -> None:
    chk.expect(label, is_4xx(r["status"]), status=r["status"],
               body=r["body"] if r["status"] < 500 and not is_4xx(r["status"]) else None)


@attack("RT-API-01", category="api", component="k06.api", finding="RT-F01", requires=("fastapi",),
        title="Negative / non-integer / overflowing paise are rejected with 4xx on every money route",
        covers=("K06:api", "K03:money-in-paise"))
def bad_paise(ctx):
    chk = Checks()
    w = _world(ctx)
    for i, v in enumerate(BAD_PAISE):
        _record(chk, f"PUT /campaigns budget_per_case_paise={v!r}",
                w.call("PUT", "/campaigns", json={"id": f"CX{i}", "budget_per_case_paise": v,
                                                  "start_date": "2026-11-20", "end_date": "2026-11-30"}))
        _record(chk, f"POST /decide est_cost_paise={v!r}",
                w.call("POST", "/decide", json=w.decide_body(t=T.format(f"1{i % 9}:0{i}"), est_cost_paise=v)))
        _record(chk, f"POST /events/payment-verified amount_paise={v!r}",
                w.call("POST", "/events/payment-verified", json={"obligation_id": "B1-A-1", "amount_paise": v,
                                                                "at": T.format("09:00"), "payment_id": f"P{i}",
                                                                "reference": f"UTR-{i}"}))
    committed_before = w.ledger.campaign_state("C1")["committed_paise"]
    for i, v in enumerate(BAD_PAISE):
        bid = f"R{i}"  # a fresh reserved touch per value: reconcile is once-only
        w.borrower(bid)
        d = w.call("POST", "/decide", json=w.decide_body(bid))
        must(d["status"] == 200 and d["body"].get("reserved"), f"reserve {bid}")
        _record(chk, f"POST /reconcile actual_cost_paise={v!r}",
                w.call("POST", f"/reconcile/{d['body']['instruction_id']}",
                       params={} if v is None else {"actual_cost_paise": v}))
    committed, floor = w.ledger.campaign_state("C1")["committed_paise"], committed_before + 20 * len(BAD_PAISE)
    chk.expect("rejected reconciles leave committed budget at the reserved estimates", committed == floor,
               committed_paise=committed, expected=floor)
    obl = w.ledger._conn().execute("SELECT amount_paid_paise FROM obligations WHERE obligation_id='B1-A-1'").fetchone()[0]
    chk.equal("B1 EMI untouched by rejected payments", obl, 0)
    return chk.result()


@attack("RT-API-02", category="api", component="k06.api", finding="RT-F02", requires=("fastapi",),
        title="Unknown borrower id -> 4xx on every borrower-scoped route (no 500, no silent write)",
        covers=("K06:api",))
def unknown_borrower(ctx):
    chk = Checks()
    w = _world(ctx)
    nb = "NO-SUCH-BORROWER"
    t = T.format("10:00")
    for label, (m, url, kw) in {
        "POST /events/response": ("POST", "/events/response", {"json": {"borrower_id": nb, "at": t, "channel": "sms"}}),
        "POST /events/call": ("POST", "/events/call", {"json": {"borrower_id": nb, "at": t, "answered_seconds": 30}}),
        "POST /events/stop": ("POST", "/events/stop", {"json": {"borrower_id": nb, "at": t}}),
        "POST /events/opt-in": ("POST", "/events/opt-in", {"json": {"borrower_id": nb, "channel": "sms",
                                                                    "consent_version": "v1", "at": t}}),
        "POST /events/exception DISPUTE": ("POST", "/events/exception", {"json": {"borrower_id": nb, "at": t,
                                                                                   "event": "DISPUTE", "band": "B1"}}),
        "POST /events/exception ABUSIVE": ("POST", "/events/exception", {"json": {"borrower_id": nb, "at": t,
                                                                                   "event": "ABUSIVE", "band": "B1"}}),
        "PUT /borrowers/{id}/lender": ("PUT", f"/borrowers/{nb}/lender", {"json": {"lender_id": "EXAMPLE"}}),
        "POST /control/supervisor-clear/{id}": ("POST", f"/control/supervisor-clear/{nb}", {}),
        "POST /decide": ("POST", "/decide", {"json": {**w.decide_body(), "borrower_id": nb, "obligation_id": f"{nb}-A-1"}}),
        "POST /preview": ("POST", "/preview", {"json": {**w.decide_body(), "borrower_id": nb, "obligation_id": f"{nb}-A-1"}}),
        "GET /borrowers/{id}": ("GET", f"/borrowers/{nb}", {}),
    }.items():
        _record(chk, label, w.call(m, url, **kw))
    c = w.ledger._conn()
    written = {tbl: c.execute(f"SELECT COUNT(*) FROM {tbl} WHERE borrower_id=?", (nb,)).fetchone()[0]
               for tbl in ("stop_for_day", "tickets", "borrower_state", "touches")}
    chk.equal("rows written for the unknown borrower", written, {k: 0 for k in written})
    return chk.result()


@attack("RT-API-03", category="api", component="k06.api", requires=("fastapi",),
        title="Unknown campaign / loan / EMI / payment / instruction ids -> 4xx, never 500",
        covers=("K06:api",))
def unknown_other_ids(ctx):
    chk = Checks()
    w = _world(ctx)
    t = T.format("11:00")
    for label, (m, url, kw) in {
        "POST /campaigns/{id}/enroll": ("POST", "/campaigns/NO-CAMP/enroll", {"json": {"loan_ids": ["B1-A"]}}),
        "POST /campaigns/{id}/status": ("POST", "/campaigns/NO-CAMP/status", {"json": {"status": "PAUSED"}}),
        "POST /campaigns/C1/enroll unknown loan": ("POST", "/campaigns/C1/enroll", {"json": {"loan_ids": ["NO-LOAN"]}}),
        "POST /decide unknown campaign": ("POST", "/decide", {"json": w.decide_body(t=t, campaign_id="NO-CAMP")}),
        "POST /preview unknown campaign": ("POST", "/preview", {"json": w.decide_body(t=t, campaign_id="NO-CAMP")}),
        "POST /decide EMI of another borrower": ("POST", "/decide", {"json": w.decide_body(t=t, obligation_id="B2-A-1")}),
        "POST /events/payment-verified unknown EMI": ("POST", "/events/payment-verified", {"json": {
            "obligation_id": "NO-EMI", "amount_paise": 100, "at": t, "payment_id": "P1", "reference": "R1"}}),
        "POST /events/payment-reversed unknown payment": ("POST", "/events/payment-reversed", {"json": {
            "payment_id": "NO-PAY", "at": t, "reason": "x"}}),
        "POST /dispatch/{id}": ("POST", "/dispatch/NO-INS", {}),
        "POST /reconcile/{id}": ("POST", "/reconcile/NO-INS", {"params": {"actual_cost_paise": 10}}),
        "POST /provider-status/{id}": ("POST", "/provider-status/NO-INS", {"json": {"status": "SENT"}}),
    }.items():
        _record(chk, label, w.call(m, url, **kw))
    r = w.call("POST", "/resolve-uncertain/NO-INS")
    chk.expect("POST /resolve-uncertain/{id} unknown -> no 5xx (reports UNKNOWN_INSTRUCTION)", r["status"] < 500,
               status=r["status"], body=r["body"])
    chk.equal("no touches created", len(w.touches()), 0)
    return chk.result()


@attack("RT-API-04", category="api", component="k06.api", finding="RT-F03", requires=("fastapi",),
        title="Malformed enums, channels, dates and wrong-typed facts -> 4xx, never 500",
        covers=("K06:api",))
def malformed_inputs(ctx):
    chk = Checks()
    w = _world(ctx)
    t = T.format("10:00")
    for label, (m, url, kw) in {
        "opt-in channel 'telegram'": ("POST", "/events/opt-in", {"json": {"borrower_id": "B1", "channel": "telegram",
                                                                         "consent_version": "v", "at": t}}),
        "stop channel 'telegram'": ("POST", "/events/stop", {"json": {"borrower_id": "B1", "at": t, "channel": "telegram"}}),
        "response channel 'telegram'": ("POST", "/events/response", {"json": {"borrower_id": "B1", "at": t,
                                                                             "channel": "telegram"}}),
        "call channel 'telegram'": ("POST", "/events/call", {"json": {"borrower_id": "B1", "at": t,
                                                                     "answered_seconds": 30, "channel": "telegram"}}),
        "decide channel 'telegram'": ("POST", "/decide", {"json": {**w.decide_body(), "channel": "telegram"}}),
        "decide at '2026-13-01'": ("POST", "/decide", {"json": w.decide_body(t="2026-13-01T10:00:00")}),
        "campaign status 'PWNED'": ("POST", "/campaigns/C1/status", {"json": {"status": "PWNED"}}),
        "exception event 'PWNED'": ("POST", "/events/exception", {"json": {"borrower_id": "B1", "at": t,
                                                                          "event": "PWNED", "band": "B1"}}),
        "exception band 'B9'": ("POST", "/events/exception", {"json": {"borrower_id": "B1", "at": t,
                                                                       "event": "NEEDS_TIME", "band": "B9"}}),
        "provider-status 'PWNED'": ("POST", "/provider-status/NO-INS", {"json": {"status": "PWNED"}}),
        "gate/evaluate dpd '12' (string)": ("POST", "/gate/evaluate", {"json": {"at": t, "channel": "sms", "dpd": "12"}}),
        "gate/evaluate sends_today_channel 'x'": ("POST", "/gate/evaluate", {"json": {"at": t, "channel": "sms",
                                                                                      "dpd": 12, "sends_today_channel": "x"}}),
        "gate/evaluate at 'garbage'": ("POST", "/gate/evaluate", {"json": {"at": "garbage", "channel": "sms", "dpd": 12}}),
        "dispatch-due now 'garbage'": ("POST", "/dispatch-due", {"params": {"now": "garbage"}}),
        "days/{day}/start 2026-13-01": ("POST", "/days/2026-13-01/start", {}),
    }.items():
        _record(chk, label, w.call(m, url, **kw))
    rows = {r["channel"]: r["opted_out"] for r in w.ledger.borrower_state("B1")["consents"]}
    chk.expect("B1 consents untouched", set(rows.values()) == {0}, consents=rows)
    return chk.result()


@attack("RT-API-05", category="api", component="k06.api", finding="RT-F10", requires=("fastapi",),
        title="POST /spine/import refuses paths outside the data directory and echoes nothing",
        covers=("K06:api", "brief:synthetic-data-only"))
def import_arbitrary_path(ctx):
    chk = Checks()
    w = _world(ctx)
    secret = ctx.path("outside") / "secret.csv"
    secret.parent.mkdir(parents=True, exist_ok=True)
    with open(secret, "w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=COLUMNS)
        wr.writeheader()
        wr.writerow({c: "" for c in COLUMNS} | {"Loan ID": "X1", "Name": "n", "Mobile Number": "TOKEN-SECRET-42",
                                                "Product": "p", "EMI Amount": "1", "POS": "1", "TOS": "1",
                                                "Bounce Date": "2026-08-01", "Remaining EMIs": "1",
                                                "Risk Tagging": "r", "Payment Link": "l"})
    r = w.call("POST", "/spine/import", json={"csv_path": str(secret)})
    chk.expect("file outside backend/data -> 4xx", is_4xx(r["status"]), status=r["status"])
    chk.expect("file contents are not echoed", "TOKEN-SECRET-42" not in str(r["body"]),
               echoed="TOKEN-SECRET-42" in str(r["body"]))
    probe_missing = w.call("POST", "/spine/import", json={"csv_path": "/definitely/not/here.csv"})
    probe_exists = w.call("POST", "/spine/import", json={"csv_path": str(policy_dir() / "K01-decision-policy" / "policy.json")})
    chk.expect("no existence oracle (missing vs existing system file answer alike)",
               probe_missing["status"] == probe_exists["status"],
               missing=probe_missing["status"], existing=probe_exists["status"])
    return chk.result()
