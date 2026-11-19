"""K26 reports: the lender's five sheets + the CM-02 daily per-segment report, on a hand-built scenario
run through the real ledger, supervisor and executor (all SIMULATED)."""
import csv
import importlib
import io
import json
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from kairos_backend import reports as R
from kairos_backend.executor import Executor, FakeProvider
from kairos_backend.ledger import Instruction, Ledger

D = date(2026, 11, 20)        # Friday
D2 = date(2026, 11, 21)       # Saturday (a contact day)
TPL = {"sms": "SMS_EN", "whatsapp": "WA_HI", "email": "EM_EN", "ivr": "IVR_HI"}
COST = {"sms": 20, "whatsapp": 40, "email": 5, "ivr": 150}
UTC = timezone.utc


def at(hh, mm=0, day=D):
    return datetime(day.year, day.month, day.day, hh, mm)


def add_loan(lg, bid, dpd, risk_tag=None):
    conn = lg._spine()
    with conn:
        conn.execute("INSERT INTO borrowers (borrower_id, full_name, phone, email, language) VALUES (?,?,?,?, 'en')",
                     (bid, f"Priya Testname {bid}", "+910000000000", f"{bid.lower()}@example.invalid"))
        for ch in ("sms", "whatsapp", "email", "ivr", "voicebot"):
            conn.execute("INSERT INTO consents (borrower_id, channel, consented, opted_out, updated_at) VALUES (?,?,1,0,?)",
                         (bid, ch, D.isoformat()))
        conn.execute("INSERT INTO loans (loan_id, borrower_id, product, principal_paise, risk_tag)"
                     " VALUES (?,?,'personal_loan',6000000,?)", (f"{bid}-A", bid, risk_tag))
        conn.execute("INSERT INTO obligations (obligation_id, loan_id, installment_no, due_date, amount_due_paise)"
                     " VALUES (?,?,1,?,500000)", (f"{bid}-A-1", f"{bid}-A", (D - timedelta(days=dpd)).isoformat()))
    return f"{bid}-A"


def send(lg, ex, bid, ch, when, iid):
    r = lg.reserve(Instruction(bid, f"{bid}-A-1", "C1", ch, TPL[ch], when, COST[ch], instruction_id=iid))
    assert r.reserved, (iid, r.decision)
    return ex.dispatch(iid, when)


def build(lg):
    """B1 (B1 band, C1): sms delivered, whatsapp sent, ivr call picked up 30 s, partial payment.
    B2 (B2 band, C3): sms rejected by provider, ivr placed, whatsapp sent then a reply.
    B3 (B3 band, no tag): sms sent (scheduled in UTC), a 'STOP' text and a STOP opt-out.
    Day 2: B1 sms again (Sent only)."""
    for t, (ch, lang) in {"SMS_EN": ("sms", "en"), "WA_HI": ("whatsapp", "hi"), "EM_EN": ("email", "en"),
                          "IVR_HI": ("ivr", "hi")}.items():
        lg.upsert_template(t, ch, lang)
    lg.upsert_campaign("C1", budget_per_case_paise=10_000, start_date=D, end_date=D + timedelta(days=10))
    lg.enroll("C1", [add_loan(lg, "B1", 12, "C1 - First-Time Bounce"), add_loan(lg, "B2", 45, "C3 - Medium Risk"),
                     add_loan(lg, "B3", 70)])
    reject_b2_sms = lambda t: "REJECTED" if t["borrower_id"] == "B2" else "ACCEPTED"
    ex = Executor(lg, {"sms": FakeProvider("sms", cost_paise=18, script=reject_b2_sms),
                       "whatsapp": FakeProvider("whatsapp"), "ivr": FakeProvider("ivr", cost_paise=150)})
    lg.start_day(D)
    send(lg, ex, "B1", "sms", at(9, 15), "T-B1-SMS")
    lg.record_provider_status("T-B1-SMS", "DELIVERED")
    lg.record_provider_status("T-B1-SMS", "SENT")          # late, lower: must not overwrite DELIVERED
    send(lg, ex, "B1", "whatsapp", at(9, 45), "T-B1-WA")
    send(lg, ex, "B1", "ivr", at(10, 5), "T-B1-IVR")
    lg.record_call("B1", at(10, 6), answered_seconds=30, channel="ivr")
    assert send(lg, ex, "B2", "sms", at(9, 0), "T-B2-SMS").outcome == "FAILED"
    send(lg, ex, "B2", "ivr", at(12, 30), "T-B2-IVR")
    send(lg, ex, "B2", "whatsapp", at(13, 0), "T-B2-WA")
    lg.record_response("B2", at(13, 20), channel="whatsapp", details="I will pay on Monday")
    send(lg, ex, "B3", "sms", datetime(2026, 11, 20, 4, 0, tzinfo=UTC), "T-B3-SMS")   # = 09:30 IST
    lg.record_response("B3", at(10, 59), channel="sms", details="STOP")
    lg.record_stop("B3", at(11, 0), channel="sms")
    lg.record_payment_verified("B1-A-1", 100_000, at(15, 0), "PAY-1", "UTR-1")
    lg.start_day(D2)
    send(lg, ex, "B1", "sms", at(10, 0, D2), "T-B1-SMS-2")
    return ex


