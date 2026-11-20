// GET /health/details: database, policy, models, background loop, uptime. "Test models" adds ?probe=true
// (one tiny live call per model). Offline it says so; there is no mock health.
import { useState } from 'react'
import { Activity, Database, ShieldCheck, Sparkles, Timer } from 'lucide-react'
import { useApi, useApiStatus } from '../api/hooks'
import { getHealthDetails } from '../api/kairos'
import { Button, Card, CardHead, Chip, Empty, cx } from './ui'

const fmtUptime = (s) => (s >= 3600 ? `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m` : s >= 60 ? `${Math.floor(s / 60)}m` : `${s}s`)

// Display names for the hosted models (the vendor's model ids stay in the API and the logs).
const modelLabel = (id) => (/prism/i.test(id) ? 'Decision model' : /conversations/i.test(id) ? 'Conversation model'
  : /deepseek/i.test(id) ? 'Explanation model' : id)

function Row({ icon: I, label, ok, children }) {
  return (
    <div className="flex items-start gap-3 border-t border-line py-3 first:border-t-0">
      <I size={15} className={cx('mt-0.5 shrink-0', ok === false ? 'text-bad' : 'text-muted')} />
      <div className="min-w-0 flex-1">
        <div className="flex items-center justify-between gap-3 text-[13.5px] font-semibold">{label}{ok !== undefined && <Chip tone={ok ? 'ok' : 'bad'}>{ok ? 'OK' : 'Problem'}</Chip>}</div>
        <div className="mt-0.5 text-[12.5px] text-muted">{children}</div>
      </div>
    </div>
  )
}

export default function SystemHealth() {
  const status = useApiStatus()
  const res = useApi((o) => getHealthDetails(false, o), { deps: [status.state], poll: 30000 })
  const [probe, setProbe] = useState(null)
  const d = res.data
  const m = d?.checks?.models
  const live = probe?.checks?.models?.live

  const runProbe = async () => {
    setProbe({ loading: true })
    try { setProbe(await getHealthDetails(true)) } catch (e) { setProbe({ error: e?.message || 'failed' }) }
  }

  return (
    <Card>
      <CardHead title="System health" sub="GET /health/details · refreshes every 30 s"
        right={d ? <Chip tone={d.ok ? 'ok' : 'bad'} dot>{d.ok ? 'All systems OK' : 'Degraded'}</Chip> : null} />
      <div className="px-6 pb-4">
        {!d ? <Empty>{res.loading ? 'Checking…' : 'Kairos API unreachable — no health data.'}</Empty> : <>
          <Row icon={Database} label="Database" ok={d.checks.database.ok}>
            {d.checks.database.ok ? `${d.checks.database.latency_ms} ms · ${d.checks.database.rows.borrowers} borrowers · ${d.checks.database.rows.touches} touches · ${d.checks.database.rows.audit} audit rows` : d.checks.database.error}
          </Row>
          <Row icon={ShieldCheck} label="Policy" ok>K01 v{d.checks.policy.version} loaded</Row>
          <Row icon={Sparkles} label="AI models" ok={m.ok}>
            {m.configured ? <>Key from {m.key_source} · circuit {m.circuit} · {Object.values(m.stats?.calls || {}).reduce((a, b) => a + b, 0)} calls so far</> : 'Off — no key configured (Kairos runs fully deterministic)'}
            {m.configured && (
              <div className="mt-2 space-y-1.5">
                <Button size="sm" icon={Activity} onClick={runProbe} disabled={probe?.loading}>{probe?.loading ? 'Testing…' : 'Test models live'}</Button>
                {live && Object.entries(live).map(([name, r]) => (
                  <div key={name} className="flex items-center justify-between font-mono text-[12px]">
                    <span>{modelLabel(name)}</span><span className={r.ok ? 'text-ok' : 'text-bad'}>{r.ok ? `✓ ${r.latency_ms} ms` : `✗ ${r.error}`}</span>
                  </div>
                ))}
                {probe?.error && <div className="text-[12px] text-bad">{probe.error}</div>}
              </div>
            )}
          </Row>
          <Row icon={Timer} label="Server">Up {fmtUptime(d.uptime_s)} · version {d.version || '—'} · {d.env}</Row>
        </>}
      </div>
    </Card>
  )
}
