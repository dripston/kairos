// Where the Kairos backend lives. Override with VITE_KAIROS_API in frontend/.env.local, e.g.
//   VITE_KAIROS_API=http://127.0.0.1:8000
//   VITE_KAIROS_API=none                -> static demo mode (GitHub Pages): no network calls, labelled mocks only
// A visitor can point a deployed build at a backend with ?api=https://host (remembered), or ?api=none to reset.
function resolveApiBase() {
  try {
    const q = new URLSearchParams(window.location.search).get('api')
    if (q) {
      if (q === 'none' || q === 'default') window.localStorage.removeItem('kairos.api')
      else window.localStorage.setItem('kairos.api', q)
    }
    const saved = window.localStorage.getItem('kairos.api')
    if (saved) return saved
  } catch { /* storage blocked: fall through */ }
  return import.meta.env.VITE_KAIROS_API || 'http://127.0.0.1:8000'
}
const RAW_API = resolveApiBase()
export const STATIC_DEMO = RAW_API === 'none'
export const API_BASE = STATIC_DEMO ? '' : RAW_API.replace(/\/+$/, '')

// Campaign the live panels read when the selected mock campaign is not in the backend.
export const DEFAULT_CAMPAIGN = import.meta.env.VITE_KAIROS_CAMPAIGN || 'C-01'

// Optional K09 replay report (backend/reports/k09_replay_report.json). Not served by the API today:
// copy it to frontend/public/k09_replay_report.json (gitignored) or point this at any URL that serves it.
export const REPLAY_REPORT_URL = import.meta.env.VITE_KAIROS_REPLAY_REPORT || `${import.meta.env.BASE_URL}k09_replay_report.json`

// A request that takes longer than this is treated as "API unreachable" and the panel falls back to mock data.
export const TIMEOUT_MS = Number(import.meta.env.VITE_KAIROS_TIMEOUT_MS || 4000)

// UI role -> X-Kairos-Role header (backend: viewer | operator | supervisor | policy_owner). A demo switch, not auth.
export const ROLE_HEADER = { operator: 'operator', supervisor: 'supervisor', policy: 'policy_owner' }
export const ROLE_ACTOR = { operator: 'ops.desk', supervisor: 'ops.supervisor', policy: 'policy.owner' }
export const ROLE_LABEL = { operator: 'Operator', supervisor: 'Supervisor', policy: 'Policy owner' }
