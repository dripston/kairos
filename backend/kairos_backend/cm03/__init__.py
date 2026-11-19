"""CM-03: inbound WhatsApp servicing agent (verify, service, feed back).

    engine.ConversationEngine  deterministic per-conversation state machine
                               greet -> identify -> OTP verify -> intent -> resolve / hand off -> close
    otp.OtpService             6 digits, 5-min expiry, 3 attempts then lockout; stored hashed
    fraud                      deterministic, explainable fake / fraud detectors
    tools.ToolBox              the audited tool calls the engine makes (spine, OTP, links, PTP, STOP,
                               tickets, WhatsApp reply); every call is a CRM timeline envelope
    world                      synthetic borrowers + synthetic D7 bank feed (SIMULATED)
    api.router                 /cm03/whatsapp/inbound, /cm03/conversations/{id}, /cm03/metrics, /cm03/console

Everything here is SIMULATED: synthetic borrowers, a synthetic bank feed, and a WhatsApp adapter
that runs in dry-run mode (nothing leaves the machine).
"""
from __future__ import annotations

PROVENANCE = "SIMULATED"

__all__ = ["PROVENANCE"]
