// Offline fallbacks: the same shapes the API returns, built from the existing mock store (src/lib/mock.js).
// Everything here is SIMULATED and every panel that shows it says so.
import { BANDS, CHANNEL_META, ESTIMATES, SEGMENT_RPR_FACTOR, SEGMENT_SHARE, SERIES, SWITCH_DAY, UNCERTAINTY } from '../lib/mock'
import { fmtClock } from '../lib/store'

const MOCK_POLICY = 'mock'
const ts = (day, min) => `Day ${day} ${fmtClock(min)}`
const BAND_ID = { '1–29': 'B1', '30–59': 'B2', '60–89': 'B3', '90+': 'B4' }

const QUEUE_ACTION = { RESERVED: 'SEND', SCHEDULED: 'SEND', APPROVED: 'SEND', NEEDS_APPROVAL: 'SEND', HELD: 'WAIT', WAITING: 'WAIT', PAUSED: 'WAIT', CANCELED: 'CANCEL' }

/** Decisions in the /audit row shape (type DECISION). */
export function mockDecisions(state, allQueue) {
  const rows = allQueue.map((q, i) => ({
    id: i + 1, ts: q.when, actor: 'supervisor', type: 'DECISION', borrower_id: q.borrowerId, instruction_id: q.id,
    decision: QUEUE_ACTION[q.status] || 'WAIT', reason: q.reason, rule: q.status === 'NEEDS_APPROVAL' ? 'APPROVAL_REQUIRED' : q.status,
    policy_version: MOCK_POLICY, detail: { channel: q.channel, template_id: q.template, loan_id: q.loan },
  }))
  state.borrowers.filter((b) => b.nextAction === 'HANDOFF').slice(0, 3).forEach((b, i) => rows.push({
    id: 900 + i, ts: ts(state.day, 600 + i * 7), actor: 'supervisor', type: 'DECISION', borrower_id: b.id, instruction_id: null,
    decision: 'HANDOFF', reason: 'Hardship reported — a person owns this account', rule: 'HARDSHIP_HOLD', policy_version: MOCK_POLICY, detail: {},
  }))
  return rows
}

export function mockCampaign(state, d, campId) {
  const c = state.campaigns.find((x) => x.id === campId) || state.campaigns[0]
  return {
    id: c.id, name: c.name, status: c.status, budget_per_case_paise: Math.round((c.totalBudget * 100) / Math.max(1, c.enrolled)),
    allocated_cases: c.enrolled, pool_paise: c.totalBudget * 100, committed_paise: Math.round(d.spend * 100), collected_paise: 0, cured_count: d.payments,
    target_met: false, days: [{ day: `Day ${state.day}`, allowance_paise: c.dailyBudget * 100, committed_paise: Math.round(d.todaySpend * 100) }],
  }
}

/** Approvals in the /hitl/approvals shape, from mock queue rows that need a person. */
export function mockApprovals(state, allQueue) {
  return allQueue.filter((q) => q.status === 'NEEDS_APPROVAL').map((q) => {
    const b = state.borrowers.find((x) => x.id === q.borrowerId)
    return {
      instruction_id: q.id, borrower_id: q.borrowerId, loan_id: q.loan, campaign_id: q.campaignId, channel: q.channel, template_id: q.template,
      scheduled_at: q.when, status: 'PENDING_APPROVAL', reason: 'APPROVAL_REQUIRED', est_cost_paise: Math.round(q.cost * 100),
      rules: q.channel === 'ivr' ? ['VOICE_CHANNEL'] : ['STANDARD_INTENSITY_HIGH_DPD'], origin: 'auto', expires_at: `Day ${state.day} 19:00`,
      full_name: q.name, phone: b?.phone ?? '—', mock: true,
    }
  })
}

export const MOCK_PERMISSIONS = {
  operator: ['view', 'reject', 'pause', 'resume', 'hold', 'cancel_next', 'handoff', 'assign', 'dnc', 'schedule_manual'],
  supervisor: ['view', 'approve', 'reject', 'pause', 'resume', 'hold', 'cancel_next', 'handoff', 'assign', 'dnc', 'schedule_manual', 'record_opt_in', 'lift_hold'],
  policy_owner: ['view', 'approve', 'reject', 'pause', 'resume', 'hold', 'cancel_next', 'handoff', 'assign', 'dnc', 'schedule_manual', 'record_opt_in', 'lift_hold', 'register_template'],
}

