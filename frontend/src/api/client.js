// Thin fetch wrapper for the Kairos backend. No dependencies.
// - OfflineError: network failure or timeout -> panels fall back to mock data (labelled SIMULATED · offline mock).
// - ApiError: the backend answered with an error status (403 role, 404 route/record missing, 409 refused, 422 invalid).
// Never log request bodies or URLs: demo-recipient calls carry phone numbers.
import { API_BASE, STATIC_DEMO, TIMEOUT_MS } from './config'

export class OfflineError extends Error {
  constructor(message = 'Kairos API unreachable') {
    super(message)
    this.name = 'OfflineError'
  }
}

export class ApiError extends Error {
  /** @param {number} status @param {string} message @param {?string} code */
  constructor(status, message, code = null) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
  }
}

// ---------- shared connection status (one per page load) ----------
const listeners = new Set()
let status = { state: 'unknown', policyVersion: null, env: null, base: API_BASE, checkedAt: null }
export const getApiStatus = () => status
export function subscribeApiStatus(fn) {
  listeners.add(fn)
  return () => listeners.delete(fn)
}
export function setApiStatus(patch) {
  const next = { ...status, ...patch }
  // keep the same object when nothing changed: useSyncExternalStore re-renders on every new snapshot
  if (next.state === status.state && next.policyVersion === status.policyVersion && next.env === status.env) return
  status = { ...next, checkedAt: Date.now() }
  listeners.forEach((fn) => fn())
}

// Turn a FastAPI error body into one readable line. Masks any long digit run so a phone number in a
// validation message is never shown.
export function maskDigits(text) {
  return String(text ?? '').replace(/\+?\d[\d\s-]{5,}\d/g, (m) => {
    const d = m.replace(/\D/g, '')
    return '*'.repeat(Math.max(0, d.length - 4)) + d.slice(-4)
  })
}
function errorText(body, status) {
  const d = body?.detail
  if (!d) return `HTTP ${status}`
  if (typeof d === 'string') return maskDigits(d)
  if (Array.isArray(d)) return maskDigits(d.map((x) => x.msg || JSON.stringify(x)).join('; '))
  if (d.message) return maskDigits(d.message)
  return maskDigits(JSON.stringify(d))
}

/**
 * @param {string} path  e.g. "/audit?limit=200"
 * @param {{method?: string, body?: any, role?: string, timeout?: number, signal?: AbortSignal, raw?: boolean}} [opt]
 */
export async function request(path, opt = {}) {
  const { method = 'GET', body, role, timeout = TIMEOUT_MS, raw = false } = opt
  if (STATIC_DEMO) {
    setApiStatus({ state: 'offline' })
    throw new OfflineError('Static demo build — no backend configured')
  }
  const ctl = new AbortController()
  const timer = setTimeout(() => ctl.abort(), timeout)
  const outer = opt.signal
  const onAbort = () => ctl.abort()
  outer?.addEventListener('abort', onAbort)
  const headers = { Accept: 'application/json' }
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  if (role) headers['X-Kairos-Role'] = role
  let res
  try {
    res = await fetch(API_BASE + path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body), signal: ctl.signal })
  } catch (e) {
    if (outer?.aborted) throw e // caller unmounted: not an outage
    setApiStatus({ state: 'offline' })
    throw new OfflineError(e?.name === 'AbortError' ? 'Kairos API timed out' : 'Kairos API unreachable')
  } finally {
    clearTimeout(timer)
    outer?.removeEventListener('abort', onAbort)
  }
  if (status.state !== 'online') setApiStatus({ state: 'online' })
  if (raw) return res
  let data = null
  const text = await res.text()
  try { data = text ? JSON.parse(text) : null } catch { data = text }
  if (!res.ok) throw new ApiError(res.status, errorText(data, res.status), data?.detail?.code ?? null)
  return data
}

export async function checkHealth() {
  try {
    const h = await request('/health', { timeout: 2500 })
    setApiStatus({ state: 'online', policyVersion: h?.policy_version ?? null, env: h?.env ?? null })
    return h
  } catch (e) {
    if (e instanceof OfflineError) setApiStatus({ state: 'offline' })
    return null
  }
}
