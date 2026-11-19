// Classify a CRM envelope / audit row into a display kind for the live feed and the journey timeline.
import {
  Gavel, Send, CircleCheck, Eye, MessageSquareReply, AudioLines, MessagesSquare, Layers, BadgeCheck, CalendarClock, Hand,
  Ban, Info, ShieldAlert, KeyRound, TrendingUp, FileBarChart, Copy,
} from 'lucide-react'

export const KINDS = {
  decision: { label: 'Decision', icon: Gavel, tone: 'info' },
  dispatch: { label: 'Dispatch', icon: Send, tone: 'neutral' },
  delivery: { label: 'Delivery / read', icon: Eye, tone: 'ok' },
  reply: { label: 'Reply', icon: MessageSquareReply, tone: 'info' },
  voice: { label: 'Voice', icon: AudioLines, tone: 'violet' },
  cm03: { label: 'CM-03', icon: MessagesSquare, tone: 'info' },
  otp: { label: 'OTP', icon: KeyRound, tone: 'ok' },
  fraud: { label: 'Fraud flag', icon: ShieldAlert, tone: 'bad' },
  churn: { label: 'Cohort / layer', icon: Layers, tone: 'violet' },
  payment: { label: 'Payment', icon: BadgeCheck, tone: 'ok' },
  hold: { label: 'Hold', icon: CalendarClock, tone: 'warn' },
  stop: { label: 'STOP / consent', icon: Ban, tone: 'bad' },
  human: { label: 'Human', icon: Hand, tone: 'info' },
  learner: { label: 'Learner', icon: TrendingUp, tone: 'violet' },
  report: { label: 'Report', icon: FileBarChart, tone: 'neutral' },
  duplicate: { label: 'Duplicate', icon: Copy, tone: 'neutral' },
  other: { label: 'Event', icon: Info, tone: 'neutral' },
}
export const DECISION_TONE = { SEND: 'ok', WAIT: 'warn', CANCEL: 'bad', HANDOFF: 'violet' }

/** @param {Object} e  /events/since event, /audit row, or a journey envelope {event_type, payload} */
export function kindOf(e) {
  const t = String(e.type || e.event_type || '').toUpperCase().replace(/\./g, '_')
  const actor = String(e.actor || e.source || '').toLowerCase()
  if (t.includes('DUPLICATE')) return 'duplicate'
  if (t.includes('FRAUD') || t.includes('MISMATCH') || t.includes('FAKE') || t.includes('INJECTION')) return 'fraud'
  if (t.includes('OTP')) return 'otp'
  if (t.startsWith('CM03') || actor.includes('cm03')) return 'cm03'
  if (t.includes('COHORT') || t.includes('LAYER') || t.includes('CHURN') || t.includes('RISK_TIER')) return 'churn'
  if (t === 'DECISION' || t.includes('SUPERVISOR_DECISION')) return 'decision'
  if (t.includes('VOICE') || t.includes('CALL_DISPOSITION')) return 'voice'
  if (t.includes('PAYMENT')) return 'payment'
  if (t.includes('STOP') || t.includes('DNC') || t.includes('CONSENT') || t.includes('OPT_OUT')) return 'stop'
  if (t === 'RESPONSE' || t.includes('REPLY')) return 'reply'
  if (t === 'PROVIDER_STATUS') return ['DELIVERED', 'READ', 'CLICKED'].includes(String(e.reason).toUpperCase()) ? 'delivery' : 'dispatch'
  if (t.includes('DELIVER') || t.includes('READ')) return 'delivery'
  if (t.includes('SEND_TIME') || t.includes('DISPATCH') || t.includes('TOUCH') || t === 'CALL_RESULT') return 'dispatch'
  if (t.includes('HOLD') || t.startsWith('EXCEPTION') || t.includes('PTP') || t.includes('HARDSHIP')) return 'hold'
  if (t.includes('LEARN') || t.includes('OUTCOME') || t.includes('ESTIMATE')) return 'learner'
  if (t.includes('REPORT')) return 'report'
  if (t.startsWith('HUMAN') || t.startsWith('HITL') || t.includes('APPROV') || actor.startsWith('human')) return 'human'
  return 'other'
}

/** One line of text for an event, whatever its source. */
export function textOf(e) {
  const d = e.detail || e.payload || {}
  const k = kindOf(e)
  if (k === 'decision') return [e.decision || d.decision, e.reason || d.reason, e.rule || d.rule].filter(Boolean).join(' · ')
  if (k === 'voice') return [d.effective || d.effective_disposition || d.disposition, d.confidence != null ? `conf ${Number(d.confidence).toFixed(2)}` : null, e.reason || d.rule, d.action].filter(Boolean).join(' · ')
  if (k === 'delivery' || k === 'dispatch') return [d.previous && d.now ? `${d.previous} → ${d.now}` : null, e.reason || d.status, d.channel].filter(Boolean).join(' · ')
  return [e.reason || d.reason || d.text || d.summary, d.channel, d.action].filter(Boolean).join(' · ') || String(e.type || e.event_type || '')
}
