"""PII scrubber for free text stored at rest (red-team RT-F08 tickets, RT-F09 audit / events / payments).

scrub() masks e-mail addresses, Indian phone numbers and — when the caller passes them from the K03
spine — the borrower's own name and number. It uses the same placeholders as hitl.scrub() on the
k06/hitl-control branch ("[phone]", "[email]"), so the two can be merged into one; until then
this is the scrubber the Ledger uses for tickets, audit reasons, reply text and reversal reasons.
"""
from __future__ import annotations

import re
from typing import Optional

__all__ = ["scrub"]

# Every pattern starts only at a token boundary / a digit or "+", so a long run of letters, digits or
# spaces (a 200k-character reply) is scanned in linear time, not retried from every position.
_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# Indian numbers: optional +91 / 91 / 0 prefix, then 10 digits written together or grouped 5+5, 3+3+4, 4+3+3.
# Not inside a longer digit run or word, so 12-digit UPI RRNs, dates and ids are left alone.
_IN_PHONE = re.compile(
    r"(?<![\w+])(?:\+?91[\s-]?|0)?"
    r"(?:\d{10}|\d{5}[\s-]\d{5}|\d{3}[\s-]\d{3}[\s-]\d{4}|\d{4}[\s-]\d{3}[\s-]\d{3})(?!\d)")
_SEP = r"[\s\-.()]*"


def _own_phone(phone: Optional[str]) -> Optional[re.Pattern]:
    """The borrower's own number in any spacing, with or without +91 / 0, even inside a word."""
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) < 8:
        return None
    core = digits[-10:]
    return re.compile(rf"(?=[+\d])(?:\+?{_SEP}91{_SEP}|0{_SEP})?" + _SEP.join(core))


def _own_name(name: Optional[str]) -> Optional[re.Pattern]:
    parts = (name or "").split()
    return re.compile(r"\s+".join(map(re.escape, parts)), re.IGNORECASE) if parts else None


def scrub(text: Optional[str], *, name: Optional[str] = None, phone: Optional[str] = None,
          name_as: str = "[name]") -> str:
    """Mask PII in free text before it is stored. `name` / `phone` are the borrower's identity from the
    spine (borrowers.full_name / phone); the full name becomes `name_as` (e.g. the borrower id)."""
    out = _EMAIL.sub("[email]", text or "")
    own = _own_phone(phone)
    if own:
        out = own.sub("[phone]", out)
    out = _IN_PHONE.sub("[phone]", out)
    who = _own_name(name)
    if who:
        out = who.sub(lambda _: name_as, out)
    return out