/** CM-02 daily per-segment report in the /reports/daily-segment shape. */
export function mockDailySegment(day) {
  const spend = SERIES.adaptive.spend[day - 1]
  const replies = SERIES.adaptive.replies[day - 1]
  const rpr = replies / spend
  const mix = { sms: day >= SWITCH_DAY ? 0.45 : 0.3, whatsapp: day >= SWITCH_DAY ? 0.3 : 0.45, email: 0.1, ivr: 0.15 }
  const segments = []
  BANDS.forEach((b) => Object.entries(mix).forEach(([ch, share]) => {
    const s = spend * SEGMENT_SHARE[b] * share
    const r = s * rpr * SEGMENT_RPR_FACTOR[b] * (ch === 'email' ? 0.3 : 1)
    segments.push({
      band: BAND_ID[b], channel: ch, attempts: Math.round(s / CHANNEL_META[ch].cost), reach: Math.round((s / CHANNEL_META[ch].cost) * 0.9),
      meaningful_replies: Math.round(r), responders: Math.round(r), spend_committed_inr: +s.toFixed(2), replies_per_1000_inr: s ? +((r / s) * 1000).toFixed(1) : 0, cap_breaches: 0,
    })
  }))
  const tot = segments.reduce((a, x) => ({ attempts: a.attempts + x.attempts, meaningful_replies: a.meaningful_replies + x.meaningful_replies, spend_committed_inr: a.spend_committed_inr + x.spend_committed_inr, cap_breaches: 0 }), { attempts: 0, meaningful_replies: 0, spend_committed_inr: 0, cap_breaches: 0 })
  return {
    report: 'daily_segment', provenance: 'SIMULATED', campaign_id: 'C-01', day: `Day ${day}`, policy_version: MOCK_POLICY, segments,
    totals: { ...tot, spend_committed_inr: +tot.spend_committed_inr.toFixed(2), replies_per_1000_inr: +((tot.meaningful_replies / tot.spend_committed_inr) * 1000).toFixed(1) },
    learning: { available: true, arms: mockArms(day), decision_share: [], decisions_today: 0 }, notes: ['Built-in mock (SIMULATED).'],
  }
}

/** K09 arm scores (posterior mean per band × channel × slot) in the report's learning.arms shape. */
export function mockArms(day) {
  const slot = { whatsapp: 'evening', sms: 'morning', ivr: 'midday', email: 'morning' }
  const out = []
  BANDS.forEach((b, i) => ['whatsapp', 'sms', 'ivr', 'email'].forEach((ch) => {
    const mean = ESTIMATES[ch][day - 1] * [1.1, 1, 0.85, 0.6][i]
    const n = Math.round(1 / Math.max(0.0004, UNCERTAINTY[day - 1] ** 2) * [1, 0.8, 0.5, 0.25][i] * (ch === 'email' ? 0.4 : 1))
    out.push({ band: BAND_ID[b], channel: ch, slot: slot[ch], posterior_mean: +mean.toFixed(4), n })
  }))
  return out
}

/** Channel switches the mock campaign shows (WhatsApp → SMS on Day 4 for the two early bands). */
export function mockSwitches(day) {
  if (day < SWITCH_DAY) return []
  return ['B1', 'B2'].map((band) => ({ band, day: SWITCH_DAY, date: `Day ${SWITCH_DAY}`, from: 'whatsapp', to: 'sms' }))
}

// CLAUDE.md "Sarvam voice bots" table (registry 0.2.0) — used only when /voice/registry is unreachable.
export const MOCK_VOICE_REGISTRY = {
  registry: {
    registry_version: 'mock', provider: 'sarvam',
    supported_languages: ['Hindi', 'English', 'Marathi', 'Tamil', 'Telugu', 'Kannada', 'Bengali', 'Gujarati'],
    voices: { Ritu: { gender: 'female' }, Advait: { gender: 'male' } },
    use_cases: {
      PRE_DUE_REMINDER: { bands: ['PRE_DUE'], agent_id: 'Kairos---Pr-0282af37-ef9d', voice: 'Ritu', requires_human_approval: false },
      EMI_REMINDER_EARLY: { bands: ['B1'], agent_id: 'Kairos---EM-15f2766f-4eba', voice: 'Ritu', requires_human_approval: false },
      CALLBACK: { bands: '*', agent_id: 'Kairos---Ca-f080ffa8-2d1d', voice: 'Advait', requires_human_approval: false },
      MID_BUCKET_RESOLUTION: { bands: ['B2', 'B3'], agent_id: 'Kairos---Mi-edcd3099-61a2', voice: 'Advait', requires_human_approval: false },
      PTP_FOLLOW_UP: { bands: '*', agent_id: 'Kairos---PT-07a93e21-1a9b', voice: 'Ritu', requires_human_approval: false },
      HARDSHIP_TRIAGE: { bands: '*', agent_id: 'Kairos---Ha-1f02cd05-dfa0', voice: 'Advait', requires_human_approval: true },
    },
  },
  thresholds: { policy_version: 'mock', status: 'DRAFT', min_confidence_any: null },
}

/** What the registry would pick for a mock borrower (band by DPD; no Gate check). */
export function mockVoiceSelect(b) {
  const band = b ? (b.dpd < 1 ? 'PRE_DUE' : b.dpd < 31 ? 'B1' : b.dpd < 91 ? 'B2' : 'B4') : 'B1'
  const uc = Object.entries(MOCK_VOICE_REGISTRY.registry.use_cases).find(([, u]) => Array.isArray(u.bands) && u.bands.includes(band))
  const lang = b?.language && MOCK_VOICE_REGISTRY.registry.supported_languages.includes(b.language) ? b.language : 'Hindi'
  if (!uc) return { use_case: null, agent_id: null, voice: null, language: lang, reason: `no bot: NO_USE_CASE_FOR_BAND (${band})`, ready: false, requires_human_approval: false, registry_version: 'mock' }
  return { use_case: uc[0], agent_id: uc[1].agent_id, voice: uc[1].voice, language: lang, reason: `band ${band} → ${uc[0]}`, ready: true, requires_human_approval: uc[1].requires_human_approval, registry_version: 'mock' }
}