@pytest.fixture
def lg(policy, tmp_path):
    ledger = Ledger(policy, tmp_path / "k26.db")
    build(ledger)
    yield ledger
    ledger.close()


def by(rows, *keys):
    return {tuple(r[k] for k in keys) if len(keys) > 1 else r[keys[0]]: r for r in rows}


# ---------- the mapping ----------
def test_mapping_table_is_ordinal_and_complete():
    assert [R.RANK[x] for x in R.MESSAGE_SCALE] == sorted(R.RANK[x] for x in R.MESSAGE_SCALE)
    assert R.MESSAGE_SCALE == ("No data", "Sent", "Delivered", "Read", "Clicked", "Responded")
    assert [R.RANK[x] for x in R.CALL_SCALE] == [1, 2, 3, 4]
    from kairos_backend.ledger import RANK as LEDGER_RANK
    for status in (*LEDGER_RANK, "FAILED", "CANCELED"):
        assert ("touches.status", status) in R._MAP, status
    for ev in ("touch_sent", "reply_received", "payment_received", "payment_reversed", "consent_changed"):
        assert ("events.event_type", ev) in R._MAP
    # payments and STOP are never dispositions
    for ev in ("payment_received", "payment_reversed", "consent_changed"):
        assert R.label_for("events.event_type", ev, "sms") is None
        assert R.label_for("events.event_type", ev, "ivr") is None
    assert R.label_for("touches.status", "RESERVED", "sms") is None
    assert R.label_for("touches.status", "DELIVERED", "sms") == "Delivered"
    assert R.label_for("touches.status", "DELIVERED", "ivr") == "Attempted"  # answered != connected
    assert R.label_for("audit.CALL_RESULT", "answered_seconds > threshold", "ivr") == "Connected"
    assert R.label_for("channel_events.status", "CLICKED", "whatsapp") == "Clicked"
    assert R.label_for("channel_events.status", "BUSY", "ivr") == "Busy"


def test_best_never_lets_lower_or_older_overwrite_higher():
    t0 = datetime(2026, 11, 20, 9)
    ev = lambda lab, mins: R.Disposition("B", "sms", t0 + timedelta(minutes=mins), lab, "I", "touches")
    assert R._best([ev("Sent", 0), ev("Delivered", 1), ev("Sent", 5)]).label == "Delivered"
    assert R._best([ev("Responded", 0), ev("Read", 9)]).label == "Responded"
    assert R._best([ev("Failed", 0), ev("Sent", 1)]).label == "Sent"


# ---------- the five sheets ----------
def test_columns_are_exactly_the_lender_template(lg):
    for name, fn in R.SHEETS.items():
        rows = fn(lg, D, D2)
        assert rows and all(list(r) == R.SHEET_COLUMNS[name] for r in rows), name


def test_ftd_is_best_disposition(lg):
    f = {k: r["Latest Best Disposition"] for k, r in by(R.ftd(lg, D, D2), "Customer", "Channel").items()}
    assert f == {("B1", "sms"): "Delivered",      # DELIVERED, then a late SENT and a day-2 Sent: still Delivered
                 ("B1", "whatsapp"): "Sent", ("B1", "ivr"): "Connected",
                 ("B2", "sms"): "Failed", ("B2", "ivr"): "Attempted", ("B2", "whatsapp"): "Responded",
                 ("B3", "sms"): "Sent"}           # 'STOP' text is not Responded


