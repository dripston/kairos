"""No-network transports for the Sarvam client: recorded fixtures, a deterministic STAND-IN, and a recorder.

FixtureTransport replays a response recorded from a live run (tests/data/sarvam_recorded.json, keyed by
a hash of the request body; the inputs were already masked, the key is never part of the hash or the file).
When there is no recording it answers with a deterministic STAND-IN so every code path (parsing, schema
fallback, validator, fact check) runs offline:

  * prism       -> the intent stack's own Naive Bayes model + safety regexes, in a prism-like JSON shape;
                   every `bad_every`-th answer is malformed on purpose (schema fallback must hold)
  * conversations -> the approved line (Hinglish style gets a "Ji, " prefix); every `bad_every`-th
                   candidate is deliberately bad (amount, threat, promise, too long, link) - the validator
                   must catch every one
  * deepseek    -> a sentence built from the facts; every `bad_every`-th one invents a number, a phone or
                   advice - the fact check must catch every one

STAND-IN numbers measure the PLUMBING, never the model. Reports say "FIXTURE / STAND-IN, LIVE NOT RUN".
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .config import BACKEND_DIR

RECORDED_PATH = BACKEND_DIR / "tests" / "data" / "sarvam_recorded.json"

_DISP_FROM_INTENT = {"PAY_NOW": "PAY_NOW_LINK", "PTP": "PTP", "NEEDS_TIME": "NEEDS_TIME_NO_DATE",
                     "ALREADY_PAID": "ALREADY_PAID", "CALLBACK": "CALLBACK_REQUESTED", "HARDSHIP": "HARDSHIP",
                     "DISPUTE": "DISPUTE", "STOP": "STOP_REQUESTED", "WRONG_NUMBER": "WRONG_PERSON",
                     "HUMAN": "HUMAN_REQUESTED", "LANGUAGE_CHANGE": "LANGUAGE_BARRIER", "ABUSIVE": "ABUSIVE",
                     "LINK_REQUEST": "PAY_NOW_LINK", "UNKNOWN": "NO_RESPONSE", "GREETING": "NO_RESPONSE",
                     "BALANCE_QUERY": "NEEDS_TIME_NO_DATE", "STATEMENT_REQUEST": "DISPUTE"}
BAD_PHRASES = [("amount", " Please pay ₹5,000 today."), ("threat", " Otherwise legal action will follow."),
               ("promise", " We will waive your late fee."), ("link", " Pay at https://evil.example/x"),
               ("length", None)]
BAD_NARRATIVES = [("invented_number", " About 37 borrowers were affected."), ("pii", " Call +91 98765 43210."),
                  ("advice", " We recommend a waiver.")]


def body_hash(path: str, body: Optional[bytes]) -> str:
    return hashlib.sha256(path.encode() + b"|" + (body or b"")).hexdigest()


def _path(url: str) -> str:
    m = re.match(r"https?://[^/]+(/.*)$", url)
    return m.group(1) if m else url


def load_recorded(path: Path = RECORDED_PATH) -> Dict[str, Any]:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {}


class FixtureTransport:
    def __init__(self, recorded: Optional[Dict[str, Any]] = None, bad_every: int = 7, standin: bool = True):
        self.recorded = load_recorded() if recorded is None else recorded
        self.bad_every = bad_every
        self.standin = standin
        self.log: List[Dict[str, Any]] = []        # {kind, injected_bad, ...} per stand-in answer
        self._n = {"prism": 0, "conversations": 0, "deepseek": 0}
        self._lock = threading.Lock()
        self.replayed = 0

    def __call__(self, url: str, headers: Dict[str, str], body: Optional[bytes], timeout: float) -> Tuple[int, bytes]:
        path = _path(url)
        h = body_hash(path, body)
        if h in self.recorded:
            with self._lock:
                self.replayed += 1
            return 200, json.dumps(self.recorded[h]).encode("utf-8")
        if not self.standin:
            return 404, b'{"error": "no recorded fixture"}'
        req = json.loads((body or b"{}").decode("utf-8"))
        if path.endswith("/systemone"):
            return 200, json.dumps(self._prism(req)).encode("utf-8")
        if req.get("model") == "deepseekv4.1-flash-1017":
            return 200, json.dumps(self._chat(self._narrate(req))).encode("utf-8")
        return 200, json.dumps(self._chat(self._phrase(req))).encode("utf-8")

    def _tick(self, kind: str) -> int:
        with self._lock:
            self._n[kind] += 1
            return self._n[kind]

    @staticmethod
    def _chat(text: str) -> Dict[str, Any]:
        return {"model": "stand-in", "choices": [{"index": 0, "message": {"role": "assistant", "content": text}}]}

    # -- prism ----------------------------------------------------------------------------------
    def _prism(self, req: Dict[str, Any]) -> Dict[str, Any]:
        from ..intent_stack import _SELF_HARM, ABUSIVE, HARDSHIP, STOP, UNKNOWN, default_model, rule_hits
        n = self._tick("prism")
        state = req.get("state", "")
        text = state.split("Message: ", 1)[-1].split("Summary: ", 1)[-1]
        qs = req.get("questions", {})
        choice = "disposition" if "disposition" in qs else "intent"
        if self.bad_every and n % self.bad_every == 0:
            self.log.append({"kind": "prism", "injected_bad": True})
            return {"answers": {choice: {"answer": "NOT_A_LABEL"}}}
        model = default_model()
        probs = model.predict_proba(text) if model else {UNKNOWN: 1.0}
        label, p = max(probs.items(), key=lambda kv: (kv[1], kv[0]))
        hits = rule_hits(text)
        yes = lambda c: "yes" if c else "no"  # noqa: E731
        ans = {"stop_request": {"answer": yes(STOP in hits)}, "hardship": {"answer": yes(HARDSHIP in hits)},
               "self_harm": {"answer": yes(_SELF_HARM in hits)}, "abusive": {"answer": yes(ABUSIVE in hits)},
               "confidence": {"answer": round(float(p), 3)}}
        ans[choice] = {"answer": _DISP_FROM_INTENT.get(label, "NO_RESPONSE") if choice == "disposition" else label}
        self.log.append({"kind": "prism", "injected_bad": False})
        return {"model": "stand-in-prism", "answers": ans}

    # -- conversations --------------------------------------------------------------------------
    def _phrase(self, req: Dict[str, Any]) -> str:
        n = self._tick("conversations")
        user = json.loads(req["messages"][-1]["content"])
        line, style = user["approved_line"], user["style"]
        if self.bad_every and n % self.bad_every == 0:
            kind, extra = BAD_PHRASES[(n // self.bad_every) % len(BAD_PHRASES)]
            self.log.append({"kind": "conversations", "injected_bad": True, "bad_kind": kind})
            return (line + " ") * 3 if extra is None else line + extra
        self.log.append({"kind": "conversations", "injected_bad": False})
        return ("Ji, " + line) if "Hinglish" in style else line

    # -- deepseek -------------------------------------------------------------------------------
    def _narrate(self, req: Dict[str, Any]) -> str:
        n = self._tick("deepseek")
        user = json.loads(req["messages"][-1]["content"])
        kind, f = user["kind"], user["facts"]
        if kind == "k05_summary":
            text = f"{f['line_1_channel_evidence']} {f['line_2_borrower_intent']}"
        elif kind == "case_summary":
            text = (f"Case {f['ticket_id']} ({f.get('type')}) for {f.get('borrower_id')} is waiting for a person: "
                    f"{f.get('summary_line_1') or ''}.")
        elif kind == "explain_decision":
            d = f.get("latest_decision") or {}
            text = (f"The latest event was {d.get('decision') or d.get('type')} by {d.get('actor')}, under rule "
                    f"{d.get('rule') or 'none recorded'}.")
        else:
            t = f.get("totals") or {}
            text = (f"Campaign {f.get('campaign_id')} on {f.get('day')} ({f.get('provenance')}) made {t.get('attempts')} "
                    f"attempts with {t.get('meaningful_replies')} meaningful replies and {t.get('cap_breaches')} cap breaches.")
        if self.bad_every and n % self.bad_every == 0:
            bk, extra = BAD_NARRATIVES[(n // self.bad_every) % len(BAD_NARRATIVES)]
            self.log.append({"kind": "deepseek", "injected_bad": True, "bad_kind": bk})
            return text + extra
        self.log.append({"kind": "deepseek", "injected_bad": False})
        return text


class RecordingTransport:
    """Wraps the live transport and keeps every 200 response by request hash (save() writes the file)."""

    def __init__(self, inner, path: Path = RECORDED_PATH):
        self.inner, self.path = inner, Path(path)
        self.recorded = load_recorded(self.path)
        self._lock = threading.Lock()

    def __call__(self, url: str, headers: Dict[str, str], body: Optional[bytes], timeout: float) -> Tuple[int, bytes]:
        status, raw = self.inner(url, headers, body, timeout)
        if status == 200:
            try:
                data = json.loads(raw.decode("utf-8"))
            except Exception:
                return status, raw
            with self._lock:
                self.recorded[body_hash(_path(url), body)] = data
        return status, raw

    def save(self) -> int:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self.path.write_text(json.dumps(self.recorded, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
            return len(self.recorded)
