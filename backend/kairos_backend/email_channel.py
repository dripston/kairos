"""K20 email channel: sandbox SMTP adapter, labelled REPLAY provider, callback handling.

Two providers implement the executor's Provider protocol (send(touch) / status(...)):

* SmtpSandboxProvider — real SMTP, but only to an approved SANDBOX relay. It exists only
  when KAIROS_SMTP_HOST/PORT/USER/PASSWORD/FROM are set AND KAIROS_SMTP_SANDBOX=1, and it
  refuses every recipient outside KAIROS_SMTP_ALLOWED_DOMAINS (default ``example.test``).
  SMTP gives us "accepted by the relay" (250) and nothing more: no delivery receipts, no
  bounces. A network error is UNKNOWN — never assumed delivered.
* ReplayEmailProvider — the default. Deterministic, labelled ``provenance="REPLAY"``. It
  accepts sends and scripts the callbacks (delivered / soft_bounce / hard_bounce /
  unsubscribe) per instruction or recipient, so the demo can show every path. No mail
  leaves the process.

Callbacks (from either source) go through EmailEvents.apply(), which updates the ledger
idempotently and returns a CRM-envelope dict:

  delivered    → touch DELIVERED. Reach only — NEVER a reply (K01 reply_attribution lists
                 "email delivery" under does_not_count_as_reply): no stop-for-day, no reward.
  soft_bounce  → audit + envelope only. The touch keeps its status (see README for why).
  hard_bounce  → that address is blocked for the borrower (email_suppression, hashed — raw
                 addresses are never stored), email consent off with consent_version
                 "hard_bounce@<iso>", queued EMAIL touches canceled (budget released).
  unsubscribe  → email consent off with consent_version "unsubscribed@<iso>", queued EMAIL
                 touches canceled. Other channels untouched (scope="email"). scope="all" is
                 the K01 STOP path (all channels).

Every applied event writes exactly one audit row; a duplicate callback is a no-op that
returns the original envelope with duplicate=True. The Supervisor replans at the next
allowed touch: the Gate's CONSENT rule now answers WAIT/NO_CONSENT for email.
"""
from __future__ import annotations

import hashlib
import hmac
import itertools
import json
import os
import smtplib
import socket
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.utils import parseaddr
from typing import Callable, Mapping, Optional, Union

from spine.ledger import record_event

from .executor import ProviderResult
from .ledger import RANK, Ledger

CHANNEL = "email"
KINDS = ("delivered", "soft_bounce", "hard_bounce", "unsubscribe")
PROVENANCE_REPLAY = "REPLAY"
PROVENANCE_SANDBOX = "SANDBOX_SMTP"

EMAIL_SCHEMA = """
CREATE TABLE IF NOT EXISTS email_suppression (
  borrower_id TEXT NOT NULL, address_hash TEXT NOT NULL, reason TEXT NOT NULL, at TEXT NOT NULL,
  PRIMARY KEY (borrower_id, address_hash));
CREATE TABLE IF NOT EXISTS email_events (
  event_key TEXT PRIMARY KEY, kind TEXT NOT NULL, borrower_id TEXT NOT NULL, instruction_id TEXT,
  address_hash TEXT, provenance TEXT NOT NULL, at TEXT NOT NULL, envelope TEXT NOT NULL);
-- K03 consents has no version column: the email consent version (why it was withdrawn / which
-- re-entry version restored it) is kept here, next to the spine's consents row it describes.
CREATE TABLE IF NOT EXISTS email_consent_versions (
  borrower_id TEXT PRIMARY KEY, version TEXT NOT NULL, updated_at TEXT NOT NULL);
"""

AddressResolver = Union[Callable[[str], Optional[str]], Mapping[str, str]]


class EmailConfigError(RuntimeError):
    """Sandbox SMTP is not configured (or not marked sandbox). Use ReplayEmailProvider."""


# ---------- addresses ----------
def normalize_address(address: str) -> str:
    return parseaddr(address or "")[1].strip().lower()