def test_aici_counts(lg):
    a = {k: (r["Attempted Intensity"], r["Connected Intensity"]) for k, r in by(R.aici(lg, D, D2), "Customer", "Channel").items()}
    assert a == {("B1", "sms"): (2, 1), ("B1", "whatsapp"): (1, 0), ("B1", "ivr"): (1, 1),
                 ("B2", "sms"): (1, 0), ("B2", "ivr"): (1, 0), ("B2", "whatsapp"): (1, 1), ("B3", "sms"): (1, 0)}
    one_day = by(R.aici(lg, D, D), "Customer", "Channel")
    assert one_day[("B1", "sms")]["Attempted Intensity"] == 1


def test_trail_order(lg):
    t = {k: r["Disposition"] for k, r in by(R.trails(lg, D, D2), "Customer", "Channel").items()}
    assert t[("B1", "sms")] == "Sent > Delivered > Sent"
    assert t[("B2", "whatsapp")] == "Sent > Responded"
    assert t[("B1", "ivr")] == "Attempted > Connected"
    assert t[("B2", "sms")] == "Failed"


def test_data_dump_is_chronological_ist_and_has_no_facts(lg):
    rows = R.data_dump(lg, D, D2)
    keys = [(r["Date"], r["Time"]) for r in rows]
    assert keys == sorted(keys)
    labels = {r["Disposition"] for r in rows}
    assert labels <= set(R.RANK)                     # payments / STOP never appear as dispositions
    b3 = [r for r in rows if r["Customer"] == "B3"]
    assert [(r["Date"], r["Time"], r["Disposition"]) for r in b3] == [("2026-11-20", "09:30:00", "Sent")]
    reply = [r for r in rows if r["Customer"] == "B2" and r["Disposition"] == "Responded"]
    assert [(r["Channel"], r["Time"]) for r in reply] == [("whatsapp", "13:20:00")]


def test_hourly_buckets_are_ist(lg):
    rows = R.hourly(lg, D, D)
    assert {"Customer": "B3", "Time": "2026-11-20 09:00-10:00", "Channel": "sms"} in rows  # sent 04:00 UTC
    assert {"Customer": "B1", "Time": "2026-11-20 10:00-11:00", "Channel": "ivr"} in rows
    assert [r["Time"] for r in rows] == sorted(r["Time"] for r in rows)
    # a UTC window 03:30-04:30Z is 09:00-10:00 IST
    utc = R.hourly(lg, "2026-11-20T03:30:00+00:00", "2026-11-20T04:30:00+00:00")
    assert {r["Time"] for r in utc} == {"2026-11-20 09:00-10:00"}
    assert len(utc) == 4  # B2 sms 09:00 (failed, still an attempt), B1 sms 09:15, B3 sms 09:30, B1 whatsapp 09:45


def test_period_filter_and_campaign_filter(lg):
    assert {r["Customer"] for r in R.ftd(lg, D2, D2)} == {"B1"}
    assert R.ftd(lg, D, D2, campaign_id="NOPE") == []
    assert R.aici(lg, D, D2, campaign_id="C1") == R.aici(lg, D, D2)
    with pytest.raises(ValueError):
        R.window(lg, "not-a-date", None)


def test_no_pii_and_hashed_ids(lg, tmp_path):
    files = R.export_pack(R.report_pack(lg, D, D2), tmp_path / "pack")
    text = "".join(p.read_text(encoding="utf-8") for p in files)
    for pii in ("Priya", "Testname", "+910000000000", "example.invalid"):
        assert pii not in text
    hashed = R.aici(lg, D, D2, hash_ids=True, salt="share-1")
    assert all(r["Customer"].startswith("CUST-") for r in hashed)
    assert {r["Customer"] for r in hashed}.isdisjoint({"B1", "B2", "B3"})
    assert len({r["Customer"] for r in hashed}) == 3
    other = R.aici(lg, D, D2, hash_ids=True, salt="share-2")
    assert {r["Customer"] for r in other}.isdisjoint({r["Customer"] for r in hashed})


