// Live event feed: every CRM envelope as it happens. Polls GET /events/since every 1.5 s; falls back to diffing
// GET /audit when that route is missing, and to a SIMULATED generator when the API is unreachable.
import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { Pause, Play, Radio } from 'lucide-react'
import { getAudit, getEventsSince } from '../api/kairos'
import { ApiError, OfflineError } from '../api/client'
import { KINDS, DECISION_TONE, kindOf, textOf } from '../lib/feed'
import { fmtTs, normProv, PROV_TONE } from '../lib/format'
import { TONE_BG, TONE_TEXT } from '../lib/events'
import { Card, CardHead, Chip, Empty, StatusChip, cx } from './ui'

const MAX = 150
const FILTERS = ['All', 'Decisions', 'Touches', 'Replies', 'Voice', 'CM-03', 'Payments', 'Holds']
const FILTER_KINDS = {
  Decisions: ['decision'], Touches: ['dispatch', 'delivery'], Replies: ['reply'], Voice: ['voice'], 'CM-03': ['cm03', 'otp', 'fraud'],
  Payments: ['payment'], Holds: ['hold', 'stop', 'human', 'churn'],
}

// SIMULATED stream for when the API is down: the hero borrower's story, looping.
const MOCK_SCRIPT = [
  { type: 'DECISION', decision: 'SEND', reason: 'ELIGIBLE', rule: 'ALL_PASSED', detail: { channel: 'whatsapp' } },
  { type: 'SEND_TIME_CHECK', decision: 'SEND', reason: 'ELIGIBLE', rule: 'ALL_PASSED', detail: { channel: 'whatsapp' } },
  { type: 'PROVIDER_STATUS', reason: 'DELIVERED', detail: { previous: 'SENT', now: 'DELIVERED', channel: 'whatsapp' } },
  { type: 'PROVIDER_STATUS', reason: 'READ', detail: { previous: 'DELIVERED', now: 'READ', channel: 'whatsapp' } },
  { type: 'RESPONSE', actor: 'borrower', reason: 'reply', detail: { channel: 'whatsapp' } },
  { type: 'EXCEPTION_PTP_OFFERED', reason: 'CAPTURE_PTP', detail: { hold: 'PTP_HOLD' } },
  { type: 'DECISION', decision: 'WAIT', reason: 'PTP_HOLD', rule: 'PTP_HOLD', detail: { channel: 'sms' } },
  { type: 'VOICE_DISPOSITION', actor: 'agent:voice', reason: 'K17.CALLBACK.ACCEPT', detail: { disposition: 'CALLBACK', effective: 'CALLBACK', confidence: 0.88, action: 'CALLBACK_TASK' } },
  { type: 'CM03_MESSAGE', actor: 'cm03', reason: 'Inbound: "already paid" + UPI screenshot', detail: { channel: 'whatsapp' } },
  { type: 'CM03_OTP_VERIFIED', actor: 'cm03', reason: 'OTP verified · sender bound to account', detail: {} },
  { type: 'CM03_FRAUD_FLAG', actor: 'cm03', reason: 'UTR not in D7 — fake screenshot caught, no receipt', detail: {} },
  { type: 'PAYMENT_VERIFIED', actor: 'tool:d7', reason: 'D7 settled · clears EMI 2 of loan A only', detail: {} },
  { type: 'DECISION', decision: 'CANCEL', reason: 'OBLIGATION_CLEARED', rule: 'OBLIGATION_CLEARED', detail: { channel: 'sms' } },
  { type: 'COHORT_CHANGED', actor: 'auto-churn', reason: 'C3 → C2 · layer L2 → L1', detail: {} },
  { type: 'DECISION', decision: 'HANDOFF', reason: 'HARDSHIP_HOLD', rule: 'HARDSHIP_HOLD', detail: {} },
]
const MOCK_BORROWERS = ['B-1042', 'B-0213', 'B-0388', 'B-0451', 'B-0129']

