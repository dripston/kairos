"""K22: the approved-template selector.

Reads the template catalog (policy/K22-template-catalog/catalog.json) and, for one
channel, answers: which approved template ID may carry this message, or WAIT.

    select(channel, language, purpose, intensity, band) -> TemplateChoice

Inputs stay distinct (spec: keep reply confidence, payment likelihood and permitted
contact intensity apart): this module only consumes the *permitted intensity*
(LOW | STANDARD | WAIT) from agent 3 and the intent/purpose from agents 1-2. It never
infers intensity from confidence or likelihood.

Rules (K01 `templates`: approved_only, languages, no_suitable_template -> WAIT):
- approved templates only; an unapproved template is never returned;
- language must be one of K01 `templates.languages` and must match the template
  exactly - no fallback to another language;
- intensity WAIT -> WAIT; prefer the exact intensity; never escalate LOW -> STANDARD;
  STANDARD may de-escalate to LOW when no STANDARD template fits;
- the template must list the borrower's DPD band;
- REMINDER templates carry a CN tone tier (1-4: T1/T2 = LOW, T3/T4 = STANDARD). Without
  an explicit `tier` the least intense tier that fits wins; `tier=` asks for one tier and
  WAITs if it does not fit (it never exceeds the permitted intensity);
- at PRE_DUE a REMINDER request is served by PRE_DUE_REMINDER templates (K01: pre-due
  purpose is "reminder"); post-due REMINDER templates never list PRE_DUE;
- PAYMENT_RECEIVED (a receipt) is only chosen after D7/LMS verification
  (intent PAYMENT_VERIFIED or payment_verified=True); a borrower's "already paid" claim WAITs;
- ties are broken by (tier, template id), so the choice is deterministic.

Every catalog row also carries an `approval` block written by template_compliance.py
(the collections-compliance checklist); `approved` must agree with it.

WAIT is per channel (architecture): select_for_channels() returns one choice per
channel, so "no Hindi IVR template" does not block a Hindi WhatsApp send.

sync_to_ledger() copies the catalog into the Ledger's templates table so the K01
Gate's TEMPLATE_APPROVED check reads the same catalog the selector used.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional, Union

from .policy import Policy, load_policy, policy_dir

SEND, WAIT = "SEND", "WAIT"

CATALOG_DIRNAME = "K22-template-catalog"
CATALOG_FILENAME = "catalog.json"

PURPOSES = ("REMINDER", "PRE_DUE_REMINDER", "NACH_REPRESENTMENT", "ASK_PAYMENT_DATE", "PTP_CONFIRM",
            "PAYMENT_RECEIVED", "CALLBACK_ACK", "HARDSHIP_ACK", "STOP_CONFIRM")
TEMPLATE_INTENSITIES = ("LOW", "STANDARD")          # what a template can be tagged with
REQUEST_INTENSITIES = TEMPLATE_INTENSITIES + ("WAIT",)  # what agent 3 may permit

# CN tone tiers (Personal Loan 10-day post-due strategy) -> permitted intensity they need.
TIER_INTENSITY = {1: "LOW", 2: "LOW", 3: "STANDARD", 4: "STANDARD"}
TIER_NAMES = {1: "Informational", 2: "Standard reminder", 3: "Escalated", 4: "Final notice"}
TEMPLATE_TYPES = {"sms": ("sms",), "whatsapp": ("whatsapp",), "email": ("email",), "ivr": ("ivr_blast",),
                  "voicebot": ("voicebot_script",)}
APPROVED_STATUS = "APPROVED_FOR_DEMO"
PRE_DUE_BAND = "PRE_DUE"

# Intensities tried, in order, for each permitted intensity. LOW never escalates.
_INTENSITY_LADDER = {"STANDARD": ("STANDARD", "LOW"), "LOW": ("LOW",)}

# Intent (agents 1-2 / K13 event names) -> template purpose. Anything not listed -> REMINDER.
INTENT_TO_PURPOSE = {
    "NEEDS_TIME": "ASK_PAYMENT_DATE",        # K13 E07: ask for a date within 2 days
    "PTP": "PTP_CONFIRM",
    "PTP_OFFERED": "PTP_CONFIRM",            # K13 event name
    "CALLBACK": "CALLBACK_ACK",
    "CALLBACK_REQUEST": "CALLBACK_ACK",      # K13 E08 event name
    "HARDSHIP": "HARDSHIP_ACK",              # K13 E09: neutral ack, no collection ask
    "PAYMENT_VERIFIED": "PAYMENT_RECEIVED",  # receipt only once D7/LMS verified the payment
}
DEFAULT_PURPOSE = "REMINDER"
# K13 routed-stop types end the automated conversation; STOP is an opt-out. No outreach template.
# (The STOP handler sends the one STOP_CONFIRM itself: select(..., purpose="STOP_CONFIRM", ...).)
NO_TEMPLATE_INTENTS = frozenset({"ABUSIVE", "LEGAL_THREAT", "FRAUD_CLAIM", "STOP", "OPT_OUT"})
# A borrower *saying* they paid is not a verified payment: no receipt, no reminder - WAIT until D7/LMS.
UNVERIFIED_PAYMENT_INTENTS = frozenset({"PAID", "CLAIMS_PAID", "ALREADY_PAID"})
STOP_CONFIRM_PURPOSE = "STOP_CONFIRM"

REQUIRED_FIELDS = ("id", "channel", "lang", "purpose", "intensity", "dpd_bands", "approved", "version",
                   "provider_template_name", "variables")


class CatalogError(ValueError):
    """The catalog file is missing, unreadable or inconsistent with K01."""


def default_catalog_path() -> Path:
    """policy/K22-template-catalog/catalog.json under KAIROS_POLICY_DIR (default ../policy)."""
    return policy_dir() / CATALOG_DIRNAME / CATALOG_FILENAME


def purpose_for_intent(intent: Optional[str]) -> Optional[str]:
    """Map an agent intent to a template purpose. None means "no template may be sent"
    (K13 routed-stop types, STOP, an unverified "already paid" claim). Unknown or missing
    intents fall back to REMINDER."""
    key = (intent or "").strip().upper()
    if key in NO_TEMPLATE_INTENTS or key in UNVERIFIED_PAYMENT_INTENTS:
        return None
    return INTENT_TO_PURPOSE.get(key, DEFAULT_PURPOSE)


@dataclass(frozen=True)
class Template:
    id: str
    channel: str
    lang: str
    purpose: str
    intensity: str
    dpd_bands: tuple
    approved: bool
    version: int
    provider_template_name: str
    variables: tuple
    tier: Optional[int] = None          # CN tone tier 1-4 (REMINDER message templates only)
    extra: dict = field(default_factory=dict, compare=False, hash=False)

    @property
    def type(self) -> Optional[str]:
        return self.extra.get("type")

    @property
    def approval_status(self) -> Optional[str]:
        return (self.extra.get("approval") or {}).get("status")


@dataclass
class TemplateChoice:
    decision: str                       # SEND | WAIT
    template_id: Optional[str]
    reason: str
    catalog_version: str
    channel: str
    language: Optional[str] = None
    purpose: Optional[str] = None
    requested_intensity: Optional[str] = None
    intensity: Optional[str] = None     # intensity of the chosen template (None on WAIT)
    band: Optional[str] = None
    provider_template_name: Optional[str] = None
    template_version: Optional[int] = None
    tier: Optional[int] = None          # tier of the chosen template (None on WAIT / untiered)

    @property
    def send(self) -> bool:
        return self.decision == SEND

    def as_dict(self) -> dict:
        return asdict(self)


class TemplateCatalog:
    def __init__(self, templates: list[Template], meta: dict, policy: Policy, source: str = "<dict>"):
        self.templates = sorted(templates, key=lambda t: t.id)
        self.meta = meta
        self.policy = policy
        self.source = source
        self._by_id = {t.id: t for t in self.templates}

    # ---------- loading ----------
    @classmethod
    def load(cls, path: Optional[Union[str, Path]] = None, policy: Optional[Policy] = None) -> "TemplateCatalog":
        """Load and validate the catalog. Default path follows KAIROS_POLICY_DIR like policy.py;
        K01 (channels, bands) defaults to load_policy() from the same policy dir."""
        p = Path(path) if path else default_catalog_path()
        if not p.exists():
            raise CatalogError(f"{p}: template catalog not found (expected policy/{CATALOG_DIRNAME}/{CATALOG_FILENAME})")
        try:
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            raise CatalogError(f"{p}: not valid JSON ({e})") from e
        return cls.from_dict(data, policy=policy, source=str(p))

    @classmethod
    def from_dict(cls, data: Any, policy: Optional[Policy] = None, source: str = "<dict>") -> "TemplateCatalog":
        policy = policy or load_policy()
        errors: list[str] = []
        if not isinstance(data, dict):
            raise CatalogError(f"{source}: top level must be an object with 'catalog_version' and 'templates'")
        if not isinstance(data.get("catalog_version"), str) or not data["catalog_version"].strip():
            errors.append("'catalog_version' must be a non-empty string")
        rows = data.get("templates")
        if not isinstance(rows, list) or not rows:
            errors.append("'templates' must be a non-empty list")
            rows = []

        channels = set(policy.raw["channels"])
        bands = {b["id"] for b in policy.raw["dpd_bands"]}
        seen: set[str] = set()
        templates: list[Template] = []
        for i, r in enumerate(rows):
            where = f"templates[{i}]"
            if not isinstance(r, dict):
                errors.append(f"{where}: must be an object")
                continue
            if isinstance(r.get("id"), str) and r["id"]:
                where += f" '{r['id']}'"
            missing = [k for k in REQUIRED_FIELDS if k not in r]
            if missing:
                errors.append(f"{where}: missing field(s) {', '.join(missing)}")
                continue
            bad = len(errors)
            tid = r["id"]
            if not isinstance(tid, str) or not tid.strip():
                errors.append(f"{where}: 'id' must be a non-empty string")
            elif tid in seen:
                errors.append(f"{where}: duplicate id")
            else:
                seen.add(tid)
            if r["channel"] not in channels:
                errors.append(f"{where}: channel {r['channel']!r} is not a K01 channel {sorted(channels)}")
            if not isinstance(r["lang"], str) or not r["lang"].strip() or r["lang"] != r["lang"].strip().lower():
                errors.append(f"{where}: 'lang' must be a lowercase language code such as 'en' or 'hi'")
            if r["purpose"] not in PURPOSES:
                errors.append(f"{where}: purpose {r['purpose']!r} must be one of {list(PURPOSES)}")
            if r["intensity"] not in TEMPLATE_INTENSITIES:
                errors.append(f"{where}: intensity {r['intensity']!r} must be one of {list(TEMPLATE_INTENSITIES)}")
            if not isinstance(r["dpd_bands"], list) or not r["dpd_bands"]:
                errors.append(f"{where}: 'dpd_bands' must be a non-empty list of K01 band ids")
            else:
                unknown = [b for b in r["dpd_bands"] if b not in bands]
                if unknown:
                    errors.append(f"{where}: dpd_bands {unknown} are not K01 bands {sorted(bands)}")
            if not isinstance(r["approved"], bool):
                errors.append(f"{where}: 'approved' must be true or false (got {r['approved']!r})")
            if not isinstance(r["version"], int) or isinstance(r["version"], bool) or r["version"] < 1:
                errors.append(f"{where}: 'version' must be a positive integer")
            if not isinstance(r["provider_template_name"], str) or not r["provider_template_name"].strip():
                errors.append(f"{where}: 'provider_template_name' must be a non-empty string")
            if not isinstance(r["variables"], list) or not all(isinstance(v, str) and v for v in r["variables"]):
                errors.append(f"{where}: 'variables' must be a list of variable names")
            tier = r.get("tier")
            if tier is not None:
                if tier not in TIER_INTENSITY or isinstance(tier, bool):
                    errors.append(f"{where}: 'tier' must be 1, 2, 3 or 4 (got {tier!r})")
                elif r["purpose"] != "REMINDER":
                    errors.append(f"{where}: only REMINDER templates carry a tier")
                elif r["intensity"] != TIER_INTENSITY[tier]:
                    errors.append(f"{where}: tier {tier} must be intensity {TIER_INTENSITY[tier]} (T1/T2 LOW, T3/T4 STANDARD)")
            if "type" in r and r["type"] not in TEMPLATE_TYPES.get(r["channel"], ()):
                errors.append(f"{where}: type {r['type']!r} does not fit channel {r['channel']!r}")
            appr = r.get("approval")
            if appr is not None:
                if not isinstance(appr, dict) or not isinstance(appr.get("status"), str):
                    errors.append(f"{where}: 'approval' must be an object with a 'status'")
                elif isinstance(r["approved"], bool) and r["approved"] != (appr["status"] == APPROVED_STATUS):
                    errors.append(f"{where}: approved={r['approved']} disagrees with approval.status {appr['status']!r}")
            if len(errors) > bad:
                continue
            templates.append(Template(
                id=tid, channel=r["channel"], lang=r["lang"], purpose=r["purpose"], intensity=r["intensity"],
                dpd_bands=tuple(r["dpd_bands"]), approved=r["approved"], version=r["version"],
                provider_template_name=r["provider_template_name"], variables=tuple(r["variables"]),
                tier=tier, extra={k: v for k, v in r.items() if k not in REQUIRED_FIELDS and k != "tier"},
            ))
        if errors:
            raise CatalogError(f"{source}: invalid template catalog:\n  - " + "\n  - ".join(errors))
        meta = {k: v for k, v in data.items() if k != "templates"}
        return cls(templates, meta, policy, source)

    # ---------- identity ----------
    @property
    def version(self) -> str:
        return self.meta["catalog_version"]

    @property
    def status(self) -> Optional[str]:
        return self.meta.get("status")

    @property
    def is_placeholder(self) -> bool:
        return str(self.status or "").upper() == "PLACEHOLDER"

    def get(self, template_id: str) -> Optional[Template]:
        return self._by_id.get(template_id)

    def __iter__(self) -> Iterator[Template]:
        return iter(self.templates)

    def __len__(self) -> int:
        return len(self.templates)

    # ---------- selection ----------
    def select(self, channel: str, language: Optional[str], purpose: Optional[str], intensity: str,
               band: Optional[str], *, intent: Optional[str] = None, tier: Optional[int] = None,
               payment_verified: bool = False) -> TemplateChoice:
        """Pick one approved template for this channel, or WAIT.

        purpose: a PURPOSES value, or None to derive it from `intent` (purpose_for_intent).
        intensity: permitted contact intensity from agent 3 - LOW | STANDARD | WAIT.
        band: K01 band id (policy.band_for(dpd)); None means the borrower is not in scope.
        tier: optional CN tone tier (1-4) for REMINDER; must fit the permitted intensity.
        payment_verified: True only when D7/LMS verified the payment (needed for PAYMENT_RECEIVED;
            intent PAYMENT_VERIFIED implies it).
        Unknown channel / purpose / intensity / band / tier values raise ValueError (caller bug);
        a real "nothing fits" is always a WAIT decision with a reason.
        """
        ch = (channel or "").strip().lower()
        lang = (language or "").strip().lower() or None
        level = (intensity or "").strip().upper()
        if ch not in self.policy.raw["channels"]:
            raise ValueError(f"unknown channel {channel!r}; K01 channels are {self.policy.raw['channels']}")
        if level not in REQUEST_INTENSITIES:
            raise ValueError(f"unknown intensity {intensity!r}; expected one of {list(REQUEST_INTENSITIES)}")
        if band is not None and band not in {b["id"] for b in self.policy.raw["dpd_bands"]}:
            raise ValueError(f"unknown band {band!r}; use a K01 band id (policy.band_for(dpd))")
        if tier is not None and (tier not in TIER_INTENSITY or isinstance(tier, bool)):
            raise ValueError(f"unknown tier {tier!r}; expected 1, 2, 3 or 4")
        intent_key = (intent or "").strip().upper()
        if purpose is None:
            purpose = purpose_for_intent(intent)
        elif purpose not in PURPOSES:
            raise ValueError(f"unknown purpose {purpose!r}; expected one of {list(PURPOSES)}")
        if intent_key == "PAYMENT_VERIFIED":
            payment_verified = True
        # K01: the pre-due band's purpose is "reminder"; it is served by pre-due templates.
        if purpose == "REMINDER" and band == PRE_DUE_BAND:
            purpose = "PRE_DUE_REMINDER"

        no_fit = self.policy.raw["templates"].get("no_suitable_template", WAIT)

        def wait(reason: str, decision: str = WAIT) -> TemplateChoice:
            return TemplateChoice(decision, None, reason, self.version, ch, lang, purpose, level or None, None, band)

        if purpose is None:
            if intent_key in UNVERIFIED_PAYMENT_INTENTS:
                return wait("PAYMENT_NOT_VERIFIED")   # check D7/LMS first; never a receipt on a claim
            return wait("INTENT_NOT_AUTOMATED")       # K13 routed-stop / STOP: a person handles it
        if level == "WAIT":
            return wait("INTENSITY_WAIT")
        if purpose == "PAYMENT_RECEIVED" and not payment_verified:
            return wait("PAYMENT_NOT_VERIFIED")
        if tier is not None:
            if purpose not in ("REMINDER", "PRE_DUE_REMINDER"):
                raise ValueError(f"tier applies to REMINDER only, not {purpose}")
            if TIER_INTENSITY[tier] not in _INTENSITY_LADDER[level]:
                return wait("TIER_EXCEEDS_INTENSITY")
        if lang not in self.policy.template_languages:
            return wait("LANGUAGE_NOT_SUPPORTED", no_fit)  # K01: no fallback to another language
        if band is None:
            return wait("NOT_IN_SCOPE")
        if not self.policy.channel_allowed(band, ch):
            return wait("CHANNEL_NOT_ALLOWED")

        fits = [t for t in self.templates
                if t.approved and t.channel == ch and t.lang == lang and t.purpose == purpose and band in t.dpd_bands
                and (tier is None or t.tier == tier)]
        fits.sort(key=lambda t: (t.tier or 0, t.id))      # least intense tier first, then lowest id
        for step in _INTENSITY_LADDER[level]:
            step_fits = [t for t in fits if t.intensity == step]
            if step_fits:
                t = step_fits[0]
                return TemplateChoice(SEND, t.id, "MATCH" if step == level else "DE_ESCALATED", self.version,
                                      ch, lang, purpose, level, t.intensity, band, t.provider_template_name, t.version,
                                      t.tier)
        return wait("NO_APPROVED_TEMPLATE", no_fit)

    def select_for_channels(self, channels: Iterable[str], language: Optional[str], purpose: Optional[str],
                            intensity: Union[str, Mapping[str, str]], band: Optional[str], *,
                            intent: Optional[str] = None, tier: Optional[int] = None,
                            payment_verified: bool = False) -> dict[str, TemplateChoice]:
        """One choice per channel; a WAIT on one channel never blocks another.
        `intensity` may be one value for all channels or a {channel: intensity} mapping
        (a channel missing from the mapping gets WAIT)."""
        out: dict[str, TemplateChoice] = {}
        for ch in channels:
            level = intensity.get(ch, "WAIT") if isinstance(intensity, Mapping) else intensity
            out[ch] = self.select(ch, language, purpose, level, band, intent=intent, tier=tier,
                                  payment_verified=payment_verified)
        return out

    # ---------- ledger ----------
    def sync_to_ledger(self, ledger, withdraw_missing: bool = True) -> dict:
        """Upsert every catalog template (approved flag preserved) into the Ledger's templates
        table in one transaction, so Gate TEMPLATE_APPROVED sees exactly this catalog.
        withdraw_missing: mark ledger templates that are no longer in the catalog as
        unapproved (K22 owns the catalog - e.g. placeholder ids after the D13 swap)."""
        withdrawn: list[str] = []
        with ledger.tx() as c:
            for t in self.templates:
                c.execute("INSERT INTO templates (id, channel, lang, approved) VALUES (?,?,?,?)"
                          " ON CONFLICT(id) DO UPDATE SET channel=excluded.channel, lang=excluded.lang,"
                          " approved=excluded.approved", (t.id, t.channel, t.lang, int(t.approved)))
            if withdraw_missing:
                for row in c.execute("SELECT id FROM templates WHERE approved=1").fetchall():
                    if row["id"] not in self._by_id:
                        withdrawn.append(row["id"])
                c.executemany("UPDATE templates SET approved=0 WHERE id=?", [(i,) for i in withdrawn])
        return {"catalog_version": self.version, "upserted": len(self.templates), "withdrawn": sorted(withdrawn)}

    def summary(self) -> dict:
        return {
            "catalog_version": self.version,
            "status": self.status,
            "placeholder": self.is_placeholder,
            "source": self.source,
            "templates": len(self.templates),
            "approved": sum(t.approved for t in self.templates),
        }

    def tier_ladder(self, channel: str, language: str, band: str) -> dict:
        """{tier: template id or None} of approved REMINDER templates for one channel/language/band
        (for the frontend and for a strategy that schedules T1-T4 by cohort and day)."""
        out = {}
        for n in sorted(TIER_INTENSITY):
            hits = [t.id for t in self.templates if t.approved and t.channel == channel and t.lang == language
                    and t.purpose == "REMINDER" and t.tier == n and band in t.dpd_bands]
            out[n] = hits[0] if hits else None
        return out
