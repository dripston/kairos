import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { ResponsiveContainer, BarChart, Bar as RBar, XAxis, YAxis, CartesianGrid, Tooltip, Legend } from 'recharts'
import {
  Pause, Play, Square, Download, Check, X, CalendarClock, Hand, Minus, Plus, Snowflake, ShieldCheck,
  TriangleAlert, Info, CircleX, Search, ChevronRight, Lock, ArrowLeft,
} from 'lucide-react'
import { useKairos, useDerived, fmtClock } from '../lib/store'
import { CHANNEL_META, COLLECTED, HERO_ID, LEARNING, SERIES, bestByDay } from '../lib/mock'
import { EVENT_META, AUDIT_TYPES, MODULE, TONE_TEXT } from '../lib/events'
import {
  Button, Card, CardHead, ChannelIcons, ChannelTag, Chip, Dot, Empty, Input, KpiStrip, PageHeader, ReasonDialog, Segmented, Select,
  StatusChip, Td, Th, Toggle, cx, inr, pct,
} from '../components/ui'
import { ApprovalsQueue, BudgetCaps, Cm03Console, DemoRecipients, LiveDecisions } from '../components/LiveControl'
import LiveFeed from '../components/LiveFeed'

const ACTIVE = ['RESERVED', 'SCHEDULED', 'NEEDS_APPROVAL', 'APPROVED']
const RANGES = { Today: 1, '7 days': 7, 'To date': Infinity }

function downloadCsv(name, rows) {
  const csv = rows.map((r) => r.map((c) => `"${String(c ?? '').replace(/"/g, '""')}"`).join(',')).join('\n')
  const a = document.createElement('a')
  a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }))
  a.download = name
  a.click()
}

const lakh = (n) => (n >= 1e5 ? `₹${(n / 1e5).toFixed(2)}L` : inr(n))

