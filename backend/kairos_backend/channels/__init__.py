"""Kairos channel layer: provider maps for the Executor.

    build_providers("mock")        sms, whatsapp, ivr, voicebot -> MockChannelProvider (SIMULATED);
                                   email -> the K20 email adapter if present, else FakeProvider.
    build_providers("phone-demo")  sms -> MessagesAppSmsProvider, whatsapp -> WhatsAppCloudTestProvider
                                   (both dry-run unless dry_run=False AND their env flags are set);
                                   ivr, voicebot, email as in "mock".

Callbacks from any provider go through normalize.apply_callback(ledger, event).
"""
from __future__ import annotations

import importlib
import os
from typing import Dict, Mapping, Optional

from ..executor import FakeProvider
from .mock import CHANNELS, PROFILES, ChannelProfile, MockChannelProvider
from .normalize import apply_all, apply_callback, dedupe_key, is_stop_text
from .phone_demo import (DemoSafetyError, MessagesAppSmsProvider, WhatsAppCloudTestProvider, click_to_chat_url,
                         ledger_recipient_lookup, mask_number)

MODES = ("mock", "phone-demo")

__all__ = ["build_providers", "MODES", "MockChannelProvider", "ChannelProfile", "PROFILES", "CHANNELS",
           "apply_callback", "apply_all", "dedupe_key", "is_stop_text", "MessagesAppSmsProvider",
           "WhatsAppCloudTestProvider", "DemoSafetyError", "click_to_chat_url", "mask_number", "email_provider"]


def email_provider(seed: int = 0):
    """K20 owns email (kairos_backend/email_channel.py). If that module exposes
    `build_provider(seed=...)` or `build_provider()` we use it; otherwise a FakeProvider."""
    try:
        mod = importlib.import_module("kairos_backend.email_channel")
    except ImportError:
        return FakeProvider("email")
    factory = getattr(mod, "build_provider", None)
    if callable(factory):
        try:
            return factory(seed=seed)
        except TypeError:
            return factory()
    return FakeProvider("email")


def build_providers(mode: Optional[str] = None, *, seed: int = 0, profile="realistic", ledger=None,
                    env: Optional[Mapping[str, str]] = None, dry_run: bool = True,
                    runner=None, transport=None) -> Dict[str, object]:
    """Provider map for Executor(ledger, providers). mode defaults to $KAIROS_CHANNELS or "mock".
    With a ledger, demo adapters look up the borrower's phone in the K03 spine (allow-list still applies)."""
    env_map = dict(os.environ if env is None else env)
    mode = mode or env_map.get("KAIROS_CHANNELS", "mock")
    if mode not in MODES:
        raise ValueError(f"unknown channel mode {mode!r}; expected one of {MODES}")
    providers: Dict[str, object] = {ch: MockChannelProvider(ch, seed=seed, profile=profile) for ch in CHANNELS}
    providers["email"] = email_provider(seed)
    if mode == "phone-demo":
        lookup = ledger_recipient_lookup(ledger) if ledger is not None else None
        providers["sms"] = MessagesAppSmsProvider(env=env_map, dry_run=dry_run, runner=runner, recipient_lookup=lookup)
        providers["whatsapp"] = WhatsAppCloudTestProvider(env=env_map, dry_run=dry_run, transport=transport,
                                                          recipient_lookup=lookup)
    return providers
