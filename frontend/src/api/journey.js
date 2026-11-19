// The scripted borrower journey: normaliser for the integrator's run_journey.py output, plus a built-in
// SIMULATED copy for when neither POST /demo/journey/run nor a saved journey JSON is available.

const E = (event_type, payload, extra = {}) => ({ event_type, payload, ...extra })

export const MOCK_JOURNEY = {
  provenance: 'SIMULATED',
  seed: 42,
  borrower_id: 'BRW-DEMO-01',
  borrower: {
    name: 'Asha T. (synthetic)', borrower_id: 'BRW-DEMO-01', phone: '••••••4417', language: 'Hindi', region: 'MH',
    cohort: 'C2', risk_tier: 'Medium', layer: 'L1', next_action: 'WAIT', holds: [],
    emis: [{ id: 'A-EMI-3', loan: 'Loan A', due: '05 Nov', amount: '₹4,850', status: 'Due' }, { id: 'B-EMI-7', loan: 'Loan B', due: '10 Nov', amount: '₹3,200', status: 'Due' }],
  },
  steps: [
    { id: 'pre_due', title: 'Pre-due reminder', story: 'Three days before the EMI date the Supervisor picks WhatsApp in the evening — the best replies per rupee for this cohort. Consent, caps and budget pass; the send is reserved, then re-checked at send time.',
      borrower: { next_action: 'SEND · WhatsApp 18:00' },
      events: [E('supervisor.decision', { decision: 'SEND', reason: 'ELIGIBLE', rule: 'ALL_PASSED', policy_version: '1.1.0', channel: 'whatsapp' }), E('touch.status', { previous: 'SENT', now: 'DELIVERED', channel: 'whatsapp', reason: 'DELIVERED' })] },
    { id: 'bounce', title: 'NACH bounce', story: 'The auto-debit bounces. The EMI is now 1 day past due, the borrower moves from PRE_DUE to B1 and auto-churn lifts the risk tier.',
      borrower: { cohort: 'C3', risk_tier: 'High', layer: 'L2', next_action: 'SEND · SMS 10:00', emis: [{ id: 'A-EMI-3', loan: 'Loan A', due: '05 Nov', amount: '₹4,850', status: 'Overdue · 1 DPD' }, { id: 'B-EMI-7', loan: 'Loan B', due: '10 Nov', amount: '₹3,200', status: 'Due' }] },
      events: [E('payment.bounced', { reason: 'NACH bounce · insufficient funds' }), E('churn.cohort_changed', { reason: 'C2 → C3 · risk Medium → High · layer L1 → L2' }), E('supervisor.decision', { decision: 'SEND', reason: 'ELIGIBLE', rule: 'ALL_PASSED', policy_version: '1.1.0', channel: 'sms' })] },
    { id: 'stop', title: 'STOP on SMS → every channel', story: 'The borrower replies STOP to the SMS. Consent is per channel, but STOP suppresses outbound on every channel from the next cycle. Two queued touches are canceled.',
      borrower: { next_action: 'CANCEL · STOP on file', holds: ['STOP · all channels'] },
      events: [E('consent.stop', { reason: 'STOP received on SMS', channel: 'sms' }), E('supervisor.decision', { decision: 'CANCEL', reason: 'STOP_OPT_OUT', rule: 'STOP_OPT_OUT', policy_version: '1.1.0' }), E('touch.canceled', { reason: '2 queued touches canceled (WhatsApp, IVR)' })] },
    { id: 'cm03', title: 'CM-03 · OTP + fake UTR caught', story: 'Inbound on WhatsApp: "already paid" with a UPI screenshot. Nothing is disclosed before OTP. After OTP the UTR is checked in D7 — it does not exist. Fake screenshot caught; no receipt.',
      borrower: { holds: ['STOP · all channels', 'Dispute · fake UTR'] },
      events: [E('cm03.message', { reason: 'Inbound: "already paid" + screenshot', channel: 'whatsapp' }), E('cm03.otp_verified', { reason: 'OTP verified · sender bound to account' }), E('cm03.fraud_flag', { reason: 'UTR not found in D7 → edited screenshot, routed to disputes' })] },
    { id: 'hardship', title: 'Hardship', story: 'The borrower explains a medical emergency. Kairos acknowledges neutrally, pauses ordinary outreach and opens a hardship case for a person.',
      borrower: { next_action: 'HANDOFF · hardship desk', holds: ['STOP · all channels', 'Hardship hold'] },
      events: [E('exception.hardship', { reason: 'HARDSHIP_HOLD · case opened (Collections · Hardship desk)' }), E('supervisor.decision', { decision: 'HANDOFF', reason: 'HARDSHIP_HOLD', rule: 'HARDSHIP_HOLD', policy_version: '1.1.0' })] },
    { id: 'hitl', title: 'Human approval', story: 'A supervisor reviews the case and approves a consented hardship-triage callback (that bot always needs a person). The Gate re-runs at approval and again at call time.',
      borrower: { next_action: 'SEND · approved callback (Advait)' },
      events: [E('hitl.approved', { reason: 'Supervisor approved HARDSHIP_TRIAGE callback', actor: 'human:ops.supervisor' }), E('voice.call_disposition', { disposition: 'PTP', effective_disposition: 'PTP', confidence: 0.86, rule: 'K17.PTP.ACCEPT', action: 'PTP_HOLD' })] },
    { id: 'payment', title: 'Payment verified · that EMI only', story: 'D7 settles ₹4,850 against Loan A. Only that EMI clears and only its queued touches are canceled — Loan B stays open.',
      borrower: { next_action: 'WAIT · Loan B due 10 Nov', emis: [{ id: 'A-EMI-3', loan: 'Loan A', due: '05 Nov', amount: '₹4,850', status: 'Paid · verified' }, { id: 'B-EMI-7', loan: 'Loan B', due: '10 Nov', amount: '₹3,200', status: 'Due' }] },
      events: [E('payment.verified', { reason: 'D7 settled · cleared Loan A EMI 3 only' }), E('touch.canceled', { reason: '1 queued Loan A touch canceled; Loan B untouched' })] },
    { id: 'duplicate', title: 'Duplicate ignored', story: 'The payment webhook arrives a second time. Idempotent on the bank reference: no second credit, no state change.',
      events: [E('payment.duplicate_ignored', { reason: 'Same UTR already applied — ignored' })] },
    { id: 'learner', title: 'Learner update', story: 'The 24 h reply window closes. One reward for the executed WhatsApp touch updates the B1 × WhatsApp × evening arm; the STOP counts against SMS.',
      borrower: { cohort: 'C2', risk_tier: 'Medium', layer: 'L1' },
      events: [E('learner.outcome_finalized', { reason: 'B1 · WhatsApp · evening: α 9→10, n 58→59 · posterior 0.167' }), E('churn.cohort_changed', { reason: 'C3 → C2 after verified payment · layer L2 → L1' })] },
    { id: 'report', title: 'Day-end report', story: 'The CM-02 daily report lists spend, replies and learning per DPD band × channel. Cap breaches: 0.',
      borrower: { next_action: 'WAIT · hardship case open' },
      events: [E('report.daily_segment', { reason: 'Daily per-segment report · cap breaches 0' })] },
  ],
}

/** Accepts several plausible shapes of the integrator's journey JSON and returns MOCK_JOURNEY's shape. */
export function normalizeJourney(raw) {
  if (!raw) return null
  const steps = raw.steps || raw.journey || raw.timeline || (Array.isArray(raw) ? raw : [])
  if (!Array.isArray(steps) || !steps.length) return null
  return {
    provenance: raw.provenance || 'SIMULATED',
    seed: raw.seed ?? raw.matched?.seed ?? null,
    borrower_id: raw.borrower_id || raw.borrower?.borrower_id || null,
    borrower: raw.borrower || raw.initial_borrower || raw.card || {},
    steps: steps.map((s, i) => ({
      id: s.id || s.step || s.name || `step-${i + 1}`,
      title: s.title || s.name || s.label || String(s.step || s.id || `Step ${i + 1}`).replace(/_/g, ' '),
      story: s.story || s.text || s.narrative || s.description || '',
      borrower: s.borrower || s.card || s.state || s.borrower_card || null,
      events: s.events || s.envelopes || s.crm_envelopes || [],
      provenance: s.provenance,
    })),
  }
}
