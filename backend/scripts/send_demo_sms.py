"""Send ONE approved demo SMS through the phone-demo SMS provider — dry run unless --live.

Works for every SMS demo provider: Android phone (SMS Gateway for Android, KAIROS_DEMO_ANDROID_SMS=1)
and Mac Messages + iPhone (KAIROS_DEMO_SMS=1). With --provider auto (default) the order is
Android, then Messages. All the adapter's safety rails apply: env flag, allow-listed recipient,
approved template id, credentials from env. The request is printed with the number and secrets masked.

    python scripts/send_demo_sms.py --list
    python scripts/send_demo_sms.py --to 0001 --template DEMO_SMS_HELLO            # dry run (default)
    python scripts/send_demo_sms.py --to 0001 --template DEMO_SMS_HELLO --live     # really sends
    python scripts/send_demo_sms.py --status DEMO-CLI-1795150000                   # Android: ask the phone

--to takes a full number that is already allow-listed, or just its last digits / masked form
(e.g. 0001 or +********0001) — matched against the allow-list, so you never type the full number.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # run from anywhere without installing

from kairos_backend.channels import (AndroidSmsGatewayProvider, DemoSafetyError,  # noqa: E402
                                     MessagesAppSmsProvider, select_sms_provider)
from kairos_backend.channels.phone_demo import DEMO_SMS_TEMPLATES, effective_allowlist, mask_number  # noqa: E402

PROVIDERS = {"android": AndroidSmsGatewayProvider, "messages": MessagesAppSmsProvider}


def resolve_recipient(arg: str, allow: frozenset) -> str:
    """Full allow-listed number, or a unique match on its last digits (>= 4)."""
    digits = re.sub(r"\D", "", arg)
    full = [n for n in allow if re.sub(r"\D", "", n) == digits]
    if full:
        return full[0]
    if len(digits) < 4:
        raise SystemExit("--to: give the full allow-listed number or at least its last 4 digits")
    hits = sorted(n for n in allow if re.sub(r"\D", "", n).endswith(digits))
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise SystemExit(f"--to: no allow-listed number ends in {digits[-4:]}. Allow-listed: "
                         f"{[mask_number(n) for n in sorted(allow)] or 'none'} (add yours: see backend/README.md)")
    raise SystemExit(f"--to: {len(hits)} allow-listed numbers match; give more digits: {[mask_number(n) for n in hits]}")


def build(provider: str, live: bool):
    if provider == "auto":
        p = select_sms_provider(os.environ, dry_run=not live)
        if not isinstance(p, tuple(PROVIDERS.values())):
            raise SystemExit("No SMS demo provider is enabled. Set KAIROS_DEMO_ANDROID_SMS=1 (Android phone) "
                             "or KAIROS_DEMO_SMS=1 (Mac + iPhone) first.")
        return p
    return PROVIDERS[provider](env=os.environ, dry_run=not live)


def show_status(provider, iid: str) -> int:
    if not isinstance(provider, AndroidSmsGatewayProvider):
        print("--status is only available for the Android provider (the Messages app has no receipts).")
        return 1
    st = provider.status(iid)
    body = provider.last_state.get(iid) or {}
    recips = [{**r, "phoneNumber": mask_number(r.get("phoneNumber"))} for r in body.get("recipients") or []]
    print(json.dumps({"instruction_id": iid, "status": st, "gateway_state": body.get("state"),
                      "recipients": recips, "states": body.get("states")}, indent=2))
    return 0 if st not in ("UNKNOWN", "NOT_FOUND") else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--to", help="allow-listed number, or its last digits / masked form")
    ap.add_argument("--template", help=f"approved demo template id: {', '.join(DEMO_SMS_TEMPLATES)}")
    ap.add_argument("--param", action="append", default=[], metavar="KEY=VALUE",
                    help="template placeholder, e.g. --param amount=4,999 --param due_date='25 Nov'")
    ap.add_argument("--provider", choices=("auto", "android", "messages"), default="auto")
    ap.add_argument("--live", action="store_true", help="really send (default: dry run, nothing leaves the machine)")
    ap.add_argument("--id", help="instruction id (default DEMO-CLI-<unix time>)")
    ap.add_argument("--status", metavar="INSTRUCTION_ID", help="Android: ask the phone what happened to a send")
    ap.add_argument("--list", action="store_true", help="show approved templates and the masked allow-list")
    a = ap.parse_args(argv)

    if a.list:
        print("Approved demo SMS templates:")
        for tid, text in DEMO_SMS_TEMPLATES.items():
            print(f"  {tid}: {text}")
        allow = effective_allowlist(os.environ)
        print("Allow-listed recipients:", [mask_number(n) for n in sorted(allow)] or "none")
        return 0
    provider = build(a.provider, a.live)
    if a.status:
        return show_status(provider, a.status)
    if not a.to or not a.template:
        ap.error("--to and --template are required (or use --list / --status)")

    to = resolve_recipient(a.to, effective_allowlist(os.environ))
    params = dict(p.split("=", 1) for p in a.param if "=" in p)
    touch = {"instruction_id": a.id or f"DEMO-CLI-{int(time.time())}", "borrower_id": "DEMO-CLI", "channel": "sms",
             "template_id": a.template, "to": to, "params": params}
    try:
        _, preview = provider.prepare(touch)
    except DemoSafetyError as exc:
        print(f"REFUSED by {provider.name}: {exc}")
        return 1
    print(f"provider: {provider.name}{' (' + provider.mode() + ' mode)' if hasattr(provider, 'mode') else ''}")
    print(f"instruction_id: {touch['instruction_id']}")
    print(f"to: {preview.recipient_masked}")
    print(f"text: {preview.text}")
    print("request (masked):")
    print(json.dumps(preview.request, indent=2, ensure_ascii=False))
    if not a.live:
        print("\nDRY RUN: nothing was sent. Add --live to send it for real.")
        return 0
    res = provider.send(touch)
    print(f"\nresult: {res.status}" + (f" provider_ref={res.provider_ref}" if res.provider_ref else "")
          + (f" error={res.error}" if res.error else ""))
    if res.status == "ACCEPTED" and isinstance(provider, AndroidSmsGatewayProvider):
        print(f"check delivery: python scripts/send_demo_sms.py --status {touch['instruction_id']}")
    return 0 if res.status == "ACCEPTED" else 1


if __name__ == "__main__":
    sys.exit(main())
