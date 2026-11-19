"""Sarvam API client (stdlib urllib): cache, timeout, circuit breaker, call budget, masked errors.

    client = shared_client()            # None when there is no key or the models are switched off
    rep = client.systemone(state, questions)          # sarvam-prism   POST /v2/systemone
    rep = client.chat(model, messages, ...)           # LLMs           POST /v2/chat/completions

Every call returns a ModelReply and never raises. A reply carries model, version, latency and whether
it came from the cache; callers put ModelReply.meta() on the CRM envelope. The key travels only in the
`api-subscription-key` header; error strings pass through config.mask_secret() before they are kept.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from .config import load_api_key, mask_secret, models_switched_off

log = logging.getLogger("kairos.sarvam")

BASE_URL = "https://api.sarvam.ai"
CHAT_PATH = "/v2/chat/completions"
SYSTEMONE_PATH = "/v2/systemone"
ADAPTER_VERSION = "kairos-sarvam-1"

PRISM = "sarvam-prism"
CONVERSATIONS = "sarvam-105b-conversations"
DEEPSEEK = "deepseekv4.1-flash-1017"

HttpTransport = Callable[[str, Dict[str, str], Optional[bytes], float], Tuple[int, bytes]]


def urllib_transport(url: str, headers: Dict[str, str], body: Optional[bytes], timeout: float) -> Tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, headers=headers, method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (fixed https host)
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read() or b""


@dataclass
class ModelReply:
    data: Optional[Any]
    error: Optional[str] = None
    latency_ms: int = 0
    model: str = ""
    version: Optional[str] = None     # what the API says it served, when it says
    cached: bool = False

    @property
    def ok(self) -> bool:
        return self.error is None and self.data is not None

    def meta(self) -> Dict[str, Any]:
        """What goes on a CRM envelope: never the input, never the key."""
        return {"model": self.model, "version": self.version or self.model, "adapter": ADAPTER_VERSION,
                "latency_ms": self.latency_ms, "cached": self.cached, "error": self.error}


class CircuitBreaker:
    """Open after `threshold` consecutive failures; closed again (half-open probe) after `cooldown_s`."""

    def __init__(self, threshold: int = 3, cooldown_s: float = 60.0, clock: Callable[[], float] = time.monotonic):
        self.threshold, self.cooldown_s, self.clock = threshold, cooldown_s, clock
        self.failures = 0
        self.opened_at: Optional[float] = None
        self._lock = threading.Lock()

    @property
    def state(self) -> str:
        with self._lock:
            if self.opened_at is None:
                return "closed"
            return "half_open" if self.clock() - self.opened_at >= self.cooldown_s else "open"

    def allow(self) -> bool:
        return self.state != "open"

    def success(self) -> None:
        with self._lock:
            self.failures, self.opened_at = 0, None

    def failure(self) -> None:
        with self._lock:
            self.failures += 1
            if self.failures >= self.threshold:
                self.opened_at = self.clock()


@dataclass
class ClientStats:
    calls: Dict[str, int] = field(default_factory=dict)          # network calls per model
    cache_hits: int = 0
    errors: Dict[str, int] = field(default_factory=dict)         # error kind -> count
    skipped_breaker: int = 0
    skipped_budget: int = 0
    latencies_ms: Dict[str, List[int]] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        def pct(xs: List[int], p: float) -> Optional[int]:
            xs = sorted(xs)
            return xs[min(len(xs) - 1, int(round(p * (len(xs) - 1))))] if xs else None
        return {"calls": dict(self.calls), "cache_hits": self.cache_hits, "errors": dict(self.errors),
                "skipped_breaker_open": self.skipped_breaker, "skipped_budget": self.skipped_budget,
                "latency_ms": {m: {"p50": pct(v, 0.5), "p95": pct(v, 0.95), "n": len(v)}
                               for m, v in self.latencies_ms.items()}}


def _family(model: str) -> str:
    return "prism" if model == PRISM else "llm"


class SarvamClient:
    """One client per process (see shared_client) or per eval run. Thread-safe."""

    def __init__(self, api_key: Optional[str] = None, transport: Optional[HttpTransport] = None,
                 base_url: str = BASE_URL, timeouts: Optional[Dict[str, float]] = None,
                 max_calls: Optional[Dict[str, int]] = None, breaker: Optional[CircuitBreaker] = None,
                 cache_size: int = 5000, live: bool = True):
        self._key = api_key
        self.transport = transport or urllib_transport
        self.base_url = base_url.rstrip("/")
        self.timeouts = {"prism": 5.0, "llm": 6.0, **(timeouts or {})}
        self.max_calls = dict(max_calls or {})                     # family -> cap (None = no cap)
        self.breaker = breaker or CircuitBreaker()
        self.cache_size = cache_size
        self.live = live                                            # False: a fixture transport (no network)
        self.stats = ClientStats()
        self._cache: "OrderedDict[str, ModelReply]" = OrderedDict()
        self._lock = threading.Lock()
        self._family_calls: Dict[str, int] = {}

    def __repr__(self) -> str:   # never show the key
        return f"SarvamClient(base_url={self.base_url!r}, live={self.live}, key={'set' if self._key else 'none'})"

    def enabled(self) -> bool:
        return bool(self._key)

    def _err(self, kind: str, model: str, t0: Optional[float] = None, detail: str = "") -> ModelReply:
        with self._lock:
            self.stats.errors[kind] = self.stats.errors.get(kind, 0) + 1
        msg = mask_secret(f"{kind}{': ' + detail if detail else ''}", [self._key])
        ms = int((time.monotonic() - t0) * 1000) if t0 else 0
        log.info("sarvam %s %s", model, msg)
        return ModelReply(None, msg, ms, model)

    def post(self, path: str, body: Dict[str, Any], model: str) -> ModelReply:
        """POST JSON; cache by body hash; breaker + budget first. Returns parsed JSON in .data."""
        if not self._key:
            return ModelReply(None, "disabled: no key", 0, model)
        payload = json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ck = hashlib.sha256(path.encode() + b"|" + payload).hexdigest()
        with self._lock:
            hit = self._cache.get(ck)
            if hit is not None:
                self._cache.move_to_end(ck)
                self.stats.cache_hits += 1
                return ModelReply(hit.data, None, hit.latency_ms, hit.model, hit.version, True)
        fam = _family(model)
        if not self.breaker.allow():
            with self._lock:
                self.stats.skipped_breaker += 1
            return ModelReply(None, "circuit open", 0, model)
        with self._lock:
            cap = self.max_calls.get(fam)
            if cap is not None and self._family_calls.get(fam, 0) >= cap:
                self.stats.skipped_budget += 1
                return ModelReply(None, "budget exhausted", 0, model)
            self._family_calls[fam] = self._family_calls.get(fam, 0) + 1
            self.stats.calls[model] = self.stats.calls.get(model, 0) + 1
        headers = {"Content-Type": "application/json", "api-subscription-key": self._key}
        t0 = time.monotonic()
        try:
            status, raw = self.transport(self.base_url + path, headers, payload, self.timeouts[fam])
        except Exception as e:  # timeout, DNS, TLS, adapter bug: all fall through
            self.breaker.failure()
            kind = "timeout" if "timeout" in type(e).__name__.lower() or "timed out" in str(e).lower() else "transport"
            return self._err(kind, model, t0, type(e).__name__)
        ms = int((time.monotonic() - t0) * 1000)
        with self._lock:
            self.stats.latencies_ms.setdefault(model, []).append(ms)
        if status != 200:
            self.breaker.failure()
            return self._err(f"http_{status}", model, t0)
        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception:
            self.breaker.failure()
            return self._err("bad_json", model, t0)
        self.breaker.success()
        version = data.get("model") if isinstance(data, dict) and isinstance(data.get("model"), str) else None
        rep = ModelReply(data, None, ms, model, version)
        with self._lock:
            self._cache[ck] = rep
            while len(self._cache) > self.cache_size:
                self._cache.popitem(last=False)
        return rep

    # -- the two endpoints ------------------------------------------------------------------
    def systemone(self, state: str, questions: Dict[str, Any], model: str = PRISM) -> ModelReply:
        return self.post(SYSTEMONE_PATH, {"model": model, "state": state, "questions": questions}, model)

    def chat(self, model: str, messages: List[Dict[str, str]], temperature: float = 0.0, top_p: float = 1.0,
             max_tokens: int = 300, reasoning_effort: Optional[str] = None) -> ModelReply:
        body: Dict[str, Any] = {"model": model, "messages": messages, "temperature": temperature, "top_p": top_p,
                                "max_tokens": max_tokens}
        if reasoning_effort:
            body["reasoning_effort"] = reasoning_effort
        rep = self.post(CHAT_PATH, body, model)
        if not rep.ok:
            return rep
        text = chat_text(rep.data)
        if text is None:
            return ModelReply(None, "schema: no message content", rep.latency_ms, model, rep.version, rep.cached)
        return ModelReply({"text": text}, None, rep.latency_ms, model, rep.version, rep.cached)


def chat_text(resp: Any) -> Optional[str]:
    """OpenAI-style choices[0].message.content; reasoning text (if any) is ignored."""
    try:
        msg = resp["choices"][0]["message"]
        content = msg.get("content")
        if isinstance(content, list):   # content parts
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        return content.strip() if isinstance(content, str) and content.strip() else None
    except Exception:
        return None


# ---------------------------------------------------------------------------------------------
# Process-wide client
# ---------------------------------------------------------------------------------------------

_SHARED: Optional[SarvamClient] = None
_SHARED_KEY_SOURCE = "none"
_SHARED_LOCK = threading.Lock()


def shared_client(env: Optional[Mapping[str, str]] = None) -> Optional[SarvamClient]:
    """The process client, or None when there is no key or KAIROS_SARVAM_MODELS=off."""
    global _SHARED, _SHARED_KEY_SOURCE
    e = os.environ if env is None else env
    if models_switched_off(e):
        return None
    with _SHARED_LOCK:
        if _SHARED is None:
            key, src = load_api_key(e)
            if not key:
                return None
            _SHARED, _SHARED_KEY_SOURCE = SarvamClient(api_key=key), src
        return _SHARED


def shared_key_source() -> str:
    return _SHARED_KEY_SOURCE


def reset_shared_client() -> None:
    global _SHARED, _SHARED_KEY_SOURCE
    with _SHARED_LOCK:
        _SHARED, _SHARED_KEY_SOURCE = None, "none"
