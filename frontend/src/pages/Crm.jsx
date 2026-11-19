import { useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { Search, ChevronLeft, ChevronRight, Star, SlidersHorizontal, RotateCcw } from 'lucide-react'
import { useKairos, useDerived, decideHero } from '../lib/store'
import { BANDS, REGIONS, RISK_TAGS, RESPONSES } from '../lib/mock'
import { useApi, useApiStatus } from '../api/hooks'
import { getAudit } from '../api/kairos'
import { SourceBadge } from '../components/live'
import { Button, Card, ChannelTag, Chip, Input, KpiStrip, PageHeader, Segmented, Select, StatusChip, Td, Th, cx, inr, pct } from '../components/ui'

const PAGE = 15
const lakh = (n) => (n >= 1e5 ? `₹${(n / 1e5).toFixed(1)}L` : inr(n))

export default function Crm() {
  const { state } = useKairos()
  const d = useDerived()
  const nav = useNavigate()
  const [params] = useSearchParams()
  const [q, setQ] = useState('')
  const [flag, setFlag] = useState(params.get('flag') || 'All')
  const [f, setF] = useState({ band: 'All', risk: 'All', region: 'All', cohort: params.get('cohort') || 'All', response: 'All', next: 'All', status: 'All' })
  const [showFilters, setShowFilters] = useState(false)
  const [page, setPage] = useState(0)
  const set = (patch) => { setF({ ...f, ...patch }); setPage(0) }

  const all = useMemo(() => {
    const h = state.hero
    const action = h.flags.stop ? 'STOP' : h.loans.every((l) => l.status === 'PAID') ? 'DONE' : decideHero(h, state.day).action
    const hero = { ...h, nextAction: action, lastTouchDay: state.day, flags: { ...h.flags, hold: h.flags.hold && h.flags.hold.until > state.day ? h.flags.hold : null } }
    return [hero, ...state.borrowers]
  }, [state])

  const rows = all.filter((b) => {
    if (q && !`${b.id} ${b.name} ${b.loans.map((l) => l.id).join(' ')} ${b.phone}`.toLowerCase().includes(q.toLowerCase())) return false
    if (f.band !== 'All' && b.band !== f.band) return false
    if (f.risk !== 'All' && b.riskTag !== f.risk) return false
    if (f.region !== 'All' && b.region !== f.region) return false
    if (f.cohort !== 'All' && b.cohortId !== f.cohort) return false
    if (f.response !== 'All' && b.lastResponse !== f.response) return false
    if (f.next !== 'All' && b.nextAction !== f.next) return false
    if (f.status !== 'All' && b.status !== f.status) return false
    if (flag === 'Hold' && !b.flags.hold) return false
    if (flag === 'STOP' && !(b.flags.stop || b.flags.dnc)) return false
    if (flag === 'Hardship' && !b.flags.hardship) return false
    if (flag === 'Paid' && !b.loans.some((l) => l.status === 'PAID')) return false
    return true
  })
  const pages = Math.max(1, Math.ceil(rows.length / PAGE))
  const view = rows.slice(page * PAGE, page * PAGE + PAGE)
  const outstanding = all.reduce((a, b) => a + b.loans.reduce((x, l) => x + (l.status === 'PAID' ? 0 : l.outstanding), 0), 0)
  const avgDpd = all.reduce((a, b) => a + b.dpd, 0) / all.length
  const activeFilters = Object.values(f).filter((v) => v !== 'All').length
  const cohortName = (id) => state.cohorts.find((c) => c.id === id)?.name ?? '—'

  return (
    <>
      <PageHeader title="Smart CRM" sub="The portfolio — one record per borrower. Every agent reads it; Aladdin decides from it."
        chip={<Chip tone="warn">Synthetic data</Chip>} />
      <SpineLookup />
      <div className="mb-5">
        <KpiStrip items={[
          { label: 'Total loans', value: all.reduce((a, b) => a + b.loans.length, 0), sub: `${all.length} borrowers` },
          { label: 'Total outstanding', value: lakh(outstanding), sub: 'Open loans only' },
          { label: 'Average DPD', value: Math.round(avgDpd), sub: 'Across the portfolio' },
          { label: 'Collected so far', value: lakh(d.collected), sub: `${pct(d.collectionPct)} of overdue` },
          { label: 'Cohorts', value: state.cohorts.length, sub: `${all.filter((b) => !b.cohortId).length} loans in none` },
        ]} />
      </div>
      <Card>
        <div className="flex flex-wrap items-center gap-3 border-b border-line px-5 py-3.5">
          <div className="relative w-72">
            <Search size={14} className="absolute top-1/2 left-3 -translate-y-1/2 text-faint" />
            <Input value={q} onChange={(e) => { setQ(e.target.value); setPage(0) }} placeholder="Search loan ID, name or phone…" className="pl-8" />
          </div>
          <Segmented value={flag} onChange={(v) => { setFlag(v); setPage(0) }} options={['All', 'Hold', 'STOP', 'Hardship', 'Paid']} />
          <button onClick={() => setShowFilters(!showFilters)} className={cx('inline-flex h-9 items-center gap-2 rounded-lg border px-3 text-[13px]', showFilters || activeFilters ? 'border-fg/40 text-fg' : 'border-line text-muted')}>
            <SlidersHorizontal size={14} /> Filters{activeFilters ? ` · ${activeFilters}` : ''}
          </button>
          {activeFilters > 0 && <button onClick={() => set({ band: 'All', risk: 'All', region: 'All', cohort: 'All', response: 'All', next: 'All', status: 'All' })} className="inline-flex items-center gap-1 text-[12.5px] text-muted hover:text-fg"><RotateCcw size={12} /> Reset</button>}
          <div className="flex-1" />
          <span className="text-[12.5px] text-muted">{rows.length} borrowers</span>
        </div>
        {showFilters && (
          <div className="grid grid-cols-4 gap-3 border-b border-line bg-raised/30 px-5 py-3.5">
            <Select value={f.cohort} onChange={(v) => set({ cohort: v })} options={[{ value: 'All', label: 'Any cohort' }, ...state.cohorts.map((c) => ({ value: c.id, label: c.name }))]} />
            <Select value={f.risk} onChange={(v) => set({ risk: v })} options={[{ value: 'All', label: 'Any risk tag' }, ...Object.entries(RISK_TAGS).map(([k, v]) => ({ value: k, label: v }))]} />
            <Select value={f.region} onChange={(v) => set({ region: v })} options={[{ value: 'All', label: 'Any region' }, ...REGIONS]} />
            <Select value={f.band} onChange={(v) => set({ band: v })} options={[{ value: 'All', label: 'Any DPD band' }, ...BANDS.map((b) => ({ value: b, label: `${b} DPD` }))]} />
            <Select value={f.response} onChange={(v) => set({ response: v })} options={[{ value: 'All', label: 'Any last response' }, ...RESPONSES]} />
            <Select value={f.next} onChange={(v) => set({ next: v })} options={[{ value: 'All', label: 'Any next action' }, 'SEND', 'WAIT', 'HANDOFF', 'STOP', 'DONE']} />
            <Select value={f.status} onChange={(v) => set({ status: v })} options={[{ value: 'All', label: 'Any status' }, 'Active', 'Collected']} />
          </div>
        )}
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead><tr><Th>Borrower · loan</Th><Th>DPD</Th><Th>Outstanding</Th><Th>Risk tag</Th><Th>Region</Th><Th>NACH</Th><Th>Last response</Th><Th>Cohort</Th><Th>Next action</Th><Th>Status</Th></tr></thead>
            <tbody>
              {view.map((b) => (
                <tr key={b.id} onClick={() => nav(`/crm/${b.id}`)} className="cursor-pointer hover:bg-raised/60">
                  <Td className="whitespace-nowrap">
                    <div className="flex items-center gap-2.5">
                      <div className="grid h-7 w-7 place-items-center rounded-full bg-info/15 text-[10.5px] font-bold text-info">{b.name.split(' ').map((p) => p[0]).join('')}</div>
                      <div>
                        <div className="flex items-center gap-1.5 font-semibold">{b.name}{b.hero && <Star size={12} className="fill-warn text-warn" />}</div>
                        <div className="font-mono text-[11.5px] text-muted">{b.loans.map((l) => l.id).join(' · ')}</div>
                      </div>
                    </div>
                  </Td>
                  <Td><Chip tone={b.dpd >= 60 ? 'bad' : b.dpd >= 30 ? 'warn' : 'neutral'}>{b.dpd}</Chip></Td>
                  <Td className="tabular">{inr(b.loans.reduce((a, l) => a + (l.status === 'PAID' ? 0 : l.outstanding), 0))}</Td>
                  <Td className="whitespace-nowrap text-[12.5px] text-muted">{RISK_TAGS[b.riskTag]}</Td>
                  <Td className="text-[12.5px] text-muted"><div>{b.region}</div><div className="text-[11.5px] text-faint">{b.state}</div></Td>
                  <Td><span className={cx('text-[12.5px]', b.nach === 'Bounced' ? 'text-warn' : 'text-muted')}>{b.nach}</span></Td>
                  <Td><span className={cx('text-[12.5px]', b.lastResponse === 'Replied' ? 'text-ok' : 'text-muted')}>{b.lastResponse}</span><div><ChannelTag ch={b.lastTouchChannel} muted /></div></Td>
                  <Td className="whitespace-nowrap text-[12.5px]">{cohortName(b.cohortId)}</Td>
                  <Td><StatusChip s={b.nextAction}>{b.nextAction}</StatusChip></Td>
                  <Td>{b.status === 'Collected' ? <Chip tone="ok">Collected</Chip> : <Chip>Active</Chip>}</Td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="flex items-center justify-between border-t border-line px-5 py-3 text-[12.5px] text-muted">
          <span>Page {page + 1} of {pages}</span>
          <div className="flex gap-1.5">
            <button disabled={page === 0} onClick={() => setPage(page - 1)} className="rounded-md border border-line p-1 disabled:opacity-30"><ChevronLeft size={14} /></button>
            <button disabled={page >= pages - 1} onClick={() => setPage(page + 1)} className="rounded-md border border-line p-1 disabled:opacity-30"><ChevronRight size={14} /></button>
          </div>
        </div>
      </Card>
    </>
  )
}

// The backend has no borrower list endpoint: open any spine borrower by id, or pick one seen in the K06 audit.
function SpineLookup() {
  const nav = useNavigate()
  const status = useApiStatus()
  const res = useApi((o) => getAudit({ limit: 1000 }, o), { deps: [status.state] })
  const [id, setId] = useState('')
  const recent = [...new Set((res.data || []).map((r) => r.borrower_id).filter(Boolean))].slice(0, 14)
  return (
    <Card className="mb-4 flex flex-wrap items-center gap-3 px-5 py-3.5">
      <span className="text-[13px] font-semibold">K03 spine borrower</span>
      <form onSubmit={(e) => { e.preventDefault(); if (id.trim()) nav(`/crm/${encodeURIComponent(id.trim())}`) }} className="flex items-center gap-2">
        <div className="w-56"><Input value={id} onChange={(e) => setId(e.target.value)} placeholder="Borrower id, e.g. BRW-…" /></div>
        <Button type="submit" size="md">Open</Button>
      </form>
      <SourceBadge res={res} />
      <div className="flex min-w-0 flex-1 flex-wrap gap-1.5">
        {res.source === 'live' ? (recent.length ? recent.map((b) => (
          <button key={b} onClick={() => nav(`/crm/${b}`)} className="rounded-full border border-line px-2 py-0.5 font-mono text-[11.5px] text-muted hover:text-fg">{b}</button>
        )) : <span className="text-[12px] text-muted">No borrowers in the audit yet.</span>)
          : <span className="text-[12px] text-muted">API offline — the table below is the built-in mock (SIMULATED).</span>}
      </div>
    </Card>
  )
}