def test_csv_round_trip(lg, tmp_path):
    pack = R.report_pack(lg, D, D2)
    paths = R.export_pack(pack, tmp_path / "pack")
    assert sorted(p.name for p in paths) == sorted(f"{n}.csv" for n in R.SHEETS)
    for name, rows in pack.items():
        with open(tmp_path / "pack" / f"{name}.csv", encoding="utf-8") as f:
            back = list(csv.DictReader(f))
        assert back == [{k: str(v) for k, v in r.items()} for r in rows]
    empty = R.export_csv([], tmp_path / "empty.csv", R.SHEET_COLUMNS["ftd"])
    assert empty.read_text().strip() == "Customer,Channel,Latest Best Disposition"


# ---------- CM-02 daily per-segment ----------
def test_daily_segment_report(lg):
    rep = R.daily_segment_report(lg, "C1", D)
    assert rep["provenance"] == "SIMULATED" and rep["day"] == "2026-11-20"
    s = by(rep["segments"], "band", "channel")
    assert set(s) == {("B1", "sms"), ("B1", "whatsapp"), ("B1", "ivr"), ("B2", "sms"), ("B2", "ivr"),
                      ("B2", "whatsapp"), ("B3", "sms")}
    b1_sms = s[("B1", "sms")]
    assert (b1_sms["attempts"], b1_sms["reach"]) == (1, 1)
    assert (b1_sms["spend_reserved_paise"], b1_sms["spend_actual_paise"], b1_sms["spend_committed_paise"]) == (20, 18, 18)
    assert b1_sms["spend_actual_inr"] == 0.18
    assert (s[("B1", "ivr")]["attempts"], s[("B1", "ivr")]["reach"]) == (1, 1)
    b2_sms = s[("B2", "sms")]
    assert (b2_sms["attempts"], b2_sms["reach"], b2_sms["spend_reserved_paise"]) == (1, 0, 0)  # rejected: released
    wa = s[("B2", "whatsapp")]
    assert (wa["meaningful_replies"], wa["responders"], wa["unreconciled"]) == (1, 1, 1)
    assert wa["spend_committed_paise"] == 40 and wa["replies_per_1000_inr"] == 2500.0
    assert s[("B3", "sms")]["meaningful_replies"] == 0          # STOP is not a reply
    assert s[("B1", "whatsapp")]["replies_per_1000_inr"] == 0.0
    assert all(r["cap_breaches"] == 0 for r in rep["segments"])
    # payments are separate, per band; the payment does not move B1 to CLEARED for its own day
    assert rep["payments_by_band"] == [{"band": "B1", "verified_payments": 1, "verified_paise": 100_000,
                                        "verified_inr": 1000.0, "payers": 1, "reversals": 0, "reversed_paise": 0,
                                        "reversed_inr": 0.0}]
    assert "verified_payments" not in rep["segments"][0]
    t = rep["totals"]
    assert (t["attempts"], t["reach"], t["meaningful_replies"], t["verified_payments"]) == (7, 3, 1, 1)
    # cohorts from risk_tag
    c = by(rep["cohorts"], "cohort", "channel")
    assert set(k[0] for k in c) == {"C1", "C3", "UNTAGGED"}
    assert c[("C3", "whatsapp")]["meaningful_replies"] == 1
    assert [p["cohort"] for p in rep["payments_by_cohort"]] == ["C1"]
    assert rep["learning"]["available"] is False
    day2 = R.daily_segment_report(lg, "C1", D2)
    assert by(day2["segments"], "band", "channel")[("B1", "sms")]["attempts"] == 1
    assert day2["payments_by_band"] == []
    with pytest.raises(KeyError):
        R.daily_segment_report(lg, "NOPE", D)


def test_daily_segment_without_risk_tags(lg):
    lg._conn().execute("UPDATE loans SET risk_tag=NULL")
    rep = R.daily_segment_report(lg, "C1", D)
    assert rep["cohorts"] == [] and rep["payments_by_cohort"] == []
    assert any("risk_tag" in n for n in rep["notes"])


def test_reversal_reported_separately(lg):
    lg.record_payment_reversed("PAY-1", at(16, 0, D2), "bounced")
    assert R.daily_segment_report(lg, "C1", D)["payments_by_band"] == []  # reversed since: not verified
    p = R.daily_segment_report(lg, "C1", D2)["payments_by_band"]
    assert [(r["band"], r["reversals"], r["reversed_paise"]) for r in p] == [("B1", 1, 100_000)]


