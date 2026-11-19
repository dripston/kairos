"""Runtime allow-list for the phone-demo adapters (SMS via the Mac's Messages app, WhatsApp test number).

The env var KAIROS_DEMO_ALLOWLIST still works; this adds numbers an operator enters at runtime
(e.g. from the frontend's "Demo recipients" panel) so a live demo can use any team member's phone
without editing config. Numbers are stored ONLY on this machine in a gitignored file
(default ~/.kairos/demo_recipients.json, outside the repo; override KAIROS_DEMO_RECIPIENTS_FILE) and are only ever
returned masked. Being on the list never sends anything by itself: the channel flag
(KAIROS_DEMO_SMS / KAIROS_DEMO_WHATSAPP = 1), an approved template and dry_run=False are all still required.
"""
from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path
from typing import Mapping, Optional

_LOCK = threading.Lock()
# Outside every repo checkout on purpose: personal numbers can never be committed by accident.
DEFAULT_FILE = Path.home() / ".kairos" / "demo_recipients.json"


def _file(env: Optional[Mapping[str, str]] = None) -> Path:
    env = os.environ if env is None else env
    return Path(env.get("KAIROS_DEMO_RECIPIENTS_FILE") or DEFAULT_FILE)


def to_e164(number: str, default_country: str = "+91") -> str:
    """Accepts '9876543210', '09876543210', '+91 98765 43210', '0091…' (fake examples). India by default."""
    from .phone_demo import normalize_e164  # local import avoids a cycle
    n = re.sub(r"[\s\-().]", "", number or "")
    if re.fullmatch(r"0?[6-9]\d{9}", n):          # Indian mobile without country code
        n = default_country + n[-10:]
    return normalize_e164(n)


def load(env: Optional[Mapping[str, str]] = None) -> frozenset:
    p = _file(env)
    if not p.exists():
        return frozenset()
    try:
        return frozenset(json.loads(p.read_text()).get("recipients", []))
    except (ValueError, OSError):
        return frozenset()


def _save(numbers: frozenset, env: Optional[Mapping[str, str]] = None) -> None:
    p = _file(env)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps({"recipients": sorted(numbers)}, indent=2))
    os.chmod(tmp, 0o600)
    tmp.replace(p)


def add(number: str, env: Optional[Mapping[str, str]] = None) -> str:
    e164 = to_e164(number)
    with _LOCK:
        _save(load(env) | {e164}, env)
    return e164


def remove(number: str, env: Optional[Mapping[str, str]] = None) -> bool:
    e164 = to_e164(number)
    with _LOCK:
        cur = load(env)
        if e164 not in cur:
            return False
        _save(cur - {e164}, env)
    return True


def resolve(hint: str, env: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """Find one listed number from a hint that is safe to put in a URL or a log line: the masked form
    ('+********3210'), the last 4+ digits ('3210'), or (legacy) the full number. A hint that matches more
    than one listed number is refused (DemoSafetyError); no match returns None."""
    from .phone_demo import DemoSafetyError
    hint = (hint or "").strip()
    digits = re.sub(r"\D", "", hint)
    if "*" not in hint and "•" not in hint and len(digits) >= 8:
        e164 = to_e164(hint)
        return e164 if e164 in load(env) else None
    if len(digits) < 4:
        raise DemoSafetyError("give the masked number or at least its last 4 digits")
    hits = [n for n in load(env) if re.sub(r"\D", "", n).endswith(digits[-4:])
            and (not hint.startswith("+") or n.startswith("+"))]
    if "*" in hint or "•" in hint:  # a masked form also fixes the length
        hits = [n for n in hits if len(re.sub(r"\D", "", n)) == len(re.sub(r"[^\d*•]", "", hint))]
    if len(hits) > 1:
        raise DemoSafetyError("more than one demo recipient matches; give more digits")
    return hits[0] if hits else None


def remove_matching(hint: str, env: Optional[Mapping[str, str]] = None) -> bool:
    """remove() by a masked / last-4 hint (see resolve)."""
    with _LOCK:
        n = resolve(hint, env)
        if n is None:
            return False
        _save(load(env) - {n}, env)
    return True


def masked(env: Optional[Mapping[str, str]] = None) -> list:
    from .phone_demo import mask_number
    return [mask_number(n) for n in sorted(load(env))]
