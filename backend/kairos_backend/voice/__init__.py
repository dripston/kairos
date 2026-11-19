"""K17/K18 Sarvam voice engine: bot registry (which agent / voice / language per call) and the
post-call disposition ingester (thresholds -> ledger effects -> CRM envelope). See backend/README.md."""
from .dispositions import DISPOSITIONS, UNCLEAR, Disposition, parse_disposition, report_level
from .ingest import VoiceIngester, decide
from .registry import load_registry, load_thresholds, select_bot

__all__ = ["DISPOSITIONS", "UNCLEAR", "Disposition", "parse_disposition", "report_level", "VoiceIngester",
           "decide", "load_registry", "load_thresholds", "select_bot"]
