// Typed endpoint functions for the Kairos backend (see ./types.js for the shapes).
// Branch that serves each route today is noted; integration/all is expected to serve all of them.
import { request, maskDigits } from './client'
import { API_BASE, REPLAY_REPORT_URL } from './config'

const q = (params) => {
  const s = new URLSearchParams()
  Object.entries(params).forEach(([k, v]) => v != null && v !== '' && s.set(k, String(v)))
  const out = s.toString()
  return out ? `?${out}` : ''
}
const enc = encodeURIComponent

/** Keep only the last 4 digits of a phone number. */
export function maskPhone(p) {
  if (!p) return '—'
  const d = String(p).replace(/\D/g, '')
  if (d.length <= 4) return '•'.repeat(d.length)
  return '•'.repeat(Math.min(6, d.length - 4)) + d.slice(-4)
}

// ---------- K06 core (main) ----------
/** @returns {Promise<import('./types').Health>} */
export const getHealth = (o) => request('/health', o)
export const getPolicy = (o) => request('/policy', o)
/** @returns {Promise<import('./types').CampaignState>} */
export const getCampaign = (id, o) => request(`/campaigns/${enc(id)}`, o)
/** @returns {Promise<import('./types').AuditRow[]>} */
export const getAudit = ({ limit = 500, borrowerId } = {}, o) => request(`/audit${q({ limit, borrower_id: borrowerId })}`, o)
/** @returns {Promise<import('./types').CapBreaches>} */
export const getCapBreaches = (o) => request('/cap-breaches', o)

/**
 * Borrower from the K03 spine + K06 state. The phone is masked and the e-mail dropped here, before any page
 * sees it (no PII on screen or in logs).
 * @returns {Promise<import('./types').BorrowerState>}
 */
export async function getBorrower(id, o) {
  const b = await request(`/borrowers/${enc(id)}`, o)
  const out = { ...b, phone: maskPhone(b?.phone) }
  delete out.email
  return out
}

// ---------- K06 human in the loop (k06/hitl-control) — role travels as X-Kairos-Role ----------
export const getHitlPolicy = (role, o) => request('/hitl/policy', { ...o, role })
/** @returns {Promise<import('./types').Approval[]>} */
export const getApprovals = ({ status = 'PENDING', borrowerId } = {}, role, o) =>
  request(`/hitl/approvals${q({ status, borrower_id: borrowerId })}`, { ...o, role })
export const approve = (instructionId, { actor, reason }, role) =>
  request(`/hitl/approvals/${enc(instructionId)}/approve`, { method: 'POST', role, body: { actor, reason } })
export const reject = (instructionId, { actor, reason }, role) =>
  request(`/hitl/approvals/${enc(instructionId)}/reject`, { method: 'POST', role, body: { actor, reason } })
/** @returns {Promise<import('./types').BorrowerControls>} */
export const getBorrowerControls = (id, role, o) => request(`/hitl/borrowers/${enc(id)}`, { ...o, role })
/** action: pause | resume | hold | cancel_next | handoff | assign | dnc | record_opt_in */
export const borrowerAction = (id, body, role) =>
  request(`/hitl/borrowers/${enc(id)}/actions`, { method: 'POST', role, body })
/** @returns {Promise<import('./types').ManualPreview>} */
export const manualPreview = (body, role) => request('/hitl/manual/preview', { method: 'POST', role, body })
export const getHitlAudit = ({ borrowerId, limit = 200, humanOnly } = {}, role, o) =>
  request(`/hitl/audit${q({ borrower_id: borrowerId, limit, human_only: humanOnly })}`, { ...o, role })
export const getCases = ({ status = 'OPEN', borrowerId } = {}, role, o) =>
  request(`/hitl/cases${q({ status, borrower_id: borrowerId })}`, { ...o, role })

// ---------- K26 reports (k26/report-pack) ----------
/** @returns {Promise<import('./types').DailySegment>} */
export const getDailySegment = ({ campaignId, day }, o) => request(`/reports/daily-segment${q({ campaign_id: campaignId, day })}`, o)
export const dailySegmentCsvUrl = ({ campaignId, day }) => `${API_BASE}/reports/daily-segment${q({ campaign_id: campaignId, day, format: 'csv' })}`

// ---------- K17/K18 voice (k17-k18/voice-engine) ----------
export const getVoiceRegistry = (o) => request('/voice/registry', o)
/** @returns {Promise<import('./types').VoiceSelection>} */
export const selectVoiceBot = (body, o) => request('/voice/select', { ...o, method: 'POST', body })
export const getVoiceCall = (callId, o) => request(`/voice/calls/${enc(callId)}`, o)

// ---------- demo recipients (k07-k08/channel-adapters) — numbers only ever come back masked ----------
/** @returns {Promise<import('./types').DemoRecipients>} */
export const getDemoRecipients = (o) => request('/demo/recipients', o)
export const addDemoRecipient = (number) => request('/demo/recipients', { method: 'POST', body: { number } })
// The backend removes by the full number (the list is masked, so the operator re-types it).
export const removeDemoRecipient = (number) => request(`/demo/recipients/${enc(number)}`, { method: 'DELETE' })

// ---------- discovery ----------
/** Paths the running backend serves (from FastAPI's OpenAPI document). */
export async function getRoutes(o) {
  const doc = await request('/openapi.json', o)
  return new Set(Object.keys(doc?.paths || {}))
}
export const CM03_CONSOLE_URL = `${API_BASE}/cm03/console`

// ---------- K09 replay report (static JSON, optional) ----------
export async function getReplayReport(o) {
  const res = await fetch(REPLAY_REPORT_URL, { signal: o?.signal, headers: { Accept: 'application/json' } })
  if (!res.ok) throw new Error(`replay report not served (${res.status})`)
  const type = res.headers.get('content-type') || ''
  if (!type.includes('json')) throw new Error('replay report not served')
  return res.json()
}

export { maskDigits }

// ---------- live feed + journey (frontend/live-api backend addition; integration/all) ----------
/** GET /events/since — audit rows after `cursor` as CRM-envelope-like events. */
export const getEventsSince = ({ cursor = 0, limit = 200, borrowerId } = {}, o) =>
  request(`/events/since${q({ cursor, limit, borrower_id: borrowerId })}`, o)
/** POST /demo/journey/run — the integrator's scripted borrower journey (scripts/run_journey.py). */
export const runJourney = (seed, o) => request('/demo/journey/run', { ...o, method: 'POST', body: { seed: Number(seed) || 42 }, timeout: 60000 })
/** A saved journey report (reports/journey/*.json copied to frontend/public/journey/journey.json). */
export async function getJourneyFile(o) {
  const res = await fetch('/journey/journey.json', { signal: o?.signal, headers: { Accept: 'application/json' } })
  if (!res.ok || !(res.headers.get('content-type') || '').includes('json')) throw new Error('no saved journey')
  return res.json()
}
