import { useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import {
  ResponsiveContainer, ComposedChart, Bar as RBar, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend,
} from 'recharts'
import {
  Pause, Play, Square, Download, Check, X, CalendarClock, Hand, Minus, Plus, Snowflake, ShieldCheck,
  TriangleAlert, Info, CircleX, Search, ChevronDown, ChevronUp, Lock,
} from 'lucide-react'
import { useKairos, useDerived, fmtClock } from '../lib/store'
import { BANDS, CHANNEL_META, SERIES, SEGMENT_SHARE, SEGMENT_RPR_FACTOR, sumTo, HERO_ID } from '../lib/mock'
import { EVENT_META, AUDIT_TYPES, MODULE, TONE_TEXT } from '../lib/events'
import {
  Button, Card, CardHead, ChannelTag, Chip, Dot, Empty, Input, KpiStrip, PageHeader, ReasonDialog, Segmented, Select,
  StatusChip, Td, Th, Toggle, cx, inr, pct,
} from '../components/ui'

const ACTIVE = ['RESERVED', 'SCHEDULED', 'NEEDS_APPROVAL', 'APPROVED']

function downloadCsv(name, rows) {
  const csv = rows.map((r) => r.map((c) => `"${String(c ?? '').replace(/"/g, '""')}"`).join(',')).join('\n')
  const a = document.createElement('a')
  a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }))
  a.download = name
  a.click()
}

export default function ControlRoom() {
  const { state, dispatch } = useKairos()
  const d = useDerived()
  const [campId, setCampId] = useState('C-01')
  const [range, setRange] = useState('Campaign')
  const [dialog, setDialog] = useState(null)
  const camp = state.campaigns.find((c) => c.id === campId)

  const pendingCount = d.allQueue.filter((q) => ACTIVE.includes(q.status)).length
  const ask = (cfg) => setDialog(cfg)

  return (
    <>
      <PageHeader title="Control Room" sub="Live campaign execution and human control — pause, change or override anything, any time."
        chip={<Chip tone="ok" dot>Monitoring live</Chip>}
        right={<>
          <Segmented value={range} onChange={setRange} options={['Today', '7 Days', 'Campaign']} />
          <Select value={campId} onChange={setCampId} options={state.campaigns.filter((c) => c.status !== 'DRAFT').map((c) => ({ value: c.id, label: `${c.id} · ${c.name}` }))} className="w-64" />
          <Button icon={Download} onClick={() => downloadCsv(`kairos-events-day${state.day}.csv`, [['day', 'time', 'type', 'borrower', 'channel', 'actor', 'detail'], ...state.events.map((e) => [e.day, fmtClock(e.min), e.type, e.borrowerId, e.channel, e.actor, e.detail])])}>Export CSV</Button>
          <Button variant={state.globalPause ? 'primary' : 'danger'} icon={state.globalPause ? Play : Pause}
            onClick={() => ask({ title: state.globalPause ? 'Resume all outbound' : 'Pause all outbound', danger: !state.globalPause,
              effect: state.globalPause ? 'Paused sends return to the queue and are re-checked against every guardrail before they go.' : `Every channel stops sending immediately. ${pendingCount} pending sends will be held — nothing is lost.`,
              confirm: state.globalPause ? 'Resume' : 'Pause everything', run: (reason) => dispatch({ type: 'GLOBAL_PAUSE', reason }) })}>
            {state.globalPause ? 'Resume all outbound' : 'Pause all outbound'}
          </Button>
        </>} />

      {state.globalPause && (
        <div className="mb-5 flex items-center gap-3 rounded-xl border border-bad/30 bg-bad/10 px-5 py-3 text-[13.5px] text-bad">
          <Pause size={16} /> <b>All outbound is paused.</b> <span className="text-bad/80">Nothing will be sent on any channel until a person resumes.</span>
        </div>
      )}

      <div className="space-y-5">
        <KpiStrip items={[
          { label: 'Replies per ₹', value: d.rpr.toFixed(3), sub: <span><span className="text-ok">↑ {pct(d.lift, 0)}</span> vs fixed</span>, ring: Math.min(1, d.lift / 0.4), ringTone: 'orange' },
          { label: 'Spend vs budget', value: inr(d.spend), sub: `of ${inr(d.budgetTotal)}`, ring: d.spend / d.budgetTotal, ringTone: 'fg' },
          { label: 'Unique reach', value: d.reach, sub: `of ${camp.enrolled} enrolled` },
          { label: 'Verified payments', value: d.payments, sub: `fixed cadence: ${d.fPayments}` },
          { label: 'Opt-outs', value: d.optOuts, sub: 'All channels suppressed' },
          { label: 'Cap breaches', value: '0', valueClass: 'text-ok', sub: 'Accepted violations', ring: 1, ringTone: 'ok' },
        ]} />

        <div className="grid grid-cols-3 gap-5">
          <Card className="col-span-2">
            <CardHead title="Replies & spend" sub={`Kairos vs fixed cadence · Day 1–${state.day} · same seed, budget and caps`} right={<Chip tone="warn">SIMULATED</Chip>} />
            <TrendChart day={state.day} />
          </Card>
          <ChannelStatus camp={camp} d={d} />
        </div>

        <div className="grid grid-cols-3 gap-5">
          <Queue className="col-span-2" rows={d.allQueue} ask={ask} />
          <CampaignControls camp={camp} ask={ask} />
        </div>

        <div className="grid grid-cols-3 gap-5">
          <InboxCard className="col-span-2" />
          <Alerts d={d} />
        </div>

        <div className="grid grid-cols-2 gap-5">
          <Segments ask={ask} />
          <Activity />
        </div>

        <AuditLog />
      </div>

      <ReasonDialog open={!!dialog} title={dialog?.title} effect={dialog?.effect} danger={dialog?.danger} confirmLabel={dialog?.confirm}
        onClose={() => setDialog(null)} onConfirm={(reason) => dialog.run(reason)} />
    </>
  )
}

