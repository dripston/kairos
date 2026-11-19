"""K20 email channel: eligibility, delivery (reach, never a reply), bounce and unsubscribe
suppression, idempotent callbacks, sandbox-only SMTP, deterministic REPLAY. No network."""
import json
import smtplib
import socket
from datetime import date, datetime, timedelta

import pytest

from kairos_backend.email_channel import (
    EmailConfigError, EmailEvent, EmailEvents, ReplayEmailProvider, SmtpConfig, SmtpSandboxProvider,
    address_hash, email_eligible, email_provider_from_env, restore_email_consent, suppression_check)
from kairos_backend.executor import Executor
from kairos_backend.ledger import Instruction, Ledger

D = date(2026, 11, 20)  # a Friday — a contact day
ADDR = {"B1": "b1@example.test", "B2": "b2@example.test"}
SMTP_ENV = {"KAIROS_SMTP_HOST": "smtp.sandbox.test", "KAIROS_SMTP_PORT": "587", "KAIROS_SMTP_USER": "u",
            "KAIROS_SMTP_PASSWORD": "pw", "KAIROS_SMTP_FROM": "kairos@example.test", "KAIROS_SMTP_SANDBOX": "1"}


def at(hh, mm=0, day=D):
    return datetime(day.year, day.month, day.day, hh, mm)


@pytest.fixture
def ledger(policy, tmp_path):
    lg = Ledger(policy, tmp_path / "k20.db")
    lg.upsert_campaign("C1", budget_per_case_inr=100, allocated_cases=10, start_date=D, end_date=D + timedelta(days=10))
    for t, ch, lang in [("SMS_EN", "sms", "en"), ("WA_HI", "whatsapp", "hi"), ("EM_EN", "email", "en")]:
        lg.upsert_template(t, ch, lang)
    for bid in ("B1", "B2"):
        lg.upsert_borrower(bid, dpd=12, dpd_updated_at=at(7),
                           consent={"sms": True, "whatsapp": True, "email": True, "ivr": True, "voicebot": False},
                           consent_version={"email": "v1", "sms": "v1"})
        lg.upsert_obligation(f"{bid}-A", bid, "C1", overdue_inr=5000)
    yield lg
    lg.close()


def ins(bid="B1", ch="email", t=None, cost=0.05):
    tpl = {"sms": "SMS_EN", "whatsapp": "WA_HI", "email": "EM_EN"}[ch]
    return Instruction(bid, f"{bid}-A", "C1", ch, tpl, t or at(10), cost)


def send_email(ledger, provider, t=at(10), bid="B1"):
    r = ledger.reserve(ins(bid, "email", t))
    assert r.reserved
    out = Executor(ledger, {"email": provider}).dispatch(r.instruction_id, t)
    return r.instruction_id, out


def email_audit(ledger, bid="B1"):
    return [a for a in ledger.audit_log(borrower_id=bid) if a["type"].startswith("EMAIL_")]


# ---------- delivery: reach only ----------
def test_delivery_is_reach_and_never_a_reply(ledger):
    prov = ReplayEmailProvider(ADDR)
    iid, out = send_email(ledger, prov)
    assert out.outcome == "SENT"
    (ev,) = prov.drain()
    env = EmailEvents(ledger).apply(ev)
    assert env["event_type"] == "provider.delivery" and env["provenance"] == "REPLAY"
    assert env["payload"]["status"] == "DELIVERED" and env["payload"]["counts_as_reply"] is False
    assert env["payload"]["reward"] is None
    assert ledger.touch(iid)["status"] == "DELIVERED"
    state = ledger.borrower_state("B1")
    assert state["stop_for_day"] == []  # a reply would stop the day; delivery must not
    assert not [a for a in ledger.audit_log(borrower_id="B1") if a["type"] == "RESPONSE"]
    assert ledger.reserve(ins(ch="sms", t=at(11), cost=0.2)).reserved
    assert [a["type"] for a in email_audit(ledger)] == ["EMAIL_DELIVERED"]  # one status event