def address_hash(address: str, key: Optional[str] = None) -> str:
    """Keyed hash of the normalized address. Only this hash is ever stored.
    Key: KAIROS_EMAIL_HASH_KEY (set it in any shared environment; changing it orphans
    existing suppressions)."""
    k = (key if key is not None else os.environ.get("KAIROS_EMAIL_HASH_KEY", "kairos-dev")).encode()
    return "eh_" + hmac.new(k, normalize_address(address).encode(), hashlib.sha256).hexdigest()[:32]


def _resolver(resolve: AddressResolver) -> Callable[[str], Optional[str]]:
    return resolve.get if isinstance(resolve, Mapping) else resolve


def ensure_schema(ledger: Ledger) -> None:
    """Create the two email tables on the ledger's database (additive; once per Ledger)."""
    if not getattr(ledger, "_email_schema_ready", False):
        ledger._conn().executescript(EMAIL_SCHEMA)
        ledger._email_schema_ready = True


def is_suppressed(ledger: Ledger, borrower_id: str, address: str) -> bool:
    ensure_schema(ledger)
    return ledger._conn().execute(
        "SELECT 1 FROM email_suppression WHERE borrower_id=? AND address_hash=?",
        (borrower_id, address_hash(address))).fetchone() is not None


def suppression_check(ledger: Ledger) -> Callable[[str, str], bool]:
    """Pass to a provider as ``is_suppressed`` so a blocked address is refused at send time too."""
    return lambda borrower_id, address: is_suppressed(ledger, borrower_id, address)


# ---------- consent on the K03 spine ----------
def _consent(c, borrower_id: str) -> Optional[dict]:
    """Email consent as the Gate sees it: K03 `consents` (consented / opted_out per channel) plus the
    K06 STOP flag (`borrower_state.stopped`), and the version kept in `email_consent_versions`.
    None when the borrower is not on the spine."""
    if c.execute("SELECT 1 FROM borrowers WHERE borrower_id=?", (borrower_id,)).fetchone() is None:
        return None
    row = c.execute("SELECT consented, opted_out FROM consents WHERE borrower_id=? AND channel=?",
                    (borrower_id, CHANNEL)).fetchone()
    st = c.execute("SELECT stopped FROM borrower_state WHERE borrower_id=?", (borrower_id,)).fetchone()
    ver = c.execute("SELECT version FROM email_consent_versions WHERE borrower_id=?", (borrower_id,)).fetchone()
    return {"stopped": bool((st and st["stopped"]) or (row and row["opted_out"])),
            "consented": bool(row and row["consented"] and not row["opted_out"]),
            "version": ver["version"] if ver else None}


def _set_email_consent(c, ledger: Ledger, borrower_id: str, consented: bool, version: str, at_iso: str,
                       details: str) -> None:
    c.execute("INSERT INTO consents (borrower_id, channel, consented, opted_out, updated_at, source)"
              " VALUES (?,?,?,0,?,'provided') ON CONFLICT (borrower_id, channel) DO UPDATE SET"
              " consented=excluded.consented, updated_at=excluded.updated_at",
              (borrower_id, CHANNEL, int(consented), at_iso))
    c.execute("INSERT INTO email_consent_versions VALUES (?,?,?) ON CONFLICT (borrower_id) DO UPDATE SET"
              " version=excluded.version, updated_at=excluded.updated_at", (borrower_id, version, at_iso))
    record_event(c, f"EV-EMAIL-{borrower_id}-{version}", borrower_id, "consent_changed", at_iso,
                 channel=CHANNEL, details=details)


