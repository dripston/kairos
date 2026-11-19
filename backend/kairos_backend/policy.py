"""Load the K01 outreach policy and K13 exceptions policy.

policy/ (repo root) is the single source of truth. Code reads every number from
policy.json / exceptions.json and never hard-codes one (K01 README rule).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_POLICY_DIR = REPO_ROOT / "policy"

# India has no DST, so a fixed offset is exact and avoids needing tzdata on Windows.
_FIXED_ZONES = {"Asia/Kolkata": timezone(timedelta(hours=5, minutes=30), "IST")}


def policy_dir() -> Path:
    return Path(os.environ.get("KAIROS_POLICY_DIR", DEFAULT_POLICY_DIR))


def tightest(*vals: Optional[int]) -> Optional[int]:
    """Smallest non-null cap; None if no source sets one (K01 effective_cap_rule)."""
    present = [v for v in vals if v is not None]
    return min(present) if present else None


def loosest_gap(*vals: Optional[int]) -> Optional[int]:
    """Longest non-null minimum gap (K01: effective gap = longest of the sources)."""
    present = [v for v in vals if v is not None]
    return max(present) if present else None


@dataclass
class Policy:
    raw: dict
    exceptions: dict = field(default_factory=dict)
    lenders: dict[str, dict] = field(default_factory=dict)
    official_caps: dict = field(default_factory=dict)

    # ---------- identity ----------
    @property
    def version(self) -> str:
        return self.raw["policy_version"]

    @property
    def exceptions_version(self) -> Optional[str]:
        return self.exceptions.get("policy_version")

    @property
    def tz(self):
        name = self.raw.get("timezone", "Asia/Kolkata")
        if name in _FIXED_ZONES:
            return _FIXED_ZONES[name]
        from zoneinfo import ZoneInfo  # needs tzdata on Windows for other zones

        return ZoneInfo(name)

    def now(self) -> datetime:
        return datetime.now(self.tz)

    def local(self, dt: datetime) -> datetime:
        return dt.astimezone(self.tz) if dt.tzinfo else dt.replace(tzinfo=self.tz)

    # ---------- bands ----------
    def band_for(self, dpd: int) -> Optional[str]:
        for b in self.raw["dpd_bands"]:
            lo, hi = b["dpd_min"], b["dpd_max"]
            if (lo is None or dpd >= lo) and (hi is None or dpd <= hi):
                return b["id"]
        return None

    def band(self, band_id: str) -> dict:
        return next(b for b in self.raw["dpd_bands"] if b["id"] == band_id)

    def channel_allowed(self, band_id: str, channel: str) -> bool:
        return channel in self.raw["channels_allowed_by_band"].get(band_id, [])

    # ---------- calendar ----------
    def is_quiet_day(self, d: date) -> bool:
        qd = self.raw["contact_window"]["quiet_days"]
        return d.strftime("%a").upper()[:3] in qd["weekdays"] or d.isoformat() in qd["national_holidays"]

    def in_contact_window(self, hhmm: str) -> bool:
        w = self.raw["contact_window"]
        return w["start"] <= hhmm < w["end_exclusive"]

    def contact_days(self, start: date, end: date) -> list[date]:
        days, d = [], start
        while d <= end:
            if not self.is_quiet_day(d):
                days.append(d)
            d += timedelta(days=1)
        return days

    def next_contact_day(self, d: date) -> date:
        d += timedelta(days=1)
        while self.is_quiet_day(d):
            d += timedelta(days=1)
        return d

    # ---------- caps (strictest of policy / lender / official) ----------
    @property
    def caps(self) -> dict:
        return self.raw["caps"]

    def lender(self, lender_id: Optional[str]) -> dict:
        return self.lenders.get(lender_id or "", {})

    def channel_day_cap(self, band: str, channel: str, lender_id: Optional[str] = None,
                        official: Optional[int] = None) -> Optional[int]:
        lender = self.lender(lender_id)
        ch_override = lender.get("per_channel_overrides", {}).get(channel, {}) or {}
        if official is None:
            official = (self.official_caps.get("per_channel_per_day_by_band", {}) or {}).get(band)
        return tightest(
            self.caps["per_channel_per_day_by_band"].get(band),
            (lender.get("per_channel_per_day_by_band") or {}).get(band),
            ch_override.get("per_day"),
            official,
        )

    def all_channel_day_cap(self, band: str, lender_id: Optional[str] = None) -> tuple[Optional[int], Optional[int]]:
        """Returns (effective cap, policy's own value) so the reason code can tell them apart."""
        pol = self.caps["all_channels_per_day_by_band"].get(band)
        lender = self.lender(lender_id)
        return tightest(pol, (lender.get("all_channels_per_day_by_band") or {}).get(band)), pol

    def weekly_caps(self, band: str, channel: str, lender_id: Optional[str] = None) -> tuple[Optional[int], Optional[int]]:
        """(per-channel weekly cap, all-channel weekly cap). Policy sets none today; lenders may."""
        lender = self.lender(lender_id)
        ch_override = lender.get("per_channel_overrides", {}).get(channel, {}) or {}
        per_ch = tightest((lender.get("per_channel_per_week_by_band") or {}).get(band), ch_override.get("per_week"))
        per_all = (lender.get("all_channels_per_week_by_band") or {}).get(band)
        return per_ch, per_all

    def min_gap_minutes(self, lender_id: Optional[str] = None) -> int:
        lender = self.lender(lender_id)
        return loosest_gap(
            self.caps["min_gap_minutes_same_channel"],
            lender.get("min_gap_minutes_same_channel"),
            self.official_caps.get("min_gap_minutes_same_channel"),
        )

    # ---------- other knobs ----------
    @property
    def stale_after_hours(self) -> float:
        return self.raw["dpd_rules"]["stale_after_hours"]

    @property
    def call_pickup_threshold_s(self) -> int:
        trig = next(t for t in self.raw["stop_for_day_triggers"] if t["id"] == "CALL_PICKUP")
        return trig["threshold_seconds"]

    @property
    def template_languages(self) -> list[str]:
        return self.raw["templates"]["languages"]

    @property
    def evaluation_order(self) -> list[str]:
        return list(self.raw["evaluation_order"])

    def summary(self) -> dict[str, Any]:
        return {
            "policy_id": self.raw["policy_id"],
            "policy_version": self.version,
            "status": self.raw.get("status"),
            "exceptions_version": self.exceptions_version,
            "evaluation_order": self.evaluation_order,
            "lenders": sorted(self.lenders),
            "official_caps_loaded": bool(self.official_caps),
        }


def _read(p: Path) -> dict:
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def load_policy(base: Optional[Path] = None, lender_files: Optional[list[Path]] = None,
                official_caps_file: Optional[Path] = None) -> Policy:
    """Load K01 (+ K13 if present). Lender caps come from policy.caps.lender_overrides
    (when it names a lender) plus any extra lender files passed in."""
    base = Path(base) if base else policy_dir()
    raw = _read(base / "K01-decision-policy" / "policy.json")
    exc_path = base / "K13-operational-exceptions" / "exceptions.json"
    exceptions = _read(exc_path) if exc_path.exists() else {}

    lenders: dict[str, dict] = {}
    built_in = raw["caps"].get("lender_overrides") or {}
    if built_in.get("lender_id"):
        lenders[built_in["lender_id"]] = built_in
    for lf in lender_files or []:
        data = _read(Path(lf))
        lenders[data.get("lender_id") or Path(lf).stem] = data

    official = {}
    off_path = official_caps_file or (base / "official_caps.json")  # organisers' D11, when it lands
    if Path(off_path).exists():
        official = _read(Path(off_path))

    return Policy(raw=raw, exceptions=exceptions, lenders=lenders, official_caps=official)
