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
- ties are broken by template id (lowest wins), so the choice is deterministic.

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

PURPOSES = ("REMINDER", "ASK_PAYMENT_DATE", "PTP_CONFIRM", "PAYMENT_RECEIVED", "CALLBACK_ACK", "HARDSHIP_ACK")
TEMPLATE_INTENSITIES = ("LOW", "STANDARD")          # what a template can be tagged with
REQUEST_INTENSITIES = TEMPLATE_INTENSITIES + ("WAIT",)  # what agent 3 may permit

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
    "PAID": "PAYMENT_RECEIVED",              # never send a reminder to someone who just paid
    "PAYMENT_VERIFIED": "PAYMENT_RECEIVED",
}
DEFAULT_PURPOSE = "REMINDER"
# K13 routed-stop types end the automated conversation; STOP is an opt-out. No template.
NO_TEMPLATE_INTENTS = frozenset({"ABUSIVE", "LEGAL_THREAT", "FRAUD_CLAIM", "STOP", "OPT_OUT"})

REQUIRED_FIELDS = ("id", "channel", "lang", "purpose", "intensity", "dpd_bands", "approved", "version",
                   "provider_template_name", "variables")


class CatalogError(ValueError):
    """The catalog file is missing, unreadable or inconsistent with K01."""


def default_catalog_path() -> Path:
    """policy/K22-template-catalog/catalog.json under KAIROS_POLICY_DIR (default ../policy)."""
    return policy_dir() / CATALOG_DIRNAME / CATALOG_FILENAME


def purpose_for_intent(intent: Optional[str]) -> Optional[str]:
    """Map an agent intent to a template purpose. None means "no template may be sent"
    (K13 routed-stop types, STOP). Unknown or missing intents fall back to REMINDER."""
    key = (intent or "").strip().upper()
    if key in NO_TEMPLATE_INTENTS:
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
    extra: dict = field(default_factory=dict, compare=False, hash=False)


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
            if len(errors) > bad:
                continue
            templates.append(Template(
                id=tid, channel=r["channel"], lang=r["lang"], purpose=r["purpose"], intensity=r["intensity"],
                dpd_bands=tuple(r["dpd_bands"]), approved=r["approved"], version=r["version"],
                provider_template_name=r["provider_template_name"], variables=tuple(r["variables"]),
                extra={k: v for k, v in r.items() if k not in REQUIRED_FIELDS},
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
               band: Optional[str], *, intent: Optional[str] = None) -> TemplateChoice:
        """Pick one approved template for this channel, or WAIT.

        purpose: a PURPOSES value, or None to derive it from `intent` (purpose_for_intent).
        intensity: permitted contact intensity from agent 3 - LOW | STANDARD | WAIT.
        band: K01 band id (policy.band_for(dpd)); None means the borrower is not in scope.
        Unknown channel / purpose / intensity / band ids raise ValueError (caller bug);
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
        if purpose is None:
            purpose = purpose_for_intent(intent)
        elif purpose not in PURPOSES:
            raise ValueError(f"unknown purpose {purpose!r}; expected one of {list(PURPOSES)}")

        no_fit = self.policy.raw["templates"].get("no_suitable_template", WAIT)

        def wait(reason: str, decision: str = WAIT) -> TemplateChoice:
            return TemplateChoice(decision, None, reason, self.version, ch, lang, purpose, level or None, None, band)

        if purpose is None:
            return wait("INTENT_NOT_AUTOMATED")       # K13 routed-stop / STOP: a person handles it
        if level == "WAIT":
            return wait("INTENSITY_WAIT")
        if lang not in self.policy.template_languages:
            return wait("LANGUAGE_NOT_SUPPORTED", no_fit)  # K01: no fallback to another language
        if band is None:
            return wait("NOT_IN_SCOPE")
        if not self.policy.channel_allowed(band, ch):
            return wait("CHANNEL_NOT_ALLOWED")

        fits = [t for t in self.templates
                if t.approved and t.channel == ch and t.lang == lang and t.purpose == purpose and band in t.dpd_bands]
        for tier in _INTENSITY_LADDER[level]:
            tier_fits = [t for t in fits if t.intensity == tier]  # self.templates is id-sorted
            if tier_fits:
                t = tier_fits[0]
                return TemplateChoice(SEND, t.id, "MATCH" if tier == level else "DE_ESCALATED", self.version,
                                      ch, lang, purpose, level, t.intensity, band, t.provider_template_name, t.version)
        return wait("NO_APPROVED_TEMPLATE", no_fit)

    def select_for_channels(self, channels: Iterable[str], language: Optional[str], purpose: Optional[str],
                            intensity: Union[str, Mapping[str, str]], band: Optional[str], *,
                            intent: Optional[str] = None) -> dict[str, TemplateChoice]:
        """One choice per channel; a WAIT on one channel never blocks another.
        `intensity` may be one value for all channels or a {channel: intensity} mapping
        (a channel missing from the mapping gets WAIT)."""
        out: dict[str, TemplateChoice] = {}
        for ch in channels:
            level = intensity.get(ch, "WAIT") if isinstance(intensity, Mapping) else intensity
            out[ch] = self.select(ch, language, purpose, level, band, intent=intent)
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