function mockEvent(n) {
  const s = MOCK_SCRIPT[n % MOCK_SCRIPT.length]
  const now = new Date()
  const iso = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}T${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}:00`
  return { id: `m${n}`, actor: 'supervisor', policy_version: '1.1.0', ...s, occurred_at: iso, borrower_id: MOCK_BORROWERS[Math.floor(n / MOCK_SCRIPT.length) % MOCK_BORROWERS.length], provenance: 'SIMULATED', mock: true }
}

/** Polling feed state machine: 'since' (GET /events/since) → 'audit' (diff GET /audit) → 'mock'. */
function useLiveFeed({ interval = 1500, borrowerId } = {}) {
  const [events, setEvents] = useState([])
  const [mode, setMode] = useState('connecting')
  const [paused, setPaused] = useState(false)
  const st = useRef({ cursor: null, mode: 'since', n: 0, seen: new Set() })

  useEffect(() => {
    if (paused) return undefined
    let alive = true
    const ctl = new AbortController()
    const add = (list) => {
      const fresh = list.filter((e) => !st.current.seen.has(e.id))
      fresh.forEach((e) => st.current.seen.add(e.id))
      if (fresh.length) setEvents((prev) => [...fresh.reverse(), ...prev].slice(0, MAX))
    }
    const tick = async () => {
      const s = st.current
      try {
        if (s.mode === 'since') {
          if (s.cursor == null) {
            // seed with the latest rows, then follow the cursor
            const rows = await getAudit({ limit: 40, borrowerId }, { signal: ctl.signal })
            s.cursor = rows.reduce((m, r) => Math.max(m, r.id), 0)
            await getEventsSince({ cursor: s.cursor, limit: 1, borrowerId }, { signal: ctl.signal }) // probe the route
            add([...rows].reverse().map((r) => ({ ...r, occurred_at: r.ts, provenance: 'SIMULATED' })))
          } else {
            const out = await getEventsSince({ cursor: s.cursor, borrowerId }, { signal: ctl.signal })
            s.cursor = out.cursor
            add(out.events)
          }
          if (alive) setMode('since')
        } else if (s.mode === 'audit') {
          const rows = await getAudit({ limit: 60, borrowerId }, { signal: ctl.signal })
          add([...rows].reverse().map((r) => ({ ...r, occurred_at: r.ts, provenance: 'SIMULATED' })))
          if (alive) setMode('audit')
        } else {
          // offline: emit one simulated envelope per tick, and keep probing the API
          add([mockEvent(s.n++)])
          if (alive) setMode('mock')
          try { await getAudit({ limit: 1 }, { signal: ctl.signal, timeout: 1200 }); s.mode = 'since'; s.cursor = null; s.seen = new Set(); if (alive) setEvents([]) } catch { /* still offline */ }
        }
      } catch (e) {
        if (ctl.signal.aborted) return
        if (e instanceof ApiError && e.status === 404 && s.mode === 'since') s.mode = 'audit'
        else if (e instanceof OfflineError) { if (s.mode !== 'mock') { s.mode = 'mock'; s.seen = new Set(); if (alive) setEvents([]) } }
      }
    }
    tick()
    const t = setInterval(tick, interval)
    return () => { alive = false; ctl.abort(); clearInterval(t) }
  }, [paused, interval, borrowerId])

  return { events, mode, paused, setPaused }
}

const MODE_LABEL = { connecting: 'Connecting…', since: 'Streaming · GET /events/since every 1.5 s', audit: 'Polling · GET /audit (no /events/since on this backend)', mock: 'API offline · simulated stream' }

export function FeedRow({ e, compact }) {
  const k = kindOf(e)
  const m = KINDS[k]
  const I = m.icon
  const tone = k === 'decision' ? DECISION_TONE[e.decision] || m.tone : m.tone
  const d = e.detail || e.payload || {}
  return (
    <div className={cx('anim-fade flex gap-2.5 rounded-lg px-2 py-1.5', compact ? '' : 'hover:bg-raised/60')}>
      <div className={cx('mt-0.5 grid h-6 w-6 shrink-0 place-items-center rounded-md', TONE_BG[tone])}><I size={13} className={TONE_TEXT[tone]} /></div>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-1.5 text-[12.5px]">
          <span className={cx('font-semibold', TONE_TEXT[tone])}>{m.label}</span>
          {e.decision && <StatusChip s={e.decision}>{e.decision}</StatusChip>}
          {d.confidence != null && <Chip tone={d.confidence >= 0.8 ? 'ok' : d.confidence >= 0.6 ? 'warn' : 'bad'}>conf {Number(d.confidence).toFixed(2)}</Chip>}
          {e.borrower_id && <Link to={`/crm/${e.borrower_id}`} className="font-mono text-[11.5px] text-muted hover:underline">{e.borrower_id}</Link>}
          <span className="ml-auto flex items-center gap-1.5">
            <Chip tone={PROV_TONE[normProv(e.provenance)]}>{normProv(e.provenance)}</Chip>
            <span className="font-mono text-[11px] text-faint">{fmtTs(e.occurred_at || e.ts)}</span>
          </span>
        </div>
        <div className="truncate text-[12px] text-muted" title={textOf(e)}>
          {textOf(e)}{e.policy_version && k === 'decision' ? <span className="text-faint"> · policy v{e.policy_version}</span> : null}
          <span className="ml-1.5 font-mono text-[10.5px] text-faint">{e.event_type || e.type}</span>
        </div>
      </div>
    </div>
  )
}

export default function LiveFeed({ className, height = 520, borrowerId }) {
  const { events, mode, paused, setPaused } = useLiveFeed({ borrowerId })
  const [f, setF] = useState('All')
  const view = events.filter((e) => f === 'All' || FILTER_KINDS[f].includes(kindOf(e)))
  return (
    <Card className={cx('flex flex-col', className)}>
      <CardHead title={<span className="inline-flex items-center gap-2"><Radio size={15} className={cx(mode === 'mock' ? 'text-warn' : 'text-ok', !paused && 'live-dot')} /> Live event feed</span>}
        sub={MODE_LABEL[mode]}
        right={<>
          {mode === 'mock' ? <Chip tone="warn">SIMULATED · offline</Chip> : <Chip tone="warn" dot>SIMULATED · API</Chip>}
          <button onClick={() => setPaused(!paused)} title={paused ? 'Resume' : 'Pause'} className="grid h-7 w-7 place-items-center rounded-md text-muted hover:bg-raised hover:text-fg">{paused ? <Play size={13} /> : <Pause size={13} />}</button>
        </>} />
      <div className="mx-5 mb-2 flex flex-wrap gap-1">
        {FILTERS.map((x) => (
          <button key={x} onClick={() => setF(x)} className={cx('rounded-full border px-2 py-0.5 text-[11.5px]', f === x ? 'border-fg bg-fg text-bg' : 'border-line text-muted hover:text-fg')}>{x}</button>
        ))}
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-3 pb-3" style={{ maxHeight: height }}>
        {view.length ? view.map((e) => <FeedRow key={e.id} e={e} />) : <Empty>{mode === 'connecting' ? 'Connecting…' : 'Waiting for events…'}</Empty>}
      </div>
    </Card>
  )
}