export default function ControlRoom() {
  const { state, dispatch } = useKairos()
  const d = useDerived()
  const [params] = useSearchParams()
  const [range, setRange] = useState('To date')
  const [dialog, setDialog] = useState(null)
  const ask = (cfg) => setDialog(cfg)
  const { scope } = state

  // deep links from Cohorts / Campaigns: /control?scope=cohort&id=C3  or  ?campaign=C-01
  useEffect(() => {
    if (params.get('campaign')) dispatch({ type: 'SET_SCOPE', scopeType: 'campaign', id: params.get('campaign') })
    else if (params.get('scope')) dispatch({ type: 'SET_SCOPE', scopeType: params.get('scope'), id: params.get('id') || 'ALL' })
  }, [params, dispatch])

  const cohortName = (id) => state.cohorts.find((c) => c.id === id)?.name ?? id
  const campaignName = (id) => state.campaigns.find((c) => c.id === id)?.name ?? id
  const scopeLabel = scope.id === 'ALL' ? (scope.type === 'cohort' ? 'All cohorts' : 'All campaigns')
    : scope.type === 'cohort' ? cohortName(scope.id) : campaignName(scope.id)
  const inScope = (row) => scope.id === 'ALL' || (scope.type === 'cohort' ? row.cohortId === scope.id : row.campaignId === scope.id)

  // share of the portfolio this scope covers (by overdue) — scales the portfolio-level series
  const everyone = useMemo(() => [state.hero, ...state.borrowers], [state])
  const share = useMemo(() => {
    if (scope.id === 'ALL') return 1
    const od = (b) => b.loans.reduce((a, l) => a + l.emi, 0)
    const inS = everyone.filter((b) => (scope.type === 'cohort' ? b.cohortId === scope.id : b.campaignId === scope.id))
    return inS.reduce((a, b) => a + od(b), 0) / d.overdueStart
  }, [scope, everyone, d.overdueStart])

  const days = Math.min(RANGES[range], state.day)
  const from = state.day - days
  const win = (arr) => arr.slice(from, state.day).reduce((a, b) => a + b, 0)
  const collected = win(COLLECTED.adaptive) * share + (state.hero.loans[0].status === 'PAID' && inScope(state.hero) ? state.hero.loans[0].emi : 0)
  const fCollected = win(COLLECTED.fixed) * share
  const spend = win(SERIES.adaptive.spend) * share
  const replies = win(SERIES.adaptive.replies) * share
  const fRpr = win(SERIES.fixed.replies) / win(SERIES.fixed.spend)
  const payments = Math.round(win(SERIES.adaptive.payments) * share)
  const pendingCount = d.allQueue.filter((q) => ACTIVE.includes(q.status) && inScope(q)).length

  return (
    <>
      <PageHeader title="Control Room" sub="Live collections, decided by Aladdin — pause, change or override anything, any time."
        chip={<Chip tone="ok" dot>Monitoring live</Chip>}
        right={<>
          <Segmented value={range} onChange={setRange} options={Object.keys(RANGES)} />
          <Button icon={Download} onClick={() => downloadCsv(`kairos-events-day${state.day}.csv`, [['day', 'time', 'type', 'borrower', 'channel', 'actor', 'detail'], ...state.events.map((e) => [e.day, fmtClock(e.min), e.type, e.borrowerId, e.channel, e.actor, e.detail])])}>Export CSV</Button>
          <Button variant={state.globalPause ? 'primary' : 'danger'} icon={state.globalPause ? Play : Pause}
            onClick={() => ask({ title: state.globalPause ? 'Resume all outbound' : 'Pause all outbound', danger: !state.globalPause,
              effect: state.globalPause ? 'Paused sends return to the queue and are re-checked against every guardrail before they go.' : `Every channel stops sending immediately. ${d.allQueue.filter((q) => ACTIVE.includes(q.status)).length} pending sends will be held — nothing is lost.`,
              confirm: state.globalPause ? 'Resume' : 'Pause everything', run: (reason) => dispatch({ type: 'GLOBAL_PAUSE', reason }) })}>
            {state.globalPause ? 'Resume all outbound' : 'Pause all outbound'}
          </Button>
        </>} />

      {/* scope: cohort or campaign — the only two lenses */}
      <Card className="mb-5 flex flex-wrap items-center gap-3 px-5 py-3">
        <span className="text-[13px] text-muted">View by</span>
        <Segmented value={scope.type} onChange={(v) => dispatch({ type: 'SET_SCOPE', scopeType: v, id: 'ALL' })}
          options={[{ value: 'cohort', label: 'Cohort' }, { value: 'campaign', label: 'Campaign' }]} />
        <Select value={scope.id} onChange={(v) => dispatch({ type: 'SET_SCOPE', id: v })} className="w-72"
          options={scope.type === 'cohort'
            ? [{ value: 'ALL', label: 'All cohorts' }, ...[...state.cohorts].sort((a, b) => a.priority - b.priority).map((c) => ({ value: c.id, label: c.name }))]
            : [{ value: 'ALL', label: 'All campaigns' }, ...state.campaigns.filter((c) => c.status !== 'DRAFT').map((c) => ({ value: c.id, label: c.name }))]} />
        <span className="text-[12.5px] text-muted">{scope.id === 'ALL' ? 'Whole portfolio' : `${pct(share, 0)} of portfolio overdue`} · {range === 'To date' ? `Day 1–${state.day}` : range === 'Today' ? `Day ${state.day}` : `Day ${from + 1}–${state.day}`}</span>
        <div className="flex-1" />
        <Link to="/cohorts" className="text-[12.5px] text-muted hover:text-fg">Manage cohorts →</Link>
      </Card>

      {state.globalPause && (
        <div className="mb-5 flex items-center gap-3 rounded-xl border border-bad/30 bg-bad/10 px-5 py-3 text-[13.5px] text-bad">
          <Pause size={16} /> <b>All outbound is paused.</b> <span className="text-bad/80">Nothing will be sent on any channel until a person resumes.</span>
        </div>
      )}

      <div className="space-y-5">
        <KpiStrip items={[
          { label: 'Collected', value: lakh(collected), sub: <span><span className="text-ok">↑ {pct(collected / fCollected - 1, 0)}</span> vs fixed</span>, ring: Math.min(1, collected / (d.overdueStart * share)), ringTone: 'ok' },
          { label: 'Collection %', value: pct((collected) / (d.overdueStart * share)), sub: `of ${lakh(d.overdueStart * share)} overdue` },
          { label: 'Avg collected / day', value: lakh(collected / Math.max(1, days)), sub: `${days} day${days > 1 ? 's' : ''}` },
          { label: 'Verified payments', value: payments, sub: 'D7-settled' },
          { label: 'Replies per ₹', value: (replies / spend).toFixed(3), sub: `fixed: ${fRpr.toFixed(3)} · spend ${inr(spend)}` },
          { label: 'Cap breaches', value: '0', valueClass: 'text-ok', sub: 'Accepted violations', ring: 1, ringTone: 'ok' },
        ]} />

        <Alerts d={d} />

        {/* live from the Kairos API (falls back to labelled mocks when the backend is offline) */}
        <div className="grid grid-cols-3 gap-5">
          <LiveDecisions className="col-span-2" />
          <LiveFeed height={560} />
        </div>
        <div className="grid grid-cols-3 gap-5">
          <ApprovalsQueue className="col-span-2" />
          <BudgetCaps campId={scope.type === 'campaign' && scope.id !== 'ALL' ? scope.id : 'C-01'} />
        </div>
        <div className="grid grid-cols-3 gap-5">
          <Cm03Console className="col-span-2" />
          <DemoRecipients />
        </div>

        <div className="grid grid-cols-3 gap-5">
          <CollectionsTrend share={share} scopeLabel={scopeLabel} />
          <RegionScorecard d={d} />
        </div>

        <CohortPerformance d={d} />

        <div className="grid grid-cols-3 items-start gap-5">
          <div className="col-span-2 space-y-5">
            <Queue rows={d.allQueue} ask={ask} cohortName={cohortName} campaignName={campaignName} pendingCount={pendingCount} />
            <ChannelStatus d={d} />
            <InboxCard />
          </div>
          <CampaignControls ask={ask} />
        </div>

        <LearningByCohort ask={ask} />

        <AuditLog />
      </div>

      <ReasonDialog open={!!dialog} title={dialog?.title} effect={dialog?.effect} danger={dialog?.danger} confirmLabel={dialog?.confirm}
        onClose={() => setDialog(null)} onConfirm={(reason) => dialog.run(reason)} />
    </>
  )
}

// ---------------- collections ----------------
function CollectionsTrend({ share, scopeLabel }) {
  const { state } = useKairos()
  const n = Math.min(6, state.day)
  const data = Array.from({ length: n }, (_, i) => {
    const day = state.day - n + i + 1
    return { day: `D${day}`, Kairos: Math.round(COLLECTED.adaptive[day - 1] * share), 'Fixed cadence': Math.round(COLLECTED.fixed[day - 1] * share) }
  })
  return (
    <Card className="col-span-2">
      <CardHead title="Collections — last 6 days" sub={`${scopeLabel} · verified ₹ per day, Kairos vs fixed cadence on the same budget and caps`} right={<Chip tone="warn">SIMULATED</Chip>} />
      <div className="h-[250px] px-3 pb-3">
        <ResponsiveContainer>
          <BarChart data={data} margin={{ top: 8, right: 16, bottom: 0, left: 4 }} barGap={4}>
            <CartesianGrid stroke="#222" vertical={false} />
            <XAxis dataKey="day" stroke="#6b6b6b" tickLine={false} axisLine={false} fontSize={12} />
            <YAxis stroke="#6b6b6b" tickLine={false} axisLine={false} fontSize={12} tickFormatter={(v) => `₹${Math.round(v / 1000)}k`} />
            <Tooltip formatter={(v) => inr(v)} contentStyle={{ background: '#1c1c1c', border: '1px solid #333', borderRadius: 8, fontSize: 12 }} cursor={{ fill: 'rgba(255,255,255,0.03)' }} />
            <Legend wrapperStyle={{ fontSize: 12 }} iconType="circle" iconSize={8} />
            <RBar isAnimationActive={false} dataKey="Kairos" fill="#22c55e" radius={[4, 4, 0, 0]} barSize={22} />
            <RBar isAnimationActive={false} dataKey="Fixed cadence" fill="#3b3b3b" radius={[4, 4, 0, 0]} barSize={22} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </Card>
  )
}