# ---------- eligibility ----------
def email_eligible(ledger: Ledger, borrower_id: str, address: Optional[str] = None) -> tuple[bool, str]:
    """(eligible, reason) for the email channel: STOP, then email consent (with the reason the
    consent was withdrawn), then — if an address is given — its suppression. Caps, window,
    holds and budget are the Gate's job at reserve/dispatch time."""
    ensure_schema(ledger)
    b = _consent(ledger._conn(), borrower_id)
    if b is None:
        raise KeyError(borrower_id)
    if b["stopped"]:
        return False, "STOP_OPT_OUT"
    if not b["consented"]:
        version = b["version"] or ""
        if version.startswith("unsubscribed@"):
            return False, "EMAIL_UNSUBSCRIBED"
        if version.startswith("hard_bounce@"):
            return False, "EMAIL_HARD_BOUNCE"
        return False, "NO_CONSENT"
    if address is not None and is_suppressed(ledger, borrower_id, address):
        return False, "EMAIL_ADDRESS_SUPPRESSED"
    return True, "ELIGIBLE"


def restore_email_consent(ledger: Ledger, borrower_id: str, address: str, consent_version: str,
                          at: datetime, actor: str = "borrower") -> tuple[bool, str]:
    """Per-channel re-entry for email with a recorded consent version (K01 STOP_OPT_OUT.re_entry).
    Refuses an address that hard-bounced for this borrower, and refuses after STOP (that
    re-entry is Ledger.opt_in). Other channels are not touched."""
    ensure_schema(ledger)
    ahash = address_hash(address)
    with ledger.tx() as c:
        b = _consent(c, borrower_id)
        if b is None:
            raise KeyError(borrower_id)
        if c.execute("SELECT 1 FROM email_suppression WHERE borrower_id=? AND address_hash=?",
                     (borrower_id, ahash)).fetchone():
            return False, "EMAIL_ADDRESS_SUPPRESSED"
        if b["stopped"]:
            return False, "STOP_OPT_OUT"
        _set_email_consent(c, ledger, borrower_id, True, consent_version, ledger._iso(at),
                           f"email re-entry, consent version {consent_version}")
        ledger._audit(c, actor, "EMAIL_CONSENT_RESTORED", borrower_id, at=at, channel=CHANNEL,
                      consent_version=consent_version, address_hash=ahash)
    return True, "ELIGIBLE"


# ---------- callbacks → ledger ----------
@dataclass
class EmailEvent:
    """One provider callback. ``address`` is hashed on arrival and never stored raw.
    Give ``instruction_id`` (borrower is taken from the touch) or ``borrower_id``."""
    kind: str                                  # delivered | soft_bounce | hard_bounce | unsubscribe
    at: datetime
    instruction_id: Optional[str] = None
    borrower_id: Optional[str] = None
    address: Optional[str] = None
    event_id: Optional[str] = None             # provider's id; dedupe key when present
    provenance: str = PROVENANCE_REPLAY
    scope: str = CHANNEL                       # unsubscribe only: "email" | "all"
    detail: dict = field(default_factory=dict)  # e.g. {"smtp_code": "550", "diagnostic": "..."}


