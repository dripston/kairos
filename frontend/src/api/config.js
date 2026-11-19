// Where the Kairos backend lives. Override with VITE_KAIROS_API in frontend/.env.local, e.g.
//   VITE_KAIROS_API=http://127.0.0.1:8000
export const API_BASE = (import.meta.env.VITE_KAIROS_API || 'http://127.0.0.1:8000').replace(/\/+$/, '')

// Campaign the live panels read when the selected mock campaign is not in the backend.
export const DEFAULT_CAMPAIGN = import.meta.env.VITE_KAIROS_CAMPAIGN || 'C-01'

// Optional K09 replay report (backend/reports/k09_replay_report.json). Not served by the API today:
// copy it to frontend/public/k09_replay_report.json (gitignored) or point this at any URL that serves it.
export const REPLAY_REPORT_URL = import.meta.env.VITE_KAIROS_REPLAY_REPORT || '/k09_replay_report.json'

// A request that takes longer than this is treated as "API unreachable" and the panel falls back to mock data.
export const TIMEOUT_MS = Number(import.meta.env.VITE_KAIROS_TIMEOUT_MS || 4000)

// UI role -> X-Kairos-Role header (backend: viewer | operator | supervisor | policy_owner). A demo switch, not auth.
export const ROLE_HEADER = { operator: 'operator', supervisor: 'supervisor', policy: 'policy_owner' }
export const ROLE_ACTOR = { operator: 'ops.desk', supervisor: 'ops.supervisor', policy: 'policy.owner' }
export const ROLE_LABEL = { operator: 'Operator', supervisor: 'Supervisor', policy: 'Policy owner' }
