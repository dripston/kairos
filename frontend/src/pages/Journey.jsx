// Live journey: one borrower through the whole system, step by step, with the CRM envelopes filling in.
// Source order: POST /demo/journey/run (integration/all) → frontend/public/journey/journey.json → built-in SIMULATED copy.
import { useEffect, useMemo, useState } from 'react'
import { Play, Pause, SkipForward, RotateCcw, Check, Loader } from 'lucide-react'
import { getJourneyFile, maskPhone, runJourney } from '../api/kairos'
import { OfflineError } from '../api/client'
import { MOCK_JOURNEY, normalizeJourney } from '../api/journey'
import { normProv } from '../lib/format'
import { ProvChip } from '../components/live'
import LiveFeed, { FeedRow } from '../components/LiveFeed'
import { Button, Card, CardHead, Chip, Empty, Input, PageHeader, Segmented, StatusChip, cx } from '../components/ui'

const SPEEDS = { Slow: 4200, Normal: 2600, Fast: 1200 }

function mergeCard(base, patch) {
  if (!patch) return base
  return { ...base, ...patch }
}

function asList(v) {
  if (!v) return []
  if (Array.isArray(v)) return v.map((x) => (typeof x === 'string' ? x : x.label || x.kind || x.reason || JSON.stringify(x)))
  return [String(v)]
}

function BorrowerCard({ card, changed }) {
  const phone = card.phone && /\d{5,}/.test(String(card.phone).replace(/\D/g, '')) ? maskPhone(card.phone) : card.phone
  const next = String(card.next_action || '—')
  const action = next.split(/[ ·]/)[0]
  const F = ({ k, label, children }) => (
    <div className={cx('rounded-lg border px-3 py-2 transition-colors', changed.has(k) ? 'border-info/50 bg-info/10' : 'border-line')}>
      <div className="text-[11.5px] text-muted">{label}</div>
      <div className="text-[14px] font-semibold">{children}</div>
    </div>
  )
  return (
    <Card className="p-5">
      <div className="mb-3 flex items-center gap-3">
        <div className="grid h-10 w-10 place-items-center rounded-full bg-info/15 text-[12px] font-bold text-info">{String(card.name || card.full_name || 'B').split(' ').map((p) => p[0]).join('').slice(0, 2)}</div>
        <div className="min-w-0">
          <div className="truncate text-[15px] font-semibold">{card.name || card.full_name || card.borrower_id}</div>
          <div className="font-mono text-[12px] text-muted">{card.borrower_id} · {phone || '—'} · {card.language || '—'}</div>
        </div>
      </div>
      <div className="grid grid-cols-3 gap-2">
        <F k="cohort" label="Cohort">{card.cohort ?? '—'}</F>
        <F k="risk_tier" label="Risk tier">{card.risk_tier ?? card.risk ?? '—'}</F>
        <F k="layer" label="Layer">{card.layer ?? '—'}</F>
      </div>
      <div className={cx('mt-2 rounded-lg border px-3 py-2 transition-colors', changed.has('next_action') ? 'border-info/50 bg-info/10' : 'border-line')}>
        <div className="text-[11.5px] text-muted">Next action</div>
        <div className="flex items-center gap-2 text-[14px] font-semibold">{['SEND', 'WAIT', 'CANCEL', 'HANDOFF'].includes(action) && <StatusChip s={action}>{action}</StatusChip>}<span>{next.replace(/^(SEND|WAIT|CANCEL|HANDOFF)\s*·?\s*/, '')}</span></div>
      </div>
      <div className={cx('mt-2 rounded-lg border px-3 py-2', changed.has('holds') ? 'border-info/50 bg-info/10' : 'border-line')}>
        <div className="mb-1 text-[11.5px] text-muted">Holds & flags</div>
        <div className="flex flex-wrap gap-1">{asList(card.holds).length ? asList(card.holds).map((h) => <Chip key={h} tone={/stop|fraud|dispute/i.test(h) ? 'bad' : 'warn'}>{h}</Chip>) : <span className="text-[12.5px] text-muted">None</span>}</div>
      </div>
      {!!(card.emis || card.obligations || []).length && (
        <div className={cx('mt-2 rounded-lg border px-3 py-2', changed.has('emis') ? 'border-info/50 bg-info/10' : 'border-line')}>
          <div className="mb-1 text-[11.5px] text-muted">EMIs</div>
          {(card.emis || card.obligations).map((e) => (
            <div key={e.id || e.obligation_id} className="flex items-center justify-between py-0.5 text-[12.5px]">
              <span>{e.loan || e.loan_id} · <span className="font-mono text-muted">{e.id || e.obligation_id}</span></span>
              <span className="text-muted">{e.amount || ''} {e.due ? `· ${e.due}` : ''}</span>
              <Chip tone={/paid/i.test(e.status) ? 'ok' : /overdue/i.test(e.status) ? 'bad' : 'warn'}>{e.status}</Chip>
            </div>
          ))}
        </div>
      )}
    </Card>
  )
}