class EmailEvents:
    def __init__(self, ledger: Ledger):
        self.ledger = ledger
        ensure_schema(ledger)

    def apply_all(self, events) -> list[dict]:
        return [self.apply(e) for e in events]

    def apply(self, ev: EmailEvent) -> dict:
        if ev.kind not in KINDS:
            raise ValueError(f"unknown email event kind {ev.kind!r}")
        if ev.kind == "unsubscribe" and ev.scope not in (CHANNEL, "all"):
            raise ValueError(f"unknown unsubscribe scope {ev.scope!r}")
        lg = self.ledger
        ahash = address_hash(ev.address) if ev.address else None
        with lg.tx() as c:
            t = None
            if ev.instruction_id:
                t = c.execute("SELECT * FROM touches WHERE instruction_id=?", (ev.instruction_id,)).fetchone()
                if t is None:
                    raise KeyError(ev.instruction_id)
                if t["channel"] != CHANNEL:
                    raise ValueError(f"{ev.instruction_id} is a {t['channel']} touch, not email")
            borrower_id = t["borrower_id"] if t is not None else ev.borrower_id
            if not borrower_id:
                raise ValueError("email event needs instruction_id or borrower_id")
            if ev.kind == "hard_bounce" and ahash is None:
                raise ValueError("hard_bounce needs the bounced address")
            key = ev.event_id or f"{ev.kind}:{ev.instruction_id or borrower_id}:{ahash or ''}"
            seen = c.execute("SELECT envelope FROM email_events WHERE event_key=?", (key,)).fetchone()
            if seen is not None:  # duplicate callback: no state change, no audit row
                return {**json.loads(seen["envelope"]), "applied": False, "duplicate": True}

            at_iso = lg._iso(ev.at)
            payload: dict = {"status": ev.kind.upper(), "counts_as_reply": False, "reward": None,
                             "address_hash": ahash, **ev.detail}
            canceled = 0
            touch_status = t["status"] if t is not None else None
            if ev.kind == "delivered" and t is not None:
                touch_status = self._upgrade(c, t, "DELIVERED")
            elif ev.kind == "hard_bounce":
                c.execute("INSERT OR IGNORE INTO email_suppression VALUES (?,?,?,?)",
                          (borrower_id, ahash, "HARD_BOUNCE", at_iso))
                payload.update(self._withdraw_email(c, borrower_id, f"hard_bounce@{at_iso}", keep_existing=True))
                canceled = self._cancel_email(c, borrower_id, "EMAIL_HARD_BOUNCE")
                payload["suppressed"] = True
            elif ev.kind == "unsubscribe":
                if ev.scope == "all":  # K01 STOP semantics: every channel
                    # same writes as Ledger.record_stop (which opens its own transaction)
                    lg._set_state(c, borrower_id, stopped=1)
                    c.execute("UPDATE consents SET opted_out=1, updated_at=? WHERE borrower_id=?", (at_iso, borrower_id))
                    record_event(c, f"EV-STOP-{borrower_id}-{at_iso}", borrower_id, "consent_changed", at_iso,
                                 channel=CHANNEL, details="STOP: email unsubscribe (scope all), all channels")
                    canceled = lg._cancel_queued(c, "borrower_id=?", (borrower_id,), "STOP_OPT_OUT")
                    payload.update({"opted_out": True})
                else:
                    payload.update(self._withdraw_email(c, borrower_id, f"unsubscribed@{at_iso}"))
                    canceled = self._cancel_email(c, borrower_id, "EMAIL_UNSUBSCRIBED")
                payload["scope"] = ev.scope
            # soft_bounce: transient — audit + envelope only, touch untouched

            payload.update({"touch_status": touch_status, "canceled_queued": canceled,
                            "replan": ev.kind in ("hard_bounce", "unsubscribe")})
            envelope = {
                "event_type": "consent.changed" if ev.kind == "unsubscribe" else "provider.delivery",
                "event_id": key,
                "occurred_at": at_iso,
                "borrower_id": borrower_id,
                "instruction_id": ev.instruction_id,
                "channel": CHANNEL,
                "provenance": ev.provenance,
                "policy_version": lg.policy.version,
                "payload": payload,
            }
            c.execute("INSERT INTO email_events VALUES (?,?,?,?,?,?,?,?)",
                      (key, ev.kind, borrower_id, ev.instruction_id, ahash, ev.provenance, at_iso,
                       json.dumps(envelope, default=str)))
            lg._audit(c, f"provider:{CHANNEL}", f"EMAIL_{ev.kind.upper()}", borrower_id, ev.instruction_id,
                      at=ev.at, reason=ev.kind, provenance=ev.provenance, event_id=key, **{
                          k: v for k, v in payload.items() if k not in ("status",)})
            return {**envelope, "applied": True, "duplicate": False}

    # -- helpers (run inside the caller's transaction) --
    def _upgrade(self, c, t, target: str) -> str:
        """Same rule as Ledger.record_provider_status: never downgrade; CANCELED/FAILED stay."""
        cur = t["status"]
        if cur not in RANK or RANK[target] <= RANK[cur]:
            return cur
        c.execute("UPDATE touches SET status=?, updated_at=? WHERE instruction_id=?",
                  (target, self.ledger._iso(self.ledger.policy.now()), t["instruction_id"]))
        return target

    def _withdraw_email(self, c, borrower_id: str, version: str, keep_existing: bool = False) -> dict:
        b = _consent(c, borrower_id)
        if b is None:
            raise KeyError(borrower_id)
        previous = b["version"]
        # a hard bounce on an already-withdrawn channel keeps the earlier reason (e.g. unsubscribed)
        new = previous if (keep_existing and not b["consented"] and previous) else version
        _set_email_consent(c, self.ledger, borrower_id, False, new, version.split("@", 1)[1],
                           f"email consent withdrawn: {version.split('@', 1)[0]}")
        return {"consent": {CHANNEL: False}, "consent_version": new,
                "previous_consent_version": previous}

    def _cancel_email(self, c, borrower_id: str, reason: str) -> int:
        return self.ledger._cancel_queued(c, "borrower_id=? AND channel=?", (borrower_id, CHANNEL), reason)


