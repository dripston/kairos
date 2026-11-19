"""Android SMS demo adapter (SMS Gateway for Android / SMSGate). A fake transport stands in for the phone;
no test opens a network connection (urlopen is blocked by an autouse fixture). Numbers are placeholders."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import urllib.error
import urllib.request

import pytest

from kairos_backend.channels import (AndroidSmsGatewayProvider, DemoSafetyError, MessagesAppSmsProvider,
                                     MockChannelProvider, apply_all, build_providers, select_sms_provider)
from kairos_backend.channels import demo_recipients as dr
from kairos_backend.channels.android_sms import gateway_message_id, verify_webhook_signature
from kairos_backend.executor import Executor
from tests.test_channels import lg, send  # noqa: F401  (ledger fixture + helper)
from tests.test_ledger import at, ins

PHONE = "+910000000001"
OTHER = "+910000000002"
BASE = "http://192.0.2.10:8080"          # TEST-NET-1 documentation address
ENV = {"KAIROS_DEMO_ANDROID_SMS": "1", "KAIROS_DEMO_ALLOWLIST": PHONE, "SMSGATE_MODE": "local",
       "SMSGATE_URL": BASE, "SMSGATE_USER": "sms", "SMSGATE_PASSWORD": "pw-test-1234567"}
AUTH = "Basic " + base64.b64encode(b"sms:pw-test-1234567").decode()


@pytest.fixture(autouse=True)
def no_network(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise AssertionError("tests must never touch the network")
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    # never read the operator's real ~/.kairos/demo_recipients.json
    monkeypatch.setenv("KAIROS_DEMO_RECIPIENTS_FILE", str(tmp_path / "none.json"))


def env(**kw):
    return {**ENV, "KAIROS_DEMO_RECIPIENTS_FILE": "/nonexistent/demo_recipients.json", **kw}


class FakeGateway:
    """Records requests; answers like the SMSGate API (POST /messages -> 202, GET /messages/{id})."""

    def __init__(self, post=(202, None), get=(200, {"state": "Delivered"})):
        self.calls, self.post, self.get = [], post, get

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append({"method": method, "url": url, "headers": headers,
                           "json": json.loads(body) if body else None})
        if method == "POST":
            code, resp = self.post
            if resp is None:
                sent = json.loads(body)
                resp = {"id": sent["id"], "deviceId": "dev1", "state": "Pending", "isHashed": False,
                        "isEncrypted": False, "recipients": [{"phoneNumber": sent["phoneNumbers"][0], "state": "Pending"}],
                        "states": {"Pending": "2026-11-19T10:00:00.000+05:30"}}
            return code, resp
        code, resp = self.get
        return code, {"id": url.rsplit("/", 1)[-1], "deviceId": "dev1", "recipients": [], **resp}


def touch(iid="INS-a1", to=PHONE, tpl="DEMO_SMS_HELLO", **kw):
    return {"instruction_id": iid, "borrower_id": "B1", "channel": "sms", "template_id": tpl, "to": to, **kw}


# ---------------------------------------------------------------- rails
def test_refuses_without_flag():
    gw = FakeGateway()
    r = AndroidSmsGatewayProvider(env=env(KAIROS_DEMO_ANDROID_SMS="0"), transport=gw, dry_run=False).send(touch())
    assert r.status == "REJECTED" and r.error.startswith("DEMO_DISABLED") and gw.calls == []


def test_refuses_number_not_allowlisted_and_masks_it():
    gw = FakeGateway()
    r = AndroidSmsGatewayProvider(env=env(), transport=gw, dry_run=False).send(touch(to=OTHER))
    assert r.status == "REJECTED" and r.error.startswith("NOT_ALLOWLISTED") and gw.calls == []
    assert "0000000002" not in r.error and r.error.endswith("0002")


def test_runtime_demo_recipient_file_is_part_of_the_allowlist(tmp_path):
    f = str(tmp_path / "r.json")
    dr.add(OTHER, {"KAIROS_DEMO_RECIPIENTS_FILE": f})
    gw = FakeGateway()
    p = AndroidSmsGatewayProvider(env=env(KAIROS_DEMO_RECIPIENTS_FILE=f), transport=gw, dry_run=False)
    assert p.send(touch(to=OTHER)).status == "ACCEPTED" and len(gw.calls) == 1


def test_refuses_free_text_and_unapproved_template():
    gw = FakeGateway()
    p = AndroidSmsGatewayProvider(env=env(), transport=gw, dry_run=False)
    assert p.send(touch(tpl=None, text="pay now!!")).error.startswith("TEMPLATE_NOT_APPROVED")
    assert p.send(touch(tpl="SMS_EN")).error.startswith("TEMPLATE_NOT_APPROVED")
    assert gw.calls == []


@pytest.mark.parametrize("drop,err", [("SMSGATE_PASSWORD", "MISSING_CREDENTIALS"), ("SMSGATE_USER", "MISSING_CREDENTIALS"),
                                      ("SMSGATE_URL", "MISSING_SMSGATE_URL")])
def test_refuses_without_config_from_env(drop, err):
    gw = FakeGateway()
    e = {k: v for k, v in env().items() if k != drop}
    assert AndroidSmsGatewayProvider(env=e, transport=gw, dry_run=False).send(touch()).error.startswith(err)
    assert gw.calls == []


def test_refuses_bad_mode_url_and_sim():
    gw = FakeGateway()
    for bad, err in [({"SMSGATE_MODE": "lan"}, "BAD_SMSGATE_MODE"), ({"SMSGATE_URL": "ftp://x"}, "BAD_SMSGATE_URL"),
                     ({"SMSGATE_URL": "http://u:p@192.0.2.10:8080"}, "BAD_SMSGATE_URL"),
                     ({"SMSGATE_MODE": "cloud", "SMSGATE_URL": "http://example.test"}, "BAD_SMSGATE_URL"),
                     ({"SMSGATE_SIM": "4"}, "BAD_SMSGATE_SIM")]:
        assert AndroidSmsGatewayProvider(env=env(**bad), transport=gw, dry_run=False).send(touch()).error.startswith(err)
    assert gw.calls == []


def test_dry_run_is_default_and_preview_is_masked():
    gw = FakeGateway()
    p = AndroidSmsGatewayProvider(env=env(), transport=gw)
    assert p.dry_run is True
    r = p.send(touch())
    assert (r.status, r.error) == ("REJECTED", "DRY_RUN") and gw.calls == [] and p.sent == {}
    (pv,) = p.dry_runs
    dumped = json.dumps(pv.request)
    assert "0000000001" not in dumped and "pw-test-1234567" not in dumped and AUTH not in dumped
    assert pv.request["json"]["phoneNumbers"] == ["+********0001"]
    assert pv.request["url"] == f"{BASE}/messages" and pv.recipient_masked == "+********0001"


# ---------------------------------------------------------------- request shape
def test_local_request_body_and_basic_auth():
    gw = FakeGateway()
    p = AndroidSmsGatewayProvider(env=env(SMSGATE_SIM="2"), transport=gw, dry_run=False)
    r = p.send(touch(tpl="DEMO_SMS_REMINDER_EN", params={"amount": "4,999"}))
    assert (r.status, r.provider_ref) == ("ACCEPTED", "INS-a1")
    (c,) = gw.calls
    assert (c["method"], c["url"]) == ("POST", "http://192.0.2.10:8080/messages")
    assert c["headers"] == {"Authorization": AUTH, "Content-Type": "application/json"}
    body = c["json"]
    assert body == {"id": "INS-a1", "textMessage": {"text": body["textMessage"]["text"]}, "phoneNumbers": [PHONE],
                    "withDeliveryReport": True, "simNumber": 2, "ttl": 3600}
    assert "Rs 4,999" in body["textMessage"]["text"] and "STOP" in body["textMessage"]["text"]
    # once only: a second send of the same instruction makes no request
    assert p.send(touch(tpl="DEMO_SMS_REMINDER_EN")).status == "ACCEPTED" and len(gw.calls) == 1


def test_cloud_mode_uses_public_api_and_ttl_can_be_disabled():
    gw = FakeGateway()
    e = {k: v for k, v in env(SMSGATE_MODE="cloud", SMSGATE_TTL_S="0").items() if k != "SMSGATE_URL"}
    AndroidSmsGatewayProvider(env=e, transport=gw, dry_run=False).send(touch())
    assert gw.calls[0]["url"] == "https://api.sms-gate.app/3rdparty/v1/messages"
    assert "ttl" not in gw.calls[0]["json"] and "simNumber" not in gw.calls[0]["json"]


def test_message_id_is_the_instruction_id_or_a_stable_hash():
    assert gateway_message_id("INS-0123456789abcdef") == "INS-0123456789abcdef"
    long_id = "DEMO:" + "x" * 60
    assert gateway_message_id(long_id) == gateway_message_id(long_id)
    assert len(gateway_message_id(long_id)) <= 36 and gateway_message_id(long_id).startswith("kairos-")


def test_error_responses():
    r = AndroidSmsGatewayProvider(env=env(), transport=FakeGateway(post=(401, {"message": "Unauthorized"})),
                                  dry_run=False).send(touch())
    assert r.status == "REJECTED" and r.error.startswith("SMSGATE_401")
    r = AndroidSmsGatewayProvider(env=env(), transport=FakeGateway(post=(503, {"message": "QueueLimitExceeded"})),
                                  dry_run=False).send(touch())
    assert r.status == "UNKNOWN"
    p = AndroidSmsGatewayProvider(env=env(), transport=FakeGateway(post=(409, {"message": "exists"})), dry_run=False)
    assert (p.send(touch()).status, p.sent) == ("ACCEPTED", {"INS-a1": "INS-a1"})   # gateway already has it

    def refused(*a):
        raise urllib.error.URLError(ConnectionRefusedError(61, "refused"))
    r = AndroidSmsGatewayProvider(env=env(), transport=refused, dry_run=False).send(touch())
    assert r.status == "REJECTED" and r.error.startswith("SMSGATE_UNREACHABLE")


# ---------------------------------------------------------------- status
@pytest.mark.parametrize("state,expected", [("Pending", "ACCEPTED"), ("Processed", "ACCEPTED"), ("Sent", "SENT"),
                                            ("Delivered", "DELIVERED"), ("Failed", "FAILED"), ("Cancelled", "FAILED"),
                                            ("Cancelling", "UNKNOWN"), ("Weird", "UNKNOWN")])
def test_status_maps_processing_state(state, expected):
    gw = FakeGateway(get=(200, {"state": state}))
    p = AndroidSmsGatewayProvider(env=env(), transport=gw, dry_run=False)
    assert p.status("INS-a1", None) == expected
    (c,) = gw.calls
    assert (c["method"], c["url"], c["headers"]) == ("GET", f"{BASE}/messages/INS-a1", {"Authorization": AUTH})


def test_status_not_found_and_disabled():
    assert AndroidSmsGatewayProvider(env=env(), transport=FakeGateway(get=(404, {}))).status("INS-a1") == "NOT_FOUND"
    gw = FakeGateway()
    assert AndroidSmsGatewayProvider(env=env(KAIROS_DEMO_ANDROID_SMS="0"), transport=gw).status("INS-a1") == "UNKNOWN"
    assert gw.calls == []


def test_timeout_then_resolve_uncertain_asks_gateway_by_instruction_id(lg):  # noqa: F811
    def timeout(method, url, headers, body, t):
        if method == "POST":
            raise TimeoutError("timed out")
        return 200, {"id": url.rsplit("/", 1)[-1], "state": "Sent"}
    p = AndroidSmsGatewayProvider(env=env(), transport=timeout, dry_run=False, recipient_lookup=lambda t: PHONE)
    iid, out = send(lg, p, "sms", tpl="DEMO_SMS_HELLO")
    assert out.outcome == "UNCERTAIN"
    assert Executor(lg, {"sms": p}).resolve_uncertain(iid) == "SENT"     # asked, never resent


# ---------------------------------------------------------------- webhooks
def hook(event, iid, **payload):
    return {"deviceId": "ffffffffceb0b1db0000018e937c815b", "event": event, "id": f"evt-{event}-{iid}",
            "webhookId": "LreFUt-Z3sSq0JufY9uWB", "payload": {"messageId": iid, **payload}}


def test_webhooks_map_to_delivery_events_and_inbound_stop(lg):  # noqa: F811
    gw = FakeGateway()
    p = AndroidSmsGatewayProvider(env=env(), transport=gw, dry_run=False, recipient_lookup=lambda t: PHONE)
    iid, out = send(lg, p, "sms", tpl="DEMO_SMS_HELLO")
    assert (out.outcome, out.provider_ref) == ("SENT", iid) and gw.calls[0]["json"]["id"] == iid
    queued = lg.reserve(ins("B1", "whatsapp", at(14))).instruction_id
    events = p.ingest_webhook([
        hook("sms:delivered", iid, phoneNumber=PHONE, recipient=PHONE, simNumber=1,
             deliveredAt="2026-11-19T10:00:05.000+05:30"),
        hook("sms:sent", iid, phoneNumber=PHONE, sender=None, recipient=PHONE, partsCount=1,
             sentAt="2026-11-19T10:00:01.000+05:30"),
        hook("system:ping", iid, health={}),
        hook("sms:received", "in-1", phoneNumber="0000000001", sender="0000000001", recipient=None,
             message="STOP", receivedAt="2026-11-19T10:05:00Z"),
    ])
    assert [e["status"] for e in events] == ["DELIVERED", "SENT", "REPLY"]
    assert all(e["provenance"] == "DEMO" and e["channel"] == "sms" and e["instruction_id"] == iid for e in events)
    envs = apply_all(lg, events)
    assert envs[0]["event_type"] == "provider.delivery" and not envs[0]["counts_as_reply"]
    assert envs[0]["read_status"] == "UNKNOWN" and envs[0]["reachability"] == "REACHABLE"
    assert envs[1]["ignored_reason"] == "STALE"
    assert envs[2]["event_type"] == "borrower.message" and envs[2]["stop"] and not envs[2]["counts_as_reply"]
    assert lg.borrower_state("B1")["state"]["stopped"] == 1
    assert lg.touch(queued)["status"] == "CANCELED"


def test_failed_webhook_is_reach_only_and_reply_is_a_reply(lg):  # noqa: F811
    p = AndroidSmsGatewayProvider(env=env(), transport=FakeGateway(), dry_run=False, recipient_lookup=lambda t: PHONE)
    iid, _ = send(lg, p, "sms", tpl="DEMO_SMS_HELLO")
    (failed,) = p.ingest_webhook(hook("sms:failed", iid, phoneNumber=PHONE, reason="RESULT_ERROR_NO_SERVICE",
                                      failedAt="2026-11-19T10:00:05+05:30"))
    assert failed["status"] == "FAILED" and "NO_SERVICE" in failed["error"]
    env_failed = apply_all(lg, [failed])[0]
    assert env_failed["reachability"] == "UNREACHABLE" and not env_failed["counts_as_reply"]
    (reply,) = p.ingest_webhook(hook("sms:received", "in-2", phoneNumber=PHONE, sender=PHONE, message="paid yesterday",
                                     receivedAt="2026-11-19T11:00:00.000+05:30"))
    env_reply = apply_all(lg, [reply])[0]
    assert env_reply["counts_as_reply"] and not env_reply["stop"] and env_reply["text"] == "paid yesterday"


def test_unknown_messages_and_strangers_are_skipped():
    p = AndroidSmsGatewayProvider(env=env())
    assert p.ingest_webhook(hook("sms:received", "x", phoneNumber=OTHER, sender=OTHER, message="STOP",
                                 receivedAt="2026-11-19T10:00:00Z")) == []
    assert p.ingest_webhook(hook("sms:delivered", "not a known id!", deliveredAt="2026-11-19T10:00:00Z")) == []
    assert p.ingest_webhook(hook("sms:cancelled", "INS-a1", cancelledAt="2026-11-19T10:00:00Z")) == []


def test_webhook_signature_enforced_when_key_set():
    key = "signing-key-test"
    p = AndroidSmsGatewayProvider(env=env(SMSGATE_WEBHOOK_SIGNING_KEY=key))
    raw = json.dumps(hook("sms:sent", "INS-a1", phoneNumber=PHONE, sentAt="2026-11-19T10:00:01Z")).encode()
    ts = "1795150000"
    sig = hmac.new(key.encode(), raw + ts.encode(), hashlib.sha256).hexdigest()
    (ev,) = p.ingest_webhook(raw_body=raw, headers={"X-Signature": sig, "X-Timestamp": ts}, now=1795150010)
    assert ev["status"] == "SENT" and ev["instruction_id"] == "INS-a1"
    for headers, now in [({"X-Signature": "00" * 32, "X-Timestamp": ts}, 1795150010),   # wrong signature
                         ({"X-Signature": sig, "X-Timestamp": ts}, 1795150000 + 301),     # stale
                         ({}, 1795150010)]:                                               # missing
        with pytest.raises(DemoSafetyError, match="WEBHOOK_SIGNATURE_INVALID"):
            p.ingest_webhook(raw_body=raw, headers=headers, now=now)
    with pytest.raises(DemoSafetyError):
        p.ingest_webhook(json.loads(raw))           # a parsed payload alone cannot be verified
    assert not verify_webhook_signature(key, raw, "not-a-number", sig)


# ---------------------------------------------------------------- logs
def test_logs_mask_numbers_and_never_show_the_password(caplog):
    caplog.set_level(logging.DEBUG, logger="kairos.channels.demo")
    AndroidSmsGatewayProvider(env=env(), transport=FakeGateway()).send(touch())                      # dry run
    AndroidSmsGatewayProvider(env=env(), transport=FakeGateway(), dry_run=False).send(touch(to=OTHER))  # refused
    AndroidSmsGatewayProvider(env=env(), transport=FakeGateway(), dry_run=False).send(touch(iid="INS-a2"))
    AndroidSmsGatewayProvider(env=env()).ingest_webhook(hook("sms:received", "x", sender=OTHER, message="hi"))
    text = caplog.text
    assert "+********0001" in text and "+********0002" in text
    assert "0000000001" not in text and "0000000002" not in text and "pw-test-1234567" not in text


# ---------------------------------------------------------------- selection
def test_sms_provider_priority_android_then_messages_then_mock():
    both = {**env(), "KAIROS_DEMO_SMS": "1"}
    assert isinstance(select_sms_provider(both), AndroidSmsGatewayProvider)
    assert isinstance(select_sms_provider({"KAIROS_DEMO_SMS": "1"}), MessagesAppSmsProvider)
    assert isinstance(select_sms_provider({}), MockChannelProvider)
    d = build_providers("phone-demo", env=both, sms_transport=FakeGateway())
    assert isinstance(d["sms"], AndroidSmsGatewayProvider) and d["sms"].dry_run
    assert isinstance(build_providers("mock", env=both)["sms"], MockChannelProvider)   # mock mode stays mock


# ---------------------------------------------------------------- CLI (scripts/send_demo_sms.py)
def _cli():
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / "scripts" / "send_demo_sms.py"
    spec = importlib.util.spec_from_file_location("send_demo_sms", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def cli_env(monkeypatch):
    for k, v in env().items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("KAIROS_DEMO_SMS", raising=False)
    return monkeypatch


def test_cli_dry_run_prints_masked_request_and_sends_nothing(cli_env, capsys):
    rc = _cli().main(["--to", "0001", "--template", "DEMO_SMS_HELLO", "--id", "DEMO-CLI-1"])
    out = capsys.readouterr().out
    assert rc == 0 and "DRY RUN" in out and "demo-android-sms (local mode)" in out
    assert '"url": "http://192.0.2.10:8080/messages"' in out and "+********0001" in out
    assert "0000000001" not in out and "pw-test-1234567" not in out


def test_cli_live_send_and_status(cli_env, capsys):
    import kairos_backend.channels.android_sms as mod
    gw = FakeGateway()
    cli_env.setattr(mod, "_urllib_transport", gw)
    cli = _cli()
    assert cli.main(["--to", PHONE, "--template", "DEMO_SMS_HELLO", "--id", "DEMO-CLI-2", "--live"]) == 0
    assert "result: ACCEPTED provider_ref=DEMO-CLI-2" in capsys.readouterr().out
    assert gw.calls[0]["json"]["phoneNumbers"] == [PHONE]
    assert cli.main(["--status", "DEMO-CLI-2"]) == 0 and '"status": "DELIVERED"' in capsys.readouterr().out


def test_cli_refusals(cli_env, capsys):
    cli = _cli()
    assert cli.main(["--to", "0001", "--template", "NOT_A_TEMPLATE"]) == 1
    assert "TEMPLATE_NOT_APPROVED" in capsys.readouterr().out
    with pytest.raises(SystemExit, match="no allow-listed number"):
        cli.main(["--to", "9999", "--template", "DEMO_SMS_HELLO"])
    cli_env.setenv("KAIROS_DEMO_ANDROID_SMS", "0")
    with pytest.raises(SystemExit, match="No SMS demo provider is enabled"):
        cli.main(["--to", "0001", "--template", "DEMO_SMS_HELLO"])
