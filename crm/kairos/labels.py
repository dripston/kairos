"""Display names shared by ingestion and views."""

_CHANNEL_NAMES = {"SMS": "SMS", "WHATSAPP": "WhatsApp", "EMAIL": "Email", "IVR": "IVR", "VOICE": "Voice"}


def channel_name(channel: str) -> str:
    return _CHANNEL_NAMES.get(channel, channel.title())


def rupees(amount) -> str:
    return "amount not stated" if amount is None else f"₹{amount:,}"