# ---------- providers ----------
def _default_render(touch: dict) -> tuple[str, str]:
    return (f"Kairos sandbox message ({touch.get('template_id')})",
            f"Sandbox rendering of template {touch.get('template_id')} for instruction {touch['instruction_id']}.\n"
            "This message was sent from a sandbox environment.")


class ReplayEmailProvider:
    """Deterministic, labelled REPLAY. Outcome per send, first match wins:
    script[instruction_id] → script[recipient] → recipient local part starting with an outcome
    tag (e.g. ``hard_bounce.b7@example.test``) → ``default``.
    Outcomes: delivered | soft_bounce | hard_bounce | unsubscribe (callbacks, after ACCEPTED),
    rejected (REJECTED at send), timeout (UNKNOWN at send, but the provider has it)."""

    name = "email-replay"
    provenance = PROVENANCE_REPLAY
    OUTCOMES = KINDS + ("rejected", "timeout")
    # minutes after the scheduled slot at which each scripted callback "arrives"
    OFFSETS = {"delivered": 2, "soft_bounce": 5, "hard_bounce": 5, "unsubscribe": 60}

    def __init__(self, resolve_address: AddressResolver, script: Optional[Mapping[str, str]] = None,
                 default: str = "delivered", is_suppressed: Optional[Callable[[str, str], bool]] = None):
        if default not in self.OUTCOMES:
            raise ValueError(default)
        self.resolve = _resolver(resolve_address)
        self.script = dict(script or {})
        self.default = default
        self.is_suppressed = is_suppressed
        self.known: dict[str, str] = {}      # instruction_id -> ACCEPTED | DELIVERED
        self.sent: list[str] = []
        self._pending: list[EmailEvent] = []

    def outcome_for(self, instruction_id: str, address: str) -> str:
        norm = normalize_address(address)
        out = self.script.get(instruction_id) or self.script.get(norm)
        if out is None:
            local = norm.split("@", 1)[0]
            out = next((o for o in sorted(self.OUTCOMES, key=len, reverse=True) if local.startswith(o)), self.default)
        if out not in self.OUTCOMES:
            raise ValueError(f"unknown replay outcome {out!r}")
        return out

    def send(self, touch: dict) -> ProviderResult:
        iid = touch["instruction_id"]
        to = self.resolve(touch["borrower_id"])
        if not to:
            return ProviderResult("REJECTED", error="NO_EMAIL_ADDRESS")
        if self.is_suppressed and self.is_suppressed(touch["borrower_id"], to):
            return ProviderResult("REJECTED", error="ADDRESS_SUPPRESSED")
        outcome = self.outcome_for(iid, to)
        if outcome == "rejected":
            return ProviderResult("REJECTED", error="REPLAY_REJECTED")
        ref = "replay-" + hashlib.sha1(iid.encode()).hexdigest()[:12]
        self.sent.append(iid)
        self.known[iid] = "ACCEPTED"
        if outcome == "timeout":
            return ProviderResult("UNKNOWN", error="REPLAY_TIMEOUT")
        base = datetime.fromisoformat(touch["scheduled_at"])
        kinds = ["delivered", "unsubscribe"] if outcome == "unsubscribe" else [outcome]
        for k in kinds:
            if k == "delivered":
                self.known[iid] = "DELIVERED"
            self._pending.append(EmailEvent(
                kind=k, at=base + timedelta(minutes=self.OFFSETS[k]), instruction_id=iid, address=to,
                event_id=f"replay:{iid}:{k}", provenance=PROVENANCE_REPLAY,
                detail={"smtp_code": "550", "diagnostic": "REPLAY: mailbox does not exist"} if k == "hard_bounce"
                else {"smtp_code": "452", "diagnostic": "REPLAY: mailbox full"} if k == "soft_bounce" else {}))
        return ProviderResult("ACCEPTED", provider_ref=ref)

    def status(self, instruction_id: str, provider_ref: Optional[str]) -> str:
        return self.known.get(instruction_id, "NOT_FOUND")

    def drain(self) -> list[EmailEvent]:
        """Scripted callbacks produced so far, in arrival order; clears the queue."""
        out, self._pending = sorted(self._pending, key=lambda e: e.at), []
        return out


