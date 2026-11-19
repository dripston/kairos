"""DEMO adapter: real SMS from a team member's own Android phone via "SMS Gateway for Android"
(SMSGate, https://sms-gate.app, Apache-2.0). OFF by default.

    AndroidSmsGatewayProvider   POSTs to the SMSGate API. Two modes:
        local  the phone itself runs the HTTP server (app: Home -> Local Server -> Start Service);
               Kairos and the phone must be on the same Wi-Fi.  POST http://<phone-ip>:8080/messages
        cloud  the phone connects to SMSGate's public relay; Kairos talks to the relay over HTTPS.
               POST https://api.sms-gate.app/3rdparty/v1/messages

Same hard safety rails as phone_demo.py (every one must pass, or send() returns REJECTED and no
request is made):
  1. Env flag KAIROS_DEMO_ANDROID_SMS=1.
  2. Recipient in effective_allowlist() = KAIROS_DEMO_ALLOWLIST ∪ ~/.kairos/demo_recipients.json.
  3. Text from an approved demo SMS template id (phone_demo.DEMO_SMS_TEMPLATES) — never free text.
  4. dry_run=True by default: the exact HTTP request is built, kept (masked) in `self.dry_runs`, and
     send() returns REJECTED("DRY_RUN") so the ledger releases the budget.
  5. Credentials from the environment only: SMSGATE_USER / SMSGATE_PASSWORD (shown in the app).
  6. Numbers masked in logs and previews (last 4 digits); the password never appears in a preview.
The HTTP transport is injectable, so tests never touch the network.

Env: KAIROS_DEMO_ANDROID_SMS=1, SMSGATE_MODE=local|cloud (default local), SMSGATE_URL (local: the
address the app shows, e.g. http://192.168.1.23:8080; cloud: optional, defaults to
https://api.sms-gate.app/3rdparty/v1 — set it only for a private SMSGate server), SMSGATE_USER,
SMSGATE_PASSWORD. Optional: SMSGATE_SIM (1-3), SMSGATE_TTL_S (default 3600; 0 = no expiry),
SMSGATE_WEBHOOK_SIGNING_KEY (verify webhook signatures).

Docs used (checked 2026-11-19):
  https://docs.sms-gate.app/getting-started/local-server/
      Local Server card -> Start Service; the app shows local/public IP and the Basic-auth username and
      password; examples use port 8080; Swagger UI at http://<device_local_ip>:8080/docs. Local mode is
      Basic auth only (no JWT). NOTE: this page's curl example still uses the singular path
      `POST /message`; the API spec below lists `/messages` (served at "/" by the local server). We use
      `/messages` for both modes. If an old app version answers 404, update the app.
  https://docs.sms-gate.app/getting-started/public-cloud-server/
      Cloud Server toggle -> Start Service -> Sign Up; credentials are generated and shown in the app.
      POST https://api.sms-gate.app/3rdparty/v1/messages with -u "username:password".
  https://capcom6.github.io/android-sms-gateway/swagger.json  (OpenAPI "SMSGate API" 1.79.1; servers:
      https://api.sms-gate.app/3rdparty/v1 and "/" for the local server)
      POST /messages -> 202 GetMessageResponse; 409 "Message with the same ID already exists";
          503 "Queue limits exceeded; ensure device is online". Body smsgateway.Message:
          textMessage{text} (the old `message` string is deprecated), phoneNumbers[] (1-100), id
          (optional, maxLength 36 — we set it, see below), simNumber (1-3), withDeliveryReport, ttl
          (seconds; conflicts with validUntil), priority, deviceId, isEncrypted.
      GET /messages/{id} -> GetMessageResponse {id, deviceId, state, recipients[{phoneNumber, state,
          error}], states{...}, isHashed, isEncrypted, createdAt}.
      ProcessingState: Pending, Cancelling, Cancelled, Processed, Sent, Delivered, Failed.
      WebHookEvent {id, webhookId, deviceId, event, payload}; SmsEventPayload {messageId, phoneNumber,
          sender, recipient, simNumber}; sms:sent +{partsCount, sentAt}; sms:delivered +{deliveredAt};
          sms:failed +{failedAt, reason}; sms:received +{message, receivedAt}.
  https://docs.sms-gate.app/features/sending-messages/   (field table; E.164 numbers unless skipPhoneValidation)
  https://docs.sms-gate.app/features/status-tracking/
      Processed = handed to Android, "indeterminate": may stay there forever; do not retry blindly.
      Delivered needs withDeliveryReport=true.
  https://docs.sms-gate.app/features/webhooks/
      Events sms:received, sms:sent, sms:delivered, sms:failed, sms:cancelled, system:ping, ...
      Registration: cloud POST https://api.sms-gate.app/3rdparty/v1/webhooks {url, event};
      local POST http://<device_ip>:8080/webhooks {id, url, event}. One event per registration.
      Signing: headers X-Signature (hex HMAC-SHA256 of raw body + X-Timestamp) and X-Timestamp (Unix s);
      key in app Settings -> Webhooks -> Signing Key; docs suggest a ±5 min timestamp window.
      Webhook URL must be HTTPS, except http://127.0.0.1 (use `adb reverse` or a tunnel); retries with
      backoff (10 s, 20 s, ... default 14 retries) until a 2xx within 30 s.
  https://docs.sms-gate.app/getting-started/webhooks/   (local registration example)
  https://docs.sms-gate.app/installation/  and  https://sms-gate.app/
      Install: APK from GitHub Releases (https://github.com/capcom6/android-sms-gateway/releases);
      Android 5.0+; permissions SEND_SMS (required), READ_PHONE_STATE (SIM choice), RECEIVE_SMS
      (sms:received). Neither page links Google Play or F-Droid.

Unclear in the docs (handled conservatively):
  * Event payloads: the spec marks `phoneNumber` required; the webhook guide's examples show
    `sender`/`recipient`. We read `phoneNumber` and fall back to recipient (outgoing) / sender (incoming).
  * The spec says the `signing_key` setting "must not be used with Cloud Server"; it does not say
    whether cloud-relayed webhooks are signed. Verification is enforced only when
    SMSGATE_WEBHOOK_SIGNING_KEY is set.
  * The webhook guide's examples show some numbers without "+"; inbound sender matching compares digits.

Idempotency: the request carries `id` = the instruction id when it fits the documented limit (≤36
chars; the ledger's ids are "INS-" + 16 hex), else a stable hash. So (a) the gateway refuses a second
copy with 409, which we treat as "already accepted"; (b) status() can ask about an instruction even
after a timeout lost the response; (c) every webhook's messageId maps straight back to the instruction.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Callable, Dict, List, Mapping, Optional, Tuple

from ..executor import ProviderResult
from .phone_demo import (DEMO_SMS_TEMPLATES, PROVENANCE, DemoPreview, DemoSafetyError, _DemoBase, mask_number,
                         render_demo_sms)

log = logging.getLogger("kairos.channels.demo")

CLOUD_BASE_URL = "https://api.sms-gate.app/3rdparty/v1"
MODES = ("local", "cloud")
DEFAULT_TTL_S = 3600          # a message queued while the phone is offline expires instead of firing hours later
SIGNATURE_TOLERANCE_S = 300   # docs: ±5 minutes

# method, url, headers, body (None for GET), timeout -> (HTTP status, parsed JSON body)
HttpTransport = Callable[[str, str, Dict[str, str], Optional[bytes], float], Tuple[int, dict]]

# SMSGate ProcessingState -> what status() reports to the Executor (resolve_uncertain):
# Pending/Processed: the gateway has it (never resend). Processed is "indeterminate" per the docs.
STATE_TO_STATUS = {"Pending": "ACCEPTED", "Processed": "ACCEPTED", "Sent": "SENT", "Delivered": "DELIVERED",
                   "Failed": "FAILED", "Cancelled": "FAILED", "Cancelling": "UNKNOWN"}
# webhook event -> normalized delivery status (reach only; never a reply)
EVENT_TO_STATUS = {"sms:sent": "SENT", "sms:delivered": "DELIVERED", "sms:failed": "FAILED"}
_EVENT_TIME = {"sms:sent": "sentAt", "sms:delivered": "deliveredAt", "sms:failed": "failedAt",
               "sms:received": "receivedAt"}
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,36}$")


def _urllib_transport(method: str, url: str, headers: Dict[str, str], body: Optional[bytes],
                      timeout: float) -> Tuple[int, dict]:
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (operator-configured gateway)
            raw = r.read()
            return r.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}


def gateway_message_id(instruction_id: str) -> str:
    """The SMSGate message id we send for an instruction (documented maxLength 36)."""
    if _SAFE_ID.match(instruction_id):
        return instruction_id
    return "kairos-" + hashlib.sha1(instruction_id.encode()).hexdigest()[:24]


def verify_webhook_signature(key: str, raw_body: bytes, timestamp: str, signature: str,
                             now: Optional[float] = None, tolerance_s: int = SIGNATURE_TOLERANCE_S) -> bool:
    """SMSGate signing: X-Signature = hex(HMAC-SHA256(key, raw_body + X-Timestamp)), timestamp in Unix seconds."""
    if not (key and timestamp and signature):
        return False
    try:
        ts = int(timestamp)
    except ValueError:
        return False
    if abs((time.time() if now is None else now) - ts) > tolerance_s:
        return False
    digest = hmac.new(key.encode(), raw_body + timestamp.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, signature.strip().lower())


def _iso(ts: Optional[str]) -> Optional[str]:
    """SMSGate date-time -> ISO string normalize.apply_callback can parse (Python 3.9 has no 'Z')."""
    if not ts:
        return None
    s = ts.strip().replace("Z", "+00:00")
    s = re.sub(r"\.(\d{1,6})\d*(?=[+-]|$)", lambda m: "." + m.group(1).ljust(6, "0"), s)
    try:
        return datetime.fromisoformat(s).isoformat()
    except ValueError:
        return None


def _digits(number: Optional[str]) -> str:
    return re.sub(r"\D", "", number or "")


class AndroidSmsGatewayProvider(_DemoBase):
    """SMS from the team member's Android phone through the SMS Gateway for Android app."""
    channel = "sms"
    env_flag = "KAIROS_DEMO_ANDROID_SMS"
    name = "demo-android-sms"

    def __init__(self, *, env: Optional[Mapping[str, str]] = None, dry_run: bool = True,
                 transport: Optional[HttpTransport] = None, templates: Optional[Dict[str, str]] = None,
                 recipient_lookup: Optional[Callable[[dict], Optional[str]]] = None, timeout_s: float = 15.0):
        super().__init__(env=env, dry_run=dry_run, recipient_lookup=recipient_lookup)
        self.transport = transport or _urllib_transport
        self.templates = DEMO_SMS_TEMPLATES if templates is None else templates
        self.timeout_s = timeout_s
        self._iid_by_ref: Dict[str, str] = {}
        self._last_iid_by_number: Dict[str, str] = {}   # recipient digits -> most recent instruction
        self.last_state: Dict[str, dict] = {}           # instruction_id -> last GET /messages/{id} body

    # ------------------------------------------------------------ config (env only)
    def mode(self) -> str:
        mode = (self.env.get("SMSGATE_MODE") or "local").strip().lower()
        if mode not in MODES:
            raise DemoSafetyError(f"BAD_SMSGATE_MODE: {mode!r}; expected local or cloud")
        return mode

    def base_url(self) -> str:
        mode = self.mode()
        url = (self.env.get("SMSGATE_URL") or "").strip().rstrip("/")
        if mode == "cloud" and not url:
            return CLOUD_BASE_URL
        if not url:
            raise DemoSafetyError("MISSING_SMSGATE_URL: set SMSGATE_URL to the address the app shows, "
                                  "e.g. http://192.168.1.23:8080")
        parts = urllib.parse.urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname or parts.query or parts.fragment \
                or parts.username or parts.password:
            raise DemoSafetyError("BAD_SMSGATE_URL: use scheme://host:port[/path], no credentials or query")
        if mode == "cloud" and parts.scheme != "https":
            raise DemoSafetyError("BAD_SMSGATE_URL: cloud / private-server mode must use https")
        return url

    def _auth(self) -> str:
        user, pw = self.env.get("SMSGATE_USER"), self.env.get("SMSGATE_PASSWORD")
        if not user or not pw:
            raise DemoSafetyError("MISSING_CREDENTIALS: set SMSGATE_USER and SMSGATE_PASSWORD (shown in the app)")
        return "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()

    def _sim(self) -> Optional[int]:
        raw = (self.env.get("SMSGATE_SIM") or "").strip()
        if not raw:
            return None
        if raw not in ("1", "2", "3"):
            raise DemoSafetyError("BAD_SMSGATE_SIM: SMSGATE_SIM must be 1, 2 or 3")
        return int(raw)

    def _ttl(self) -> Optional[int]:
        raw = (self.env.get("SMSGATE_TTL_S") or "").strip()
        if not raw:
            return DEFAULT_TTL_S
        if not raw.isdigit():
            raise DemoSafetyError("BAD_SMSGATE_TTL_S: whole seconds, 0 = no expiry")
        return int(raw) or None

    # ------------------------------------------------------------ send
    def render(self, touch: dict) -> str:
        return render_demo_sms(self.templates, touch)

    def build_request(self, touch: dict, to: str) -> dict:
        """The exact HTTP request (with the real auth header — do not log this; use the preview)."""
        text = self.render(touch)
        payload: dict = {"id": gateway_message_id(touch["instruction_id"]), "textMessage": {"text": text},
                         "phoneNumbers": [to], "withDeliveryReport": True}
        sim, ttl = self._sim(), self._ttl()
        if sim is not None:
            payload["simNumber"] = sim
        if ttl is not None:
            payload["ttl"] = ttl
        return {"method": "POST", "url": f"{self.base_url()}/messages",
                "headers": {"Authorization": self._auth(), "Content-Type": "application/json"},
                "json": payload}

    def masked(self, req: dict) -> dict:
        out = {**req, "headers": {**req["headers"], "Authorization": "Basic ****"}}   # user:password never shown
        if "json" in req:
            out["json"] = {**req["json"], "phoneNumbers": [mask_number(n) for n in req["json"]["phoneNumbers"]]}
        return out

    def prepare(self, touch: dict) -> Tuple[dict, DemoPreview]:
        """Run every safety rail and build the request. Raises DemoSafetyError on refusal."""
        to = self._check(touch)
        req = self.build_request(touch, to)
        return req, DemoPreview("sms", touch["instruction_id"], mask_number(to), touch["template_id"],
                                req["json"]["textMessage"]["text"], self.masked(req), self.dry_run)

    def send(self, touch: dict) -> ProviderResult:
        iid = touch["instruction_id"]
        if iid in self.sent:  # never send the same instruction twice
            return ProviderResult("ACCEPTED", provider_ref=self.sent[iid])
        try:
            req, preview = self.prepare(touch)
        except DemoSafetyError as exc:
            return self._refuse(touch, exc)
        if self.dry_run:
            self.dry_runs.append(preview)
            log.info("demo android-sms DRY RUN %s -> %s", iid, preview.recipient_masked)
            return ProviderResult("REJECTED", error="DRY_RUN")
        to = req["json"]["phoneNumbers"][0]
        log.info("demo android-sms sending %s -> %s via %s", iid, preview.recipient_masked, self.mode())
        try:
            code, body = self.transport("POST", req["url"], req["headers"], json.dumps(req["json"]).encode(),
                                        self.timeout_s)
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, ConnectionRefusedError):   # nothing listening: it was never received
                return ProviderResult("REJECTED", error="SMSGATE_UNREACHABLE: is Local Server started on the phone?")
            raise  # timeout / DNS etc.: the executor marks it UNCERTAIN; status() can ask by message id
        except ConnectionRefusedError:
            return ProviderResult("REJECTED", error="SMSGATE_UNREACHABLE: is Local Server started on the phone?")
        ref = req["json"]["id"]
        if code == 409:  # same id already enqueued: the gateway has it — do not count a second send
            log.warning("demo android-sms %s: gateway already has message %s", iid, ref)
        elif 200 <= code < 300:
            ref = body.get("id") or ref
            if (body.get("state") or "") == "Failed":
                err = ((body.get("recipients") or [{}])[0].get("error")) or "Failed"
                return ProviderResult("REJECTED", provider_ref=ref, error=f"SMSGATE_FAILED: {err}"[:300])
        else:
            msg = f"SMSGATE_{code}: {body.get('message') or 'no message id in response'}"[:300]
            if code >= 500 or code == 429:
                return ProviderResult("UNKNOWN", error=msg)  # transient; resolve_uncertain asks status()
            return ProviderResult("REJECTED", error=msg)
        self.sent[iid] = ref
        self._iid_by_ref[ref] = iid
        self._last_iid_by_number[_digits(to)] = iid
        return ProviderResult("ACCEPTED", provider_ref=ref)

    # ------------------------------------------------------------ status
    def status(self, instruction_id: str, provider_ref: Optional[str] = None) -> str:
        """GET /messages/{id}. ACCEPTED (Pending/Processed) | SENT | DELIVERED | FAILED | NOT_FOUND | UNKNOWN.
        Needs the flag and credentials; never sends anything."""
        ref = provider_ref or self.sent.get(instruction_id) or gateway_message_id(instruction_id)
        try:
            if self.env.get(self.env_flag) != "1":
                raise DemoSafetyError(f"DEMO_DISABLED: set {self.env_flag}=1")
            url = f"{self.base_url()}/messages/{urllib.parse.quote(ref, safe='')}"
            headers = {"Authorization": self._auth()}
        except DemoSafetyError as exc:
            log.warning("demo android-sms status %s not checked: %s", instruction_id, exc)
            return "UNKNOWN"
        try:
            code, body = self.transport("GET", url, headers, None, self.timeout_s)
        except (urllib.error.URLError, OSError) as exc:
            log.warning("demo android-sms status %s: %s", instruction_id, exc)
            return "UNKNOWN"
        if code == 404:
            return "NOT_FOUND"
        if not 200 <= code < 300:
            return "UNKNOWN"
        self.last_state[instruction_id] = body
        return STATE_TO_STATUS.get(body.get("state") or "", "UNKNOWN")

    # ------------------------------------------------------------ webhooks
    def ingest_webhook(self, payload=None, *, raw_body: Optional[bytes] = None,
                       headers: Optional[Mapping[str, str]] = None, now: Optional[float] = None) -> List[dict]:
        """SMSGate webhook (one envelope, or a list) -> normalized events for normalize.apply_callback.

        sms:sent / sms:delivered / sms:failed -> SENT / DELIVERED / FAILED (reach only, never a reply).
        sms:received -> REPLY on the most recent instruction this provider sent to that number
        (STOP and other opt-out words are handled by normalize: record_stop on all channels).
        Other events (sms:cancelled, system:ping, mms:*, ...) and unknown message ids are skipped.
        If SMSGATE_WEBHOOK_SIGNING_KEY is set, raw_body + headers are required and the signature
        (X-Signature / X-Timestamp) must verify, or DemoSafetyError("WEBHOOK_SIGNATURE_INVALID")."""
        key = self.env.get("SMSGATE_WEBHOOK_SIGNING_KEY")
        if key:
            h = {k.lower(): v for k, v in (headers or {}).items()}
            if raw_body is None or not verify_webhook_signature(key, raw_body, h.get("x-timestamp", ""),
                                                                h.get("x-signature", ""), now=now):
                raise DemoSafetyError("WEBHOOK_SIGNATURE_INVALID")
        if payload is None:
            payload = json.loads(raw_body or b"{}")
        envelopes = payload if isinstance(payload, list) else [payload]
        out: List[dict] = []
        for env in envelopes:
            event, p = env.get("event"), env.get("payload") or {}
            mid = p.get("messageId")
            if event in EVENT_TO_STATUS:
                iid = self._iid_by_ref.get(mid) or (mid if mid and _SAFE_ID.match(mid) else None)
                if not iid:
                    continue
                status = EVENT_TO_STATUS[event]
                out.append(self._event(iid, mid, status, p.get(_EVENT_TIME[event]),
                                       error=(f"SMSGATE_FAILED: {p.get('reason') or 'unknown'}"[:200]
                                              if status == "FAILED" else None),
                                       event_id=env.get("id") or f"{mid}-{status}"))
            elif event == "sms:received":
                sender = p.get("sender") or p.get("phoneNumber")
                iid = self._match_sender(sender)
                if not iid:
                    log.info("demo android-sms inbound from %s: no matching demo send, skipped", mask_number(sender))
                    continue
                out.append(self._event(iid, self.sent.get(iid), "REPLY", p.get("receivedAt"),
                                       text=p.get("message"), event_id=env.get("id") or mid))
        return out

    def _match_sender(self, sender: Optional[str]) -> Optional[str]:
        d = _digits(sender)
        if not d:
            return None
        if d in self._last_iid_by_number:
            return self._last_iid_by_number[d]
        if len(d) >= 10:  # national format ("9812345678") vs E.164 digits ("919812345678")
            hits = [iid for k, iid in self._last_iid_by_number.items() if k[-10:] == d[-10:]]
            if len(hits) == 1:
                return hits[0]
        return None

    def _event(self, iid: str, ref: Optional[str], status: str, ts: Optional[str], text: Optional[str] = None,
               error: Optional[str] = None, event_id: Optional[str] = None) -> dict:
        ev = {"channel": "sms", "instruction_id": iid, "provider_ref": ref, "status": status, "at": 0,
              "dtmf": None, "duration_s": None, "text": text, "intent": None, "error": error,
              "provenance": PROVENANCE, "event_id": event_id}
        occurred = _iso(ts)
        if occurred:
            ev["occurred_at"] = occurred
        return ev
