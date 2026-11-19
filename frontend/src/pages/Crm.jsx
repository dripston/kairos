import { useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { Search, ChevronLeft, ChevronRight, Star } from 'lucide-react'
import { useKairos, decideHero } from '../lib/store'
import { BANDS } from '../lib/mock'
import { useApi, useApiStatus } from '../api/hooks'
import { getAudit } from '../api/kairos'
import { SourceBadge } from '../components/live'
import { Button, Card, ChannelTag, Chip, Input, PageHeader, Segmented, Select, StatusChip, Td, Th, inr } from '../components/ui'

const PAGE = 15

export default function Crm() {
  const { state } = useKairos()
  const nav = useNavigate()
  const [params] = useSearchParams()
  const [q, setQ] = useState('')
  const [flag, setFlag] = useState(params.get('flag') || 'All')
  const [bandF, setBandF] = useState(params.get('band') || 'All')
  const [next, setNext] = useState(params.get('next') || 'All')
  const [page, setPage] = useState(0)

  const all = useMemo(() => {
    const h = state.hero
    const hero = { ...h, nextAction: decideLabel(h, state.day), lastTouchDay: state.day, flags: { ...h.flags, hold: h.flags.hold && h.flags.hold.until > state.day ? h.flags.hold : null } }
    return [hero, ...state.borrowers]
  }, [state])

  const rows = all.filter((b) => {
    if (q && !`${b.id} ${b.name} ${b.loans.map((l) => l.id).join(' ')} ${b.phone}`.toLowerCase().includes(q.toLowerCase())) return false
    if (bandF !== 'All' && b.band !== bandF) return false
    if (next !== 'All' && b.nextAction !== next) return false
    if (flag === 'Hold' && !b.flags.hold) return false
    if (flag === 'STOP' && !(b.flags.stop || b.flags.dnc)) return false
    if (flag === 'Hardship' && !b.flags.hardship) return false
    if (flag === 'Paid' && !b.loans.some((l) => l.status === 'PAID')) return false
    return true
  })
  const pages = Math.max(1, Math.ceil(rows.length / PAGE))
  const view = rows.slice(page * PAGE, page * PAGE + PAGE)

  return (
    <>
      <PageHeader title="Smart CRM" sub="One record per borrower — every agent reads it, the Supervisor decides from it."
        chip={<Chip tone="warn">Synthetic data</Chip>} />
      <SpineLookup />
      <Card>
        <div className="flex flex-wrap items-center gap-3 border-b border-line px-5 py-3.5">
          <div className="relative w-72">
            <Search size={14} className="absolute top-1/2 left-3 -translate-y-1/2 text-faint" />
            <Input value={q} onChange={(e) => { setQ(e.target.value); setPage(0) }} placeholder="Search ID, name, loan or phone…" className="pl-8" />
          </div>
          <Segmented value={flag} onChange={(v) => { setFlag(v); setPage(0) }} options={['All', 'Hold', 'STOP', 'Hardship', 'Paid']} />
          <Select value={bandF} onChange={(v) => { setBandF(v); setPage(0) }} options={[{ value: 'All', label: 'All DPD bands' }, ...BANDS.map((b) => ({ value: b, label: `${b} DPD` }))]} />
          <Select value={next} onChange={(v) => { setNext(v); setPage(0) }} options={[{ value: 'All', label: 'Any next action' }, 'SEND', 'WAIT', 'HANDOFF', 'STOP', 'DONE']} />
          <div className="flex-1" />
          <span className="text-[12.5px] text-muted">{rows.length} accounts</span>
        </div>
        <table className="w-full">
          <thead><tr><Th>Borrower</Th><Th>DPD</Th><Th>Loans</Th><Th>Outstanding</Th><Th>Language</Th><Th>Last touch</Th><Th>Next action</Th><Th>Flags</Th></tr></thead>
          <tbody>
            {view.map((b) => (
              <tr key={b.id} onClick={() => nav(`/crm/${b.id}`)} className="cursor-pointer hover:bg-raised/60">
                <Td>
                  <div className="flex items-center gap-2.5">
                    <div className="grid h-7 w-7 place-items-center rounded-full bg-info/15 text-[10.5px] font-bold text-info">{b.name.split(' ').map((p) => p[0]).join('')}</div>
                    <div>
                      <div className="flex items-center gap-1.5 font-semibold">{b.name}{b.hero && <Star size={12} className="fill-warn text-warn" />}</div>
                      <div className="font-mono text-[11.5px] text-muted">{b.id} · {b.phone}</div>
                    </div>
                  </div>
                </Td>
                <Td><Chip tone={b.dpd >= 60 ? 'bad' : b.dpd >= 30 ? 'warn' : 'neutral'}>{b.dpd} DPD</Chip></Td>
                <Td className="text-muted">{b.loans.map((l) => <div key={l.id} className="font-mono text-[12px]">{l.id} {l.status === 'PAID' && <span className="text-ok">· paid</span>}</div>)}</Td>
                <Td className="tabular">{inr(b.loans.reduce((a, l) => a + (l.status === 'PAID' ? 0 : l.outstanding), 0))}</Td>
                <Td className="text-muted">{b.language}</Td>
                <Td><div className="text-[12.5px] text-muted">Day {b.lastTouchDay}</div><ChannelTag ch={b.lastTouchChannel} muted /></Td>
                <Td><StatusChip s={b.nextAction}>{b.nextAction}</StatusChip></Td>
                <Td>
                  <div className="flex flex-wrap gap-1">
                    {b.flags.hold && <Chip tone="warn">Hold</Chip>}
                    {(b.flags.stop || b.flags.dnc) && <Chip tone="bad">STOP</Chip>}
                    {b.flags.hardship && <Chip tone="violet">Hardship</Chip>}
                    {b.flags.callback && <Chip tone="info">Callback</Chip>}
                    {b.flags.manualPause && <Chip tone="warn">Paused</Chip>}
                  </div>
                </Td>
              </tr>
            ))}
          </tbody>
        </table>
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

function decideLabel(h, day) {
  if (h.flags.stop) return 'STOP'
  if (h.loans.every((l) => l.status === 'PAID')) return 'DONE'
  return decideHero(h, day).action
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
