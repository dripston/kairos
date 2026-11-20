"""Read-only check that a WhatsApp Cloud API token + phone number id work, before switching CM-03 live mode on.

    cd backend && python scripts/live_wa_check.py

Reads WA_TOKEN, WA_PHONE_NUMBER_ID (and optional WA_GRAPH_VERSION, KAIROS_DEMO_ALLOWLIST, ...) from the
environment, falling back to backend/.env (written by scripts/set_whatsapp_secrets.sh). It makes two GET
requests and changes nothing:

    GET /<PHONE_NUMBER_ID>?fields=display_phone_number,verified_name,quality_rating
    GET /<PHONE_NUMBER_ID>?fields=webhook_configuration      (where Meta will deliver webhooks for this number)

The token goes only in the Authorization header (never in a URL) and is never printed. Phone numbers are
printed masked (last 4 digits). Exit 0 when the number answers; 1 otherwise.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, Dict, Mapping, Optional, Tuple

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

Getter = Callable[[str, Dict[str, str], float], Tuple[int, dict]]
EXPECTED_CALLBACK = "https://kairos-backend-5ifc.onrender.com/cm03/whatsapp/inbound"


def mask(number: Optional[str]) -> str:
    digits = re.sub(r"\D", "", number or "")
    return "<none>" if not digits else ("+" if str(number).strip().startswith("+") else "") + "*" * max(0, len(digits) - 4) + digits[-4:]


def dotenv(path: Path) -> Dict[str, str]:
    out: Dict[str, str] = {}
    try:
        for line in path.read_text().splitlines():
            m = re.match(r"^\s*([A-Z0-9_]+)\s*=\s*(.*)\s*$", line)
            if m:
                out[m.group(1)] = m.group(2).strip().strip("'\"")
    except OSError:
        pass
    return out


def _get(url: str, headers: Dict[str, str], timeout: float) -> Tuple[int, dict]:
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (fixed https host)
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}


def check(env: Mapping[str, str], get: Getter = _get, out=print) -> int:
    token, pnid = env.get("WA_TOKEN") or "", env.get("WA_PHONE_NUMBER_ID") or ""
    version = env.get("WA_GRAPH_VERSION") or "v23.0"
    if not token or not re.fullmatch(r"\d+", pnid):
        out("WA_TOKEN and a numeric WA_PHONE_NUMBER_ID are needed (environment or backend/.env). Nothing checked.")
        return 1
    if not re.fullmatch(r"v\d+\.\d+", version):
        out(f"WA_GRAPH_VERSION must look like v23.0 (got {version!r}).")
        return 1
    headers = {"Authorization": f"Bearer {token}"}
    base = f"https://graph.facebook.com/{version}/{pnid}"
    code, body = get(base + "?" + urllib.parse.urlencode({"fields": "display_phone_number,verified_name,quality_rating"}),
                     headers, 15.0)
    if code != 200 or "error" in body:
        err = body.get("error") or {}
        msg = str(err.get("message", "no details")).replace(token, "<redacted>")
        out(f"FAILED: HTTP {code}, Meta error {err.get('code', '?')} ({err.get('type', '?')}): {msg[:200]}")
        out("Common causes: the token expired or lacks whatsapp_business_messaging / whatsapp_business_management, "
            "or WA_PHONE_NUMBER_ID is not this token's number.")
        return 1
    out("OK: the token can read this business number.")
    out(f"  number        {mask(body.get('display_phone_number'))}")
    out(f"  verified name {body.get('verified_name', '?')}")
    out(f"  quality       {body.get('quality_rating', '?')}")
    code, wh = get(base + "?" + urllib.parse.urlencode({"fields": "webhook_configuration"}), headers, 15.0)
    cfg = (wh or {}).get("webhook_configuration") or {}
    if code == 200 and cfg:
        phone_cb, app_cb = cfg.get("phone_number"), cfg.get("application")
        out(f"  webhooks for this number go to: {phone_cb or app_cb or '?'}"
            f" ({'phone-number override' if phone_cb else 'app callback URL'})")
        if (phone_cb or app_cb) != EXPECTED_CALLBACK:
            out(f"  NOTE: CM-03 live mode expects {EXPECTED_CALLBACK}")
    else:
        out("  webhook configuration: not readable with this token (not needed for sending).")
    try:
        from kairos_backend.cm03.live import live_config
        lc = live_config(env)
        out(f"  CM-03 live mode with these settings: {'ON' if lc.enabled else 'off'}"
            f"{'' if lc.enabled else ' (missing: ' + ', '.join(lc.missing) + ')'}; allow-listed {mask(lc.number)}")
    except Exception as e:  # the check above is what matters; this line is a convenience
        out(f"  (could not evaluate CM-03 live mode: {type(e).__name__})")
    return 0


def main() -> int:
    env = {**dotenv(BACKEND / ".env"), **os.environ}
    return check(env)


if __name__ == "__main__":
    sys.exit(main())
