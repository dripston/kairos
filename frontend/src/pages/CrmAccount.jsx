import { useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import {
  ArrowLeft, Check, X, Brain, Radio, Gauge, FileText, Gavel, Pause, Play, CalendarClock, Hand, CircleSlash,
  Ban, UserPlus, Lock, Star, Wallet, ShieldCheck,
} from 'lucide-react'
import { useKairos, decideHero, fmtClock } from '../lib/store'
import { CHANNELS, CHANNEL_META, HERO_ID, RISK_TAGS, TEMPLATES } from '../lib/mock'
import { EVENT_META, TONE_BG, TONE_TEXT } from '../lib/events'
import { Bar, Button, Card, CardHead, ChannelTag, Chip, Empty, ReasonDialog, StatusChip, Td, Th, cx, inr, pct } from '../components/ui'

const TABS = ['Journey', 'Overview', 'Payments', 'Cases', 'Audit']

function genericAgents(b, day) {
  const ch = b.nextChannel || 'sms'
  const p = Math.max(0.08, Math.min(0.85, 0.82 - b.dpd / 140))
  const intensity = b.flags.hold || b.flags.manualPause ? 'WAIT' : b.dpd > 45 ? 'STANDARD' : 'LOW'
  const tpl = TEMPLATES.find((t) => t.channel === ch && t.lang === b.language) || TEMPLATES.find((t) => t.channel === ch)
  return {
    summary: [
      `Last touch Day ${b.lastTouchDay} on ${CHANNEL_META[b.lastTouchChannel].label}${b.lastTouchChannel === 'sms' ? ' (read unknown)' : ''}.`,
      b.flags.paid ? 'Payment verified in D7 — no further contact needed.' : b.flags.hold ? 'Promised to pay — hold active.' : b.flags.hardship ? 'Reported hardship — routed to a person.' : 'No reply yet; no stated intent.',
    ],
    channel: { best: ch, window: ch === 'whatsapp' ? '18:00–20:00' : ch === 'ivr' ? '11:00–13:00' : '10:00–12:00', alts: CHANNELS.filter((c) => c !== ch && b.consent[c] && c !== 'voicebot').slice(0, 2).map((c) => `${CHANNEL_META[c].label} · 10:00`), evidence: day > 3 ? 'Moderate' : 'Weak (pooled segment prior)' },
    propensity: { p, horizon: '7 days', intensity },
    template: tpl ? { id: tpl.id, lang: tpl.lang } : { id: null, lang: b.language },
  }
}
function genericDecision(b, day) {
  const base = [
    { label: `Consent (${b.nextChannel ? CHANNEL_META[b.nextChannel].label : 'any channel'})`, ok: !!b.nextChannel && !b.flags.stop && !b.flags.dnc },
    { label: `Cap ${b.capsUsed} of ${b.capLimit} this week`, ok: b.capsUsed < b.capLimit },
    { label: 'Budget available', ok: true },
    { label: 'Quiet hours', ok: true },
  ]
  const map = {
    SEND: { action: 'SEND', reason: `Best replies/₹ for ${b.band} DPD is ${b.nextChannel ? CHANNEL_META[b.nextChannel].label : '—'} in the next window.` },
    WAIT: { action: 'WAIT', reason: b.flags.manualPause ? 'Paused by operator.' : b.flags.hold ? `Hold active until Day ${b.flags.hold.until}.` : 'Expected replies per ₹ below threshold today — waiting is cheaper.' },
    HANDOFF: { action: 'HANDOFF', reason: 'Hardship or handoff flag — a person owns this account now.' },
    STOP: { action: 'CANCEL', reason: 'Opted out (STOP / do-not-contact). Consent cannot be overridden.' },
    DONE: { action: 'WAIT', reason: 'All obligations paid. Nothing to send.' },
  }
  return { ...map[b.nextAction], checks: base, version: 2, expires: `Day ${day} · 20:00` }
}

export default function CrmAccount() {
  const { id } = useParams()
  const { state, dispatch } = useKairos()
  const [tab, setTab] = useState('Journey')
  const [dialog, setDialog] = useState(null)
  const isHero = id === HERO_ID
  const b = isHero ? state.hero : state.borrowers.find((x) => x.id === id)

  const events = useMemo(() => state.events.filter((e) => e.borrowerId === id).sort((a, c) => (c.day - a.day) || (c.min - a.min) || (c.id - a.id)), [state.events, id])
  if (!b) return <Empty>Account {id} not found. <Link className="underline" to="/crm">Back to Smart CRM</Link></Empty>

  const agents = isHero ? state.hero.agents : genericAgents(b, state.day)
  const decision = isHero ? decideHero(state.hero, state.day) : genericDecision(b, state.day)
  const payments = isHero ? state.hero.payments : b.flags.paid ? [{ utr: '33' + b.id.slice(2) + '…' + b.loans[0].id.slice(-4), amount: b.loans[0].emi, status: 'SETTLED', loan: 'A', day: 2 }] : []
  const cases = isHero ? state.hero.cases : []
  const act = (action, title, effect, extra = {}) => setDialog({ action, title, effect, ...extra })

  return (
    <>
      <div className="mb-4 flex items-center gap-2 text-[13px] text-muted">
        <Link to="/crm" className="inline-flex items-center gap-1 hover:text-fg"><ArrowLeft size={14} /> Smart CRM</Link>
        <span>/</span><span className="text-fg">{b.id}</span>
      </div>

      <div className="grid grid-cols-[290px_minmax(0,1fr)_300px] gap-5">
        {/* LEFT · account summary */}
        <div className="space-y-4">
          <Card className="p-5">
            <div className="flex items-center gap-3">
              <div className="grid h-11 w-11 place-items-center rounded-full bg-info/15 text-[13px] font-bold text-info">{b.name.split(' ').map((p) => p[0]).join('')}</div>
              <div className="min-w-0">
                <div className="flex items-center gap-1.5 text-[16px] font-semibold">{b.name}{isHero && <Star size={13} className="fill-warn text-warn" />}</div>
                <div className="font-mono text-[12px] text-muted">{b.id}</div>
              </div>
            </div>
            <div className="mt-4 space-y-1.5 text-[13px]">
              <Row k="Phone" v={<span className="font-mono">{b.phone}</span>} />
              <Row k="Language" v={b.language} />
              <Row k="Region" v={`${b.region} · ${b.state}`} />
              <Row k="DPD" v={<Chip tone={b.dpd >= 60 ? 'bad' : b.dpd >= 30 ? 'warn' : 'neutral'}>{b.dpd} DPD · {b.band}</Chip>} />
              <Row k="Risk tag" v={RISK_TAGS[b.riskTag]} />
              <Row k="Bounce string" v={<span className="font-mono tracking-widest" title="Last 6 months, oldest first: 1 = EMI bounced">{b.bounceString}</span>} />
              <Row k="NACH · last mode" v={<span className={b.nach === 'Bounced' ? 'text-warn' : ''}>{b.nach} · {b.lastMode}</span>} />
              <Row k="Cohort" v={<Link to="/cohorts" className="hover:underline">{state.cohorts.find((c) => c.id === b.cohortId)?.name ?? '—'}</Link>} />
              <Row k="Campaign" v={<span className="text-muted">{state.campaigns.find((c) => c.id === b.campaignId)?.name ?? '—'}</span>} />
            </div>
          </Card>

          <Card className="p-5">
            <div className="mb-3 flex items-baseline justify-between"><span className="text-[13.5px] font-semibold">Consent & reach</span><span className="text-[11.5px] text-faint">sent · replied</span></div>
            <div className="space-y-2">
              {CHANNELS.map((ch) => {
                const ok = b.consent[ch] && !b.flags.stop && !b.flags.dnc
                return (
                  <div key={ch} className="flex items-center justify-between text-[13px]">
                    <ChannelTag ch={ch} muted={!ok} />
                    {ok
                      ? <span className="text-[12.5px] tabular"><b>{b.sent?.[ch] ?? 0}</b> sent · <b className={(b.replied?.[ch] ?? 0) ? 'text-ok' : ''}>{b.replied?.[ch] ?? 0}</b> replied</span>
                      : <span className="inline-flex items-center gap-1 text-[12px] text-bad"><X size={12} />{b.flags.stop || b.flags.dnc ? 'STOP' : isHero && ch === 'email' ? 'bounced' : 'no consent'}</span>}
                  </div>
                )
              })}
            </div>
          </Card>

          <Card className="p-5">
            <div className="mb-3 text-[13.5px] font-semibold">Caps & spend</div>
            <div className="mb-1.5 flex justify-between text-[12.5px]"><span className="text-muted">Contacts this week</span><span className="tabular">{b.capsUsed} / {b.capLimit}</span></div>
            <Bar value={b.capsUsed / b.capLimit} tone={b.capsUsed >= b.capLimit ? 'bad' : 'fg'} />
            <div className="mt-3 space-y-1.5 text-[13px]">
              <Row k="Next eligible" v={b.capsUsed >= b.capLimit ? `Day ${state.day + 2} · 09:00` : `Day ${state.day} · now`} />
              <Row k="Spent on borrower" v={inr(b.spend, 2)} />
            </div>
          </Card>

          <Card className="p-5">
            <div className="mb-3 text-[13.5px] font-semibold">Active flags</div>
            <div className="flex flex-wrap gap-1.5">
              {b.flags.hold && (!isHero || b.flags.hold.until > state.day) && <Chip tone="warn">Hold until Day {b.flags.hold.until}</Chip>}
              {b.flags.callback && <Chip tone="info">Callback pending</Chip>}
              {b.flags.hardship && <Chip tone="violet">Hardship</Chip>}
              {(b.flags.stop || b.flags.dnc) && <Chip tone="bad">STOP · all channels</Chip>}
              {b.flags.manualPause && <Chip tone="warn">Paused by operator</Chip>}
              {isHero && state.hero.otp === 'VERIFIED' && <Chip tone="ok">OTP verified</Chip>}
              {isHero && state.hero.otp === 'FAILED' && <Chip tone="bad">OTP failed</Chip>}
              {!(b.flags.hold && (!isHero || b.flags.hold.until > state.day)) && !b.flags.callback && !b.flags.hardship && !b.flags.stop && !b.flags.dnc && !b.flags.manualPause && !(isHero && state.hero.otp !== 'NOT_STARTED') && <span className="text-[12.5px] text-muted">None</span>}
            </div>
          </Card>
        </div>

        {/* CENTRE · tabs */}
        <div className="min-w-0">
          <div className="mb-4 flex gap-1 border-b border-line">
            {TABS.map((t) => (
              <button key={t} onClick={() => setTab(t)}
                className={cx('-mb-px border-b-2 px-3.5 py-2.5 text-[14px] font-medium', tab === t ? 'border-fg text-fg' : 'border-transparent text-muted hover:text-fg')}>{t}</button>
            ))}
          </div>

          {tab === 'Journey' && (
            <div className="space-y-4">
              <div className="grid grid-cols-2 gap-4">
                <AgentCard n="1" icon={Brain} title="Summarizer" sub="What happened · what they intend">
                  <div className="space-y-1.5 text-[13px]">{agents.summary.map((l, i) => <div key={i}><span className="mr-1.5 text-faint">{i === 0 ? 'Channels' : 'Intent'}</span>{l}</div>)}</div>
                </AgentCard>
                <AgentCard n="2" icon={Radio} title="Channel & time" sub="Where and when to reach them">
                  <div className="flex items-center gap-2 text-[14px] font-semibold"><ChannelTag ch={agents.channel.best} /> <span className="text-muted">·</span> {agents.channel.window}</div>
                  <div className="mt-1.5 text-[12.5px] text-muted">Alternatives: {agents.channel.alts.join(' · ') || '—'}</div>
                  <div className="text-[12.5px] text-muted">Evidence: {agents.channel.evidence}</div>
                </AgentCard>
                <AgentCard n="3" icon={Gauge} title="Propensity & nudge" sub="How likely to pay · how hard to push">
                  <div className="flex items-end justify-between">
                    <div><span className="text-[22px] font-semibold tabular">{pct(agents.propensity.p, 0)}</span> <span className="text-[12.5px] text-muted">verified payment in {agents.propensity.horizon}</span></div>
                    <Chip tone={agents.propensity.intensity === 'STANDARD' ? 'warn' : agents.propensity.intensity === 'WAIT' ? 'neutral' : 'ok'}>{agents.propensity.intensity}</Chip>
                  </div>
                  <div className="mt-2"><Bar value={agents.propensity.p} tone="info" /></div>
                </AgentCard>
                <AgentCard n="4" icon={FileText} title="Template" sub="Approved wording only">
                  {agents.template.id ? <>
                    <div className="font-mono text-[13px]">{agents.template.id}</div>
                    <div className="mt-1 text-[12.5px] text-muted">{agents.template.lang} · approved in registry</div>
                  </> : <div className="text-[13px] text-warn">No approved template → WAIT</div>}
                </AgentCard>
              </div>

              <Card>
                <div className="flex items-start justify-between gap-6 px-5 py-4">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2 text-[12px] font-semibold tracking-wide text-muted uppercase"><Gavel size={13} /> Aladdin's decision</div>
                    <div className="mt-2 flex items-center gap-3">
                      <span className={cx('text-[26px] font-bold tracking-tight', { SEND: 'text-ok', WAIT: 'text-warn', CANCEL: 'text-bad', HANDOFF: 'text-violet' }[decision.action])}>{decision.action}</span>
                      {decision.channel && <span className="text-[13px] text-muted"><ChannelTag ch={decision.channel} /> · {decision.when} · <span className="font-mono">{decision.template}</span></span>}
                    </div>
                    <div className="mt-1 text-[13.5px]">{decision.reason}</div>
                    <div className="mt-2 text-[12px] text-muted">Instruction v{decision.version} · expires {decision.expires}</div>
                  </div>
                  <div className="w-60 shrink-0 rounded-lg border border-line bg-bg p-3">
                    <div className="mb-1.5 text-[11.5px] font-semibold tracking-wide text-muted uppercase">Guardrail checks</div>
                    {decision.checks.map((c) => (
                      <div key={c.label} className={cx('flex items-center gap-1.5 text-[12.5px]', c.ok ? 'text-ok' : 'text-bad')}>{c.ok ? <Check size={12} /> : <X size={12} />}{c.label}</div>
                    ))}
                  </div>
                </div>
              </Card>

              <Card>
                <CardHead title="Timeline" sub="Every touch, reply, verification and decision — newest first" right={<Chip tone="warn">{state.env.replace('_', ' ')}</Chip>} />
                <Timeline events={events} />
              </Card>
            </div>
          )}

          {tab === 'Overview' && (
            <Card>
              <CardHead title="Obligations" sub="Each loan is tracked separately — clearing one never clears another" />
              <table className="w-full">
                <thead><tr><Th>Loan</Th><Th>EMI</Th><Th>Outstanding</Th><Th>DPD</Th><Th>Status</Th></tr></thead>
                <tbody>
                  {b.loans.map((l) => (
                    <tr key={l.id}>
                      <Td><span className="mr-2 text-muted">{l.label}</span><span className="font-mono">{l.id}</span></Td>
                      <Td className="tabular">{inr(l.emi)}</Td>
                      <Td className="tabular">{inr(l.outstanding)}</Td>
                      <Td>{l.dpd}</Td>
                      <Td>{l.status === 'PAID' ? <Chip tone="ok">EMI paid · verified</Chip> : <Chip tone="warn">Open</Chip>}</Td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Card>
          )}

          {tab === 'Payments' && (
            <Card>
              <CardHead title="Payments (D7)" sub="D7 and LMS are the source of truth — a screenshot is only a claim" />
              {payments.length ? (
                <table className="w-full">
                  <thead><tr><Th>UTR</Th><Th>Amount</Th><Th>Status</Th><Th>Allocated to</Th><Th>Day</Th></tr></thead>
                  <tbody>{payments.map((p, i) => (
                    <tr key={i}><Td className="font-mono">{p.utr}</Td><Td className="tabular">{inr(p.amount)}</Td>
                      <Td>{p.status === 'SETTLED' ? <Chip tone="ok">Settled</Chip> : <Chip tone="warn">Pending</Chip>}</Td>
                      <Td>Loan {p.loan} · <span className="font-mono">{b.loans.find((l) => l.label === p.loan)?.id}</span></Td><Td>Day {p.day}</Td></tr>
                  ))}</tbody>
                </table>
              ) : <Empty>No payments in D7 for this borrower yet.</Empty>}
            </Card>
          )}

          {tab === 'Cases' && (
            <Card>
              <CardHead title="Cases & documents" />
              {cases.length ? cases.map((c) => (
                <div key={c.id} className="flex items-center justify-between border-t border-line px-5 py-3 text-[13px]">
                  <div><span className="mr-2 font-mono text-muted">{c.id}</span><span className="font-semibold">{c.kind}</span> <span className="text-muted">· {c.detail}</span></div>
                  <Chip tone={c.status === 'SENT' ? 'ok' : 'warn'}>{c.status}</Chip>
                </div>
              )) : <Empty>No cases or documents.</Empty>}
            </Card>
          )}

          {tab === 'Audit' && (
            <Card>
              <CardHead title="Audit" sub="Decisions, guardrail blocks and human actions for this account" />
              <table className="w-full">
                <thead><tr><Th>Time</Th><Th>Event</Th><Th>Actor</Th><Th>Detail</Th></tr></thead>
                <tbody>{events.filter((e) => ['SUPERVISOR_DECISION', 'SEND_BLOCKED', 'HUMAN_ACTION', 'TOUCH_CANCELED', 'STOP_RECEIVED', 'D7_MISMATCH', 'INJECTION_LOGGED', 'OTP_FAILED', 'HOLD_CREATED'].includes(e.type)).map((e) => (
                  <tr key={e.id}><Td className="font-mono text-[12px] whitespace-nowrap text-muted">D{e.day} {fmtClock(e.min)}</Td><Td className="whitespace-nowrap">{EVENT_META[e.type]?.label}</Td><Td className="text-muted">{e.actor}</Td><Td>{e.detail}</Td></tr>
                ))}</tbody>
              </table>
            </Card>
          )}
        </div>

        {/* RIGHT · actions */}
        <div className="space-y-4">
          <Card className="p-5">
            <div className="text-[12px] font-semibold tracking-wide text-muted uppercase">Aladdin says</div>
            <div className="mt-2"><StatusChip s={decision.action}>{decision.action}</StatusChip></div>
            <div className="mt-2 text-[13px] text-muted">{decision.reason}</div>
          </Card>

          <Card className="p-5">
            <div className="mb-3 text-[13.5px] font-semibold">Borrower actions</div>
            <div className="grid gap-2">
              <Button icon={b.flags.manualPause ? Play : Pause} onClick={() => act('pause', b.flags.manualPause ? 'Resume contact' : 'Pause contact', b.flags.manualPause ? 'Kairos may contact this borrower again within policy.' : 'All pending sends for this borrower are held. Kairos will WAIT until you resume.')}>{b.flags.manualPause ? 'Resume contact' : 'Pause contact'}</Button>
              <Button icon={CalendarClock} onClick={() => act('hold', 'Add a hold', `Hold until Day ${state.day + 2}. Payment is re-checked before outreach resumes.`, { value: 2 })}>Hold for 2 days</Button>
              <Button icon={CircleSlash} onClick={() => act('cancel_next', 'Cancel next touch', 'The next queued send is canceled. Kairos replans at the next allowed slot.')}>Cancel next touch</Button>
              <Button icon={Hand} onClick={() => act('handoff', 'Hand off to a person', 'Ordinary outreach pauses and a case goes to the right queue with a summary.')}>Hand off to a person</Button>
              <Button icon={UserPlus} onClick={() => act('assign', 'Assign to agent', 'A named agent owns follow-ups for this account.')}>Assign to agent</Button>
              <Button variant="danger" icon={Ban} onClick={() => act('dnc', 'Add to do-not-contact', 'Every pending send on every channel is canceled. This cannot be undone by Kairos.', { danger: true })}>Do not contact</Button>
            </div>
          </Card>

          <Card className="p-5">
            <div className="mb-2 flex items-center gap-1.5 text-[13.5px] font-semibold"><Lock size={13} /> Locked by guardrails</div>
            <div className="grid gap-2">
              <Button disabled icon={ShieldCheck} title="Consent and STOP can't be overridden">Override STOP / consent</Button>
              <Button disabled icon={Wallet} title="Hard caps can't be exceeded">Exceed contact cap</Button>
            </div>
            <div className="mt-2 text-[12px] text-muted">Nobody — operator or policy owner — can override consent, STOP or a hard cap.</div>
          </Card>
        </div>
      </div>

      <ReasonDialog open={!!dialog} title={dialog?.title} effect={dialog?.effect} danger={dialog?.danger}
        onClose={() => setDialog(null)}
        onConfirm={(reason) => dispatch({ type: 'BORROWER', id: b.id, action: dialog.action, value: dialog.value, reason })} />
    </>
  )
}

function Row({ k, v }) {
  return <div className="flex items-center justify-between gap-3"><span className="text-muted">{k}</span><span className="text-right">{v}</span></div>
}

function AgentCard({ n, icon: I, title, sub, children }) {
  return (
    <Card className="p-4">
      <div className="mb-2.5 flex items-center gap-2.5">
        <span className="grid h-7 w-7 shrink-0 place-items-center rounded-md bg-raised"><I size={14} className="text-muted" /></span>
        <div className="min-w-0 leading-tight">
          <div className="text-[13.5px] font-semibold">{n} · {title}</div>
          <div className="truncate text-[12px] text-faint">{sub}</div>
        </div>
      </div>
      {children}
    </Card>
  )
}

function Timeline({ events: allEvents }) {
  const [limit, setLimit] = useState(10)
  const events = allEvents.slice(0, limit)
  if (!events.length) return <Empty>No events yet.</Empty>
  let lastDay = null
  return (
    <div className="px-5 pb-4">
      {events.map((e) => {
        const m = EVENT_META[e.type] || { label: e.type, tone: 'neutral' }
        const I = m.icon
        const header = e.day !== lastDay
        lastDay = e.day
        return (
          <div key={e.id}>
            {header && <div className="mt-3 mb-1 text-[11.5px] font-semibold tracking-wide text-faint uppercase">Day {e.day}</div>}
            <div className="flex gap-3 py-1.5">
              <div className={cx('mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-lg', TONE_BG[m.tone])}>{I && <I size={14} className={TONE_TEXT[m.tone]} />}</div>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2 text-[13px]">
                  <span className="font-semibold">{m.label}</span>
                  {e.channel && <ChannelTag ch={e.channel} muted />}
                  {e.loan && <Chip>Loan {e.loan}</Chip>}
                  {e.env === 'SIMULATED' && <Chip tone="warn">simulated</Chip>}
                  <span className="ml-auto font-mono text-[11.5px] text-faint">{fmtClock(e.min)} · {e.actor === 'supervisor' ? 'Aladdin' : e.actor}</span>
                </div>
                <div className="text-[13px] text-muted">{e.detail}</div>
              </div>
            </div>
          </div>
        )
      })}
      {allEvents.length > limit && (
        <div className="mt-2 flex items-center justify-center gap-2 border-t border-line pt-3 text-[12.5px] text-muted">
          Showing {limit} of {allEvents.length} · load
          {[10, 20, 30].map((n) => <button key={n} onClick={() => setLimit(limit + n)} className="rounded-md border border-line px-2 py-0.5 hover:text-fg">+{n}</button>)}
        </div>
      )}
    </div>
  )
}
