import { useState } from 'react'
import { ResponsiveContainer, LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ReferenceLine } from 'recharts'
import { Check, Lock, ArrowRight } from 'lucide-react'
import { useKairos, useDerived } from '../lib/store'
import { CAMPAIGN_DAYS, CHANNEL_META, ESTIMATES, LEARNING, SERIES, UNCERTAINTY, bestByDay, sumTo } from '../lib/mock'
import { Button, Card, CardHead, ChannelTag, Chip, KpiStrip, PageHeader, Segmented, Td, Th, cx, inr, pct } from '../components/ui'

const TT = { contentStyle: { background: '#1c1c1c', border: '1px solid #333', borderRadius: 8, fontSize: 12 }, cursor: { stroke: '#333' } }
const CH = ['whatsapp', 'sms', 'ivr']
const THRESHOLD = 0.1

export default function Learning() {
  const { state } = useKairos()
  const d = useDerived()
  const [lens, setLens] = useState('cohort')

  const cum = Array.from({ length: state.day }, (_, i) => ({
    day: `D${i + 1}`,
    Kairos: +(sumTo(SERIES.adaptive.replies, i + 1) / sumTo(SERIES.adaptive.spend, i + 1)).toFixed(3),
    'Fixed cadence': +(sumTo(SERIES.fixed.replies, i + 1) / sumTo(SERIES.fixed.spend, i + 1)).toFixed(3),
  }))

  return (
    <>
      <PageHeader title="Learning & Proof" sub="What Aladdin learned, where, and why — and the proof that it beats fixed cadence on the same seed, budget and caps."
        chip={<Chip tone="warn">Simulated · placeholder until the measured run</Chip>}
        right={<div className="flex items-center gap-2"><Chip>Run R-0007</Chip><Chip>Seed 42</Chip><Chip>Baseline v1</Chip><Chip>Policy v1.1.0</Chip></div>} />

      <div className="space-y-5">
        <KpiStrip items={[
          { label: 'Collected · Kairos', value: inr(d.collected), sub: `fixed cadence ${inr(d.fCollected)}` },
          { label: 'Collection lift', value: `+${pct(d.collected / d.fCollected - 1)}`, valueClass: 'text-ok', sub: 'same seed, budget and caps' },
          { label: 'Replies per ₹', value: d.rpr.toFixed(3), sub: `fixed ${d.frpr.toFixed(3)}` },
          { label: 'Replies/₹ lift', value: `+${pct(d.lift, 1)}`, valueClass: d.lift >= 0.2 ? 'text-ok' : 'text-warn', sub: d.lift >= 0.2 ? 'Above the 20% target' : 'Target ≥ 20% on hidden seed', ring: Math.min(1, d.lift / 0.2), ringTone: d.lift >= 0.2 ? 'ok' : 'warn' },
          { label: 'Cap breaches', value: '0', valueClass: 'text-ok', sub: 'Both policies · every day', ring: 1, ringTone: 'ok' },
        ]} />

        <div className="flex items-center gap-3">
          <span className="text-[13px] text-muted">Learning by</span>
          <Segmented value={lens} onChange={setLens} options={[{ value: 'cohort', label: 'Cohort (risk)' }, { value: 'channel', label: 'Channel' }, { value: 'region', label: 'Region' }]} />
          <span className="text-[12.5px] text-faint">Campaign-level learning isn't shown — a new campaign runs every day, so there is nothing stable to learn on.</span>
        </div>

        {lens === 'channel' ? <ByChannel /> : <BySlice groups={LEARNING[lens]} lens={lens} />}

        <div className="grid grid-cols-2 gap-5">
          <Card>
            <CardHead title="Adaptive vs fixed cadence" sub={`Day 1–${state.day} · same seed, audience, budget and caps`} />
            <table className="w-full">
              <thead><tr><Th>Metric</Th><Th>Fixed cadence</Th><Th>Kairos</Th><Th>Change</Th></tr></thead>
              <tbody>
                {[
                  ['Collected (₹)', inr(d.fCollected), inr(d.collected), d.collected / d.fCollected - 1, true],
                  ['Replies per ₹', d.frpr.toFixed(3), d.rpr.toFixed(3), d.lift, true],
                  ['Replies', d.fReplies, d.replies, d.replies / d.fReplies - 1, true],
                  ['Spend', inr(d.fSpend), inr(d.spend), d.spend / d.fSpend - 1, false],
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
            <CardHead title="Replies per ₹, cumulative" sub="The gap opens once Aladdin starts switching channels" />
            <div className="h-[250px] px-3 pb-3">
              <ResponsiveContainer>
                <LineChart data={cum} margin={{ top: 10, right: 20, bottom: 0, left: -10 }}>
                  <CartesianGrid stroke="#222" vertical={false} />
                  <XAxis dataKey="day" stroke="#6b6b6b" tickLine={false} axisLine={false} fontSize={12} />
                  <YAxis stroke="#6b6b6b" tickLine={false} axisLine={false} fontSize={12} domain={['dataMin - 0.02', 'dataMax + 0.02']} tickFormatter={(v) => v.toFixed(2)} />
                  <Tooltip {...TT} />
                  <Legend wrapperStyle={{ fontSize: 12 }} iconType="circle" iconSize={8} />
                  <Line isAnimationActive={false} type="monotone" dataKey="Kairos" stroke="#22c55e" strokeWidth={2.5} dot={{ r: 3 }} />
                  <Line isAnimationActive={false} type="monotone" dataKey="Fixed cadence" stroke="#6b6b6b" strokeWidth={2} strokeDasharray="5 4" dot={false} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </Card>
        </div>

        <CohortReview />
      </div>
    </>
  )
}

function SliceChart({ est, day, switches }) {
  const data = Array.from({ length: CAMPAIGN_DAYS }, (_, i) => {
    const row = { day: `D${i + 1}` }
    if (i < day) CH.forEach((c) => (row[CHANNEL_META[c].label] = +(est[c][i] / CHANNEL_META[c].cost).toFixed(3)))
    return row
  })
  return (
    <div className="h-[190px]">
      <ResponsiveContainer>
        <LineChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: -18 }}>
          <CartesianGrid stroke="#222" vertical={false} />
          <XAxis dataKey="day" stroke="#6b6b6b" tickLine={false} axisLine={false} fontSize={11} />
          <YAxis stroke="#6b6b6b" tickLine={false} axisLine={false} fontSize={11} tickFormatter={(v) => v.toFixed(1)} />
          <Tooltip {...TT} />
          {switches.filter((s) => s.day <= day).map((s) => <ReferenceLine key={s.day} x={`D${s.day}`} stroke="#ef4444" strokeDasharray="4 4" />)}
          <ReferenceLine y={THRESHOLD} stroke="#444" strokeDasharray="2 4" />
          {CH.map((c) => <Line isAnimationActive={false} key={c} type="monotone" dataKey={CHANNEL_META[c].label} stroke={CHANNEL_META[c].color} strokeWidth={c === 'ivr' ? 1.5 : 2.2} dot={{ r: 2 }} />)}
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}

function BySlice({ groups, lens }) {
  const { state } = useKairos()
  const day = state.day
  return (
    <>
      <div className="flex gap-4 text-[12px] text-muted">
        {CH.map((c) => <span key={c} className="inline-flex items-center gap-1.5"><span className="h-2 w-2 rounded-full" style={{ background: CHANNEL_META[c].color }} />{CHANNEL_META[c].label}</span>)}
        <span>· lines = expected replies per ₹ · <span className="text-bad">red dashed</span> = Aladdin switched channel · grey = WAIT threshold</span>
      </div>
      <div className="grid grid-cols-2 gap-5">
        {groups.map((g, gi) => {
          const { best, switches } = bestByDay(g.est)
          const seen = switches.filter((s) => s.day <= day)
          const now = best[day - 1]
          const u = UNCERTAINTY[day - 1] * (lens === 'region' ? 1.2 : 1 + gi * 0.15)
          return (
            <Card key={g.key}>
              <CardHead title={lens === 'cohort' ? (state.cohorts.find((c) => c.id === g.key)?.name ?? g.label) : `${g.label} region`}
                sub={`Best now: ${CHANNEL_META[now.c].label} · ${now.rpr.toFixed(2)} replies/₹ · confidence ±${pct(u)}`}
                right={seen.length ? <Chip tone="warn">{seen.length} switch{seen.length > 1 ? 'es' : ''}</Chip> : <Chip tone="ok">Stable</Chip>} />
              <div className="px-3"><SliceChart est={g.est} day={day} switches={switches} /></div>
              <div className="space-y-1.5 border-t border-line px-5 py-3 text-[12.5px]">
                <div className="text-[11.5px] font-semibold tracking-wide text-faint uppercase">What changed and why</div>
                {seen.length ? seen.map((s) => (
                  <div key={s.day}><b>Day {s.day}:</b> {CHANNEL_META[s.from].label} → <b>{CHANNEL_META[s.to].label}</b> <span className="text-muted">({s.rpr.toFixed(2)} replies/₹)</span></div>
                )) : <div className="text-muted">No switch yet — {CHANNEL_META[now.c].label} has led since Day 1.</div>}
                <div className="text-muted">{g.why}</div>
              </div>
            </Card>
          )
        })}
      </div>
    </>
  )
}

function ByChannel() {
  const { state } = useKairos()
  const day = state.day
  const data = Array.from({ length: CAMPAIGN_DAYS }, (_, i) => {
    const row = { day: `D${i + 1}` }
    if (i < day) ['whatsapp', 'sms', 'ivr', 'email'].forEach((c) => (row[CHANNEL_META[c].label] = +(ESTIMATES[c][i] * 100).toFixed(1)))
    return row
  })
  const winsBy = (c) => LEARNING.cohort.filter((g) => bestByDay(g.est).best[day - 1].c === c).length + LEARNING.region.filter((g) => bestByDay(g.est).best[day - 1].c === c).length
  return (
    <div className="grid grid-cols-3 gap-5">
      <Card className="col-span-2">
        <CardHead title="Reply rate by channel, day by day" sub="Portfolio-wide estimate (%), updated once each 24-hour reply window closes" />
        <div className="h-[280px] px-3 pb-3">
          <ResponsiveContainer>
            <LineChart data={data} margin={{ top: 10, right: 20, bottom: 0, left: -10 }}>
              <CartesianGrid stroke="#222" vertical={false} />
              <XAxis dataKey="day" stroke="#6b6b6b" tickLine={false} axisLine={false} fontSize={12} />
              <YAxis stroke="#6b6b6b" tickLine={false} axisLine={false} fontSize={12} unit="%" />
              <Tooltip {...TT} />
              <Legend wrapperStyle={{ fontSize: 12 }} iconType="circle" iconSize={8} />
              {['whatsapp', 'sms', 'ivr', 'email'].map((c) => <Line isAnimationActive={false} key={c} type="monotone" dataKey={CHANNEL_META[c].label} stroke={CHANNEL_META[c].color} strokeWidth={2} dot={{ r: 2.5 }} />)}
            </LineChart>
          </ResponsiveContainer>
        </div>
      </Card>
      <Card>
        <CardHead title="The maths today" sub={`Day ${day} · rank = reply rate ÷ cost per attempt`} />
        <table className="w-full">
          <thead><tr><Th>Channel</Th><Th>Reply rate</Th><Th>Cost</Th><Th>Replies/₹</Th><Th>Leads in</Th></tr></thead>
          <tbody>
            {['whatsapp', 'sms', 'ivr', 'email'].map((c) => ({ c, p: ESTIMATES[c][day - 1], cost: CHANNEL_META[c].cost })).map((x) => ({ ...x, rpr: x.p / x.cost }))
              .sort((a, b) => b.rpr - a.rpr).map((x, i) => (
                <tr key={x.c} className={cx(i === 0 && 'bg-ok/5')}>
                  <Td><ChannelTag ch={x.c} /></Td><Td className="tabular">{pct(x.p)}</Td><Td className="tabular text-muted">₹{x.cost.toFixed(2)}</Td>
                  <Td className={cx('font-semibold tabular', x.rpr < THRESHOLD && 'text-muted')}>{x.rpr.toFixed(2)}</Td>
                  <Td className="tabular text-muted">{winsBy(x.c)} slices</Td>
                </tr>
              ))}
          </tbody>
        </table>
        <div className="px-5 py-3 text-[12px] text-muted">Below {THRESHOLD} replies/₹ Aladdin waits instead of spending. "Leads in" = cohorts + regions where this channel is best today.</div>
      </Card>
    </div>
  )
}

function CohortReview() {
  const { state, dispatch } = useKairos()
  const [done, setDone] = useState({})
  const policy = state.role === 'policy'
  const items = [
    { id: 'p1', text: 'Start C2 and C3 cohorts on SMS instead of WhatsApp next cycle', why: 'SMS took over on Day 4 (C3) and Day 6 (C2) on replies/₹', kind: 'Channel prior' },
    { id: 'p2', text: 'Lead with IVR for the C4 · High Risk cohort and the East region', why: 'Voice reaches borrowers who ignore text', kind: 'Channel prior' },
    { id: 'p3', text: 'Lower the weekly cap from 3 to 2 for C4 · High Risk', why: 'Third contact added cost with no extra replies', kind: 'Cap change · needs policy owner' },
  ]
  return (
    <Card>
      <CardHead title="Cohort close · what to carry forward" sub="Aladdin proposes; a person approves. Cap, consent and hardship changes need the policy owner." />
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
                  onClick={() => { setDone({ ...done, [it.id]: true }); dispatch({ type: 'LOG', detail: `Approved carry-forward for next cohort: ${it.text}`, toast: 'Carry-forward approved for the next cycle' }) }}>
                  {locked ? 'Policy owner' : 'Approve'}</Button>}
            </div>
          )
        })}
      </div>
    </Card>
  )
}
