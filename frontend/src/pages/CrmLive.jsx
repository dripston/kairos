// Smart CRM borrower page on the live API: K03 spine loans/EMIs, the K06/K09/K17 event trail as CRM envelopes,
// the next action with its reason, and HITL actions (hold, handoff, do-not-contact, manual send preview).
import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  ArrowLeft, Check, X, Gavel, Pause, Play, CalendarClock, CircleSlash, Hand, UserPlus, Ban, Lock, Eye, AudioLines, Phone,
  Send, MessageSquareReply, BadgeCheck, ShieldAlert, CircleCheck, Ticket, Bot, Info,
} from 'lucide-react'
import { useKairos } from '../lib/store'
import { useApi, useApiRole, useApiStatus } from '../api/hooks'
import { borrowerAction, getAudit, getBorrowerControls, getVoiceCall, manualPreview, selectVoiceBot } from '../api/kairos'
import { OfflineError } from '../api/client'
import { TEMPLATES } from '../lib/mock'
import { fmtTs, paiseInr } from '../lib/format'
import { MockNote, ProvChip, RefreshButton, SourceBadge } from '../components/live'
import { Button, Card, CardHead, ChannelTag, Chip, Empty, Input, ReasonDialog, Select, StatusChip, Td, Th, cx } from '../components/ui'
import { TONE_BG, TONE_TEXT } from '../lib/events'

const CHS = ['sms', 'whatsapp', 'email', 'ivr', 'voicebot']
const knownCh = (ch) => (CHS.includes(ch) ? ch : null)

// audit row -> CRM envelope-style timeline item
function envelope(r) {
  const t = r.type || ''
  const base = { id: r.id, at: r.ts, actor: r.actor, raw: r, channel: knownCh(r.detail?.channel) }
  if (t === 'DECISION') return { ...base, kind: 'supervisor.decision', label: 'Supervisor decision', icon: Gavel, tone: { SEND: 'ok', WAIT: 'warn', CANCEL: 'bad', HANDOFF: 'violet' }[r.decision] || 'info', decision: r.decision, text: `${r.reason}${r.rule ? ` · ${r.rule}` : ''}` }
  if (t === 'SEND_TIME_CHECK') return { ...base, kind: 'gate.send_time', label: 'Send-time re-check', icon: CircleCheck, tone: r.decision === 'SEND' ? 'ok' : 'warn', decision: r.decision, text: `${r.reason}${r.rule ? ` · ${r.rule}` : ''}` }
  if (t === 'PROVIDER_STATUS') return { ...base, kind: 'touch.status', label: `Touch ${String(r.reason || '').toLowerCase()}`, icon: Send, tone: 'neutral', text: `${r.detail?.previous ?? '—'} → ${r.detail?.now ?? r.reason}${r.reason === 'DELIVERED' || r.reason === 'READ' ? ' · reach, not a reply' : ''}` }
  if (t === 'RESPONSE') return { ...base, kind: 'borrower.reply', label: 'Borrower replied', icon: MessageSquareReply, tone: 'info', text: r.reason }
  if (t === 'VOICE_DISPOSITION') return { ...base, kind: 'voice.call_disposition', label: `Voice · ${r.detail?.effective || r.detail?.disposition}`, icon: AudioLines, tone: 'violet', channel: 'voicebot', callId: r.detail?.call_id, confidence: r.detail?.confidence, rule: r.reason, text: `${r.detail?.action || ''}` }
  if (t === 'CALL_RESULT') return { ...base, kind: 'call.result', label: 'Call result', icon: Phone, tone: 'neutral', text: `answered ${r.detail?.answered_seconds ?? 0}s` }
  if (t.startsWith('PAYMENT')) return { ...base, kind: 'payment', label: t === 'PAYMENT_VERIFIED' ? 'Payment verified (D7/LMS)' : t.replace(/_/g, ' ').toLowerCase(), icon: BadgeCheck, tone: t.includes('REVERS') ? 'bad' : 'ok', text: r.reason || '' }
  if (t.startsWith('EXCEPTION') || t.includes('HOLD')) return { ...base, kind: 'hold', label: t.replace(/^EXCEPTION_/, '').replace(/_/g, ' ').toLowerCase().replace(/^\w/, (c) => c.toUpperCase()), icon: CalendarClock, tone: 'warn', text: [r.reason, r.detail?.hold].filter(Boolean).join(' · ') }
  if (t.includes('STOP') || t.includes('DNC')) return { ...base, kind: 'consent', label: t.replace(/_/g, ' ').toLowerCase(), icon: Ban, tone: 'bad', text: r.reason || '' }
  if (t.startsWith('HUMAN') || t.startsWith('HITL') || String(r.actor || '').startsWith('human')) return { ...base, kind: 'human.action', label: t.replace(/_/g, ' ').toLowerCase().replace(/^\w/, (c) => c.toUpperCase()), icon: Hand, tone: t.includes('REFUSED') ? 'bad' : 'info', text: [r.reason, r.detail?.action].filter(Boolean).join(' · ') }
  if (t.includes('TICKET') || t.includes('CASE')) return { ...base, kind: 'case', label: t.replace(/_/g, ' ').toLowerCase(), icon: Ticket, tone: 'violet', text: r.reason || '' }
  return { ...base, kind: t.toLowerCase(), label: t.replace(/_/g, ' ').toLowerCase().replace(/^\w/, (c) => c.toUpperCase()), icon: Info, tone: 'neutral', text: r.reason || '' }
}