# ---------- optional tables from other branches ----------
def test_optional_tables_detected_and_used(lg):
    c = lg._conn()
    assert R.optional_tables(lg) == {"channel_events": False, "learner_arms": False, "decision_scores": False,
                                     "learner_context": False}
    c.execute("CREATE TABLE channel_events (dedupe_key TEXT PRIMARY KEY, instruction_id TEXT, channel TEXT,"
              " event_type TEXT, status TEXT, received_at TEXT, envelope TEXT)")

    def ce(key, iid, ch, et, status, occurred, **env):
        c.execute("INSERT INTO channel_events VALUES (?,?,?,?,?,?,?)",
                  (key, iid, ch, et, status, "2026-11-25T00:00:00+05:30",
                   json.dumps({"occurred_at": occurred, "ignored_reason": None, **env})))
    ce("k1", "T-B1-WA", "whatsapp", "provider.delivery", "DELIVERED", "2026-11-20T09:46:00+05:30")
    ce("k2", "T-B1-WA", "whatsapp", "provider.delivery", "READ", "2026-11-20T09:50:00+05:30")
    ce("k3", "T-B1-WA", "whatsapp", "provider.delivery", "CLICKED", "2026-11-20T09:55:00+05:30")
    ce("k4", "T-B1-SMS", "sms", "provider.delivery", "READ", "2026-11-20T09:20:00+05:30")  # SMS read: never tracked
    ce("k5", "T-B2-IVR", "ivr", "call.outcome", "BUSY", "2026-11-20T12:31:00+05:30")
    ce("k6", "T-B3-SMS", "sms", "borrower.message", "REPLY", "2026-11-20T10:59:00+05:30", stop=True, text="STOP")
    f = {k: r["Latest Best Disposition"] for k, r in by(R.ftd(lg, D, D2), "Customer", "Channel").items()}
    assert f[("B1", "whatsapp")] == "Clicked" and f[("B1", "sms")] == "Delivered"
    assert f[("B2", "ivr")] == "Busy" and f[("B3", "sms")] == "Sent"
    trail = by(R.trails(lg, D, D), "Customer", "Channel")[("B1", "whatsapp")]["Disposition"]
    assert trail == "Sent > Delivered > Read > Clicked"
    dump = [r for r in R.data_dump(lg, D, D) if r["Customer"] == "B1" and r["Channel"] == "whatsapp"]
    assert [(r["Time"], r["Disposition"]) for r in dump] == [("09:45:00", "Sent"), ("09:46:00", "Delivered"),
                                                             ("09:50:00", "Read"), ("09:55:00", "Clicked")]
    assert by(R.aici(lg, D, D), "Customer", "Channel")[("B1", "whatsapp")]["Connected Intensity"] == 1
    # a reply seen by both the adapter and the spine is one Responded row
    ce("k7", "T-B2-WA", "whatsapp", "borrower.message", "REPLY", "2026-11-20T13:20:00+05:30", text="I will pay")
    assert sum(r["Disposition"] == "Responded" for r in R.data_dump(lg, D, D)) == 1
    assert R.daily_segment_report(lg, "C1", D)["totals"]["meaningful_replies"] == 1

    # K09 learner tables
    c.execute("CREATE TABLE learner_arms (band TEXT, channel TEXT, slot TEXT, alpha REAL, beta REAL, n_obs INTEGER,"
              " updated_at TEXT, PRIMARY KEY (band, channel, slot))")
    c.execute("INSERT INTO learner_arms VALUES ('B1','whatsapp','17-19', 6, 14, 12, NULL)")
    c.execute("INSERT INTO learner_arms VALUES ('B1','sms','10-12', 2, 18, 4, NULL)")
    c.execute("CREATE TABLE decision_scores (decision_id TEXT PRIMARY KEY, at TEXT, borrower_id TEXT, band TEXT,"
              " mode TEXT, model_version TEXT, action TEXT, chosen_channel TEXT, chosen_slot TEXT, reason TEXT,"
              " scores_json TEXT)")
    for i, (bid, ch, slot, when) in enumerate([("B1", "whatsapp", "17-19", "2026-11-20T08:00:00+05:30"),
                                               ("B2", "whatsapp", "17-19", "2026-11-20T08:01:00+05:30"),
                                               ("B3", "sms", "10-12", "2026-11-20T08:02:00+05:30"),
                                               ("B1", "sms", "10-12", "2026-11-21T08:00:00+05:30"),   # other day
                                               ("ZZ", "sms", "10-12", "2026-11-20T08:00:00+05:30")]):  # not enrolled
        c.execute("INSERT INTO decision_scores VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                  (f"D{i}", when, bid, "B1", "learn", "v1", "SEND", ch, slot, "", "{}"))
    c.execute("CREATE TABLE learner_context (k TEXT, v TEXT)")
    learn = R.daily_segment_report(lg, "C1", D)["learning"]
    assert learn["available"] and all(learn["tables"].values())
    arms = by(learn["arms"], "channel")
    assert arms["whatsapp"]["posterior_mean"] == 0.3 and arms["whatsapp"]["n"] == 12
    assert learn["decisions_today"] == 3
    share = by(learn["decision_share"], "chosen_channel")
    assert share["whatsapp"]["decisions"] == 2 and share["whatsapp"]["share"] == round(2 / 3, 4)
    assert learn["learner_context"] == {"rows": 0, "columns": ["k", "v"]}
    sheets = R.daily_segment_sheets(R.daily_segment_report(lg, "C1", D))
    assert {"daily_segment", "daily_payments", "daily_learning_arms", "daily_decision_share"} <= set(sheets)


def test_learner_tables_with_unexpected_shape_do_not_fail(lg):
    c = lg._conn()
    c.execute("CREATE TABLE learner_arms (arm TEXT)")
    c.execute("CREATE TABLE decision_scores (decision_id TEXT)")
    learn = R.daily_segment_report(lg, "C1", D)["learning"]
    assert learn["available"] and learn["arms"] == [] and learn["decision_share"] == []


# ---------- API ----------
@pytest.fixture
def api_client(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIROS_DB", str(tmp_path / "api.db"))
    import kairos_backend.api as api
    api = importlib.reload(api)
    build(api.ledger)
    return TestClient(api.app)


def test_api_report_routes(api_client):
    c = api_client
    for name, key in (("aici", "aici"), ("data-dump", "data_dump"), ("trails", "trails"), ("ftd", "ftd"),
                      ("hourly", "hourly")):
        j = c.get(f"/reports/{name}", params={"start": "2026-11-20", "end": "2026-11-21"}).json()
        assert j["provenance"] == "SIMULATED" and j["columns"] == R.SHEET_COLUMNS[key] and j["count"] > 0, name
        r = c.get(f"/reports/{name}", params={"start": "2026-11-20", "end": "2026-11-21", "format": "csv"})
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
        back = list(csv.DictReader(io.StringIO(r.text)))
        assert back == [{k: str(v) for k, v in row.items()} for row in j["rows"]]
    ftd = c.get("/reports/ftd", params={"start": "2026-11-20", "end": "2026-11-20"}).json()["rows"]
    assert {"Customer": "B2", "Channel": "whatsapp", "Latest Best Disposition": "Responded"} in ftd
    hashed = c.get("/reports/aici", params={"hash_ids": "true"}).json()["rows"]
    assert all(r["Customer"].startswith("CUST-") for r in hashed)
    assert c.get("/reports/nope").status_code == 404
    assert c.get("/reports/aici", params={"format": "xml"}).status_code == 422
    assert c.get("/reports/aici", params={"start": "yesterday"}).status_code == 422

    d = c.get("/reports/daily-segment", params={"campaign_id": "C1", "day": "2026-11-20"}).json()
    assert d["totals"]["attempts"] == 7 and d["payments_by_band"][0]["verified_paise"] == 100_000
    csv_d = c.get("/reports/daily-segment", params={"campaign_id": "C1", "day": "2026-11-20", "format": "csv"})
    assert csv_d.text.splitlines()[0] == ",".join(R.DAILY_SEGMENT_COLUMNS)
    assert c.get("/reports/daily-segment", params={"campaign_id": "NOPE", "day": "2026-11-20"}).status_code == 404