# ---------- hard bounce ----------
def test_hard_bounce_blocks_address_and_cancels_queued_email_only(ledger):
    prov = ReplayEmailProvider(ADDR, script={"b1@example.test": "hard_bounce"})
    iid, _ = send_email(ledger, prov)
    later_email = ledger.reserve(ins(ch="email", t=at(14))).instruction_id
    sms = ledger.reserve(ins(ch="sms", t=at(11), cost=0.2)).instruction_id
    wa = ledger.reserve(ins(ch="whatsapp", t=at(12), cost=0.4)).instruction_id
    committed = ledger.campaign_state("C1")["committed_inr"]

    env = EmailEvents(ledger).apply(prov.drain()[0])
    assert env["event_type"] == "provider.delivery" and env["payload"]["status"] == "HARD_BOUNCE"
    assert env["payload"]["canceled_queued"] == 1 and env["payload"]["replan"] is True
    assert env["payload"]["counts_as_reply"] is False
    assert ledger.touch(later_email)["status"] == "CANCELED"
    assert ledger.touch(later_email)["reason"] == "EMAIL_HARD_BOUNCE"
    assert ledger.touch(sms)["status"] == ledger.touch(wa)["status"] == "RESERVED"
    assert ledger.touch(iid)["status"] == "SENT"  # never downgraded; it was attempted
    assert ledger.campaign_state("C1")["committed_inr"] == pytest.approx(committed - 0.05)

    # stored as a hash, never the raw address
    conn = ledger._conn()
    row = conn.execute("SELECT * FROM email_suppression").fetchone()
    assert (row["borrower_id"], row["address_hash"], row["reason"]) == ("B1", address_hash("b1@example.test"), "HARD_BOUNCE")
    assert "b1@example.test" not in "\n".join(conn.iterdump())

    # later email candidates fail eligibility and the Gate; SMS / WhatsApp stay eligible
    assert email_eligible(ledger, "B1") == (False, "EMAIL_HARD_BOUNCE")
    d = ledger.reserve(ins(ch="email", t=at(16))).decision
    assert (d.decision, d.reason) == ("WAIT", "NO_CONSENT")
    assert ledger.reserve(ins(ch="sms", t=at(15), cost=0.2)).reserved
    assert ledger.reserve(ins(ch="whatsapp", t=at(16), cost=0.4)).reserved
    assert ledger.borrower_state("B1")["consent_version"]["email"].startswith("hard_bounce@")
    assert email_eligible(ledger, "B2") == (True, "ELIGIBLE")  # other borrowers unaffected


def test_hard_bounced_address_stays_blocked_but_a_new_address_can_reenter(ledger):
    EmailEvents(ledger).apply(EmailEvent("hard_bounce", at(10), borrower_id="B1", address="B1@Example.test",
                                         event_id="dsn-1"))
    assert restore_email_consent(ledger, "B1", "b1@example.test", "v2", at(11)) == (False, "EMAIL_ADDRESS_SUPPRESSED")
    assert restore_email_consent(ledger, "B1", "b1.new@example.test", "v2", at(11)) == (True, "ELIGIBLE")
    assert email_eligible(ledger, "B1", "b1.new@example.test") == (True, "ELIGIBLE")
    assert email_eligible(ledger, "B1", "b1@example.test") == (False, "EMAIL_ADDRESS_SUPPRESSED")
    # if the CRM still hands the provider the bounced address, the send is refused
    prov = ReplayEmailProvider(ADDR, is_suppressed=suppression_check(ledger))
    iid, out = send_email(ledger, prov, at(12))
    assert (out.outcome, out.reason) == ("FAILED", "ADDRESS_SUPPRESSED")
    assert prov.sent == []


