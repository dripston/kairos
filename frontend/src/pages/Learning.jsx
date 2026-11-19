import { useState } from 'react'
import {
  ResponsiveContainer, LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ReferenceLine, BarChart, Bar as RBar,
} from 'recharts'
import { Check, Lock, ArrowRight } from 'lucide-react'
import { useKairos, useDerived } from '../lib/store'
import { BANDS, CAMPAIGN_DAYS, CHANNEL_META, ESTIMATES, SERIES, SWITCH_DAY, UNCERTAINTY, sumTo } from '../lib/mock'
import { ArmScores, ChannelSwitches, DailySegmentTable, ProofCard } from '../components/LiveLearning'
import { Button, Card, CardHead, ChannelTag, Chip, KpiStrip, PageHeader, Td, Th, cx, inr, pct } from '../components/ui'

const TT = { contentStyle: { background: '#1c1c1c', border: '1px solid #333', borderRadius: 8, fontSize: 12 }, cursor: { stroke: '#333' } }
const THRESHOLD = 0.1

export default function Learning() {
  const { state } = useKairos()
  const d = useDerived()
  const day = state.day
  const [view, setView] = useState('rate')

  const est = Array.from({ length: CAMPAIGN_DAYS }, (_, i) => {
    const row = { day: `D${i + 1}` }
    if (i < day) ['whatsapp', 'sms', 'ivr', 'email'].forEach((c) => (row[CHANNEL_META[c].label] = view === 'rate' ? +(ESTIMATES[c][i] * 100).toFixed(1) : +(ESTIMATES[c][i] / CHANNEL_META[c].cost).toFixed(3)))
    return row
  })
  const today = ['whatsapp', 'sms', 'ivr', 'email'].map((c) => {
    const p = ESTIMATES[c][day - 1]
    return { c, p, cost: CHANNEL_META[c].cost, rpr: p / CHANNEL_META[c].cost }
  }).sort((a, b) => b.rpr - a.rpr)
  const day1 = ['whatsapp', 'sms'].map((c) => ({ c, rpr: ESTIMATES[c][0] / CHANNEL_META[c].cost }))

  const cum = Array.from({ length: day }, (_, i) => ({
    day: `D${i + 1}`,
    Kairos: +(sumTo(SERIES.adaptive.replies, i + 1) / sumTo(SERIES.adaptive.spend, i + 1)).toFixed(3),
    'Fixed cadence': +(sumTo(SERIES.fixed.replies, i + 1) / sumTo(SERIES.fixed.spend, i + 1)).toFixed(3),
  }))

  const mix = BANDS.map((b, i) => {
    const after = day >= SWITCH_DAY
    const base = [[0.38, 0.45, 0.1, 0.07], [0.3, 0.48, 0.12, 0.1], [0.28, 0.32, 0.2, 0.2], [0.25, 0.2, 0.25, 0.3]][i]
    const shifted = after ? [base[0] + 0.15, base[1] - 0.18, base[2] + 0.03, base[3]] : base
    return { band: `${b} DPD`, SMS: +(shifted[0] * 100).toFixed(0), WhatsApp: +(shifted[1] * 100).toFixed(0), IVR: +(shifted[2] * 100).toFixed(0), WAIT: +(shifted[3] * 100).toFixed(0) }
  })

  return (
    <>
      <PageHeader title="Learning & Proof" sub="Kairos learns inside the campaign — and beats fixed cadence on the same seed, budget and caps."
        chip={<Chip tone="warn">Simulated · placeholder until the measured run</Chip>}
        right={<div className="flex items-center gap-2 text-[12.5px] text-muted"><Chip>Run R-0007</Chip><Chip>Seed 42</Chip><Chip>Baseline v1</Chip><Chip>Policy v3</Chip></div>} />

      <div className="space-y-5">
        <ProofCard />
        <div className="grid grid-cols-3 gap-5">
          <div className="col-span-2"><ArmScores /></div>
          <ChannelSwitches />
        </div>
        <DailySegmentTable />
        <div className="flex items-center gap-3 pt-2 text-[12.5px] text-muted"><span className="h-px flex-1 bg-line" />Campaign walkthrough below is the built-in demo series (SIMULATED)<span className="h-px flex-1 bg-line" /></div>
        <KpiStrip items={[
          { label: 'Replies per ₹ · Kairos', value: d.rpr.toFixed(3), sub: `${d.replies} replies / ${inr(d.spend)}` },
          { label: 'Replies per ₹ · fixed cadence', value: d.frpr.toFixed(3), sub: `${d.fReplies} replies / ${inr(d.fSpend)}` },
          { label: 'Lift', value: `+${pct(d.lift, 1)}`, valueClass: d.lift >= 0.2 ? 'text-ok' : 'text-warn', sub: d.lift >= 0.2 ? 'Above the 20% target' : 'Target: ≥ 20% on hidden seed', ring: Math.min(1, d.lift / 0.2), ringTone: d.lift >= 0.2 ? 'ok' : 'warn' },
          { label: 'Spend saved', value: inr(d.fSpend - d.spend), sub: 'vs fixed cadence, same caps' },
          { label: 'Cap breaches', value: '0', valueClass: 'text-ok', sub: 'Both policies · every day', ring: 1, ringTone: 'ok' },
        ]} />

        <div className="grid grid-cols-3 gap-5">
          <Card className="col-span-2">
            <CardHead title="What Kairos believes, day by day" sub={view === 'rate' ? 'Estimated reply rate per channel (%) — updated once each reply window closes' : 'Expected replies per ₹ per channel — what the ranking actually uses'}
              right={<div className="inline-flex rounded-lg border border-line bg-bg p-0.5 text-[12px]">
                {[['rate', 'Reply rate'], ['rpr', 'Replies per ₹']].map(([v, l]) => <button key={v} onClick={() => setView(v)} className={cx('rounded-md px-2.5 py-1', view === v ? 'bg-raised text-fg' : 'text-muted')}>{l}</button>)}
              </div>} />
            <div className="h-[300px] px-3 pb-3">
              <ResponsiveContainer>
                <LineChart data={est} margin={{ top: 10, right: 20, bottom: 0, left: -10 }}>
                  <CartesianGrid stroke="#222" vertical={false} />
                  <XAxis dataKey="day" stroke="#6b6b6b" tickLine={false} axisLine={false} fontSize={12} />
                  <YAxis stroke="#6b6b6b" tickLine={false} axisLine={false} fontSize={12} />
                  <Tooltip {...TT} />
                  <Legend wrapperStyle={{ fontSize: 12 }} iconType="circle" iconSize={8} />
                  {day >= SWITCH_DAY && <ReferenceLine x={`D${SWITCH_DAY}`} stroke="#ef4444" strokeDasharray="4 4" label={{ value: 'switch → SMS', fill: '#ef4444', fontSize: 11, position: 'insideTopRight' }} />}
                  {view === 'rpr' && <ReferenceLine y={THRESHOLD} stroke="#6b6b6b" strokeDasharray="2 4" label={{ value: 'WAIT below', fill: '#6b6b6b', fontSize: 11, position: 'insideBottomRight' }} />}
                  {['whatsapp', 'sms', 'ivr', 'email'].map((c) => (
                    <Line isAnimationActive={false} key={c} type="monotone" dataKey={CHANNEL_META[c].label} stroke={CHANNEL_META[c].color} strokeWidth={c === 'whatsapp' || c === 'sms' ? 2.5 : 1.5} dot={{ r: 2.5 }} connectNulls={false} />
                  ))}
                </LineChart>
              </ResponsiveContainer>
            </div>
          </Card>

          <Card>
            <CardHead title="The maths" sub={`Day ${day} · rank = reply rate ÷ cost per attempt`} />
            <div className="space-y-2 px-5 pb-4">
              {today.map((t, i) => (
                <div key={t.c} className={cx('rounded-lg border px-3.5 py-2.5', i === 0 ? 'border-ok/30 bg-ok/5' : 'border-line')}>
                  <div className="flex items-center justify-between">
                    <ChannelTag ch={t.c} />
                    {i === 0 && <Chip tone="ok">Best now</Chip>}
                    {t.rpr < THRESHOLD && <Chip>Below threshold</Chip>}
                  </div>
                  <div className="mt-1 font-mono text-[12.5px] text-muted">{pct(t.p)} ÷ ₹{t.cost.toFixed(2)} = <span className={cx('font-semibold', i === 0 ? 'text-ok' : 'text-fg')}>{t.rpr.toFixed(2)}</span> replies/₹</div>
                </div>
              ))}
              <div className="rounded-lg border border-line bg-bg px-3.5 py-3 text-[12.5px] text-muted">
                Day 1: WhatsApp {day1[0].rpr.toFixed(2)} vs SMS {day1[1].rpr.toFixed(2)} → WhatsApp first.
                {day >= SWITCH_DAY ? <> As WhatsApp replies fell to {pct(ESTIMATES.whatsapp[day - 1], 0)}, SMS took over <b className="text-fg">inside the same campaign</b>.</> : <> Advance the clock to see the switch.</>}
                <div className="mt-1.5">Below {THRESHOLD} replies/₹, Kairos waits instead of spending.</div>
              </div>
            </div>
          </Card>
        </div>

        <div className="grid grid-cols-2 gap-5">
          <Card>
            <CardHead title="Adaptive vs fixed cadence" sub={`Day 1–${day} · same seed, audience, budget and caps`} />
            <table className="w-full">
              <thead><tr><Th>Metric</Th><Th>Fixed cadence</Th><Th>Kairos</Th><Th>Change</Th></tr></thead>
              <tbody>
                {[
                  ['Replies per ₹', d.frpr.toFixed(3), d.rpr.toFixed(3), d.lift, true],
                  ['Replies', d.fReplies, d.replies, d.replies / d.fReplies - 1, true],
                  ['Spend', inr(d.fSpend), inr(d.spend), d.spend / d.fSpend - 1, false],
                  ['Unique reach', d.fReach, d.reach, d.reach / d.fReach - 1, true],
                  ['Verified payments', d.fPayments, d.payments, d.payments / d.fPayments - 1, true],
                ].map(([m, f, k, ch, upGood]) => (
                  <tr key={m}><Td className="font-medium">{m}</Td><Td className="tabular text-muted">{f}</Td><Td className="font-semibold tabular">{k}</Td>
                    <Td className={cx('tabular', (ch >= 0) === upGood ? 'text-ok' : 'text-bad')}>{ch >= 0 ? '+' : ''}{pct(ch, 1)}</Td></tr>
                ))}
                <tr><Td className="font-medium">Cap breaches</Td><Td className="tabular text-muted">0</Td><Td className="font-semibold text-ok">0</Td><Td className="text-muted">—</Td></tr>
              </tbody>
            </table>
          </Card>
          <Card>
            <CardHead title="Replies per ₹, cumulative" sub="The gap opens after the channel switch" />
            <div className="h-[250px] px-3 pb-3">
              <ResponsiveContainer>
                <LineChart data={cum} margin={{ top: 10, right: 20, bottom: 0, left: -10 }}>
                  <CartesianGrid stroke="#222" vertical={false} />
                  <XAxis dataKey="day" stroke="#6b6b6b" tickLine={false} axisLine={false} fontSize={12} />
                  <YAxis stroke="#6b6b6b" tickLine={false} axisLine={false} fontSize={12} domain={['dataMin - 0.02', 'dataMax + 0.02']} tickFormatter={(v) => v.toFixed(2)} />
                  <Tooltip {...TT} />
                  <Legend wrapperStyle={{ fontSize: 12 }} iconType="circle" iconSize={8} />
                  <Line isAnimationActive={false} type="monotone" dataKey="Kairos" stroke="#f97316" strokeWidth={2.5} dot={{ r: 3 }} />
                  <Line isAnimationActive={false} type="monotone" dataKey="Fixed cadence" stroke="#3b82f6" strokeWidth={2} strokeDasharray="5 4" dot={false} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </Card>
        </div>

        <div className="grid grid-cols-2 gap-5">
          <Card>
            <CardHead title="Learning per segment" sub="Estimates tighten as reply windows close; exploration shrinks" />
            <table className="w-full">
              <thead><tr><Th>DPD band</Th><Th>Best channel now</Th><Th>Estimate</Th><Th>Uncertainty</Th><Th>Exploration</Th><Th>Status</Th></tr></thead>
              <tbody>
                {BANDS.map((b, i) => {
                  const best = day >= SWITCH_DAY && i < 2 ? 'sms' : i >= 2 ? 'ivr' : 'whatsapp'
                  const e = ESTIMATES[best][day - 1] * [1.1, 1, 0.85, 0.6][i]
                  const u = UNCERTAINTY[day - 1] * [1, 1.1, 1.4, 2][i]
                  const st = u < 0.03 ? 'Stable' : u < 0.06 ? 'Converging' : 'Exploring'
                  return (
                    <tr key={b}><Td className="font-semibold">{b}</Td><Td><ChannelTag ch={best} /></Td><Td className="tabular">{pct(e)}</Td>
                      <Td className="tabular text-muted">± {pct(u)}</Td><Td className="tabular text-muted">{Math.max(5, 40 - day * 4 + i * 3)}%</Td>
                      <Td><Chip tone={st === 'Stable' ? 'ok' : st === 'Converging' ? 'info' : 'warn'}>{st}</Chip></Td></tr>
                  )
                })}
              </tbody>
            </table>
          </Card>
          <Card>
            <CardHead title="Channel mix by segment" sub={day >= SWITCH_DAY ? 'Share of decisions today — after the switch' : 'Share of decisions today — before the switch'} />
            <div className="h-[230px] px-3 pb-3">
              <ResponsiveContainer>
                <BarChart data={mix} layout="vertical" margin={{ top: 4, right: 20, bottom: 0, left: 20 }}>
                  <CartesianGrid stroke="#222" horizontal={false} />
                  <XAxis type="number" stroke="#6b6b6b" tickLine={false} axisLine={false} fontSize={12} unit="%" domain={[0, 100]} />
                  <YAxis type="category" dataKey="band" stroke="#6b6b6b" tickLine={false} axisLine={false} fontSize={12} width={80} />
                  <Tooltip {...TT} cursor={{ fill: 'rgba(255,255,255,0.03)' }} />
                  <Legend wrapperStyle={{ fontSize: 12 }} iconType="circle" iconSize={8} />
                  <RBar isAnimationActive={false} dataKey="SMS" stackId="a" fill="#f97316" />
                  <RBar isAnimationActive={false} dataKey="WhatsApp" stackId="a" fill="#22c55e" />
                  <RBar isAnimationActive={false} dataKey="IVR" stackId="a" fill="#3b82f6" />
                  <RBar isAnimationActive={false} dataKey="WAIT" stackId="a" fill="#333" radius={[0, 4, 4, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </Card>
        </div>

        <CohortReview />
      </div>
    </>
  )
}

function CohortReview() {
  const { state, dispatch } = useKairos()
  const [done, setDone] = useState({})
  const policy = state.role === 'policy'
  const items = [
    { id: 'p1', text: 'Start 1–59 DPD on SMS instead of WhatsApp in the next cohort', why: 'SMS won replies/₹ from Day 4 in both bands', kind: 'Channel prior' },
    { id: 'p2', text: 'Move WhatsApp sends for Hindi speakers to 18:00–20:00', why: 'Evening reads 2.1× higher than mornings', kind: 'Time prior' },
    { id: 'p3', text: 'Lower the weekly cap from 3 to 2 for 90+ DPD', why: 'Third contact added cost with no extra replies', kind: 'Cap change · needs policy owner' },
  ]
  return (
    <Card>
      <CardHead title="Cohort close · what to carry forward" sub="Kairos proposes; a person approves. Cap, consent and hardship changes need the policy owner." />
      <div className="px-5 pb-4">
        {items.map((it) => {
          const locked = it.kind.includes('policy owner') && !policy
          return (
            <div key={it.id} className="flex items-center gap-4 border-t border-line py-3">
              <ArrowRight size={14} className="shrink-0 text-muted" />
              <div className="min-w-0 flex-1">
                <div className="text-[13.5px] font-medium">{it.text}</div>
                <div className="text-[12.5px] text-muted">{it.why} · <span className="text-faint">{it.kind}</span></div>
              </div>
              {done[it.id] ? <Chip tone="ok"><Check size={11} /> Approved</Chip>
                : <Button size="sm" disabled={locked} icon={locked ? Lock : Check} title={locked ? 'Switch to Policy owner in the sidebar' : ''}
                  onClick={() => { setDone({ ...done, [it.id]: true }); dispatch({ type: 'LOG', detail: `Approved carry-forward for next cohort: ${it.text}`, toast: 'Carry-forward approved for the next cohort' }) }}>
                  {locked ? 'Policy owner' : 'Approve'}</Button>}
            </div>
          )
        })}
      </div>
    </Card>
  )
}