function TrendChart({ day }) {
  const data = Array.from({ length: day }, (_, i) => ({
    day: `D${i + 1}`,
    'Kairos spend': SERIES.adaptive.spend[i],
    'Kairos replies': SERIES.adaptive.replies[i],
    'Fixed replies': SERIES.fixed.replies[i],
  }))
  return (
    <div className="h-[260px] px-3 pb-3">
      <ResponsiveContainer>
        <ComposedChart data={data} margin={{ top: 8, right: 16, bottom: 0, left: -8 }}>
          <CartesianGrid stroke="#222" vertical={false} />
          <XAxis dataKey="day" stroke="#6b6b6b" tickLine={false} axisLine={false} fontSize={12} />
          <YAxis yAxisId="l" stroke="#6b6b6b" tickLine={false} axisLine={false} fontSize={12} />
          <YAxis yAxisId="r" orientation="right" stroke="#6b6b6b" tickLine={false} axisLine={false} fontSize={12} tickFormatter={(v) => `₹${v}`} />
          <Tooltip contentStyle={{ background: '#1c1c1c', border: '1px solid #333', borderRadius: 8, fontSize: 12 }} cursor={{ fill: 'rgba(255,255,255,0.03)' }} />
          <Legend wrapperStyle={{ fontSize: 12, color: '#a1a1a1' }} iconType="circle" iconSize={8} />
          <RBar isAnimationActive={false} yAxisId="r" dataKey="Kairos spend" fill="#2a2a2a" radius={[4, 4, 0, 0]} barSize={26} />
          <Line isAnimationActive={false} yAxisId="l" type="monotone" dataKey="Kairos replies" stroke="#f97316" strokeWidth={2.5} dot={{ r: 3 }} />
          <Line isAnimationActive={false} yAxisId="l" type="monotone" dataKey="Fixed replies" stroke="#3b82f6" strokeWidth={2} strokeDasharray="5 4" dot={false} />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  )
}

