"""FIXTURES: labeled stand-ins for components other people own.

Everything in this module is SIMULATED. It exists so the K05/K14 borrower page
and replay run before Rehan's adapters and supervisor (and Ojas's K21, the K22
template selector and a verified Sarvam route) are available. Each class sets
`is_fixture = True` and a name. Ingestion stamps that onto every event the
class produces, and the UI shows it as a FIXTURE badge.

Replace each class with the real implementation of the matching protocol in
`kairos.adapters`.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List

from kairos.adapters import AgentOutput, Envelope, IntentResult, SupervisorDecision

# Policy is Ojas's sign-off (K01). Until then the fixture supervisor says so.
FIXTURE_POLICY_VERSION = "policy-draft-0 (FIXTURE, not approved)"


class FixtureSmsDlrAdapter:
    """Normalizes a sandbox-style SMS delivery report (DLR). Owner: Rehan (K08)."""
    name = "rehan.sms_provider_adapter"
    is_fixture = True

    _STATUS = {"SUBMITTED": "SENT", "SENT": "SENT", "DELIVRD": "DELIVERED", "DELIVERED": "DELIVERED",
               "UNDELIV": "FAILED", "FAILED": "FAILED"}

    def normalize(self, raw: Dict[str, Any]) -> Envelope:
        status = self._STATUS.get(str(raw["status"]).upper())
        if status is None:
            raise ValueError(f"Unknown SMS DLR status {raw['status']!r}")
        provider = raw.get("provider", "sms-sandbox")
        return {
            "event_type": "provider.delivery",
            "source": provider,
            "provenance": "SIMULATED",
            "fixture_of": self.name,
            "occurred_at": raw["status_at"],
            "dedupe_key": f"delivery:{provider}:{raw['message_id']}:{status}",
            "touch_id": raw["client_ref"],
            "payload": {"channel": "SMS", "provider": provider,
                        "provider_message_id": raw["message_id"], "status": status},
        }


class FixtureWhatsAppInboundAdapter:
    """Normalizes an inbound WhatsApp message webhook. Owner: Rehan (K07)."""
    name = "rehan.whatsapp_provider_adapter"
    is_fixture = True

    def normalize(self, raw: Dict[str, Any]) -> Envelope:
        return {
            "event_type": "borrower.message",
            "source": "whatsapp-sandbox",
            "provenance": "SIMULATED",
            "fixture_of": self.name,
            "occurred_at": raw["timestamp"],
            "dedupe_key": f"message:whatsapp:{raw['wamid']}",
            # Resolved to a touch (and so to borrower + campaign) by ingestion.
            "reply_to_provider_message_id": raw.get("context_wamid"),
            "payload": {"channel": "WHATSAPP", "provider": "whatsapp-sandbox",
                        "provider_message_id": raw["wamid"], "text": raw["text"]},
        }


class FixtureLedgerAdapter:
    """Normalizes a D7 settlement webhook that LMS has allocated to a loan. Owner: Rehan (K11)."""
    name = "rehan.d7_lms_ledger_adapter"
    is_fixture = True

    def normalize(self, raw: Dict[str, Any]) -> Envelope:
        if raw["status"] != "SETTLED":
            raise ValueError(f"Fixture ledger adapter only models SETTLED, got {raw['status']!r}")
        return {
            # The webhook delivery attempt gets its own event ID. Dedupe is on the
            # ledger transaction, so a retried webhook is caught although its ID is new.
            "event_id": f"evt-d7-{raw['delivery_attempt_id']}",
            "event_type": "payment.verified",
            "source": "d7-ledger",
            "provenance": "SIMULATED",
            "fixture_of": self.name,
            "occurred_at": raw["settled_at"],
            "dedupe_key": f"ledger:{raw['txn_ref']}:{raw['status']}",
            "borrower_id": raw["borrower_ref"],
            "loan_id": raw["loan_account"],
            "payload": {"ledger_ref": raw["txn_ref"], "amount": int(raw["amount"]),
                        "settled_at": raw["settled_at"], "lms_allocated": bool(raw["lms_allocated"])},
        }


class FixtureSarvamCallAdapter:
    """Normalizes a Sarvam IVR or voicebot call-completion webhook.

    The shape is our proposed contract (see kairos/voice/agents/*.json) until
    the Sarvam callback is verified. An intent is passed through only when the
    flow captured one: a valid DTMF choice (IVR) or a classified utterance
    (voicebot). No-answer and invalid input carry none (E10).
    """
    name = "sarvam.call_adapter"
    is_fixture = True

    _DISPOSITION = {"completed": "COMPLETED", "answered": "ANSWERED", "no-answer": "NO_ANSWER",
                    "no_answer": "NO_ANSWER", "busy": "BUSY", "failed": "FAILED", "declined": "DECLINED"}

    def normalize(self, raw: Dict[str, Any]) -> Envelope:
        disposition = self._DISPOSITION.get(str(raw["status"]).lower())
        if disposition is None:
            raise ValueError(f"Unknown Sarvam call status {raw['status']!r}")
        flow = raw["flow"].upper()
        if flow not in ("IVR", "VOICEBOT"):
            raise ValueError(f"flow must be IVR or VOICEBOT, got {raw['flow']!r}")
        return {
            "event_type": "call.outcome",
            "source": "sarvam",
            "provenance": "SIMULATED",
            "fixture_of": self.name,
            "occurred_at": raw["ended_at"],
            # One outcome per call, so a repeated completion webhook is a duplicate (E10).
            "dedupe_key": f"call:sarvam:{raw['call_id']}",
            "touch_id": raw["custom_ref"],
            "payload": {"flow": flow, "provider": "sarvam", "call_id": raw["call_id"],
                        "disposition": disposition, "dtmf": raw.get("dtmf"),
                        "duration_s": raw.get("duration_s"), "attempts": raw.get("attempts"),
                        "exit_reason": raw.get("exit_reason"), "transcript": raw.get("transcript"),
                        "intent": raw.get("intent")},
        }


class FixtureIntentClassifier:
    """Canned intent outputs keyed by inbound message ID, with a keyword fallback."""
    name = "rehan.intent_classifier"
    is_fixture = True

    CANNED = {
        "wamid.IN-5521": IntentResult(intent="NEEDS_TIME", confidence=0.91, classified_by=name),
        "wamid.IN-5534": IntentResult(intent="CLAIMS_PAID", confidence=0.88, classified_by=name,
                                      loan_id="PL100328", claimed_amount=4985,
                                      claimed_reference="UPI 4417 0921 8833"),
    }

    def classify(self, message: Dict[str, Any]) -> IntentResult:
        canned = self.CANNED.get(message.get("provider_message_id", ""))
        if canned:
            return canned
        text = message.get("text", "").lower()
        if any(w in text for w in ("time", "waqt", "baad mein", "later")):
            return IntentResult(intent="NEEDS_TIME", confidence=0.6, classified_by=self.name)
        if any(w in text for w in ("paid", "pay kar", "bhar diya")):
            return IntentResult(intent="CLAIMS_PAID", confidence=0.6, classified_by=self.name)
        if "stop" in text:
            return IntentResult(intent="STOP", confidence=0.6, classified_by=self.name)
        return IntentResult(intent="OTHER", confidence=0.3, classified_by=self.name)


class FixtureAgents:
    """K09 channel/time (Rehan), K21 propensity + intensity (Ojas), K22 template (not built)."""
    name = "fixture.recommendation_agents"
    is_fixture = True

    def recommend(self, context: Dict[str, Any]) -> List[AgentOutput]:
        needs_time = context.get("intent") == "NEEDS_TIME"
        return [
            AgentOutput(
                agent="channel_time", task_id="K09", owner="Rehan",
                headline="WhatsApp, 21 Nov 11:00: 0.45 est. replies/₹ (18% at ₹0.40) vs SMS 0.40",
                output={"channel": "WHATSAPP", "send_at": "2026-11-21T11:00:00+05:30",
                        "est_reply_rate": 0.18, "unit_cost_inr": 0.40, "replies_per_rupee": 0.45,
                        "runner_up": {"channel": "SMS", "replies_per_rupee": 0.40}}),
            AgentOutput(
                agent="propensity", task_id="K21", owner="Ojas",
                headline=("P(verified payment in 7 days) 0.34; intensity WAIT (needs-time intent overrides)"
                          if needs_time else "P(verified payment in 7 days) 0.22; intensity STANDARD"),
                output={"p_pay_7d": 0.34 if needs_time else 0.22, "horizon_days": 7,
                        "intensity": "WAIT" if needs_time else "STANDARD"}),
            AgentOutput(
                agent="template", task_id="K22", owner="Yuval",
                headline=("wa_ask_payment_date_hi_v1 (WhatsApp, hi-IN, ask for a date)" if needs_time
                          else "wa_emi_reminder_hi_v1 (WhatsApp, hi-IN, standard)"),
                output={"template_id": "wa_ask_payment_date_hi_v1" if needs_time else "wa_emi_reminder_hi_v1",
                        "channel": "WHATSAPP", "language": "hi-IN"}),
        ]


class FixtureSupervisor:
    """Minimal rules so the page can show decisions. Not Ojas-approved policy. Owner: Rehan (K06)."""
    name = "rehan.supervisor"
    is_fixture = True

    NEEDS_TIME_HOLD = timedelta(hours=48)

    def decide(self, context: Dict[str, Any]) -> SupervisorDecision:
        at = datetime.fromisoformat(context["occurred_at"])
        common = {"decided_by": self.name, "policy_version": FIXTURE_POLICY_VERSION}
        if context["trigger"] == "payment_verified":
            return SupervisorDecision(
                decision="CANCEL", scope_loan_id=context["loan_id"], **common,
                reason=f"Verified payment cleared {context['loan_label']}. Cancel queued touches for this "
                       f"obligation only; other loans keep their schedule.")
        intent = context["intent"]
        template = next((o.output.get("template_id") for o in context.get("agent_outputs", [])
                         if o.agent == "template"), None)
        if intent == "NEEDS_TIME":
            if context.get("agreed_date"):
                return SupervisorDecision(
                    decision="WAIT", expires_at=context["agreed_date"], **common,
                    reason="Borrower agreed a payment date. Hold outreach until then and recheck payment before resuming.")
            return SupervisorDecision(
                decision="WAIT", expires_at=(at + self.NEEDS_TIME_HOLD).isoformat(), **common,
                reason="Borrower asked for more time without a date. K21 recommends WAIT, so ordinary reminders "
                       f"pause for 48 h; the next allowed touch asks for a payment date ({template}).")
        if intent in ("HARDSHIP", "CALLBACK"):
            return SupervisorDecision(decision="HANDOFF", **common,
                                      reason=f"{intent.title()} needs a person. Open a supervisor case and pause nudges.")
        if intent == "STOP":
            return SupervisorDecision(decision="CANCEL", **common, reason="Borrower opted out. Cancel outreach in scope.")
        return SupervisorDecision(decision="SEND", **common,
                                  reason="No intent that changes the plan. Continue the scheduled cadence.")