function nextAction(b, decisions) {
  const s = b.state || {}
  if (s.stopped) return { action: 'CANCEL', reason: 'STOP / do-not-contact on file — every channel suppressed. Consent cannot be overridden.', source: 'K06 state' }
  if (s.hardship_until || s.hardship_supervisor) return { action: 'HANDOFF', reason: `Hardship hold${s.hardship_until ? ` until ${s.hardship_until}` : ''} — a person owns this account.`, source: 'K06 state' }
  if (s.supervisor_hold) return { action: 'WAIT', reason: 'Supervisor hold — outreach paused until a person clears it.', source: 'K06 state' }
  if (s.callback_pending) return { action: 'WAIT', reason: 'Callback pending — nudges paused while the callback is open.', source: 'K06 state' }
  if (s.ptp_date) return { action: 'WAIT', reason: `Promise to pay — hold through ${s.ptp_date}; payment re-checked before outreach resumes.`, source: 'K06 state' }
  const queued = (b.touches || []).filter((t) => ['RESERVED', 'PENDING_APPROVAL', 'DISPATCHING'].includes(t.status)).sort((a, c) => String(a.scheduled_at).localeCompare(String(c.scheduled_at)))[0]
  if (queued) return { action: 'SEND', reason: queued.status === 'PENDING_APPROVAL' ? `Queued ${queued.channel} touch is waiting for a person's approval (${queued.reason}).` : `${queued.channel} touch reserved for ${fmtTs(queued.scheduled_at)} · ${queued.template_id}.`, touch: queued, source: 'touches' }
  const last = decisions[0]
  if (last) return { action: last.decision, reason: `${last.reason}${last.rule ? ` (${last.rule})` : ''}`, version: last.policy_version, at: last.ts, source: 'last decision' }
  if ((b.loans || []).every((l) => l.cured)) return { action: 'WAIT', reason: 'All EMIs verified paid. Nothing to send.', source: 'spine' }
  return { action: 'WAIT', reason: 'No decision yet for this borrower.', source: '—' }
}

const ACTION_BTNS = [
  { a: 'pause', label: 'Pause contact', icon: Pause, effect: 'All pending sends for this borrower are held until a person resumes.' },
  { a: 'resume', label: 'Resume contact', icon: Play, effect: 'Kairos may contact this borrower again within policy.' },
  { a: 'hold', label: 'Hold for 2 days', icon: CalendarClock, effect: 'Hold for 2 days. Queued touches are canceled and payment is re-checked before outreach resumes.', extra: { days: 2 } },
  { a: 'cancel_next', label: 'Cancel next touch', icon: CircleSlash, effect: 'The next queued send is canceled and its budget released.' },
  { a: 'handoff', label: 'Hand off to a person', icon: Hand, effect: 'Ordinary outreach pauses and a HUMAN_HANDOFF case goes to the supervisor queue.' },
  { a: 'assign', label: 'Assign to me', icon: UserPlus, effect: 'You own follow-ups for this account.', extra: (actor) => ({ agent: actor }) },
  { a: 'dnc', label: 'Do not contact', icon: Ban, danger: true, effect: 'STOP on every channel. Every pending send is canceled. Kairos never undoes this; only a per-channel opt-in with a consent version re-enables a channel.' },
]