function ChannelStatus({ camp, d }) {
  const { state, dispatch } = useKairos()
  const today = state.events.filter((e) => e.day === state.day)
  const delivery = { sms: 0.97, whatsapp: 0.95, email: 0.81, ivr: 0.64 }
  return (
    <Card>
      <CardHead title="Channel status" sub="Queued and sent today, by channel" />
      <div className="grid grid-cols-2 gap-3 px-5 pb-5">
        {['sms', 'whatsapp', 'email', 'ivr'].map((ch) => {
          const queued = d.allQueue.filter((q) => q.channel === ch && ACTIVE.includes(q.status)).length
          const sent = today.filter((e) => e.type === 'SENT' && e.channel === ch).length
          const down = state.outage[ch]
          const off = !camp.channels[ch]
          return (
            <div key={ch} className={cx('rounded-lg border p-3.5', down ? 'border-bad/30 bg-bad/10' : 'border-line bg-raised/50')}>
              <div className="flex items-center justify-between">
                <span className="text-[13px] font-semibold">{CHANNEL_META[ch].label}</span>
                {down ? <span className="text-[11.5px] font-semibold text-bad">Provider down</span> : off ? <span className="text-[11.5px] text-muted">Disabled</span> : <Dot tone="ok" label="" />}
              </div>
              <div className="mt-1.5 flex items-baseline gap-1.5">
                <span className={cx('text-[24px] font-semibold tabular', down && 'text-bad')}>{queued}</span>
                <span className="text-[12.5px] text-muted">queued</span>
              </div>
              <div className={cx('text-[12px]', down ? 'text-bad/80' : 'text-muted')}>{sent} sent today · {ch === 'ivr' ? 'connect' : 'delivery'} {pct(delivery[ch], 0)}</div>
              <button onClick={() => dispatch({ type: 'CAMPAIGN', id: camp.id, action: 'channel', value: ch, reason: 'Operator decision' })}
                className="mt-2 text-[12px] text-muted underline-offset-2 hover:text-fg hover:underline">{off ? 'Enable channel' : 'Disable channel'}</button>
            </div>
          )
        })}
      </div>
    </Card>
  )
}

