"""Optional Sarvam-hosted models, used only where they improve results and never for a decision.

  sarvam-prism               intent-stack decision tier (classify only) + low-confidence voice re-classification
  sarvam-105b-conversations  CM-03 reply phrasing AFTER the engine chose state + action; output validator; verbatim fallback
  deepseekv4.1-flash-1017    operator-facing narratives (K05 summary, hand-off case summary, decision explanation,
                             K26 daily ops paragraph), labelled AI-generated, fact-checked, deterministic fallback

No key (see config.py) -> all of it is off and Kairos is byte-for-byte the deterministic system.
"""
from .client import CONVERSATIONS, DEEPSEEK, PRISM, ModelReply, SarvamClient, shared_client  # noqa: F401
from .config import feature_enabled, load_api_key, mask_secret  # noqa: F401