function RegionScorecard({ d }) {
  const best = Math.max(...d.byRegion.map((r) => r.pct))
  return (
    <Card>
      <CardHead title="Collections by region" sub="Whole portfolio · collected vs overdue at allocation" />
      <div className="space-y-3 px-5 pb-5">
        {d.byRegion.map((r) => (
          <div key={r.key}>
            <div className="mb-1 flex items-baseline justify-between text-[13px]">
              <span className="font-semibold">{r.key}</span>
              <span className="tabular"><b>{pct(r.pct)}</b> <span className="text-muted">· {lakh(r.collected)} of {lakh(r.overdue)}</span></span>
            </div>
            <div className="h-1.5 overflow-hidden rounded-full bg-raised"><div className={cx('h-full rounded-full', r.pct === best ? 'bg-ok' : r.pct < best * 0.7 ? 'bg-warn' : 'bg-fg/70')} style={{ width: `${(r.pct / best) * 100}%` }} /></div>
            <div className="mt-0.5 text-[11.5px] text-muted">{r.loans} loans · response {pct(r.responseRate, 0)}</div>
          </div>
        ))}
        {(() => {
          const worst = [...d.byRegion].sort((a, b) => a.pct - b.pct)[0]
          return worst && <div className="rounded-lg border border-warn/25 bg-warn/5 px-3 py-2 text-[12px] text-muted"><b className="text-warn">{worst.key}</b> is lagging — Aladdin is shifting it to IVR in the local language.</div>
        })()}
      </div>
    </Card>
  )
}

