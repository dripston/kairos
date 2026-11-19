"""Kairos channel layer: provider maps for the Executor.

    build_providers("mock")        sms, whatsapp, ivr, voicebot -> MockChannelProvider (SIMULATED);
                                   email -> the K20 email adapter if present, else FakeProvider.
    build_providers("phone-demo")  sms -> select_sms_provider() (below), whatsapp -> WhatsAppCloudTestProvider
                                   (demo adapters are dry-run unless dry_run=False AND their env flags are set);
                                   ivr, voicebot, email as in "mock".

SMS provider order in "phone-demo" (first match wins; see select_sms_provider):
    1. KAIROS_DEMO_ANDROID_SMS=1  -> AndroidSmsGatewayProvider  (team member's Android phone, SMSGate app)
    2. KAIROS_DEMO_SMS=1          -> MessagesAppSmsProvider     (Mac Messages + iPhone Text Message Forwarding)
    3. neither                    -> MockChannelProvider("sms") (SIMULATED; nothing leaves the machine)

Callbacks from any provider go through normalize.apply_callback(ledger, event).
"""
from __future__ import annotations

import importlib
import os
from typing import Dict, Mapping, Optional

from ..executor import FakeProvider
from .android_sms import AndroidSmsGatewayProvider
from .mock import CHANNELS, PROFILES, ChannelProfile, MockChannelProvider
from .normalize import apply_all, apply_callback, dedupe_key, is_stop_text
from .phone_demo import (DemoSafetyError, MessagesAppSmsProvider, WhatsAppCloudTestProvider, click_to_chat_url,
                         ledger_recipient_lookup, mask_number)

MODES = ("mock", "phone-demo")

__all__ = ["build_providers", "MODES", "MockChannelProvider", "ChannelProfile", "PROFILES", "CHANNELS",
           "apply_callback", "apply_all", "dedupe_key", "is_stop_text", "MessagesAppSmsProvider",
           "WhatsAppCloudTestProvider", "AndroidSmsGatewayProvider", "DemoSafetyError", "click_to_chat_url", "mask_number",
           "email_provider", "select_sms_provider"]


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


def select_sms_provider(env: Optional[Mapping[str, str]] = None, *, dry_run: bool = True, runner=None,
                        sms_transport=None, recipient_lookup=None, seed: int = 0, profile="realistic"):
    """The SMS provider for phone-demo mode: Android (SMSGate) if KAIROS_DEMO_ANDROID_SMS=1, else the Mac
    Messages app if KAIROS_DEMO_SMS=1, else the SIMULATED mock. Every demo provider keeps all its rails."""
    env_map = dict(os.environ if env is None else env)
    if env_map.get("KAIROS_DEMO_ANDROID_SMS") == "1":
        return AndroidSmsGatewayProvider(env=env_map, dry_run=dry_run, transport=sms_transport,
                                         recipient_lookup=recipient_lookup)
    if env_map.get("KAIROS_DEMO_SMS") == "1":
        return MessagesAppSmsProvider(env=env_map, dry_run=dry_run, runner=runner, recipient_lookup=recipient_lookup)
    return MockChannelProvider("sms", seed=seed, profile=profile)


def build_providers(mode: Optional[str] = None, *, seed: int = 0, profile="realistic", ledger=None,
                    env: Optional[Mapping[str, str]] = None, dry_run: bool = True,
                    runner=None, transport=None, sms_transport=None) -> Dict[str, object]:
    """Provider map for Executor(ledger, providers). mode defaults to $KAIROS_CHANNELS or "mock".
    With a ledger, demo adapters look up the borrower's phone in the K03 spine (allow-list still applies).
    `transport` is the WhatsApp HTTP transport, `sms_transport` the Android SMSGate one, `runner` osascript's."""
    env_map = dict(os.environ if env is None else env)
    mode = mode or env_map.get("KAIROS_CHANNELS", "mock")
    if mode not in MODES:
        raise ValueError(f"unknown channel mode {mode!r}; expected one of {MODES}")
    providers: Dict[str, object] = {ch: MockChannelProvider(ch, seed=seed, profile=profile) for ch in CHANNELS}
    providers["email"] = email_provider(seed)
    if mode == "phone-demo":
        lookup = ledger_recipient_lookup(ledger) if ledger is not None else None
        providers["sms"] = select_sms_provider(env_map, dry_run=dry_run, runner=runner, sms_transport=sms_transport,
                                               recipient_lookup=lookup, seed=seed, profile=profile)
        providers["whatsapp"] = WhatsAppCloudTestProvider(env=env_map, dry_run=dry_run, transport=transport,
                                                          recipient_lookup=lookup)
    return providers