# ---------- unsubscribe ----------
def test_unsubscribe_suppresses_email_only_and_records_consent_version(ledger):
    prov = ReplayEmailProvider(ADDR, script={"b1@example.test": "unsubscribe"})
    iid, _ = send_email(ledger, prov)
    queued_email = ledger.reserve(ins(ch="email", t=at(14))).instruction_id
    sms = ledger.reserve(ins(ch="sms", t=at(11), cost=0.2)).instruction_id

    delivered, unsub = prov.drain()
    assert (delivered.kind, unsub.kind) == ("delivered", "unsubscribe")
    events = EmailEvents(ledger)
    events.apply(delivered)
    env = events.apply(unsub)
    assert env["event_type"] == "consent.changed" and env["provenance"] == "REPLAY"
    assert env["payload"]["scope"] == "email" and env["payload"]["consent"] == {"email": False}
    assert env["payload"]["previous_consent_version"] == "v1"

    state = ledger.borrower_state("B1")
    assert state["consent"]["email"] is False and state["consent"]["sms"] is True
    assert state["consent"]["whatsapp"] is True and state["opted_out"] == 0
    assert state["consent_version"]["email"].startswith("unsubscribed@")
    assert state["consent_version"]["sms"] == "v1"
    assert ledger.touch(queued_email)["status"] == "CANCELED"
    assert ledger.touch(sms)["status"] == "RESERVED"
    assert ledger.touch(iid)["status"] == "DELIVERED"
    assert email_eligible(ledger, "B1") == (False, "EMAIL_UNSUBSCRIBED")
    assert ledger.reserve(ins(ch="email", t=at(16))).decision.reason == "NO_CONSENT"
    assert ledger.reserve(ins(ch="whatsapp", t=at(12), cost=0.4)).reserved
    assert [a["type"] for a in reversed(email_audit(ledger))] == ["EMAIL_DELIVERED", "EMAIL_UNSUBSCRIBE"]


def test_unsubscribe_scope_all_is_stop(ledger):
    sms = ledger.reserve(ins(ch="sms", t=at(11), cost=0.2)).instruction_id
    env = EmailEvents(ledger).apply(EmailEvent("unsubscribe", at(10), borrower_id="B1", scope="all"))
    assert env["payload"]["opted_out"] is True and env["payload"]["canceled_queued"] == 1
    assert ledger.touch(sms)["status"] == "CANCELED"
    assert email_eligible(ledger, "B1") == (False, "STOP_OPT_OUT")


def test_hard_bounce_after_unsubscribe_keeps_unsubscribe_reason(ledger):
    events = EmailEvents(ledger)
    events.apply(EmailEvent("unsubscribe", at(10), borrower_id="B1"))
    events.apply(EmailEvent("hard_bounce", at(11), borrower_id="B1", address="b1@example.test"))
    assert email_eligible(ledger, "B1") == (False, "EMAIL_UNSUBSCRIBED")
    assert email_eligible(ledger, "B1", "b1@example.test")[0] is False


# ---------- soft bounce ----------
def test_soft_bounce_is_audit_only(ledger):
    prov = ReplayEmailProvider(ADDR, script={"b1@example.test": "soft_bounce"})
    iid, _ = send_email(ledger, prov)
    queued = ledger.reserve(ins(ch="email", t=at(14))).instruction_id
    env = EmailEvents(ledger).apply(prov.drain()[0])
    assert env["payload"]["status"] == "SOFT_BOUNCE" and env["payload"]["canceled_queued"] == 0
    assert ledger.touch(iid)["status"] == "SENT" and ledger.touch(queued)["status"] == "RESERVED"
    assert email_eligible(ledger, "B1") == (True, "ELIGIBLE")
    assert len(email_audit(ledger)) == 1


# ---------- idempotency ----------
def test_duplicate_callbacks_are_noops(ledger):
    prov = ReplayEmailProvider(ADDR, script={"b1@example.test": "hard_bounce"})
    iid, _ = send_email(ledger, prov)
    events = EmailEvents(ledger)
    ev = EmailEvent("delivered", at(10, 2), instruction_id=iid, event_id="cb-1")
    first = events.apply(ev)
    again = events.apply(ev)
    assert first["applied"] and not first["duplicate"]
    assert again["duplicate"] and not again["applied"] and again["event_id"] == first["event_id"]

    bounce = prov.drain()[0]
    events.apply(bounce)
    committed = ledger.campaign_state("C1")["committed_inr"]
    assert events.apply(bounce)["duplicate"]
    unsub = EmailEvent("unsubscribe", at(12), borrower_id="B1")
    events.apply(unsub)
    version = ledger.borrower_state("B1")["consent_version"]["email"]
    assert events.apply(EmailEvent("unsubscribe", at(13), borrower_id="B1"))["duplicate"]  # same key, no event_id

    assert ledger.borrower_state("B1")["consent_version"]["email"] == version
    assert ledger.campaign_state("C1")["committed_inr"] == pytest.approx(committed)
    assert ledger._conn().execute("SELECT COUNT(*) FROM email_suppression").fetchone()[0] == 1
    assert sorted(a["type"] for a in email_audit(ledger)) == ["EMAIL_DELIVERED", "EMAIL_HARD_BOUNCE", "EMAIL_UNSUBSCRIBE"]