export default function Journey() {
  const [seed, setSeed] = useState('42')
  const [j, setJ] = useState(null)
  const [src, setSrc] = useState(null) // { kind: 'api'|'file'|'mock', why }
  const [loading, setLoading] = useState(false)
  const [idx, setIdx] = useState(-1)
  const [playing, setPlaying] = useState(false)
  const [speed, setSpeed] = useState('Normal')

  const load = async () => {
    setLoading(true)
    setPlaying(false)
    setIdx(-1)
    let data = null
    let source = null
    try {
      data = normalizeJourney(await runJourney(seed))
      source = { kind: 'api', why: 'POST /demo/journey/run' }
    } catch (e) {
      const why = e instanceof OfflineError ? 'API offline' : `POST /demo/journey/run: ${e.message}`
      try {
        data = normalizeJourney(await getJourneyFile())
        source = { kind: 'file', why: `${why} · replaying saved journey.json` }
      } catch {
        data = MOCK_JOURNEY
        source = { kind: 'mock', why }
      }
    }
    setJ(data || MOCK_JOURNEY)
    setSrc(source)
    setLoading(false)
    setIdx(0)
    setPlaying(true)
  }

  useEffect(() => {
    if (!playing || !j) return undefined
    if (idx >= j.steps.length - 1) { setPlaying(false); return undefined }
    const t = setTimeout(() => setIdx((i) => i + 1), SPEEDS[speed])
    return () => clearTimeout(t)
  }, [playing, idx, j, speed])

  const steps = j?.steps || MOCK_JOURNEY.steps
  const { card, changed, timeline } = useMemo(() => {
    const jj = j || MOCK_JOURNEY
    let c = { ...jj.borrower }
    let ch = new Set()
    const tl = []
    jj.steps.forEach((s, i) => {
      if (i > idx) return
      if (i === idx) ch = new Set(Object.keys(s.borrower || {}))
      c = mergeCard(c, s.borrower)
      ;(s.events || []).forEach((e, k) => tl.push({ ...e, id: `${i}-${k}`, step: s.title, provenance: e.provenance || s.provenance || jj.provenance,
        type: e.type || e.event_type, decision: e.decision || e.payload?.decision, reason: e.reason || e.payload?.reason, rule: e.rule || e.payload?.rule,
        policy_version: e.policy_version || e.payload?.policy_version, detail: e.detail || e.payload, occurred_at: e.occurred_at, borrower_id: e.borrower_id }))
    })
    return { card: c, changed: ch, timeline: tl.reverse() }
  }, [j, idx])
  const cur = idx >= 0 ? steps[idx] : null
  const prov = normProv(j?.provenance || 'SIMULATED')

  return (
    <>
      <PageHeader title="Live journey" sub="One synthetic borrower through every part of Kairos — watch the decisions, holds and envelopes appear as they happen."
        chip={src ? (src.kind === 'mock' ? <span title={src.why}><Chip tone="warn">{/offline/i.test(src.why) ? 'SIMULATED · offline mock' : 'SIMULATED · built-in script'}</Chip></span> : <ProvChip prov={prov} title={src.why} />) : null}
        right={<>
          <span className="text-[12.5px] text-muted">Seed</span>
          <div className="w-20"><Input value={seed} onChange={(e) => setSeed(e.target.value)} /></div>
          <Segmented size="sm" value={speed} onChange={setSpeed} options={Object.keys(SPEEDS)} />
          <Button variant="primary" size="lg" icon={loading ? Loader : Play} disabled={loading} onClick={load}>{loading ? 'Running…' : 'Run the journey'}</Button>
        </>} />
      {src && <div className="-mt-3 mb-4 text-[12px] text-muted">Source: {src.kind === 'api' ? 'POST /demo/journey/run (backend run_journey)' : src.kind === 'file' ? 'saved journey.json (replay)' : `built-in script — ${src.why}`}</div>}

      <div className="grid grid-cols-[250px_minmax(0,1fr)_400px] gap-5">
        <Card className="h-fit p-3">
          {steps.map((s, i) => (
            <button key={s.id} onClick={() => { setPlaying(false); setIdx(i) }} disabled={!j}
              className={cx('flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-[13px]', i === idx ? 'bg-raised font-semibold text-fg' : i < idx ? 'text-fg/80' : 'text-muted', 'disabled:cursor-default')}>
              <span className={cx('grid h-6 w-6 shrink-0 place-items-center rounded-full border text-[11px]', i < idx ? 'border-ok/40 bg-ok/15 text-ok' : i === idx ? 'border-info bg-info/20 text-info' : 'border-line')}>{i < idx ? <Check size={12} /> : i + 1}</span>
              {s.title}
            </button>
          ))}
          <div className="mt-2 flex gap-1.5 border-t border-line px-1 pt-3">
            <Button size="sm" icon={playing ? Pause : Play} disabled={!j} onClick={() => setPlaying(!playing)}>{playing ? 'Pause' : 'Play'}</Button>
            <Button size="sm" icon={SkipForward} disabled={!j || idx >= steps.length - 1} onClick={() => { setPlaying(false); setIdx(idx + 1) }}>Step</Button>
            <Button size="sm" variant="ghost" icon={RotateCcw} disabled={!j} onClick={() => { setPlaying(false); setIdx(0) }} />
          </div>
        </Card>

        <div className="min-w-0 space-y-4">
          <Card className="p-5">
            {cur ? (
              <div key={cur.id} className="anim-fade">
                <div className="text-[12px] font-semibold tracking-wide text-muted uppercase">Step {idx + 1} of {steps.length}</div>
                <div className="mt-1 text-[22px] font-semibold tracking-tight">{cur.title}</div>
                <div className="mt-2 text-[14px] leading-relaxed text-fg/90">{cur.story}</div>
              </div>
            ) : <div className="text-[14px] text-muted">Press <b className="text-fg">Run the journey</b>. Kairos runs the scripted journey on the backend when it can; otherwise it replays the built-in SIMULATED script.</div>}
            {j && <div className="mt-4 h-1 overflow-hidden rounded-full bg-raised"><div className="h-full rounded-full bg-info transition-all" style={{ width: `${((idx + 1) / steps.length) * 100}%` }} /></div>}
          </Card>
          <BorrowerCard card={card} changed={changed} />
          <Card>
            <CardHead title="Timeline · CRM envelopes" sub="Filling in as each step runs — newest first" right={<ProvChip prov={prov} />} />
            <div className="px-3 pb-3">
              {timeline.length ? timeline.map((e) => (
                <div key={e.id} className="flex items-start gap-2">
                  <span className="mt-2 w-24 shrink-0 truncate text-right text-[11px] text-faint">{e.step}</span>
                  <div className="min-w-0 flex-1"><FeedRow e={e} /></div>
                </div>
              )) : <Empty>No envelopes yet.</Empty>}
            </div>
          </Card>
        </div>

        <LiveFeed height={760} />
      </div>
    </>
  )
}
