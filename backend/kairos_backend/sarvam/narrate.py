"""deepseekv4.1-flash-1017: plain-language narratives for PEOPLE, built only from facts Kairos already has.

    Narrator(client).k05_summary(lines)                      K05 two-line CRM summary, reworded
    Narrator(client).case_summary(ticket, audit_rows)        hand-off case summary (hardship / dispute / fraud)
    Narrator(client).explain_decision(audit_rows)            "explain this decision" for operators
    Narrator(client).daily_ops(daily_segment_report)         K26 daily ops paragraph

It never decides anything: the narrative sits NEXT TO the deterministic record, is labelled AI-generated,
and is checked before use. Input facts are PII-scrubbed (pii.scrub + phone / e-mail masking). The output
must pass fact_check():
  * no PII (phones, e-mails) and no names other than borrower ids
  * every number in the text appears in the input facts (no invented amounts, counts or dates)
  * the key fields (ids, decision, rule, intent label ...) named for that narrative are all present
  * no advice / decision language ("recommend", "should", "waive", "approve" ...)
  * a length limit
Any failure (or no key) -> the deterministic text, with ai_generated=False.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .. import pii
from .client import DEEPSEEK, SarvamClient

NARRATIVE_VERSION = "narrative-1"
AI_LABEL = "AI-generated ({model}) from the recorded facts; not a decision. Check the audit trail."
DECISION_WORDS = ("recommend", "should", "must ", "advise", "waive", "approve", "write off", "write-off", "settle for",
                  "i suggest", "we suggest", "guarantee")
_NUM = re.compile(r"\d+(?:[.,]\d+)*")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")

SYSTEM_PROMPT = (
    "You write short, plain-English notes for loan-collections operators in India. Use ONLY the facts in the JSON "
    "you are given. Never invent numbers, dates, names, amounts or outcomes. Keep every id exactly as written. "
    "Do not give advice, do not recommend or decide anything, do not mention policy you were not given. "
    "No markdown, no lists. {shape}"
)


def _nums(s: str) -> set:
    return {n.replace(",", "") for n in _NUM.findall(s or "")}


def scrub_facts(obj: Any) -> Any:
    """Recursively pii.scrub every string (Indian / international phones, e-mails); keys kept."""
    if isinstance(obj, str):
        return pii.scrub(obj)
    if isinstance(obj, dict):
        return {k: scrub_facts(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [scrub_facts(v) for v in obj]
    return obj


def fact_check(text: str, facts_json: str, required: Iterable[str], max_chars: int) -> Optional[str]:
    if not text or not text.strip():
        return "empty"
    if len(text) > max_chars:
        return "length"
    if _EMAIL.search(text) or pii.scrub(text) != text:
        return "pii"
    extra = _nums(text) - _nums(facts_json)
    if extra:
        return "number_not_in_facts"
    low = text.lower()
    for r in required:
        if r and str(r).lower() not in low:
            return "missing_key_field"
    if any(w in low for w in DECISION_WORDS):
        return "decision_language"
    if "```" in text or text.lstrip().startswith(("{", "[", "#", "-")):
        return "format"
    return None


@dataclass
class Narrative:
    kind: str
    text: str
    ai_generated: bool
    label: str
    reason: str                               # ACCEPTED | NO_MODEL | <fact_check reason> | MODEL_ERROR:...
    meta: Dict[str, Any] = field(default_factory=dict)
    deterministic: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {"kind": self.kind, "text": self.text, "ai_generated": self.ai_generated, "label": self.label,
                "outcome": self.reason, "model": self.meta, "narrative_version": NARRATIVE_VERSION,
                "deterministic_text": self.deterministic}


DETERMINISTIC_LABEL = "Deterministic summary from the recorded facts."


class Narrator:
    def __init__(self, client: Optional[SarvamClient], model: str = DEEPSEEK):
        self.client, self.model = client, model
        self.stats: Dict[str, Any] = {"requested": 0, "accepted": 0, "rejected": {}, "model_errors": {}}

    def _count(self, bucket: str, reason: str) -> None:
        self.stats[bucket][reason] = self.stats[bucket].get(reason, 0) + 1

    def _run(self, kind: str, facts: Dict[str, Any], required: Sequence[str], deterministic: str, shape: str,
             max_chars: int) -> Narrative:
        facts = scrub_facts(facts)
        if self.client is None or not self.client.enabled():
            return Narrative(kind, deterministic, False, DETERMINISTIC_LABEL, "NO_MODEL", {}, deterministic)
        self.stats["requested"] += 1
        fj = json.dumps(facts, ensure_ascii=False, sort_keys=True, default=str)
        msgs = [{"role": "system", "content": SYSTEM_PROMPT.format(shape=shape)},
                {"role": "user", "content": json.dumps({"kind": kind, "facts": facts}, ensure_ascii=False,
                                                      sort_keys=True, default=str)}]
        try:
            rep = self.client.chat(self.model, msgs, temperature=0.0, top_p=1.0, max_tokens=400, reasoning_effort="low")
        except Exception as e:  # defensive
            self._count("model_errors", type(e).__name__)
            return Narrative(kind, deterministic, False, DETERMINISTIC_LABEL, f"MODEL_ERROR:{type(e).__name__}",
                             {"model": self.model}, deterministic)
        meta = rep.meta()
        if not rep.ok:
            self._count("model_errors", (rep.error or "error").split(":")[0])
            return Narrative(kind, deterministic, False, DETERMINISTIC_LABEL, f"MODEL_ERROR:{rep.error}", meta,
                             deterministic)
        text = " ".join(rep.data["text"].split())
        why = fact_check(text, fj, required, max_chars)
        if why:
            self._count("rejected", why)
            return Narrative(kind, deterministic, False, DETERMINISTIC_LABEL, why, meta, deterministic)
        self.stats["accepted"] += 1
        return Narrative(kind, text, True, AI_LABEL.format(model=self.model), "ACCEPTED", meta, deterministic)

    # -- the four narratives ------------------------------------------------------------------
    def k05_summary(self, lines: Sequence[str]) -> Narrative:
        """K05: reword the two deterministic lines (channel evidence, borrower intent). Every source id and
        the intent label must survive."""
        ids = sorted({i.strip() for ln in lines for grp in re.findall(r"\[([^\]]+)\]", ln) for i in grp.split(",")})
        m = re.search(r"Intent:\s*([A-Z_]+)", " ".join(lines))
        required = ids + ([m.group(1)] if m else [])
        return self._run("k05_summary", {"line_1_channel_evidence": lines[0] if lines else "",
                                         "line_2_borrower_intent": lines[1] if len(lines) > 1 else "",
                                         "source_ids": ids},
                         required, " ".join(lines), "Two sentences: channel evidence, then borrower intent. "
                         "Keep the source ids in square brackets.", max(420, int(len(" ".join(lines)) * 1.3)))

    def case_summary(self, ticket: Dict[str, Any], audit_rows: Sequence[Dict[str, Any]]) -> Narrative:
        """Hand-off case summary for the person who picks up a hardship / dispute / fraud case."""
        rows = [_audit_fact(r) for r in list(audit_rows)[:12]]
        facts = {"ticket_id": ticket.get("ticket_id"), "type": ticket.get("type"), "queue": ticket.get("queue"),
                 "borrower_id": ticket.get("borrower_id"), "summary_line_1": ticket.get("summary_line_1"),
                 "summary_line_2": ticket.get("summary_line_2"), "created_at": ticket.get("created_at"),
                 "audit": rows}
        det = (f"{facts['type']} case {facts['ticket_id']} for {facts['borrower_id']} ({facts['queue']}): "
               f"{facts['summary_line_1'] or ''}; {facts['summary_line_2'] or ''}. {len(rows)} audit event(s)"
               + (f", latest {rows[0]['type']} at {rows[0]['ts']}." if rows else "."))
        return self._run("case_summary", facts, [facts["ticket_id"], facts["borrower_id"]], det,
                         "Three sentences at most: what happened, what the borrower said, what is pending.", 600)

    def explain_decision(self, audit_rows: Sequence[Dict[str, Any]]) -> Narrative:
        """Operator 'explain this decision' from the audit trail (newest row first)."""
        rows = [_audit_fact(r) for r in list(audit_rows)[:12]]
        latest = next((r for r in rows if r.get("decision")), rows[0] if rows else {})
        det = (f"Latest decision: {latest.get('decision') or latest.get('type')} by {latest.get('actor')} at "
               f"{latest.get('ts')}, rule {latest.get('rule') or '-'}, reason {latest.get('reason') or '-'} "
               f"(policy {latest.get('policy_version') or '-'}).") if latest else "No audit events."
        required = [x for x in (latest.get("decision"), latest.get("rule")) if x]
        return self._run("explain_decision", {"latest_decision": latest, "trail": rows}, required, det,
                         "Explain in at most four sentences why the latest decision happened, citing its rule.", 700)

    def daily_ops(self, report: Dict[str, Any]) -> Narrative:
        t = report.get("totals") or {}
        facts = {"campaign_id": report.get("campaign_id"), "day": report.get("day"),
                 "provenance": report.get("provenance"),
                 "totals": {k: t.get(k) for k in ("attempts", "reach", "meaningful_replies", "responders",
                                                  "spend_committed_inr", "replies_per_1000_inr", "cap_breaches",
                                                  "verified_payments", "verified_inr")},
                 "segments": [{k: s.get(k) for k in ("band", "channel", "attempts", "meaningful_replies",
                                                     "spend_committed_inr", "cap_breaches")}
                              for s in (report.get("segments") or [])[:12]]}
        tt = facts["totals"]
        det = (f"Campaign {facts['campaign_id']} on {facts['day']} ({facts['provenance']}): {tt.get('attempts')} "
               f"attempts, {tt.get('reach')} reached, {tt.get('meaningful_replies')} meaningful replies, "
               f"committed spend INR {tt.get('spend_committed_inr')}, {tt.get('verified_payments')} verified "
               f"payments, {tt.get('cap_breaches')} cap breaches.")
        return self._run("daily_ops", facts, [str(facts["campaign_id"]), str(tt.get("cap_breaches"))], det,
                         "One paragraph of at most four sentences for the daily ops report. Say SIMULATED if the "
                         "provenance says so.", 700)


def _audit_fact(r: Dict[str, Any]) -> Dict[str, Any]:
    keep = ("ts", "actor", "type", "borrower_id", "instruction_id", "decision", "reason", "rule", "policy_version")
    return {k: r.get(k) for k in keep if r.get(k) not in (None, "")}


def shared_narrator(env=None) -> Narrator:
    """Always returns a Narrator; without a key (or with 'narrative' switched off) it gives the
    deterministic text."""
    from .client import shared_client
    from .config import feature_enabled
    return Narrator(shared_client(env) if feature_enabled("narrative", env) else None)