def test_event_validation(ledger):
    events = EmailEvents(ledger)
    with pytest.raises(ValueError):
        events.apply(EmailEvent("opened", at(10), borrower_id="B1"))
    with pytest.raises(ValueError):
        events.apply(EmailEvent("hard_bounce", at(10), borrower_id="B1"))  # no address
    sms = ledger.reserve(ins(ch="sms", t=at(11), cost=0.2)).instruction_id
    with pytest.raises(ValueError):
        events.apply(EmailEvent("delivered", at(11), instruction_id=sms))


# ---------- sandbox SMTP ----------
class FakeSMTP:
    """Records the SMTP conversation. fail_on: 'connect' | 'send' | 'auth' | None."""

    def __init__(self, fail_on=None):
        self.fail_on, self.calls, self.messages = fail_on, [], []

    def __call__(self, host, port, timeout=None):
        self.calls.append(("connect", host, port))
        if self.fail_on == "connect":
            raise ConnectionRefusedError("no route")
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.calls.append(("quit",))
        return False

    def starttls(self):
        self.calls.append(("starttls",))

    def login(self, user, password):
        self.calls.append(("login", user))
        if self.fail_on == "auth":
            raise smtplib.SMTPAuthenticationError(535, b"bad credentials")

    def send_message(self, msg):
        self.calls.append(("send",))
        if self.fail_on == "send":
            raise socket.timeout("timed out mid-DATA")
        self.messages.append(msg)
        return {}


def test_smtp_requires_full_env_and_sandbox_flag():
    with pytest.raises(EmailConfigError):
        SmtpConfig.from_env({})
    with pytest.raises(EmailConfigError):
        SmtpConfig.from_env({k: v for k, v in SMTP_ENV.items() if k != "KAIROS_SMTP_PASSWORD"})
    with pytest.raises(EmailConfigError):
        SmtpConfig.from_env({**SMTP_ENV, "KAIROS_SMTP_SANDBOX": "0"})
    assert isinstance(email_provider_from_env(ADDR, env={}), ReplayEmailProvider)
    assert isinstance(email_provider_from_env(ADDR, env={**SMTP_ENV, "KAIROS_SMTP_SANDBOX": ""}), ReplayEmailProvider)
    prov = email_provider_from_env(ADDR, env=SMTP_ENV, smtp_factory=FakeSMTP())
    assert isinstance(prov, SmtpSandboxProvider) and prov.config.allowed_domains == ("example.test",)
    assert "pw" not in repr(prov.config)


def test_smtp_refuses_non_sandbox_recipients_without_connecting(ledger):
    smtp = FakeSMTP()
    prov = SmtpSandboxProvider.from_env({"B1": "real.person@gmail.com", "B2": "x@example.test.evil.com"},
                                        env=SMTP_ENV, smtp_factory=smtp)
    _, out = send_email(ledger, prov, bid="B1")
    assert (out.outcome, out.reason) == ("FAILED", "RECIPIENT_NOT_SANDBOX")
    _, out = send_email(ledger, prov, bid="B2")
    assert out.reason == "RECIPIENT_NOT_SANDBOX"
    assert smtp.calls == []  # no network at all
    assert prov.recipient_allowed("a@mail.example.test")
    custom = SmtpSandboxProvider.from_env(ADDR, env={**SMTP_ENV, "KAIROS_SMTP_ALLOWED_DOMAINS": "sandbox.test, @qa.test"},
                                          smtp_factory=smtp)
    assert custom.recipient_allowed("a@qa.test") and not custom.recipient_allowed("b1@example.test")