export default function CrmLive({ b, res }) {
  const { dispatch } = useKairos()
  const { role, actor, label } = useApiRole()
  const status = useApiStatus()
  const id = b.borrower_id
  const audit = useApi((o) => getAudit({ limit: 500, borrowerId: id }, o), { fallback: () => [], deps: [id, status.state], poll: 15000 })
  const controls = useApi((o) => getBorrowerControls(id, role, o), { deps: [id, role, status.state] })
  const [dialog, setDialog] = useState(null)
  const [call, setCall] = useState(null)
  const [bot, setBot] = useState(null)

  const rows = useMemo(() => [...(audit.data || [])].sort((a, c) => String(c.ts).localeCompare(String(a.ts)) || c.id - a.id), [audit.data])
  const decisions = rows.filter((r) => r.type === 'DECISION' && r.decision)
  const items = rows.map(envelope)
  const next = nextAction(b, decisions)
  const allowed = new Set(controls.data?.allowed_actions || [])
  const hitlLive = controls.source === 'live'
  const reloadAll = () => { res.reload(); audit.reload(); controls.reload() }

  const toastErr = (what, e) => dispatch({ type: 'TOAST', tone: 'bad', text: `${what} refused · ${e instanceof OfflineError ? 'Kairos API unreachable' : `${e.status ?? ''} ${e.code ? `${e.code}: ` : ''}${e.message}`}` })
  const doAction = async (btn, reason) => {
    const extra = typeof btn.extra === 'function' ? btn.extra(actor) : btn.extra || {}
    try {
      const out = await borrowerAction(id, { action: btn.a, actor, reason, ...extra }, role)
      dispatch({ type: 'TOAST', text: `${btn.label} · ${id}${out?.canceled != null ? ` · ${out.canceled} queued canceled` : ''} (as ${label})`, tone: btn.danger ? 'warn' : 'ok' })
    } catch (e) { toastErr(btn.label, e) }
    reloadAll()
  }
  const openCall = async (callId) => {
    setCall({ callId, loading: true })
    try { setCall({ callId, data: await getVoiceCall(callId) }) } catch (e) { setCall({ callId, error: e.message }) }
  }
  const pickBot = async () => {
    setBot({ loading: true })
    try { setBot({ data: await selectVoiceBot({ borrower_id: id }) }) } catch (e) { setBot({ error: e instanceof OfflineError ? 'Kairos API unreachable' : e.message }) }
  }

  const s = b.state || {}
  const flags = [
    s.stopped && ['STOP · all channels', 'bad'], s.hardship_until && [`Hardship until ${s.hardship_until}`, 'violet'], s.hardship_supervisor && ['Hardship · supervisor', 'violet'],
    s.callback_pending && ['Callback pending', 'info'], s.supervisor_hold && ['Supervisor hold', 'warn'], s.ptp_date && [`PTP hold through ${s.ptp_date}`, 'warn'],
    s.broken_ptp ? [`Broken PTP × ${s.broken_ptp}`, 'bad'] : null, controls.data?.dnc && ['Do not contact', 'bad'], controls.data?.assignment && [`Assigned · ${controls.data.assignment.agent || 'agent'}`, 'info'],
    ...(controls.data?.holds || []).map((h) => [`Hold · ${h.kind || h.reason || 'manual'}${h.until ? ` until ${fmtTs(h.until)}` : ''}`, 'warn']),
  ].filter(Boolean)

  return (
    <>
      <div className="mb-4 flex items-center gap-2 text-[13px] text-muted">
        <Link to="/crm" className="inline-flex items-center gap-1 hover:text-fg"><ArrowLeft size={14} /> Smart CRM</Link>
        <span>/</span><span className="font-mono text-fg">{id}</span>
        <span className="ml-2"><SourceBadge res={res} /></span>
        <div className="flex-1" />
        <RefreshButton onClick={reloadAll} />
      </div>

      <div className="grid grid-cols-[290px_minmax(0,1fr)_300px] gap-5">
        {/* LEFT · identity, consent, state */}
        <div className="space-y-4">
          <Card className="p-5">
            <div className="flex items-center gap-3">
              <div className="grid h-11 w-11 place-items-center rounded-full bg-info/15 text-[13px] font-bold text-info">{String(b.full_name || id).split(' ').map((p) => p[0]).join('').slice(0, 2)}</div>
              <div className="min-w-0">
                <div className="truncate text-[16px] font-semibold">{b.full_name || id}</div>
                <div className="font-mono text-[12px] text-muted">{id}</div>
              </div>
            </div>
            <div className="mt-4 space-y-1.5 text-[13px]">
              <Row k="Phone" v={<span className="font-mono">{b.phone}</span>} />
              <Row k="Language" v={b.language || '—'} />
              {b.employment_type && <Row k="Employment" v={b.employment_type} />}
              <Row k="Source" v={<span className="text-muted">K03 spine · synthetic</span>} />
            </div>
          </Card>

          <Card className="p-5">
            <div className="mb-3 text-[13.5px] font-semibold">Consent (K03, per channel)</div>
            <div className="space-y-2">
              {CHS.map((ch) => {
                const c = (b.consents || []).find((x) => x.channel === ch)
                const ok = c && c.consented && !c.opted_out
                return (
                  <div key={ch} className="flex items-center justify-between text-[13px]">
                    <ChannelTag ch={ch} muted={!ok} />
                    <span className={cx('inline-flex items-center gap-1 text-[12px]', ok ? 'text-ok' : 'text-bad')}>
                      {ok ? <Check size={12} /> : <X size={12} />}{!c ? 'no record' : c.opted_out ? 'opted out' : c.consented ? `yes · ${c.source || ''}` : 'no consent'}
                    </span>
                  </div>
                )
              })}
            </div>
          </Card>

          <Card className="p-5">
            <div className="mb-3 flex items-center justify-between text-[13.5px] font-semibold">Holds & flags {!hitlLive && <SourceBadge res={controls} />}</div>
            <div className="flex flex-wrap gap-1.5">
              {flags.length ? flags.map(([t, tone]) => <Chip key={t} tone={tone}>{t}</Chip>) : <span className="text-[12.5px] text-muted">None</span>}
            </div>
          </Card>
        </div>

        {/* CENTRE · next action, loans, timeline */}
        <div className="min-w-0 space-y-4">
          <Card>
            <div className="flex items-start justify-between gap-6 px-5 py-4">
              <div className="min-w-0">
                <div className="flex items-center gap-2 text-[12px] font-semibold tracking-wide text-muted uppercase"><Gavel size={13} /> Next action</div>
                <div className="mt-2 flex items-center gap-3">
                  <span className={cx('text-[26px] font-bold tracking-tight', { SEND: 'text-ok', WAIT: 'text-warn', CANCEL: 'text-bad', HANDOFF: 'text-violet' }[next.action])}>{next.action}</span>
                  {next.touch && <span className="text-[13px] text-muted"><ChannelTag ch={knownCh(next.touch.channel)} /> · {fmtTs(next.touch.scheduled_at)} · <span className="font-mono">{next.touch.template_id}</span></span>}
                </div>
                <div className="mt-1 text-[13.5px]">{next.reason}</div>
                <div className="mt-2 text-[12px] text-muted">From {next.source}{next.version ? ` · policy v${next.version}` : ''}{next.at ? ` · ${fmtTs(next.at)}` : ''}</div>
              </div>
              <ProvChip prov={status.env} title="Backend environment — sends go through FakeProvider until real adapters are wired" />
            </div>
          </Card>

          <Card>
            <CardHead title="Loans & EMIs" sub="K03 spine · each EMI tracked separately — a verified payment clears that EMI only" right={<SourceBadge res={res} />} />
            <table className="w-full">
              <thead><tr><Th>Loan · EMI</Th><Th>Due</Th><Th>Amount due</Th><Th>Paid</Th><Th>Outstanding</Th><Th>DPD · band</Th><Th>Status</Th></tr></thead>
              <tbody>
                {(b.loans || []).flatMap((l) => (l.obligations || []).map((o, i) => (
                  <tr key={o.obligation_id}>
                    <Td><span className="font-mono">{i === 0 ? l.loan_id : ''}</span><div className="font-mono text-[11.5px] text-muted">#{o.installment_no} · {o.obligation_id}</div></Td>
                    <Td className="font-mono text-[12px]">{o.due_date}</Td>
                    <Td className="tabular">{paiseInr(o.amount_due_paise, 0)}</Td>
                    <Td className="tabular">{paiseInr(o.amount_paid_paise, 0)}</Td>
                    <Td className="tabular">{paiseInr(o.outstanding_paise, 0)}</Td>
                    <Td>{i === 0 ? <Chip tone={l.dpd >= 60 ? 'bad' : l.dpd >= 30 ? 'warn' : 'neutral'}>{l.dpd ?? '—'} DPD · {l.band ?? '—'}</Chip> : null}</Td>
                    <Td>{o.outstanding_paise <= 0 ? <Chip tone="ok">Paid · verified</Chip> : <Chip tone="warn">Open</Chip>}</Td>
                  </tr>
                )))}
              </tbody>
            </table>
            {!(b.loans || []).length && <Empty>No loans in the spine for this borrower.</Empty>}
          </Card>

          {!!(b.touches || []).length && (
            <Card>
              <CardHead title="Touches" sub="Reserved, pending approval, sent — from the K06 ledger" />
              <table className="w-full">
                <thead><tr><Th>Instruction</Th><Th>Channel</Th><Th>Scheduled</Th><Th>Template</Th><Th>Status</Th><Th>Cost</Th></tr></thead>
                <tbody>
                  {[...b.touches].reverse().slice(0, 8).map((t) => (
                    <tr key={t.instruction_id}>
                      <Td className="font-mono text-[12px]">{t.instruction_id}</Td><Td><ChannelTag ch={knownCh(t.channel)} /></Td>
                      <Td className="font-mono text-[12px]">{fmtTs(t.scheduled_at)}</Td><Td className="font-mono text-[12px]">{t.template_id}</Td>
                      <Td><Chip tone={{ SENT: 'neutral', DELIVERED: 'ok', READ: 'ok', RESERVED: 'ok', PENDING_APPROVAL: 'violet', CANCELED: 'bad', FAILED: 'bad', UNCERTAIN: 'warn' }[t.status] || 'neutral'}>{t.status}</Chip></Td>
                      <Td className="tabular">{paiseInr(t.actual_cost_paise ?? t.est_cost_paise)}</Td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Card>
          )}

          <Card>
            <CardHead title="Timeline · CRM envelopes" sub="Decisions, touches, replies, voice dispositions, payments, holds and human actions — newest first" right={<SourceBadge res={audit} />} />
            <MockNote res={audit} />
            <div className="px-5 pb-4">
              {items.length ? items.map((e) => {
                const I = e.icon
                return (
                  <div key={e.id} className="flex gap-3 border-t border-line py-2 first:border-t-0">
                    <div className={cx('mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-lg', TONE_BG[e.tone])}><I size={14} className={TONE_TEXT[e.tone]} /></div>
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2 text-[13px]">
                        <span className="font-semibold">{e.label}</span>
                        {e.decision && <StatusChip s={e.decision}>{e.decision}</StatusChip>}
                        {e.channel && <ChannelTag ch={e.channel} muted />}
                        {e.confidence != null && <Chip tone={e.confidence >= 0.8 ? 'ok' : e.confidence >= 0.6 ? 'warn' : 'bad'}>conf {Number(e.confidence).toFixed(2)}</Chip>}
                        {e.rule && <span className="font-mono text-[11.5px] text-info">{e.rule}</span>}
                        <span className="ml-auto font-mono text-[11.5px] text-faint">{fmtTs(e.at)} · {e.actor} · v{e.raw.policy_version}</span>
                      </div>
                      <div className="flex items-center gap-2 text-[12.5px] text-muted">
                        <span className="font-mono text-[11px] text-faint">{e.kind}</span>
                        <span className="min-w-0 truncate">{e.text}</span>
                        {e.callId && <button onClick={() => openCall(e.callId)} className="ml-auto shrink-0 text-[12px] text-info hover:underline">Call details</button>}
                      </div>
                      {call?.callId === e.callId && e.callId && (
                        <div className="mt-1.5 rounded-lg border border-line bg-bg p-2.5 text-[12px]">
                          {call.loading ? 'Loading…' : call.error ? <span className="text-bad">{call.error}</span> : (
                            <div className="grid grid-cols-2 gap-x-4 gap-y-0.5">
                              <span className="text-muted">Use case</span><span>{call.data.use_case}</span>
                              <span className="text-muted">Disposition</span><span>{call.data.disposition} → {call.data.effective_disposition}</span>
                              <span className="text-muted">Confidence · rule</span><span>{call.data.confidence} · <span className="font-mono">{call.data.rule}</span></span>
                              <span className="text-muted">Action</span><span>{call.data.action}</span>
                              <span className="text-muted">Duration</span><span>{call.data.duration_seconds}s · {call.data.report_label}</span>
                              <span className="text-muted">Thresholds</span><span>v{call.data.policy_version}</span>
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  </div>
                )
              }) : <Empty>{audit.loading ? 'Loading…' : 'No events for this borrower yet.'}</Empty>}
            </div>
          </Card>
        </div>

        {/* RIGHT · HITL actions */}
        <div className="space-y-4">
          <Card className="p-5">
            <div className="mb-1 flex items-center justify-between text-[13.5px] font-semibold">Borrower actions <SourceBadge res={controls} /></div>
            <div className="mb-3 text-[12px] text-muted">As {label} · X-Kairos-Role: {role}</div>
            {!hitlLive && <div className="mb-3 text-[12px] text-warn">HITL routes not reachable ({controls.offline ? 'API offline' : controls.error?.message || 'error'}) — actions disabled.</div>}
            <div className="grid gap-2">
              {ACTION_BTNS.map((btn) => {
                const ok = hitlLive && allowed.has(btn.a)
                return (
                  <Button key={btn.a} variant={btn.danger ? 'danger' : 'outline'} icon={ok ? btn.icon : Lock} disabled={!ok}
                    title={ok ? '' : hitlLive ? `Not allowed for ${label}` : 'HITL API unavailable'}
                    onClick={() => setDialog(btn)}>{btn.label}</Button>
                )
              })}
            </div>
          </Card>

          <ManualPreview id={id} b={b} role={role} enabled={hitlLive && allowed.has('hold') /* any operator+ role */} />

          <Card className="p-5">
            <div className="mb-2 flex items-center gap-1.5 text-[13.5px] font-semibold"><Bot size={14} /> Voice bot</div>
            <div className="mb-2 text-[12px] text-muted">Which Sarvam bot, voice and language Kairos would use now. Selection only — the K01 Gate still decides whether a call may happen.</div>
            <Button icon={AudioLines} onClick={pickBot} disabled={bot?.loading}>Pick bot for borrower</Button>
            {bot?.error && <div className="mt-2 text-[12px] text-bad">{bot.error}</div>}
            {bot?.data && <BotResult r={bot.data} />}
          </Card>

          <Card className="p-5">
            <div className="mb-2 flex items-center gap-1.5 text-[13.5px] font-semibold"><Lock size={13} /> Locked by guardrails</div>
            <div className="flex flex-wrap gap-1">{(controls.data?.locked_by_guardrails || ['override_stop', 'override_consent', 'exceed_cap', 'force_send', 'undo_dnc']).map((x) => <Chip key={x}>{x.replace(/_/g, ' ')}</Chip>)}</div>
            <div className="mt-2 text-[12px] text-muted">Nobody — operator, supervisor or policy owner — can do these. Attempts are refused and audited.</div>
          </Card>
        </div>
      </div>

      <ReasonDialog open={!!dialog} title={dialog ? `${dialog.label} · ${id}` : ''} effect={dialog?.effect} danger={dialog?.danger} confirmLabel={dialog?.label}
        onClose={() => setDialog(null)} onConfirm={(reason) => doAction(dialog, reason)} />
    </>
  )
}

export function BotResult({ r }) {
  return (
    <div className="mt-2 space-y-1 rounded-lg border border-line bg-bg p-3 text-[12.5px]">
      <div className="flex items-center justify-between"><span className="font-semibold">{r.use_case || 'No bot'}</span><Chip tone={r.ready ? 'ok' : 'warn'}>{r.ready ? 'ready' : 'not ready'}</Chip></div>
      {r.agent_id && <div className="font-mono text-[11.5px] text-muted">{r.agent_id}</div>}
      <div>Voice <b>{r.voice || '—'}</b> · {r.language}{r.language_code ? ` (${r.language_code})` : ''}</div>
      <div className="text-muted">{r.reason}</div>
      {r.requires_human_approval && <Chip tone="violet">Needs a person's approval</Chip>}
      <div className="text-[11px] text-faint">registry v{r.registry_version}{r.thresholds_version ? ` · thresholds v${r.thresholds_version}` : ''}</div>
    </div>
  )
}

function ManualPreview({ id, b, role, enabled }) {
  const ob = (b.loans || []).flatMap((l) => l.obligations || []).find((o) => o.outstanding_paise > 0)
  const tplIds = [...new Set([...(b.touches || []).map((t) => t.template_id).filter(Boolean), ...TEMPLATES.map((t) => t.id)])]
  const tomorrow = new Date(Date.now() + 86400000)
  const defAt = `${tomorrow.getFullYear()}-${String(tomorrow.getMonth() + 1).padStart(2, '0')}-${String(tomorrow.getDate()).padStart(2, '0')}T15:00`
  const [ch, setCh] = useState('sms')
  const [tpl, setTpl] = useState(tplIds.find((t) => t.startsWith('SMS')) || tplIds[0] || '')
  const [at, setAt] = useState(defAt)
  const [out, setOut] = useState(null)
  const run = async () => {
    setOut({ loading: true })
    try {
      setOut({ data: await manualPreview({ borrower_id: id, channel: ch, template_id: tpl, at, est_cost_paise: 20, obligation_id: ob?.obligation_id }, role) })
    } catch (e) { setOut({ error: e instanceof OfflineError ? 'Kairos API unreachable' : `${e.status ?? ''} ${e.message}` }) }
  }
  const p = out?.data
  return (
    <Card className="p-5">
      <div className="mb-2 flex items-center gap-1.5 text-[13.5px] font-semibold"><Eye size={14} /> Manual send · preview</div>
      <div className="mb-2 text-[12px] text-muted">Renders the template and runs the Gate without reserving anything. Manual sends always need a second person.</div>
      <div className="grid gap-2">
        <Select value={ch} onChange={setCh} options={CHS.slice(0, 4).map((c) => ({ value: c, label: c }))} />
        <Input list="kairos-templates" value={tpl} onChange={(e) => setTpl(e.target.value)} placeholder="Template id" />
        <datalist id="kairos-templates">{tplIds.map((t) => <option key={t} value={t} />)}</datalist>
        <Input type="datetime-local" value={at} onChange={(e) => setAt(e.target.value)} />
        <Button icon={Eye} disabled={!enabled || out?.loading} onClick={run} title={enabled ? '' : 'HITL API unavailable'}>Preview</Button>
      </div>
      {out?.error && <div className="mt-2 text-[12px] text-bad">{out.error}</div>}
      {p && (
        <div className="mt-2 space-y-1.5 rounded-lg border border-line bg-bg p-3 text-[12.5px]">
          <div className="flex items-center gap-2"><StatusChip s={p.gate?.decision || (p.allowed ? 'SEND' : 'WAIT')}>{p.gate?.decision || (p.allowed ? 'SEND' : 'WAIT')}</StatusChip><span className="font-mono text-[11.5px]">{p.reason}</span></div>
          {p.preview?.rendered && <div className="rounded border border-line px-2 py-1.5 text-[12px] text-muted">{p.preview.rendered}</div>}
          {!!p.would_need_approval?.length && <div className="text-violet">Needs approval: {p.would_need_approval.join(', ')}</div>}
          {p.locked_by_guardrails && <div className="flex items-center gap-1 text-bad"><ShieldAlert size={12} /> Locked by guardrails</div>}
          <div className="text-[11px] text-faint">policy v{p.gate?.policy_version} · band {p.gate?.band ?? '—'} · nothing reserved</div>
        </div>
      )}
    </Card>
  )
}

function Row({ k, v }) {
  return <div className="flex items-center justify-between gap-3"><span className="text-muted">{k}</span><span className="text-right">{v}</span></div>
}