@dataclass
class SmtpConfig:
    host: str
    port: int
    user: str
    password: str = field(repr=False)
    sender: str = ""
    allowed_domains: tuple = ("example.test",)
    starttls: bool = True

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "SmtpConfig":
        env = os.environ if env is None else env
        names = ["KAIROS_SMTP_HOST", "KAIROS_SMTP_PORT", "KAIROS_SMTP_USER", "KAIROS_SMTP_PASSWORD", "KAIROS_SMTP_FROM"]
        missing = [n for n in names if not env.get(n)]
        if missing:
            raise EmailConfigError(f"sandbox SMTP not configured; missing {', '.join(missing)}")
        if env.get("KAIROS_SMTP_SANDBOX") != "1":
            raise EmailConfigError("KAIROS_SMTP_SANDBOX=1 is required — only an approved sandbox relay may be used")
        domains = tuple(d.strip().lower().lstrip("@.") for d in
                        env.get("KAIROS_SMTP_ALLOWED_DOMAINS", "example.test").split(",") if d.strip())
        port = int(env["KAIROS_SMTP_PORT"])
        return cls(env["KAIROS_SMTP_HOST"], port, env["KAIROS_SMTP_USER"], env["KAIROS_SMTP_PASSWORD"],
                   env["KAIROS_SMTP_FROM"], domains or ("example.test",),
                   starttls=env.get("KAIROS_SMTP_STARTTLS", "0" if port == 465 else "1") == "1")


