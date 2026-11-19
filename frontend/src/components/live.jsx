import { Plug, PlugZap, RefreshCw } from 'lucide-react'
import { useApiStatus } from '../api/hooks'
import { Chip, cx } from './ui'
import { PROV_TONE, normProv } from '../lib/format'

export function ProvChip({ prov, className, title }) {
  const p = normProv(prov)
  return <span title={title}><Chip tone={PROV_TONE[p]} className={className}>{p}</Chip></span>
}

/**
 * Where a panel's numbers came from. Live data keeps the backend's provenance (SIMULATED until real adapters
 * are wired); a failed call shows "SIMULATED · offline mock" (or "· mock" when the API answered with an error).
 */
export function SourceBadge({ res, prov }) {
  const status = useApiStatus()
  if (!res || res.source === 'loading') return <Chip>Loading…</Chip>
  if (res.source === 'live') {
    const p = normProv(prov || res.data?.provenance || status.env)
    return <span title={`From the Kairos API (${status.base})`}><Chip tone={PROV_TONE[p]} dot>{p} · API</Chip></span>
  }
  const why = res.offline ? 'Kairos API unreachable — showing the built-in mock data' : `API answered: ${res.error?.message || 'error'} — showing mock data`
  return <span title={why}><Chip tone="warn">{res.offline ? 'SIMULATED · offline mock' : 'SIMULATED · mock'}</Chip></span>
}

/** One-line explanation under a card when it fell back to mock data. */
export function MockNote({ res, children }) {
  if (!res || res.source !== 'mock') return null
  return (
    <div className="mx-5 mb-3 rounded-lg border border-warn/25 bg-warn/5 px-3 py-2 text-[12px] text-warn">
      {res.offline ? 'Kairos API unreachable — this panel shows the built-in mock (SIMULATED).' : `API: ${res.error?.message || 'error'} — this panel shows the built-in mock (SIMULATED).`}
      {children ? <span className="text-muted"> {children}</span> : null}
    </div>
  )
}

export function RefreshButton({ onClick, busy }) {
  return (
    <button onClick={onClick} title="Refresh from the API" className="grid h-7 w-7 place-items-center rounded-md text-muted hover:bg-raised hover:text-fg">
      <RefreshCw size={13} className={cx(busy && 'animate-spin')} />
    </button>
  )
}

/** Header pill: is the backend up, which policy version, which base URL. */
export function ApiStatusPill() {
  const s = useApiStatus()
  const on = s.state === 'online'
  const title = on ? `Kairos API ${s.base} · policy v${s.policyVersion ?? '?'} · env ${s.env ?? '?'}` : `Kairos API ${s.base} not reachable — panels use mock data labelled SIMULATED`
  return (
    <span title={title} className={cx('inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border px-2.5 py-0.5 text-[12px] font-semibold',
      on ? 'border-ok/25 bg-ok/10 text-ok' : s.state === 'unknown' ? 'border-line text-muted' : 'border-warn/30 bg-warn/10 text-warn')}>
      {on ? <PlugZap size={12} /> : <Plug size={12} />}
      {on ? `API live${s.policyVersion ? ` · policy v${s.policyVersion}` : ''}` : s.state === 'unknown' ? 'API …' : 'API offline'}
    </span>
  )
}