function CohortPerformance({ d }) {
  const { state, dispatch } = useKairos()
  const rows = [...d.byCohort].sort((a, b) => (state.cohorts.find((c) => c.id === a.key)?.priority ?? 9) - (state.cohorts.find((c) => c.id === b.key)?.priority ?? 9))
  const best = Object.fromEntries(LEARNING.cohort.map((g) => [g.key, bestByDay(g.est).best[state.day - 1]]))
  return (
    <Card>
      <CardHead title="Cohort-wise collection performance" sub="Click a cohort to scope the whole Control Room to it" />
      <table className="w-full">
        <thead><tr><Th>Cohort</Th><Th>Loans</Th><Th>Overdue</Th><Th>Collected</Th><Th>Collection %</Th><Th>Response rate</Th><Th>Best channel now</Th><Th>Campaigns</Th></tr></thead>
        <tbody>
          {rows.map((r) => {
            const cohort = state.cohorts.find((c) => c.id === r.key)
            const camps = state.campaigns.filter((c) => c.cohortIds.includes(r.key))
            return (
              <tr key={r.key} onClick={() => dispatch({ type: 'SET_SCOPE', scopeType: 'cohort', id: r.key })} className={cx('cursor-pointer hover:bg-raised/60', state.scope.type === 'cohort' && state.scope.id === r.key && 'bg-raised/60')}>
                <Td className="font-semibold">{cohort?.name ?? r.key}</Td>
                <Td className="tabular text-muted">{r.loans}</Td>
                <Td className="tabular">{lakh(r.overdue)}</Td>
                <Td className="tabular">{lakh(r.collected)}</Td>
                <Td><div className="flex items-center gap-2"><span className="w-12 tabular">{pct(r.pct)}</span><div className="h-1.5 w-24 overflow-hidden rounded-full bg-raised"><div className={cx('h-full rounded-full', r.pct > 0.2 ? 'bg-ok' : r.pct > 0.12 ? 'bg-fg/70' : 'bg-warn')} style={{ width: `${Math.min(100, r.pct * 300)}%` }} /></div></div></Td>
                <Td className="tabular">{pct(r.responseRate, 0)}</Td>
                <Td>{best[r.key] ? <ChannelTag ch={best[r.key].c} /> : <span className="text-faint">—</span>}</Td>
                <Td><div className="flex flex-wrap gap-1">{camps.length ? camps.map((c) => <Chip key={c.id} tone={c.mode === 'FIXED' ? 'neutral' : 'info'}>{c.name}</Chip>) : <span className="text-[12px] text-warn">none</span>}</div></Td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </Card>
  )
}

// ---------------- queue: campaign/cohort → borrowers ----------------
function Queue({ rows, ask, className, cohortName, campaignName }) {
  const { state, dispatch } = useKairos()
  const { scope } = state
  const [open, setOpen] = useState(null) // group id when drilled into from "All"
  const [filter, setFilter] = useState('Active')
  const [chan, setChan] = useState('all')
  const [q, setQ] = useState('')
  const [sel, setSel] = useState(new Set())
  const [limit, setLimit] = useState(10)
  useEffect(() => { setOpen(null); setSel(new Set()) }, [scope.type, scope.id])

  const keyOf = (r) => (scope.type === 'cohort' ? r.cohortId : r.campaignId)
  const nameOf = (k) => (scope.type === 'cohort' ? cohortName(k) : campaignName(k))
  const groupId = scope.id !== 'ALL' ? scope.id : open
  const run = (ids, action, value) => {
    const verb = { approve: 'Approve', hold: 'Hold', cancel: 'Cancel', reschedule: 'Reschedule', switch: 'Switch channel for' }[action]
    ask({ title: `${verb} ${ids.length} send${ids.length > 1 ? 's' : ''}`, danger: action === 'cancel', confirm: verb,
      effect: action === 'cancel' ? 'Canceled sends release their reserved budget. Aladdin replans at the next allowed slot.' : action === 'switch' ? `Only borrowers with consent for ${CHANNEL_META[value].label} are switched; others are skipped.` : action === 'approve' ? 'Approved sends still pass every guardrail again at send time.' : 'Held sends stay in the queue until a person releases them.',
      run: (reason) => { dispatch({ type: 'QUEUE', ids, action, value, reason }); setSel(new Set()) } })
  }

  // ---- level 1: one row per campaign / cohort ----
  if (!groupId) {
    const groups = {}
    rows.forEach((r) => {
      const k = keyOf(r)
      if (!k) return
      const g = (groups[k] ??= { k, active: 0, approval: 0, held: 0, canceled: 0, channels: {}, next: null, ids: [] })
      if (ACTIVE.includes(r.status) || r.status === 'WAITING') g.active++
      if (r.status === 'NEEDS_APPROVAL') g.approval++
      if (['HELD', 'PAUSED', 'WAITING'].includes(r.status)) g.held++
      if (r.status === 'CANCELED') g.canceled++
      if (ACTIVE.includes(r.status)) { g.channels[r.channel] = true; g.ids.push(r.id); if (!g.next || r.min < g.next.min) g.next = r }
    })
    const list = Object.values(groups).sort((a, b) => b.active - a.active)
    return (
      <Card className={className}>
        <CardHead title="Upcoming queue" sub={`By ${scope.type} — open one to see its borrowers. ${list.reduce((a, g) => a + g.active, 0)} sends queued.`} />
        <table className="w-full">
          <thead><tr><Th>{scope.type === 'cohort' ? 'Cohort' : 'Campaign'}</Th><Th>Queued</Th><Th>Needs approval</Th><Th>Held</Th><Th>Channels</Th><Th>Next send</Th><Th className="text-right">Actions</Th></tr></thead>
          <tbody>
            {list.map((g) => (
              <tr key={g.k} className="hover:bg-raised/50">
                <Td><button onClick={() => setOpen(g.k)} className="text-left font-semibold hover:underline">{nameOf(g.k)}</button></Td>
                <Td className="tabular">{g.active}</Td>
                <Td>{g.approval ? <Chip tone="violet">{g.approval}</Chip> : <span className="text-faint">0</span>}</Td>
                <Td className="tabular text-muted">{g.held}</Td>
                <Td><ChannelIcons channels={g.channels} /></Td>
                <Td className="whitespace-nowrap text-[12.5px] text-muted">{g.next?.when ?? '—'}</Td>
                <Td className="text-right whitespace-nowrap">
                  {g.ids.length > 0 && <Button size="sm" variant="ghost" icon={Hand} onClick={() => run(g.ids, 'hold')}>Hold all</Button>}
                  <Button size="sm" icon={ChevronRight} onClick={() => setOpen(g.k)}>Open</Button>
                </Td>
              </tr>
            ))}
          </tbody>
        </table>
        {!list.length && <Empty>Nothing queued.</Empty>}
      </Card>
    )
  }

  // ---- level 2: borrowers inside one campaign / cohort ----
  const inGroup = rows.filter((r) => keyOf(r) === groupId)
  const filtered = inGroup.filter((r) =>
    (filter === 'All' ? true : filter === 'Active' ? ACTIVE.includes(r.status) || r.status === 'WAITING' : filter === 'Needs approval' ? r.status === 'NEEDS_APPROVAL' : filter === 'Held' ? ['HELD', 'PAUSED', 'WAITING'].includes(r.status) : r.status === 'CANCELED')
    && (chan === 'all' || r.channel === chan)
    && (!q || `${r.name} ${r.borrowerId} ${r.loan}`.toLowerCase().includes(q.toLowerCase())))
  const view = filtered.slice(0, limit)
  const toggle = (id) => setSel((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n })
  return (
    <Card className={className}>
      <div className="flex items-start justify-between gap-4 px-5 pt-4 pb-3">
        <div>
          {scope.id === 'ALL' && <button onClick={() => setOpen(null)} className="mb-1 inline-flex items-center gap-1 text-[12.5px] text-muted hover:text-fg"><ArrowLeft size={13} /> All {scope.type === 'cohort' ? 'cohorts' : 'campaigns'}</button>}
          <div className="text-[15px] font-semibold">Upcoming queue · {nameOf(groupId)}</div>
          <div className="text-[13px] text-muted">{inGroup.length} borrowers queued — approve, hold, cancel, reschedule or switch before it goes</div>
        </div>
      </div>
      <div className="mx-5 mb-3 flex flex-wrap items-center gap-2">
        <Segmented size="sm" value={filter} onChange={(v) => { setFilter(v); setSel(new Set()) }} options={['Active', 'Needs approval', 'Held', 'Canceled', 'All']} />
        <Select value={chan} onChange={setChan} className="h-8 w-36 text-[12.5px]" options={[{ value: 'all', label: 'All channels' }, ...['sms', 'whatsapp', 'email', 'ivr'].map((c) => ({ value: c, label: CHANNEL_META[c].label }))]} />
        <div className="relative w-56"><Search size={13} className="absolute top-1/2 left-3 -translate-y-1/2 text-faint" /><Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Name, ID or loan…" className="h-8 pl-8 text-[12.5px]" /></div>
      </div>
      {sel.size > 0 && (
        <div className="mx-5 mb-3 flex items-center gap-2 rounded-lg border border-line bg-raised px-3 py-2 text-[13px]">
          <span className="font-semibold">{sel.size} selected</span>
          <div className="flex-1" />
          <Button size="sm" icon={Check} onClick={() => run([...sel], 'approve')}>Approve</Button>
          <Button size="sm" icon={Hand} onClick={() => run([...sel], 'hold')}>Hold</Button>
          <Button size="sm" icon={CalendarClock} onClick={() => run([...sel], 'reschedule')}>+1 day</Button>
          <Button size="sm" variant="danger" icon={X} onClick={() => run([...sel], 'cancel')}>Cancel</Button>
        </div>
      )}
      <div className="overflow-x-auto">
        <table className="w-full">
          <thead><tr><Th className="w-8 pr-0" /><Th>Borrower</Th><Th>Channel · when</Th><Th>Aladdin's reason · template</Th><Th>Status</Th><Th className="text-right">Actions</Th></tr></thead>
          <tbody>
            {view.map((r) => {
              const live = ACTIVE.includes(r.status)
              return (
                <tr key={r.id} className={cx(r.hero && 'bg-warn/[0.03]')}>
                  <Td className="pr-0"><input type="checkbox" checked={sel.has(r.id)} onChange={() => toggle(r.id)} disabled={r.status === 'CANCELED'} className="accent-white" /></Td>
                  <Td className="whitespace-nowrap"><Link to={`/crm/${r.borrowerId}`} className="font-semibold hover:underline">{r.name}</Link><div className="font-mono text-[11.5px] text-muted">{r.borrowerId} · {r.loan}</div></Td>
                  <Td className="whitespace-nowrap"><ChannelTag ch={r.channel} /><div className="text-[12px] tabular text-muted">{r.when}</div></Td>
                  <Td className="text-[12.5px] text-muted"><div className="line-clamp-2" title={r.reason}>{r.reason}</div><div className="font-mono text-[11px] text-faint">{r.template}</div></Td>
                  <Td><StatusChip s={r.status} /></Td>
                  <Td className="text-right whitespace-nowrap">
                    <div className="inline-flex items-center gap-0.5">
                      {r.status === 'NEEDS_APPROVAL' && <IconBtn title="Approve" onClick={() => run([r.id], 'approve')}><Check size={14} className="text-ok" /></IconBtn>}
                      {live && <IconBtn title="Hold" onClick={() => run([r.id], 'hold')}><Hand size={14} /></IconBtn>}
                      {(r.status === 'HELD' || r.status === 'PAUSED') && !state.globalPause && <IconBtn title="Release" onClick={() => run([r.id], 'approve')}><Play size={14} /></IconBtn>}
                      {live && !r.hero && <IconBtn title="Reschedule +1 day" onClick={() => run([r.id], 'reschedule')}><CalendarClock size={14} /></IconBtn>}
                      {live && !r.hero && (
                        <select title="Switch channel" value="" onChange={(e) => e.target.value && run([r.id], 'switch', e.target.value)} className="h-7 w-[74px] rounded-md border border-line bg-bg px-1 text-[11.5px] text-muted">
                          <option value="">Switch…</option>
                          {['sms', 'whatsapp', 'email', 'ivr'].filter((c) => c !== r.channel).map((c) => <option key={c} value={c}>{CHANNEL_META[c].label}</option>)}
                        </select>
                      )}
                      {r.status !== 'CANCELED' && <IconBtn title="Cancel" onClick={() => run([r.id], 'cancel')}><X size={14} className="text-bad" /></IconBtn>}
                    </div>
                  </Td>
                </tr>
              )
            })}
          </tbody>
        </table>
        {!view.length && <Empty>Nothing here.</Empty>}
      </div>
      {filtered.length > limit && (
        <div className="flex items-center justify-center gap-2 border-t border-line py-2.5 text-[12.5px] text-muted">
          Showing {limit} of {filtered.length} · load
          {[10, 20, 30].map((n) => <button key={n} onClick={() => setLimit(limit + n)} className="rounded-md border border-line px-2 py-0.5 hover:text-fg">+{n}</button>)}
        </div>
      )}
    </Card>
  )
}

function IconBtn({ children, ...p }) {
  return <button className="grid h-7 w-7 place-items-center rounded-md text-muted hover:bg-raised hover:text-fg" {...p}>{children}</button>
}

function Stepper({ value, onDec, onInc, fmt = (v) => v, incLocked }) {
  return (
    <div className="inline-flex items-center rounded-lg border border-line">
      <button onClick={onDec} className="grid h-7 w-7 place-items-center text-muted hover:text-fg"><Minus size={13} /></button>
      <span className="min-w-[56px] text-center text-[13px] tabular">{fmt(value)}</span>
      <button onClick={onInc} title={incLocked ? 'Needs the Policy owner role' : ''} className={cx('grid h-7 w-7 place-items-center', incLocked ? 'text-faint' : 'text-muted hover:text-fg')}>{incLocked ? <Lock size={12} /> : <Plus size={13} />}</button>
    </div>
  )
}

function CampaignControls({ ask }) {
  const { state, dispatch } = useKairos()
  const [pick, setPick] = useState('C-01')
  const id = state.scope.type === 'campaign' && state.scope.id !== 'ALL' ? state.scope.id : pick
  const camp = state.campaigns.find((c) => c.id === id)
  const D = (action, value, reason = 'Operator decision') => dispatch({ type: 'CAMPAIGN', id: camp.id, action, value, reason })
  const policy = state.role === 'policy'
  return (
    <Card>
      <CardHead title="Campaign controls" sub={state.scope.type === 'campaign' && state.scope.id !== 'ALL' ? camp.name : undefined} right={<StatusChip s={camp.status} />} />
      <div className="space-y-4 px-5 pb-5">
        {!(state.scope.type === 'campaign' && state.scope.id !== 'ALL') && (
          <Select value={pick} onChange={setPick} className="w-full" options={state.campaigns.filter((c) => c.status !== 'DRAFT').map((c) => ({ value: c.id, label: c.name }))} />
        )}
        <div className="grid grid-cols-3 gap-2">
          {camp.status === 'RUNNING'
            ? <Button icon={Pause} onClick={() => ask({ title: `Pause "${camp.name}"`, effect: 'Pending sends are held and the audience is kept. Resume at any time.', confirm: 'Pause', run: (r) => D('pause', null, r) })}>Pause</Button>
            : <Button icon={Play} disabled={camp.status === 'STOPPED'} onClick={() => D(camp.status === 'SCHEDULED' ? 'start' : 'resume')}>{camp.status === 'SCHEDULED' ? 'Start' : 'Resume'}</Button>}
          <Button variant="danger" icon={Square} disabled={camp.status === 'STOPPED'} className="col-span-2"
            onClick={() => ask({ title: `Stop "${camp.name}"`, danger: true, confirm: 'Stop campaign', effect: 'Every pending reservation is canceled and its budget released. A stopped campaign cannot be resumed.', run: (r) => D('stop', null, r) })}>Stop campaign</Button>
        </div>
        <div className="space-y-2.5 rounded-lg border border-line p-3">
          <div className="flex items-center justify-between text-[13px]"><span className="text-muted">Daily budget</span>
            <Stepper value={camp.dailyBudget} fmt={(v) => inr(v)} incLocked={!policy} onDec={() => D('budget', Math.max(10, camp.dailyBudget - 10))} onInc={() => D('budget', camp.dailyBudget + 10)} /></div>
          <div className="flex items-center justify-between text-[13px]"><span className="text-muted">Contacts / borrower / week</span>
            <Stepper value={camp.caps.week} incLocked={!policy} onDec={() => D('cap', Math.max(1, camp.caps.week - 1))} onInc={() => D('cap', camp.caps.week + 1)} /></div>
          <div className="text-[11.5px] text-faint">Tightening is instant. Loosening needs the Policy owner role{policy ? ' (you have it)' : ''}.</div>
        </div>
        <div>
          <div className="mb-1.5 text-[12.5px] text-muted">Policy mode</div>
          <Segmented size="sm" value={camp.mode} onChange={(v) => D('mode', v)} options={[{ value: 'ADAPTIVE', label: 'Adaptive' }, { value: 'FIXED', label: 'Fixed' }, { value: 'SHADOW', label: 'Shadow' }]} />
        </div>
        <div className="space-y-2">
          <div className="text-[12.5px] text-muted">Channels</div>
          {['sms', 'whatsapp', 'email', 'ivr'].map((ch) => (
            <div key={ch} className="flex items-center justify-between"><ChannelTag ch={ch} /><Toggle on={camp.channels[ch]} onChange={() => D('channel', ch)} /></div>
          ))}
        </div>
        <div className="flex items-center justify-between border-t border-line pt-3">
          <span className="inline-flex items-center gap-1.5 text-[13px]"><Snowflake size={14} className="text-info" /> Freeze learning</span>
          <Toggle on={camp.frozen} onChange={() => D('freeze')} />
        </div>
        <div className="space-y-2 border-t border-line pt-3">
          <div className="text-[12.5px] text-muted">Approval mode — require a person for</div>
          {[['handoff', 'Handoffs'], ['ivr', 'IVR calls'], ['high', 'High-intensity nudges (60+ DPD)']].map(([k, l]) => (
            <div key={k} className="flex items-center justify-between text-[13px]"><span>{l}</span><Toggle on={state.approval[k]} onChange={() => dispatch({ type: 'APPROVAL', key: k })} /></div>
          ))}
        </div>
        <div className="flex items-start gap-2 rounded-lg border border-ok/20 bg-ok/5 p-3 text-[12px] text-muted">
          <ShieldCheck size={14} className="mt-0.5 shrink-0 text-ok" />
          Consent, STOP and hard caps are enforced at approval and again at send time. No control here can override them.
        </div>
      </div>
    </Card>
  )
}

function ChannelStatus({ d }) {
  const { state } = useKairos()
  const today = state.events.filter((e) => e.day === state.day)
  const delivery = { sms: 0.97, whatsapp: 0.95, email: 0.81, ivr: 0.64 }
  return (
    <Card>
      <CardHead title="Channel status" sub="Queued · sent today · delivery" />
      <div className="grid grid-cols-4 gap-3 px-5 pb-5">
        {['sms', 'whatsapp', 'email', 'ivr'].map((ch) => {
          const queued = d.allQueue.filter((q) => q.channel === ch && ACTIVE.includes(q.status)).length
          const sent = today.filter((e) => e.type === 'SENT' && e.channel === ch).length
          const down = state.outage[ch]
          return (
            <div key={ch} className={cx('rounded-lg border p-3', down ? 'border-bad/30 bg-bad/10' : 'border-line bg-raised/50')}>
              <div className="flex items-center justify-between"><span className="text-[12.5px] font-semibold">{CHANNEL_META[ch].label}</span>{down ? <span className="text-[11px] font-semibold text-bad">Down</span> : <Dot tone="ok" label="" />}</div>
              <div className={cx('mt-1 text-[20px] font-semibold tabular', down && 'text-bad')}>{queued}</div>
              <div className="text-[11.5px] text-muted">{sent} sent · {pct(delivery[ch], 0)}</div>
            </div>
          )
        })}
      </div>
    </Card>
  )
}

function InboxCard({ className }) {
  const { state, dispatch } = useKairos()
  const [tab, setTab] = useState('NEEDS_HUMAN')
  const items = state.inbox.filter((i) => i.status === tab)
  const n = (s) => state.inbox.filter((i) => i.status === s).length
  const sevTone = { high: 'bad', medium: 'warn', info: 'info' }
  return (
    <Card className={className}>
      <CardHead title="Inbox" sub="Handoffs and approvals — Aladdin routes these, a person decides"
        right={<Segmented size="sm" value={tab} onChange={setTab} options={[
          { value: 'NEEDS_HUMAN', label: `Needs a human ${n('NEEDS_HUMAN')}` },
          { value: 'APPROVAL', label: `Approvals ${n('APPROVAL')}` },
          { value: 'CLAIMED', label: `Claimed ${n('CLAIMED')}` },
          { value: 'RESOLVED', label: `Resolved ${n('RESOLVED')}` },
        ]} />} />
      <div className="px-5 pb-4">
        {items.length ? items.map((i) => (
          <div key={i.id} className="flex items-start gap-4 border-t border-line py-3">
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <Chip tone={sevTone[i.severity]}>{i.kind}</Chip>
                <Link to={`/crm/${i.borrowerId}`} className="font-mono text-[12.5px] hover:underline">{i.borrowerId}</Link>
                <ChannelTag ch={i.channel} muted />
                <span className="text-[12px] text-faint">· {i.queue} · {i.age}</span>
                {i.assignee && <span className="text-[12px] text-muted">· {i.assignee}</span>}
              </div>
              <div className="mt-1 text-[13px] text-muted">{i.summary}</div>
            </div>
            <div className="flex shrink-0 gap-1.5">
              {i.status === 'APPROVAL' && <>
                <Button size="sm" icon={Check} onClick={() => dispatch({ type: 'INBOX', id: i.id, action: 'approve' })}>Approve</Button>
                <Button size="sm" variant="danger" icon={X} onClick={() => dispatch({ type: 'INBOX', id: i.id, action: 'resolve' })}>Reject</Button>
              </>}
              {i.status === 'NEEDS_HUMAN' && <Button size="sm" onClick={() => dispatch({ type: 'INBOX', id: i.id, action: 'claim' })}>Claim</Button>}
              {(i.status === 'NEEDS_HUMAN' || i.status === 'CLAIMED') && <Button size="sm" variant="primary" onClick={() => dispatch({ type: 'INBOX', id: i.id, action: 'resolve' })}>Resolve</Button>}
            </div>
          </div>
        )) : <Empty>Nothing waiting.</Empty>}
      </div>
    </Card>
  )
}

function Alerts({ d }) {
  const { state } = useKairos()
  const list = []
  Object.entries(state.outage).forEach(([ch, down]) => down && list.push({ tone: 'bad', title: `${CHANNEL_META[ch].label} provider down`, text: 'Uncertain sends held for reconciliation — status is checked before any retry.' }))
  if (state.globalPause) list.push({ tone: 'bad', title: 'All outbound paused', text: 'No channel is sending. Resume from the header.' })
  const worst = [...d.byCohort].sort((a, b) => a.pct - b.pct)[0]
  if (worst) list.push({ tone: 'warn', title: `${state.cohorts.find((c) => c.id === worst.key)?.name ?? worst.key} is collecting slowest`, text: `${pct(worst.pct)} collected so far. Review its journey or attach an IVR campaign.` })
  if (state.day >= 4) list.push({ tone: 'warn', title: 'Reply-rate drift · WhatsApp', text: 'WhatsApp replies fell in C2/C3 and the West. Aladdin moved those sends to SMS.' })
  if (state.hero.flags.stop) list.push({ tone: 'warn', title: 'Opt-out received', text: `${HERO_ID} sent STOP — suppressed on every channel within one cycle.` })
  const needs = state.inbox.filter((i) => i.status === 'NEEDS_HUMAN').length
  if (needs) list.push({ tone: 'warn', title: `${needs} case${needs > 1 ? 's' : ''} need a person`, text: 'Hardship, disputes and waivers are routed, never decided by Kairos.' })
  const pace = SERIES.adaptive.spend[state.day - 1] / d.budgetDaily
  list.push({ tone: pace > 1 ? 'bad' : 'ok', title: `Budget pacing · ${pct(pace, 0)} of daily`, text: `Forecast ${inr(SERIES.adaptive.spend[state.day - 1])} of ${inr(d.budgetDaily)} today.` })
  const icon = { bad: CircleX, warn: TriangleAlert, ok: ShieldCheck, info: Info }
  return (
    <Card>
      <CardHead title="Alerts" sub="Things to act on now" />
      <div className="grid grid-cols-3 gap-3 px-5 pb-5">
        {list.map((a, i) => {
          const I = icon[a.tone]
          return (
            <div key={i} className={cx('flex gap-2.5 rounded-lg border p-3', a.tone === 'bad' ? 'border-bad/30 bg-bad/10' : 'border-line bg-raised/40')}>
              <I size={15} className={cx('mt-0.5 shrink-0', TONE_TEXT[a.tone])} />
              <div><div className="text-[13px] font-semibold">{a.title}</div><div className="text-[12.5px] text-muted">{a.text}</div></div>
            </div>
          )
        })}
      </div>
    </Card>
  )
}

// ---------------- learning per cohort, as a timeline ----------------
function Spark({ est, day }) {
  const W = 150, H = 36
  const chans = ['whatsapp', 'sms', 'ivr']
  const all = chans.flatMap((c) => est[c].slice(0, day).map((v) => v / CHANNEL_META[c].cost))
  const max = Math.max(...all), min = Math.min(...all)
  const y = (v) => H - 3 - ((v - min) / (max - min || 1)) * (H - 6)
  const x = (i) => (day > 1 ? (i / (day - 1)) * (W - 4) + 2 : W / 2)
  return (
    <svg width={W} height={H}>
      {chans.map((c) => (
        <polyline key={c} fill="none" stroke={CHANNEL_META[c].color} strokeWidth={c === 'ivr' ? 1.2 : 2}
          points={est[c].slice(0, day).map((v, i) => `${x(i)},${y(v / CHANNEL_META[c].cost)}`).join(' ')} />
      ))}
    </svg>
  )
}

function LearningByCohort({ ask }) {
  const { state, dispatch } = useKairos()
  const d = state.day
  return (
    <Card>
      <CardHead title="What Aladdin learned, by cohort" sub="Replies per ₹ per channel, day by day — and the moment the best channel changed"
        right={<div className="flex gap-3 text-[11.5px] text-muted">{['whatsapp', 'sms', 'ivr'].map((c) => <span key={c} className="inline-flex items-center gap-1"><span className="h-2 w-2 rounded-full" style={{ background: CHANNEL_META[c].color }} />{CHANNEL_META[c].label}</span>)}</div>} />
      <table className="w-full">
        <thead><tr><Th>Cohort</Th><Th>Day 1 → {d}</Th><Th>Best now</Th><Th>What changed</Th><Th className="text-right">Sending</Th></tr></thead>
        <tbody>
          {LEARNING.cohort.map((g) => {
            const { best, switches } = bestByDay(g.est)
            const seen = switches.filter((s) => s.day <= d)
            const paused = !!state.segmentsPaused[g.key]
            return (
              <tr key={g.key} className={cx(paused && 'opacity-60')}>
                <Td className="font-semibold whitespace-nowrap">{state.cohorts.find((c) => c.id === g.key)?.name ?? g.label}</Td>
                <Td><Spark est={g.est} day={d} /></Td>
                <Td><ChannelTag ch={best[d - 1].c} /><div className="text-[11.5px] tabular text-muted">{best[d - 1].rpr.toFixed(2)} replies/₹</div></Td>
                <Td className="max-w-[360px] text-[12.5px]">
                  {seen.length ? seen.map((s) => <div key={s.day}><b>Day {s.day}:</b> {CHANNEL_META[s.from].label} → <b>{CHANNEL_META[s.to].label}</b></div>) : <div className="text-muted">No switch — {CHANNEL_META[best[d - 1].c].label} still best</div>}
                  <div className="text-muted">{g.why}</div>
                </Td>
                <Td className="text-right"><Toggle on={!paused} onChange={() => paused ? dispatch({ type: 'SEGMENT', key: g.key, label: g.label }) : ask({ title: `Pause ${g.label}`, effect: 'Pending sends for this cohort are held. Other cohorts keep running.', confirm: 'Pause cohort', run: () => dispatch({ type: 'SEGMENT', key: g.key, label: g.label }) })} /></Td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </Card>
  )
}

// ---------------- audit log (+ activity feed as a tab) ----------------
const ACTIVITY_GROUPS = { Sends: ['SENT', 'DELIVERED', 'READ', 'SEND_RESERVED'], Replies: ['REPLIED'], Payments: ['D7_MATCHED', 'D7_PENDING', 'D7_MISMATCH', 'RECEIPT_ISSUED'] }

function AuditLog() {
  const { state } = useKairos()
  const nav = useNavigate()
  const [tab, setTab] = useState('audit')
  const [sev, setSev] = useState('All')
  const [q, setQ] = useState('')
  const [limit, setLimit] = useState(10)
  const sevOf = (e) => EVENT_META[e.type]?.sev || 'info'
  const audit = useMemo(() => state.events.filter((e) => AUDIT_TYPES.has(e.type)).sort((a, b) => (b.day - a.day) || (b.min - a.min) || (b.id - a.id)), [state.events])
  const activity = useMemo(() => state.events.filter((e) => e.borrowerId && Object.values(ACTIVITY_GROUPS).flat().includes(e.type)).sort((a, b) => (b.day - a.day) || (b.min - a.min) || (b.id - a.id)), [state.events])
  const rows = tab === 'audit'
    ? audit.filter((e) => (sev === 'All' || sevOf(e) === sev.toLowerCase()) && (!q || `${e.detail} ${e.borrowerId} ${e.actor}`.toLowerCase().includes(q.toLowerCase())))
    : activity.filter((e) => (sev === 'All' || ACTIVITY_GROUPS[sev]?.includes(e.type)) && (!q || `${e.detail} ${e.borrowerId}`.toLowerCase().includes(q.toLowerCase())))
  const sevTone = { high: 'bad', medium: 'warn', info: 'neutral' }
  return (
    <Card>
      <CardHead title="Guardrail & audit log" sub="Every blocked or canceled send with its reason, and every human action with who and why"
        right={<>
          <Segmented size="sm" value={tab} onChange={(v) => { setTab(v); setSev('All'); setLimit(10) }} options={[{ value: 'audit', label: 'Audit' }, { value: 'activity', label: 'Activity feed' }]} />
          <Button size="sm" icon={Download} onClick={() => downloadCsv(`kairos-audit-day${state.day}.csv`, [['day', 'time', 'event', 'severity', 'actor', 'detail', 'module'], ...audit.map((e) => [e.day, fmtClock(e.min), e.type, sevOf(e), e.actor, e.detail, MODULE(e)])])}>Export CSV</Button>
        </>} />
      {tab === 'audit' && (
        <div className="mx-5 mb-4 grid grid-cols-5 overflow-hidden rounded-lg border border-line">
          {[['Events', audit.length], ['Blocked by guardrails', audit.filter((e) => e.type === 'SEND_BLOCKED').length], ['Human actions', audit.filter((e) => e.type === 'HUMAN_ACTION').length], ['High severity', audit.filter((e) => sevOf(e) === 'high').length], ['Cap breaches', 0]].map(([l, v], i) => (
            <div key={l} className={cx('px-4 py-3', i && 'border-l border-line')}><div className="text-[12px] text-muted">{l}</div><div className={cx('text-[20px] font-semibold tabular', l === 'Cap breaches' && 'text-ok')}>{v}</div></div>
          ))}
        </div>
      )}
      <div className="mx-5 mb-3 flex items-center gap-3">
        <Segmented size="sm" value={sev} onChange={setSev} options={tab === 'audit' ? ['All', 'High', 'Medium', 'Info'] : ['All', 'Sends', 'Replies', 'Payments']} />
        <div className="flex-1" />
        <div className="relative w-64"><Search size={13} className="absolute top-1/2 left-3 -translate-y-1/2 text-faint" /><Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search…" className="h-8 pl-8 text-[12.5px]" /></div>
      </div>
      <table className="w-full">
        <thead><tr><Th>Time</Th><Th>Event</Th>{tab === 'audit' && <Th>Severity</Th>}<Th>Actor</Th><Th>Borrower</Th><Th>Detail</Th>{tab === 'audit' && <Th>Module</Th>}</tr></thead>
        <tbody>
          {rows.slice(0, limit).map((e) => (
            <tr key={e.id} className={cx(tab === 'activity' && 'cursor-pointer hover:bg-raised/50')} onClick={() => tab === 'activity' && nav(`/crm/${e.borrowerId}`)}>
              <Td className="font-mono text-[12px] whitespace-nowrap text-muted">D{e.day} {fmtClock(e.min)}</Td>
              <Td className="font-medium whitespace-nowrap">{EVENT_META[e.type]?.label}</Td>
              {tab === 'audit' && <Td><Chip tone={sevTone[sevOf(e)]} dot>{sevOf(e)}</Chip></Td>}
              <Td className="whitespace-nowrap text-muted">{e.actor === 'supervisor' ? 'Aladdin' : e.actor}</Td>
              <Td>{e.borrowerId ? <Link to={`/crm/${e.borrowerId}`} className="font-mono text-[12px] hover:underline">{e.borrowerId}</Link> : <span className="text-faint">—</span>}</Td>
              <Td className="text-[12.5px]">{e.detail}</Td>
              {tab === 'audit' && <Td className="whitespace-nowrap text-muted">{MODULE(e)}</Td>}
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length > limit && (
        <div className="flex items-center justify-center gap-2 border-t border-line py-2.5 text-[12.5px] text-muted">
          Showing {limit} of {rows.length} · load
          {[10, 20, 30].map((n) => <button key={n} onClick={() => setLimit(limit + n)} className="rounded-md border border-line px-2 py-0.5 hover:text-fg">+{n}</button>)}
        </div>
      )}
    </Card>
  )
}