class SmtpSandboxProvider:
    """Sends through an approved sandbox SMTP relay. SMTP only tells us ACCEPTED (relay said
    250); delivery and bounces need the relay's callbacks or the REPLAY provider.

    ``smtp_factory(host, port, timeout=...)`` is injected so tests never touch the network."""

    name = "email-smtp-sandbox"
    provenance = PROVENANCE_SANDBOX

    def __init__(self, config: SmtpConfig, resolve_address: AddressResolver,
                 smtp_factory: Optional[Callable] = None, render: Callable[[dict], tuple] = _default_render,
                 is_suppressed: Optional[Callable[[str, str], bool]] = None, timeout: float = 10.0):
        self.config = config
        self.resolve = _resolver(resolve_address)
        self.factory = smtp_factory or (smtplib.SMTP_SSL if config.port == 465 else smtplib.SMTP)
        self.render = render
        self.is_suppressed = is_suppressed
        self.timeout = timeout
        # what WE know per instruction: ACCEPTED (relay said 250) | NOT_FOUND (never handed over)
        self.known: dict[str, str] = {}
        self._ids = itertools.count(1)

    @classmethod
    def from_env(cls, resolve_address: AddressResolver, env: Optional[Mapping[str, str]] = None,
                 **kw) -> "SmtpSandboxProvider":
        return cls(SmtpConfig.from_env(env), resolve_address, **kw)

    def recipient_allowed(self, address: str) -> bool:
        norm = normalize_address(address)
        if "@" not in norm:
            return False
        domain = norm.rsplit("@", 1)[1]
        return any(domain == d or domain.endswith("." + d) for d in self.config.allowed_domains)

    def build_message(self, touch: dict, to: str) -> EmailMessage:
        subject, body = self.render(touch)
        msg = EmailMessage()
        msg["From"] = self.config.sender
        msg["To"] = to
        msg["Subject"] = subject
        msg["Message-ID"] = f"<{touch['instruction_id']}@kairos.sandbox>"
        msg["X-Kairos-Instruction-Id"] = touch["instruction_id"]
        msg["X-Kairos-Environment"] = "SANDBOX"
        msg.set_content(body)
        return msg

    def send(self, touch: dict) -> ProviderResult:
        iid = touch["instruction_id"]
        to = self.resolve(touch["borrower_id"])
        if not to:
            return ProviderResult("REJECTED", error="NO_EMAIL_ADDRESS")
        if not self.recipient_allowed(to):
            return ProviderResult("REJECTED", error="RECIPIENT_NOT_SANDBOX")  # no live borrower mail
        if self.is_suppressed and self.is_suppressed(touch["borrower_id"], to):
            return ProviderResult("REJECTED", error="ADDRESS_SUPPRESSED")
        msg = self.build_message(touch, to)
        handed_over = False
        try:
            with self.factory(self.config.host, self.config.port, timeout=self.timeout) as s:
                if self.config.starttls:
                    s.starttls()
                s.login(self.config.user, self.config.password)
                handed_over = True  # from here on the relay may have the message
                refused = s.send_message(msg)
        except smtplib.SMTPAuthenticationError:
            self.known[iid] = "NOT_FOUND"
            return ProviderResult("REJECTED", error="SMTP_AUTH_FAILED")
        except smtplib.SMTPRecipientsRefused:
            self.known[iid] = "NOT_FOUND"
            return ProviderResult("REJECTED", error="SMTP_RECIPIENT_REFUSED")
        except (smtplib.SMTPSenderRefused, smtplib.SMTPDataError) as exc:
            self.known[iid] = "NOT_FOUND"
            return ProviderResult("REJECTED", error=f"SMTP_{getattr(exc, 'smtp_code', 'ERROR')}")
        except (OSError, socket.timeout, smtplib.SMTPException) as exc:
            # network trouble: never assume delivered. If we never got past login, the relay
            # cannot have the message — status() says NOT_FOUND so resolve_uncertain can release it.
            if not handed_over:
                self.known[iid] = "NOT_FOUND"
            return ProviderResult("UNKNOWN", error=f"SMTP_NETWORK: {type(exc).__name__}")
        if refused:
            self.known[iid] = "NOT_FOUND"
            return ProviderResult("REJECTED", error="SMTP_RECIPIENT_REFUSED")
        self.known[iid] = "ACCEPTED"
        return ProviderResult("ACCEPTED", provider_ref=msg["Message-ID"])

    def status(self, instruction_id: str, provider_ref: Optional[str]) -> str:
        """SMTP has no status query: ACCEPTED if the relay said 250, NOT_FOUND if we know it
        was never handed over, otherwise UNKNOWN (the touch stays UNCERTAIN)."""
        return self.known.get(instruction_id, "UNKNOWN")


def email_provider_from_env(resolve_address: AddressResolver, env: Optional[Mapping[str, str]] = None,
                            replay_script: Optional[Mapping[str, str]] = None, **smtp_kw):
    """SmtpSandboxProvider when sandbox SMTP is fully configured, else the labelled REPLAY provider."""
    try:
        return SmtpSandboxProvider.from_env(resolve_address, env, **smtp_kw)
    except EmailConfigError:
        return ReplayEmailProvider(resolve_address, script=replay_script,
                                   is_suppressed=smtp_kw.get("is_suppressed"))
