"""DEMO adapters that reach a real phone without buying a number. OFF by default.

    MessagesAppSmsProvider     SMS from the Mac's Messages app, relayed by the team member's
                               iPhone (Text Message Forwarding), driven by `osascript`.
    WhatsAppCloudTestProvider  Meta WhatsApp Cloud API with the free TEST business number.
    click_to_chat_url()        zero-setup fallback: a wa.me link a human opens and taps Send.

Hard safety rails (every one must pass, or send() returns REJECTED and nothing leaves the box):
  1. An explicit env flag: KAIROS_DEMO_SMS=1 / KAIROS_DEMO_WHATSAPP=1.
  2. The recipient must be in KAIROS_DEMO_ALLOWLIST (comma-separated E.164, e.g. +919812345678).
  3. The text comes from an approved demo template id — never free text from an agent.
  4. dry_run=True by default: send() builds the exact command / HTTP request, records it in
     `self.dry_runs`, and returns REJECTED("DRY_RUN") so the ledger releases the budget and
     nothing is counted as sent.
  5. Credentials come from the environment only (WA_TOKEN, WA_PHONE_NUMBER_ID); never from code.
  6. Phone numbers are masked in logs and previews (all but the last 4 digits).
The command runner (osascript) and the HTTP transport are injectable, so tests never execute
osascript and never touch the network.

Known limits (documented, not hidden):
  * Messages app: no delivery receipts are available to AppleScript. A successful run is
    reported as ACCEPTED (ledger: SENT); delivery and read stay UNKNOWN. status() answers
    ACCEPTED for sends it ran and UNKNOWN otherwise — never DELIVERED.
  * WhatsApp Cloud API: the send response only means "accepted"; sent/delivered/read/failed
    arrive by webhook. ingest_webhook() turns a webhook payload into normalized events for
    normalize.apply_callback. Without a webhook endpoint, status() stays ACCEPTED.

Meta docs used for the request shape (checked 2026-11-19):
  https://developers.facebook.com/docs/whatsapp/cloud-api/get-started
      (test number, API Setup, sample: POST https://graph.facebook.com/v23.0/<TEST_BUSINESS_PHONE_NUMBER_ID>/messages,
       headers Content-Type: application/json + Authorization: Bearer <token>)
  https://developers.facebook.com/documentation/business-messaging/whatsapp/messages/send-messages.md
      (POST /<WHATSAPP_BUSINESS_PHONE_NUMBER_ID>/messages; response {messaging_product, contacts[{input, wa_id}],
       messages[{id}]}; acceptance only — status comes by webhook)
  https://developers.facebook.com/documentation/business-messaging/whatsapp/templates/overview.md
      (template body: messaging_product, recipient_type "individual", to, type "template",
       template {name, language {code}, components})
  https://developers.facebook.com/documentation/business-messaging/whatsapp/webhooks/reference/messages/status.md
      (statuses[]: id, status sent|delivered|read|played|failed, timestamp, recipient_id, errors[])
  https://faq.whatsapp.com/5913398998672934  (click to chat: https://wa.me/<number>?text=<urlencoded>)
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Dict, List, Mapping, Optional, Tuple

from ..executor import ProviderResult

log = logging.getLogger("kairos.channels.demo")

PROVENANCE = "DEMO"
E164 = re.compile(r"^\+[1-9]\d{6,14}$")

# Approved demo texts. Ids are what a touch carries in template_id. {placeholders} are filled
# only from the touch's `params` (missing ones fall back to the defaults below).
DEMO_SMS_TEMPLATES: Dict[str, str] = {
    "DEMO_SMS_REMINDER_EN": "Kairos demo: your EMI of Rs {amount} is due on {due_date}. "
                            "This is a test message. Reply STOP to opt out.",
    "DEMO_SMS_HELLO": "Kairos demo: hello from the collections assistant (test message). Reply STOP to opt out.",
}
DEMO_SMS_DEFAULTS = {"amount": "5,000", "due_date": "25 Nov"}

# touch template_id -> Meta template. hello_world (en_US) exists on every new WhatsApp Business account.
DEMO_WA_TEMPLATES: Dict[str, dict] = {
    "DEMO_WA_HELLO": {"name": "hello_world", "language": "en_US"},
}

Runner = Callable[[List[str]], "subprocess.CompletedProcess"]
Transport = Callable[[str, Dict[str, str], bytes, float], Tuple[int, dict]]


class DemoSafetyError(Exception):
    """A demo safety rail refused the send. Nothing was executed."""


def mask_number(number: Optional[str]) -> str:
    """'+919812345678' -> '+********5678'. Keeps only the last 4 digits."""
    if not number:
        return "<none>"
    digits = re.sub(r"\D", "", number)
    if len(digits) <= 4:
        return "*" * len(digits)
    return ("+" if number.strip().startswith("+") else "") + "*" * (len(digits) - 4) + digits[-4:]


def mask_secret(secret: Optional[str]) -> str:
    return "<unset>" if not secret else "****" + secret[-4:] if len(secret) > 8 else "****"


def normalize_e164(number: str) -> str:
    n = re.sub(r"[\s\-().]", "", number or "")
    if n.startswith("00"):
        n = "+" + n[2:]
    if not E164.match(n):
        raise DemoSafetyError(f"not an E.164 number: {mask_number(number)}")
    return n


def allowlist_from_env(env: Mapping[str, str]) -> frozenset:
    raw = env.get("KAIROS_DEMO_ALLOWLIST", "")
    out = set()
    for part in raw.split(","):
        if part.strip():
            out.add(normalize_e164(part.strip()))
    return frozenset(out)


def default_recipient(touch: dict) -> Optional[str]:
    """Without a lookup the touch must name its recipient (`to`/`phone`)."""
    return touch.get("to") or touch.get("phone")


def ledger_recipient_lookup(ledger) -> Callable[[dict], Optional[str]]:
    """Recipient from the K03 spine (borrowers.phone). The allow-list still applies."""
    def lookup(touch: dict) -> Optional[str]:
        return default_recipient(touch) or ledger.borrower_state(touch["borrower_id"]).get("phone")
    return lookup


def click_to_chat_url(number: str, text: str) -> str:
    """https://wa.me/<digits>?text=<urlencoded> — opens WhatsApp with the text prefilled; a human
    taps Send. Number: full international format, digits only (no +, 00, spaces or dashes)."""
    digits = re.sub(r"\D", "", normalize_e164(number))
    return f"https://wa.me/{digits}?text={urllib.parse.quote(text, safe='')}"


@dataclass
class DemoPreview:
    """What a send would do. Safe to print: numbers and tokens are masked."""
    channel: str
    instruction_id: str
    recipient_masked: str
    template_id: str
    text: Optional[str]
    request: dict           # masked command / HTTP request
    dry_run: bool


class _DemoBase:
    channel = ""
    env_flag = ""

    def __init__(self, *, env: Optional[Mapping[str, str]] = None, dry_run: bool = True,
                 recipient_lookup: Optional[Callable[[dict], Optional[str]]] = None):
        self.env = dict(os.environ if env is None else env)
        self.dry_run = dry_run
        self.recipient_lookup = recipient_lookup or default_recipient
        self.provenance = PROVENANCE
        self.dry_runs: List[DemoPreview] = []
        self.sent: Dict[str, Optional[str]] = {}   # instruction_id -> provider_ref of real sends

    def _check(self, touch: dict) -> str:
        """All rails except the template. Returns the allow-listed E.164 recipient."""
        if self.env.get(self.env_flag) != "1":
            raise DemoSafetyError(f"DEMO_DISABLED: set {self.env_flag}=1 to enable the {self.channel} demo adapter")
        allow = allowlist_from_env(self.env)
        if not allow:
            raise DemoSafetyError("DEMO_ALLOWLIST_EMPTY: set KAIROS_DEMO_ALLOWLIST")
        raw = self.recipient_lookup(touch)
        if not raw:
            raise DemoSafetyError("NO_RECIPIENT")
        to = normalize_e164(raw)
        if to not in allow:
            raise DemoSafetyError(f"NOT_ALLOWLISTED: {mask_number(to)}")
        return to

    def _refuse(self, touch: dict, exc: Exception) -> ProviderResult:
        log.warning("demo %s refused %s: %s", self.channel, touch.get("instruction_id"), exc)
        return ProviderResult("REJECTED", error=str(exc))

    def status(self, instruction_id: str, provider_ref: Optional[str] = None) -> str:
        return "ACCEPTED" if instruction_id in self.sent else "UNKNOWN"

    def callbacks(self, instruction_id: str) -> List[dict]:
        """Only what we actually know: it was handed over (ACCEPTED). Delivery/read: UNKNOWN."""
        if instruction_id not in self.sent:
            return []
        return [{"channel": self.channel, "instruction_id": instruction_id, "provider_ref": self.sent[instruction_id],
                 "status": "ACCEPTED", "at": 0, "dtmf": None, "duration_s": None, "text": None, "intent": None,
                 "error": None, "provenance": PROVENANCE, "event_id": f"{self.sent[instruction_id]}-accepted"}]


# ---------------------------------------------------------------- SMS via Messages.app
def _subprocess_runner(cmd: List[str]) -> "subprocess.CompletedProcess":
    return subprocess.run(cmd, capture_output=True, text=True, timeout=30)


class MessagesAppSmsProvider(_DemoBase):
    """SMS through macOS Messages + iPhone Text Message Forwarding. macOS only.

    The script receives the number and text as argv (`on run argv`), so no user data is ever
    spliced into AppleScript source (no quoting/injection issues).
    iMessage is used only if `imessage_fallback=True` (or env KAIROS_DEMO_SMS_IMESSAGE_FALLBACK=1)
    AND the SMS attempt failed (e.g. no SMS account because forwarding is off)."""
    channel = "sms"
    env_flag = "KAIROS_DEMO_SMS"
    name = "demo-messages-sms"

    def __init__(self, *, env: Optional[Mapping[str, str]] = None, dry_run: bool = True,
                 runner: Optional[Runner] = None, templates: Optional[Dict[str, str]] = None,
                 recipient_lookup: Optional[Callable[[dict], Optional[str]]] = None,
                 imessage_fallback: Optional[bool] = None, platform: Optional[str] = None):
        super().__init__(env=env, dry_run=dry_run, recipient_lookup=recipient_lookup)
        self.runner = runner or _subprocess_runner
        self.templates = DEMO_SMS_TEMPLATES if templates is None else templates
        self.imessage_fallback = (self.env.get("KAIROS_DEMO_SMS_IMESSAGE_FALLBACK") == "1"
                                  if imessage_fallback is None else imessage_fallback)
        self.platform = platform or sys.platform

    @staticmethod
    def osascript_command(to: str, text: str, service_type: str = "SMS") -> List[str]:
        if service_type not in ("SMS", "iMessage"):
            raise ValueError(service_type)
        lines = [
            "on run argv",
            'tell application "Messages"',
            f"set targetAccount to 1st account whose service type = {service_type}",
            "set targetParticipant to participant (item 1 of argv) of targetAccount",
            "send (item 2 of argv) to targetParticipant",
            "end tell",
            "end run",
        ]
        cmd = ["osascript"]
        for ln in lines:
            cmd += ["-e", ln]
        return cmd + [to, text]

    def render(self, touch: dict) -> str:
        tid = touch.get("template_id")
        if tid not in self.templates:
            raise DemoSafetyError(f"TEMPLATE_NOT_APPROVED: {tid!r} is not an approved demo SMS template")
        params = {**DEMO_SMS_DEFAULTS, **(touch.get("params") or {})}
        try:
            return self.templates[tid].format(**params)
        except KeyError as e:
            raise DemoSafetyError(f"TEMPLATE_PARAM_MISSING: {e}") from None

    def prepare(self, touch: dict) -> Tuple[List[str], DemoPreview]:
        """Run every safety rail and build the command. Raises DemoSafetyError on refusal."""
        to = self._check(touch)
        text = self.render(touch)
        cmd = self.osascript_command(to, text)
        masked = cmd[:-2] + [mask_number(to), text]
        return cmd, DemoPreview("sms", touch["instruction_id"], mask_number(to), touch["template_id"], text,
                                {"command": masked}, self.dry_run)

    def send(self, touch: dict) -> ProviderResult:
        iid = touch["instruction_id"]
        if iid in self.sent:  # never send the same instruction twice
            return ProviderResult("ACCEPTED", provider_ref=self.sent[iid])
        try:
            cmd, preview = self.prepare(touch)
            if not self.dry_run and self.platform != "darwin":
                raise DemoSafetyError("NOT_MACOS: the Messages adapter only runs on macOS")
        except DemoSafetyError as exc:
            return self._refuse(touch, exc)
        if self.dry_run:
            self.dry_runs.append(preview)
            log.info("demo sms DRY RUN %s -> %s", iid, preview.recipient_masked)
            return ProviderResult("REJECTED", error="DRY_RUN")
        log.info("demo sms sending %s -> %s", iid, preview.recipient_masked)
        res = self.runner(cmd)  # may raise (timeout) -> executor marks UNCERTAIN
        if res.returncode != 0 and self.imessage_fallback:
            log.warning("demo sms: SMS account failed (%s); trying iMessage fallback", (res.stderr or "").strip()[:200])
            res = self.runner(self.osascript_command(cmd[-2], cmd[-1], "iMessage"))
        if res.returncode != 0:
            return ProviderResult("REJECTED", error=f"OSASCRIPT_FAILED: {(res.stderr or '').strip()[:200]}")
        ref = f"MSGAPP-{iid}"
        self.sent[iid] = ref
        return ProviderResult("ACCEPTED", provider_ref=ref)


# ---------------------------------------------------------------- WhatsApp Cloud API (test number)
def _urllib_transport(url: str, headers: Dict[str, str], body: bytes, timeout: float) -> Tuple[int, dict]:
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (fixed https host)
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}


class WhatsAppCloudTestProvider(_DemoBase):
    """Meta WhatsApp Cloud API with the free test business number (no DID purchase).

    Env: KAIROS_DEMO_WHATSAPP=1, KAIROS_DEMO_ALLOWLIST, WA_TOKEN, WA_PHONE_NUMBER_ID,
    optional WA_GRAPH_VERSION (default v23.0, the version in Meta's get-started sample)."""
    channel = "whatsapp"
    env_flag = "KAIROS_DEMO_WHATSAPP"
    name = "demo-whatsapp-cloud"
    DEFAULT_VERSION = "v23.0"

    def __init__(self, *, env: Optional[Mapping[str, str]] = None, dry_run: bool = True,
                 transport: Optional[Transport] = None, templates: Optional[Dict[str, dict]] = None,
                 recipient_lookup: Optional[Callable[[dict], Optional[str]]] = None, timeout_s: float = 15.0,
                 send_callback_data: bool = True):
        super().__init__(env=env, dry_run=dry_run, recipient_lookup=recipient_lookup)
        self.transport = transport or _urllib_transport
        self.templates = DEMO_WA_TEMPLATES if templates is None else templates
        self.timeout_s = timeout_s
        self.send_callback_data = send_callback_data
        self._iid_by_ref: Dict[str, str] = {}
        self._last_iid_by_wa: Dict[str, str] = {}

    def build_request(self, touch: dict, to: str) -> dict:
        """The exact HTTP request (with the real token — do not log this; use preview)."""
        tid = touch.get("template_id")
        tpl = self.templates.get(tid)
        if tpl is None:
            raise DemoSafetyError(f"TEMPLATE_NOT_APPROVED: {tid!r} is not an approved demo WhatsApp template")
        token, pnid = self.env.get("WA_TOKEN"), self.env.get("WA_PHONE_NUMBER_ID")
        if not token or not pnid:
            raise DemoSafetyError("MISSING_CREDENTIALS: set WA_TOKEN and WA_PHONE_NUMBER_ID in the environment")
        if not re.fullmatch(r"\d+", pnid):
            raise DemoSafetyError("BAD_PHONE_NUMBER_ID: WA_PHONE_NUMBER_ID must be the numeric id from API Setup")
        version = self.env.get("WA_GRAPH_VERSION", self.DEFAULT_VERSION)
        if not re.fullmatch(r"v\d+\.\d+", version):
            raise DemoSafetyError(f"BAD_GRAPH_VERSION: {version!r}")
        template = {"name": tpl["name"], "language": {"code": tpl["language"]}}
        params = touch.get("params") or {}
        body_params = [params[k] for k in tpl.get("body_params", [])]
        if body_params:
            template["components"] = [{"type": "body",
                                       "parameters": [{"type": "text", "text": str(v)} for v in body_params]}]
        payload = {"messaging_product": "whatsapp", "recipient_type": "individual",
                   "to": to.lstrip("+"), "type": "template", "template": template}
        if self.send_callback_data:
            # echoed back on status webhooks so callbacks map to the instruction without a lookup table
            payload["biz_opaque_callback_data"] = touch["instruction_id"]
        return {"method": "POST", "url": f"https://graph.facebook.com/{version}/{pnid}/messages",
                "headers": {"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                "json": payload}

    def prepare(self, touch: dict) -> Tuple[dict, DemoPreview]:
        to = self._check(touch)
        req = self.build_request(touch, to)
        masked = {**req, "headers": {**req["headers"], "Authorization": "Bearer " + mask_secret(self.env.get("WA_TOKEN"))},
                  "json": {**req["json"], "to": mask_number(to)}}
        return req, DemoPreview("whatsapp", touch["instruction_id"], mask_number(to), touch["template_id"], None,
                                masked, self.dry_run)

    def send(self, touch: dict) -> ProviderResult:
        iid = touch["instruction_id"]
        if iid in self.sent:
            return ProviderResult("ACCEPTED", provider_ref=self.sent[iid])
        try:
            req, preview = self.prepare(touch)
        except DemoSafetyError as exc:
            return self._refuse(touch, exc)
        if self.dry_run:
            self.dry_runs.append(preview)
            log.info("demo whatsapp DRY RUN %s -> %s", iid, preview.recipient_masked)
            return ProviderResult("REJECTED", error="DRY_RUN")
        log.info("demo whatsapp sending %s -> %s", iid, preview.recipient_masked)
        code, body = self.transport(req["url"], req["headers"], json.dumps(req["json"]).encode(), self.timeout_s)
        if 200 <= code < 300 and body.get("messages"):
            ref = body["messages"][0].get("id")
            self.sent[iid] = ref
            self._iid_by_ref[ref] = iid
            wa_id = (body.get("contacts") or [{}])[0].get("wa_id") or req["json"]["to"]
            self._last_iid_by_wa[wa_id] = iid
            return ProviderResult("ACCEPTED", provider_ref=ref)
        err = body.get("error") or {}
        msg = f"META_{err.get('code', code)}: {err.get('message', 'no message id in response')}"[:300]
        if code >= 500 or code == 429:
            return ProviderResult("UNKNOWN", error=msg)  # transient: let resolve_uncertain decide
        return ProviderResult("REJECTED", error=msg)

    def ingest_webhook(self, payload: dict) -> List[dict]:
        """Meta webhook payload -> normalized events for normalize.apply_callback.
        statuses[] -> SENT/DELIVERED/READ/FAILED; messages[] (inbound) -> REPLY on the most recent
        instruction sent to that WhatsApp id. Unknown message ids are skipped."""
        out: List[dict] = []
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                for st in value.get("statuses", []):
                    iid = st.get("biz_opaque_callback_data") or self._iid_by_ref.get(st.get("id"))
                    status = (st.get("status") or "").upper()
                    if not iid or status not in ("SENT", "DELIVERED", "READ", "FAILED"):
                        continue
                    errs = st.get("errors") or [{}]
                    out.append(self._event(iid, st.get("id"), status, st.get("timestamp"),
                                           error=(f"{errs[0].get('code')}_{errs[0].get('title')}" if status == "FAILED" else None),
                                           event_id=f"{st.get('id')}-{status}"))
                for m in value.get("messages", []):
                    iid = self._last_iid_by_wa.get(m.get("from", ""))
                    if not iid:
                        continue
                    text = (m.get("text") or {}).get("body") or (m.get("button") or {}).get("text") \
                        or ((m.get("interactive") or {}).get("button_reply") or {}).get("title")
                    out.append(self._event(iid, self.sent.get(iid), "REPLY", m.get("timestamp"), text=text,
                                           event_id=m.get("id")))
        return out

    def _event(self, iid: str, ref: Optional[str], status: str, ts: Optional[str], text: Optional[str] = None,
               error: Optional[str] = None, event_id: Optional[str] = None) -> dict:
        occurred = datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat() if ts else None
        ev = {"channel": "whatsapp", "instruction_id": iid, "provider_ref": ref, "status": status, "at": 0,
              "dtmf": None, "duration_s": None, "text": text, "intent": None, "error": error,
              "provenance": PROVENANCE, "event_id": event_id}
        if occurred:
            ev["occurred_at"] = occurred
        return ev
