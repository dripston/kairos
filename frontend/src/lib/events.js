import {
  Send, CircleCheck, Eye, MessageSquareReply, Ban, CalendarClock, CalendarCheck, Database, Brain, Gavel,
  Lock, LockOpen, KeyRound, ScanText, Hourglass, BadgeCheck, CircleX, Receipt, CircleSlash, Ticket, ShieldAlert,
  Hand, TrendingUp, MailX, OctagonX, PauseCircle, Rocket, ServerCrash, Server, Bookmark,
} from 'lucide-react'

// label, icon, tone, audit severity
export const EVENT_META = {
  CRM_SYNCED: { label: 'Enrolled from CRM', icon: Database, tone: 'neutral', sev: 'info' },
  SUPERVISOR_DECISION: { label: "Aladdin's decision", icon: Gavel, tone: 'info', sev: 'info' },
  SEND_RESERVED: { label: 'Send reserved', icon: Bookmark, tone: 'ok', sev: 'info' },
  SEND_BLOCKED: { label: 'Send blocked', icon: Ban, tone: 'warn', sev: 'medium' },
  SENT: { label: 'Sent', icon: Send, tone: 'neutral', sev: 'info' },
  DELIVERED: { label: 'Delivered', icon: CircleCheck, tone: 'ok', sev: 'info' },
  READ: { label: 'Read', icon: Eye, tone: 'ok', sev: 'info' },
  REPLIED: { label: 'Borrower replied', icon: MessageSquareReply, tone: 'info', sev: 'info' },
  BOUNCED: { label: 'Bounced', icon: MailX, tone: 'warn', sev: 'medium' },
  HOLD_CREATED: { label: 'Hold created', icon: CalendarClock, tone: 'warn', sev: 'info' },
  HOLD_RELEASED: { label: 'Hold released', icon: CalendarCheck, tone: 'neutral', sev: 'info' },
  STOP_RECEIVED: { label: 'STOP received', icon: OctagonX, tone: 'bad', sev: 'high' },
  OTP_SENT: { label: 'OTP sent', icon: KeyRound, tone: 'neutral', sev: 'info' },
  OTP_VERIFIED: { label: 'OTP verified', icon: LockOpen, tone: 'ok', sev: 'info' },
  OTP_FAILED: { label: 'OTP failed', icon: Lock, tone: 'bad', sev: 'medium' },
  MEDIA_EXTRACTED: { label: 'Voice / image read', icon: ScanText, tone: 'info', sev: 'info' },
  D7_PENDING: { label: 'D7 · payment pending', icon: Hourglass, tone: 'warn', sev: 'info' },
  D7_MATCHED: { label: 'D7 · payment matched', icon: BadgeCheck, tone: 'ok', sev: 'info' },
  D7_MISMATCH: { label: 'D7 · mismatch', icon: CircleX, tone: 'bad', sev: 'high' },
  RECEIPT_ISSUED: { label: 'Receipt issued', icon: Receipt, tone: 'ok', sev: 'info' },
  TOUCH_CANCELED: { label: 'Touch canceled', icon: CircleSlash, tone: 'warn', sev: 'medium' },
  TICKET_CREATED: { label: 'Routed to a person', icon: Ticket, tone: 'violet', sev: 'medium' },
  INJECTION_LOGGED: { label: 'Injection ignored', icon: ShieldAlert, tone: 'bad', sev: 'high' },
  HUMAN_ACTION: { label: 'Human action', icon: Hand, tone: 'info', sev: 'info' },
  ESTIMATE_UPDATED: { label: 'Estimates updated', icon: TrendingUp, tone: 'violet', sev: 'info' },
  CAMPAIGN_PAUSED: { label: 'Campaign paused', icon: PauseCircle, tone: 'warn', sev: 'medium' },
  CAMPAIGN_STOPPED: { label: 'Campaign stopped', icon: OctagonX, tone: 'bad', sev: 'high' },
  CAMPAIGN_ARMED: { label: 'Campaign armed', icon: Rocket, tone: 'ok', sev: 'info' },
  PROVIDER_DOWN: { label: 'Provider down', icon: ServerCrash, tone: 'bad', sev: 'high' },
  PROVIDER_UP: { label: 'Provider recovered', icon: Server, tone: 'ok', sev: 'info' },
  AGENT_OUTPUT: { label: 'Agent output', icon: Brain, tone: 'neutral', sev: 'info' },
}

export const TONE_TEXT = { neutral: 'text-muted', ok: 'text-ok', warn: 'text-warn', bad: 'text-bad', info: 'text-info', violet: 'text-violet' }
export const TONE_BG = { neutral: 'bg-white/[0.06]', ok: 'bg-ok/10', warn: 'bg-warn/10', bad: 'bg-bad/10', info: 'bg-info/10', violet: 'bg-violet/10' }

// events that belong in the guardrail & audit log
export const AUDIT_TYPES = new Set([
  'SEND_BLOCKED', 'TOUCH_CANCELED', 'STOP_RECEIVED', 'HUMAN_ACTION', 'D7_MISMATCH', 'INJECTION_LOGGED', 'OTP_FAILED',
  'HOLD_CREATED', 'CAMPAIGN_PAUSED', 'CAMPAIGN_STOPPED', 'CAMPAIGN_ARMED', 'PROVIDER_DOWN', 'PROVIDER_UP', 'TICKET_CREATED', 'BOUNCED',
])

export const MODULE = (e) => {
  if (e.type === 'HUMAN_ACTION') return 'Control Room'
  if (e.actor === 'guardrail' || e.type === 'SEND_BLOCKED') return 'Guardrail engine'
  if (e.type.startsWith('D7') || e.type.startsWith('OTP') || e.type === 'MEDIA_EXTRACTED') return 'Verification'
  if (e.type.startsWith('PROVIDER') || e.type === 'BOUNCED') return 'Channel adapter'
  if (e.type.startsWith('CAMPAIGN')) return 'Campaigns'
  if (e.type === 'TICKET_CREATED') return 'Routing'
  return 'Aladdin'
}