def test_smtp_sends_to_sandbox_recipient(ledger):
    smtp = FakeSMTP()
    prov = SmtpSandboxProvider.from_env(ADDR, env=SMTP_ENV, smtp_factory=smtp)
    iid, out = send_email(ledger, prov)
    assert out.outcome == "SENT" and ledger.touch(iid)["status"] == "SENT"
    assert [c[0] for c in smtp.calls] == ["connect", "starttls", "login", "send", "quit"]
    (msg,) = smtp.messages
    assert msg["To"] == "b1@example.test" and msg["From"] == "kairos@example.test"
    assert msg["X-Kairos-Environment"] == "SANDBOX" and msg["X-Kairos-Instruction-Id"] == iid
    assert out.provider_ref == f"<{iid}@kairos.sandbox>"


def test_smtp_connect_error_is_unknown_then_released(ledger):
    prov = SmtpSandboxProvider.from_env(ADDR, env=SMTP_ENV, smtp_factory=FakeSMTP("connect"))
    iid, out = send_email(ledger, prov)
    assert out.outcome == "UNCERTAIN" and ledger.touch(iid)["status"] == "UNCERTAIN"
    assert ledger.campaign_state("C1")["committed_inr"] == pytest.approx(0.05)  # still counted
    ex = Executor(ledger, {"email": prov})
    assert ex.resolve_uncertain(iid) == "FAILED"  # never handed over → release, replan a NEW instruction
    assert ledger.campaign_state("C1")["committed_inr"] == pytest.approx(0)


def test_smtp_error_mid_send_stays_uncertain(ledger):
    smtp = FakeSMTP("send")
    prov = SmtpSandboxProvider.from_env(ADDR, env=SMTP_ENV, smtp_factory=smtp)
    iid, out = send_email(ledger, prov)
    assert out.outcome == "UNCERTAIN"
    ex = Executor(ledger, {"email": prov})
    assert ex.resolve_uncertain(iid) == "UNCERTAIN"  # relay may have it: keep cap slot + cost, no resend
    assert ledger.touch(iid)["status"] == "UNCERTAIN"
    assert [c[0] for c in smtp.calls].count("send") == 1
    assert ledger.campaign_state("C1")["committed_inr"] == pytest.approx(0.05)


def test_smtp_auth_failure_is_rejected(ledger):
    prov = SmtpSandboxProvider.from_env(ADDR, env=SMTP_ENV, smtp_factory=FakeSMTP("auth"))
    _, out = send_email(ledger, prov)
    assert (out.outcome, out.reason) == ("FAILED", "SMTP_AUTH_FAILED")


# ---------- replay ----------
def test_replay_is_deterministic_and_labelled():
    addrs = {"B1": "hard_bounce.b1@example.test", "B2": "b2@example.test"}  # outcome tag in the local part

    def run():
        p = ReplayEmailProvider(addrs)
        refs = [p.send({"instruction_id": f"I{i}", "borrower_id": b, "scheduled_at": at(10).isoformat()})
                for i, b in enumerate(("B1", "B2"))]
        return [r.provider_ref for r in refs], [(e.kind, e.event_id, e.at, e.provenance) for e in p.drain()]

    assert run() == run()
    _, evs = run()
    assert [(e[0], e[1]) for e in evs] == [("delivered", "replay:I1:delivered"), ("hard_bounce", "replay:I0:hard_bounce")]
    assert all(e[3] == "REPLAY" for e in evs)


def test_replay_timeout_resolves_without_resend_and_rejected_fails(ledger):
    prov = ReplayEmailProvider(ADDR, script={"b1@example.test": "timeout", "b2@example.test": "rejected"})
    iid, out = send_email(ledger, prov)
    assert out.outcome == "UNCERTAIN"
    assert Executor(ledger, {"email": prov}).resolve_uncertain(iid) == "SENT"
    assert prov.sent == [iid]
    _, out = send_email(ledger, prov, bid="B2")
    assert (out.outcome, out.reason) == ("FAILED", "REPLAY_REJECTED")
    assert prov.drain() == []


def test_envelope_is_json_serializable(ledger):
    prov = ReplayEmailProvider(ADDR)
    send_email(ledger, prov)
    env = EmailEvents(ledger).apply(prov.drain()[0])
    assert json.loads(json.dumps(env))["payload"]["status"] == "DELIVERED"