function Queue({ rows, ask, className }) {
  const { state, dispatch } = useKairos()
  const [filter, setFilter] = useState('Active')
  const [sel, setSel] = useState(new Set())
  const [all, setAll] = useState(false)
  const filtered = rows.filter((r) => filter === 'All' ? true : filter === 'Active' ? ACTIVE.includes(r.status) || r.status === 'WAITING' : filter === 'Needs approval' ? r.status === 'NEEDS_APPROVAL' : filter === 'Held' ? ['HELD', 'PAUSED', 'WAITING'].includes(r.status) : r.status === 'CANCELED')
  const view = all ? filtered : filtered.slice(0, 9)
  const toggle = (id) => setSel((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n })
  const run = (ids, action, value) => {
    const verb = { approve: 'Approve', hold: 'Hold', cancel: 'Cancel', reschedule: 'Reschedule', switch: 'Switch channel for' }[action]
    ask({ title: `${verb} ${ids.length} send${ids.length > 1 ? 's' : ''}`, danger: action === 'cancel', confirm: verb,
      effect: action === 'cancel' ? 'Canceled sends release their reserved budget. Kairos replans at the next allowed slot.' : action === 'switch' ? `Only borrowers with consent for ${CHANNEL_META[value].label} are switched; others are skipped.` : action === 'approve' ? 'Approved sends still pass every guardrail again at send time.' : 'Held sends stay in the queue until a person releases them.',
      run: (reason) => { dispatch({ type: 'QUEUE', ids, action, value, reason }); setSel(new Set()) } })
  }
  const counts = {
    Active: rows.filter((r) => ACTIVE.includes(r.status) || r.status === 'WAITING').length,
    'Needs approval': rows.filter((r) => r.status === 'NEEDS_APPROVAL').length,
    Held: rows.filter((r) => ['HELD', 'PAUSED', 'WAITING'].includes(r.status)).length,
    Canceled: rows.filter((r) => r.status === 'CANCELED').length,
  }
  return (
    <Card className={className}>
      <CardHead title="Upcoming queue" sub="Every reserved or scheduled send — approve, hold, cancel, reschedule or switch before it goes"
        right={<Segmented size="sm" value={filter} onChange={(v) => { setFilter(v); setSel(new Set()) }} options={['Active', 'Needs approval', 'Held', 'Canceled', 'All'].map((o) => ({ value: o, label: counts[o] != null ? `${o} ${counts[o]}` : o }))} />} />
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
          <thead><tr><Th className="w-8 pr-0" /><Th>Borrower</Th><Th>Channel · when</Th><Th>Why · template</Th><Th>Status</Th><Th className="text-right">Actions</Th></tr></thead>
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
                        <select title="Switch channel" value="" onChange={(e) => e.target.value && run([r.id], 'switch', e.target.value)}
                          className="h-7 w-[74px] rounded-md border border-line bg-bg px-1 text-[11.5px] text-muted">
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
      {filtered.length > 9 && (
        <button onClick={() => setAll(!all)} className="flex w-full items-center justify-center gap-1 border-t border-line py-2.5 text-[12.5px] text-muted hover:text-fg">
          {all ? <><ChevronUp size={14} /> Show less</> : <><ChevronDown size={14} /> Show all {filtered.length}</>}
        </button>
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

function CampaignControls({ camp, ask }) {
  const { state, dispatch } = useKairos()
  const D = (action, value, reason = 'Operator decision') => dispatch({ type: 'CAMPAIGN', id: camp.id, action, value, reason })
  const policy = state.role === 'policy'
  return (
    <Card>
      <CardHead title="Campaign controls" sub={camp.name} right={<StatusChip s={camp.status} />} />
      <div className="space-y-4 px-5 pb-5">
        <div className="grid grid-cols-3 gap-2">
          {camp.status === 'RUNNING'
            ? <Button icon={Pause} onClick={() => ask({ title: `Pause "${camp.name}"`, effect: 'Pending sends are held and the audience is kept. Resume at any time.', confirm: 'Pause', run: (r) => D('pause', null, r) })}>Pause</Button>
            : <Button icon={Play} disabled={camp.status === 'STOPPED'} onClick={() => D(camp.status === 'SCHEDULED' ? 'start' : 'resume')}>{camp.status === 'SCHEDULED' ? 'Start now' : 'Resume'}</Button>}
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
          <Segmented size="sm" value={camp.mode} onChange={(v) => D('mode', v)} options={[{ value: 'ADAPTIVE', label: 'Adaptive' }, { value: 'FIXED', label: 'Fixed cadence' }, { value: 'SHADOW', label: 'Shadow' }]} />
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

function InboxCard({ className }) {
  const { state, dispatch } = useKairos()
  const [tab, setTab] = useState('NEEDS_HUMAN')
  const items = state.inbox.filter((i) => i.status === tab)
  const n = (s) => state.inbox.filter((i) => i.status === s).length
  const sevTone = { high: 'bad', medium: 'warn', info: 'info' }
  return (
    <Card className={className}>
      <CardHead title="Inbox" sub="Handoffs and approvals — Kairos routes these, a person decides"
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
  if (state.day >= 4) list.push({ tone: 'warn', title: 'Reply-rate drift · WhatsApp', text: `WhatsApp fell from 18% to ${(([0.18, 0.18, 0.17, 0.15, 0.13, 0.12, 0.12, 0.12, 0.118, 0.118])[state.day - 1] * 100).toFixed(0)}% in 1–59 DPD. Kairos shifted spend to SMS.` })
  if (state.hero.flags.stop) list.push({ tone: 'warn', title: 'Opt-out received', text: `${HERO_ID} sent STOP — suppressed on every channel within one cycle.` })
  const needs = state.inbox.filter((i) => i.status === 'NEEDS_HUMAN').length
  if (needs) list.push({ tone: 'warn', title: `${needs} case${needs > 1 ? 's' : ''} need a person`, text: 'Hardship, disputes and waivers are routed, never decided by Kairos.' })
  const pace = SERIES.adaptive.spend[state.day - 1] / d.budgetDaily
  list.push({ tone: pace > 1 ? 'bad' : 'ok', title: `Budget pacing · ${pct(pace, 0)} of daily`, text: `Forecast ${inr(SERIES.adaptive.spend[state.day - 1])} of ${inr(d.budgetDaily)} today. Spend is reserved before each send.` })
  list.push({ tone: 'info', title: '1 uncertain send', text: 'One WhatsApp send has no provider status yet — it will not be retried until reconciled.' })
  const icon = { bad: CircleX, warn: TriangleAlert, ok: ShieldCheck, info: Info }
  return (
    <Card>
      <CardHead title="Alerts" sub="Things that need attention now" />
      <div className="space-y-2 px-5 pb-5">
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

function Segments({ ask }) {
  const { state, dispatch } = useKairos()
  const d = state.day
  const spend = sumTo(SERIES.adaptive.spend, d)
  const replies = sumTo(SERIES.adaptive.replies, d)
  const rpr = replies / spend
  const status = d < 3 ? 'Exploring' : d < 6 ? 'Converging' : 'Stable'
  return (
    <Card>
      <CardHead title="Segments" sub="Spend, responses and learning per DPD band — updated each day" />
      <table className="w-full">
        <thead><tr><Th>DPD band</Th><Th>Accounts</Th><Th>Spend</Th><Th>Replies</Th><Th>Replies/₹</Th><Th>Learning</Th><Th className="text-right">Active</Th></tr></thead>
        <tbody>
          {BANDS.map((b) => {
            const paused = !!state.segmentsPaused[b]
            const s = spend * SEGMENT_SHARE[b]
            const r = s * rpr * SEGMENT_RPR_FACTOR[b]
            return (
              <tr key={b} className={cx(paused && 'opacity-60')}>
                <Td className="font-semibold whitespace-nowrap">{b}</Td>
                <Td className="tabular text-muted">{state.borrowers.filter((x) => x.band === b).length + (b === '30–59' ? 1 : 0)}</Td>
                <Td className="tabular">{inr(s)}</Td>
                <Td className="tabular">{Math.round(r)}</Td>
                <Td className="tabular">{(r / s).toFixed(3)}</Td>
                <Td><Chip tone={status === 'Stable' ? 'ok' : status === 'Converging' ? 'info' : 'warn'}>{status} · {Math.max(5, 40 - d * 4)}% explore</Chip></Td>
                <Td className="text-right"><Toggle on={!paused} onChange={() => paused ? dispatch({ type: 'SEGMENT', band: b }) : ask({ title: `Pause ${b} DPD`, effect: `Pending sends for ${b} DPD borrowers are held. Other segments keep running.`, confirm: 'Pause segment', run: () => dispatch({ type: 'SEGMENT', band: b }) })} /></Td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </Card>
  )
}

const ACTIVITY_GROUPS = { Sends: ['SENT', 'DELIVERED', 'READ', 'SEND_RESERVED'], Replies: ['REPLIED'], Blocked: ['SEND_BLOCKED', 'TOUCH_CANCELED', 'STOP_RECEIVED'], Payments: ['D7_MATCHED', 'D7_PENDING', 'D7_MISMATCH', 'RECEIPT_ISSUED'] }

function Activity() {
  const { state } = useKairos()
  const nav = useNavigate()
  const [f, setF] = useState('All')
  const groups = ACTIVITY_GROUPS
  const list = useMemo(() => [...state.events].filter((e) => e.borrowerId && (f === 'All' || groups[f].includes(e.type))).sort((a, b) => (b.day - a.day) || (b.min - a.min) || (b.id - a.id)).slice(0, 12), [state.events, f])
  return (
    <Card>
      <CardHead title="Live activity" sub="Newest first · click a row to open the borrower" right={<Segmented size="sm" value={f} onChange={setF} options={['All', 'Sends', 'Replies', 'Blocked', 'Payments']} />} />
      <div className="px-2 pb-3">
        {list.map((e) => {
          const m = EVENT_META[e.type]
          const I = m.icon
          return (
            <button key={e.id} onClick={() => nav(`/crm/${e.borrowerId}`)} className="flex w-full items-center gap-3 rounded-lg px-3 py-2 text-left hover:bg-raised">
              <I size={14} className={cx('shrink-0', TONE_TEXT[m.tone])} />
              <span className="w-[120px] shrink-0 text-[13px] font-medium">{m.label}</span>
              <span className="w-[56px] shrink-0 font-mono text-[12px] text-muted">{e.borrowerId}</span>
              <span className="min-w-0 flex-1 truncate text-[12.5px] text-muted">{e.detail}</span>
              <span className="shrink-0 font-mono text-[11.5px] text-faint">D{e.day} {fmtClock(e.min)}</span>
            </button>
          )
        })}
        {!list.length && <Empty>No events.</Empty>}
      </div>
    </Card>
  )
}

function AuditLog() {
  const { state } = useKairos()
  const [sev, setSev] = useState('All')
  const [q, setQ] = useState('')
  const [all, setAll] = useState(false)
  const rows = useMemo(() => state.events.filter((e) => AUDIT_TYPES.has(e.type)).sort((a, b) => (b.day - a.day) || (b.min - a.min) || (b.id - a.id)), [state.events])
  const sevOf = (e) => EVENT_META[e.type]?.sev || 'info'
  const view = rows.filter((e) => (sev === 'All' || sevOf(e) === sev.toLowerCase()) && (!q || `${e.detail} ${e.borrowerId} ${e.actor}`.toLowerCase().includes(q.toLowerCase())))
  const blocked = rows.filter((e) => e.type === 'SEND_BLOCKED').length
  const humanN = rows.filter((e) => e.type === 'HUMAN_ACTION').length
  const high = rows.filter((e) => sevOf(e) === 'high').length
  const sevTone = { high: 'bad', medium: 'warn', info: 'neutral' }
  return (
    <Card>
      <CardHead title="Guardrail & audit log" sub="Every blocked or canceled send with its reason, and every human action with who and why"
        right={<Button size="sm" icon={Download} onClick={() => downloadCsv(`kairos-audit-day${state.day}.csv`, [['day', 'time', 'event', 'severity', 'actor', 'detail', 'module'], ...rows.map((e) => [e.day, fmtClock(e.min), e.type, sevOf(e), e.actor, e.detail, MODULE(e)])])}>Export CSV</Button>} />
      <div className="mx-5 mb-4 grid grid-cols-5 overflow-hidden rounded-lg border border-line">
        {[['Events', rows.length], ['Blocked by guardrails', blocked], ['Human actions', humanN], ['High severity', high], ['Cap breaches', 0]].map(([l, v], i) => (
          <div key={l} className={cx('px-4 py-3', i && 'border-l border-line')}><div className="text-[12px] text-muted">{l}</div><div className={cx('text-[20px] font-semibold tabular', l === 'Cap breaches' && 'text-ok')}>{v}</div></div>
        ))}
      </div>
      <div className="mx-5 mb-3 flex items-center gap-3">
        <Segmented size="sm" value={sev} onChange={setSev} options={['All', 'High', 'Medium', 'Info']} />
        <div className="flex-1" />
        <div className="relative w-64"><Search size={13} className="absolute top-1/2 left-3 -translate-y-1/2 text-faint" /><Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search audit log…" className="h-8 pl-8 text-[12.5px]" /></div>
      </div>
      <table className="w-full">
        <thead><tr><Th>Time</Th><Th>Event</Th><Th>Severity</Th><Th>Actor</Th><Th>Borrower</Th><Th>Detail</Th><Th>Module</Th></tr></thead>
        <tbody>
          {(all ? view : view.slice(0, 10)).map((e) => (
            <tr key={e.id}>
              <Td className="font-mono text-[12px] whitespace-nowrap text-muted">D{e.day} {fmtClock(e.min)}</Td>
              <Td className="font-medium whitespace-nowrap">{EVENT_META[e.type]?.label}</Td>
              <Td><Chip tone={sevTone[sevOf(e)]} dot>{sevOf(e)}</Chip></Td>
              <Td className="whitespace-nowrap text-muted">{e.actor}</Td>
              <Td>{e.borrowerId ? <Link to={`/crm/${e.borrowerId}`} className="font-mono text-[12px] hover:underline">{e.borrowerId}</Link> : <span className="text-faint">—</span>}</Td>
              <Td className="text-[12.5px]">{e.detail}</Td>
              <Td className="whitespace-nowrap text-muted">{MODULE(e)}</Td>
            </tr>
          ))}
        </tbody>
      </table>
      {view.length > 10 && (
        <button onClick={() => setAll(!all)} className="flex w-full items-center justify-center gap-1 border-t border-line py-2.5 text-[12.5px] text-muted hover:text-fg">
          {all ? <><ChevronUp size={14} /> Show less</> : <><ChevronDown size={14} /> Show all {view.length}</>}
        </button>
      )}
    </Card>
  )
}
