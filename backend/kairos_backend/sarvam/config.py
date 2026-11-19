"""Sarvam API key loading and the on/off switches. Stdlib only; never prints, logs or returns a key in text.

Key precedence (first non-empty wins):
  1. env SARVAM_API_KEY
  2. the .env file named by env KAIROS_DOTENV
  3. backend/.env
  4. repo-root .env
  5. ~/.kairos/sarvam_api_key (the whole file is the key)

No key -> every Sarvam model tier is off and Kairos runs exactly as before (fully deterministic).
KAIROS_SARVAM_MODELS=off turns everything off even with a key (tests set it, see tests/conftest.py).
KAIROS_SARVAM_FEATURES=prism,voice,phrasing,narrative picks a subset (default: all four).
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Tuple

KEY_ENV = "SARVAM_API_KEY"
DOTENV_ENV = "KAIROS_DOTENV"
SWITCH_ENV = "KAIROS_SARVAM_MODELS"
FEATURES_ENV = "KAIROS_SARVAM_FEATURES"
FEATURES = ("prism", "voice", "phrasing", "narrative")

BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_DIR = BACKEND_DIR.parent
HOME_KEY_FILE = Path("~/.kairos/sarvam_api_key")

_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")
# Anything that looks like a Sarvam key (sk_...) or a long opaque token is masked in error text.
_SECRET_LIKE = re.compile(r"sk_[A-Za-z0-9_\-]{4,}")


def parse_dotenv(text: str) -> Dict[str, str]:
    """KEY=VALUE lines, '#' comments, optional single / double quotes, no shell expansion."""
    out: Dict[str, str] = {}
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _LINE.match(line)
        if not m:
            continue
        k, v = m.group(1), m.group(2)
        if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
            v = v[1:-1]
        else:
            v = re.split(r"\s+#", v, maxsplit=1)[0].strip()   # inline comment after an unquoted value
        out[k] = v
    return out


def read_dotenv(path: Path) -> Dict[str, str]:
    """Parsed .env, or {} when missing / unreadable. Never raises, never echoes content."""
    try:
        return parse_dotenv(Path(path).expanduser().read_text(encoding="utf-8"))
    except Exception:
        return {}


def _read_key_file(path: Path) -> str:
    try:
        return Path(path).expanduser().read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def key_sources(env: Optional[Mapping[str, str]] = None) -> List[Tuple[str, Path]]:
    """(label, path) for the file sources, in precedence order (after the env var)."""
    e = os.environ if env is None else env
    out: List[Tuple[str, Path]] = []
    if e.get(DOTENV_ENV):
        out.append((DOTENV_ENV, Path(e[DOTENV_ENV])))
    out += [("backend/.env", BACKEND_DIR / ".env"), (".env", REPO_DIR / ".env"),
            ("~/.kairos/sarvam_api_key", HOME_KEY_FILE)]
    return out


def load_api_key(env: Optional[Mapping[str, str]] = None,
                 sources: Optional[Iterable[Tuple[str, Path]]] = None) -> Tuple[Optional[str], str]:
    """(key or None, source label). The label is safe to show ("env", "backend/.env", ...); the key is not."""
    e = os.environ if env is None else env
    v = (e.get(KEY_ENV) or "").strip()
    if v:
        return v, "env"
    for label, path in (key_sources(e) if sources is None else sources):
        if label.endswith("sarvam_api_key"):
            v = _read_key_file(path)
        else:
            v = (read_dotenv(path).get(KEY_ENV) or "").strip()
        if v:
            return v, label
    return None, "none"


def models_switched_off(env: Optional[Mapping[str, str]] = None) -> bool:
    e = os.environ if env is None else env
    return (e.get(SWITCH_ENV) or "").strip().lower() in ("0", "off", "false", "no", "disabled")


def feature_enabled(name: str, env: Optional[Mapping[str, str]] = None) -> bool:
    """Is this feature allowed by the switches? (A key is still needed: see client.shared_client.)"""
    e = os.environ if env is None else env
    if models_switched_off(e):
        return False
    raw = (e.get(FEATURES_ENV) or "").strip()
    if not raw:
        return True
    return name in {f.strip().lower() for f in raw.split(",")}


def mask_secret(text: object, secrets: Iterable[Optional[str]] = ()) -> str:
    """Error text safe to log: every known secret and anything sk_-shaped becomes 'sk_***'."""
    s = "" if text is None else str(text)
    for sec in secrets:
        if sec and len(sec) >= 4:
            s = s.replace(sec, "sk_***")
    return _SECRET_LIKE.sub("sk_***", s)
