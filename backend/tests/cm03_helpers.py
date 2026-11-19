"""Shared CM-03 test world: fresh ledger + engine on a temp SQLite file, a movable clock, demo borrowers."""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from kairos_backend.cm03.engine import ConversationEngine
from kairos_backend.cm03.world import DEMO_FEED, seed_demo
from kairos_backend.ledger import Ledger

B1042, B2001, B3003 = "+910000001042", "+910000002001", "+910000003003"
UNREGISTERED = "+910999999999"
SETTLED_REF, PENDING_REF = DEMO_FEED["settled"], DEMO_FEED["pending"]


class Clock:
    def __init__(self, policy):
        self.now = datetime(2026, 11, 20, 11, 0, tzinfo=policy.tz)

    def __call__(self):
        return self.now

    def advance(self, **kw):
        self.now += timedelta(**kw)


class Chat:
    def __init__(self, policy, tmp_path):
        self.clock = Clock(policy)
        self.ledger = Ledger(policy, tmp_path / "cm03.db")
        self.engine = ConversationEngine(self.ledger, clock=self.clock)
        self.fixtures = seed_demo(self.ledger, self.clock().date())

    def say(self, phone, text=None, **kw):
        return self.engine.handle(phone, text, **kw)

    def otp(self, phone):
        msgs = [m for m in self.engine.adapter.inbox(phone) if m["kind"] == "otp"]
        return re.match(r"(\d{6})", msgs[-1]["text"]).group(1) if msgs else None

    def verify(self, phone, first="hi"):
        self.say(phone, first)
        return self.say(phone, self.otp(phone))

    def timeline(self, conversation_id):
        return self.engine.timeline(conversation_id)
