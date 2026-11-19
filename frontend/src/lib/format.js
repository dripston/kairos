// Small formatters shared by the live (API-backed) panels.

/** Normalise any provenance value to one of the three labels the judges see. */
export function normProv(p) {
  const s = String(p || '').toUpperCase().replace(/[_-]/g, ' ').trim()
  if (s === 'LIVE' || s === 'LIVE SANDBOX') return 'LIVE SANDBOX'
  if (s === 'REPLAY') return 'REPLAY'
  return 'SIMULATED'
}
export const PROV_TONE = { 'LIVE SANDBOX': 'ok', REPLAY: 'info', SIMULATED: 'warn' }

export const paiseInr = (p, d = 2) => '₹' + (Number(p || 0) / 100).toLocaleString('en-IN', { minimumFractionDigits: d, maximumFractionDigits: d })

/** "2026-11-20T10:03:00+05:30" -> "20/11 10:03" (the backend already sends IST). */
export const fmtTs = (iso) => {
  if (!iso) return '—'
  const m = String(iso).match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/)
  return m ? `${m[3]}/${m[2]} ${m[4]}:${m[5]}` : String(iso)
}
export const dayOf = (iso) => String(iso || '').slice(0, 10)
export const todayIso = () => {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}
