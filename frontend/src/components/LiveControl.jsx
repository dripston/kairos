// Control Room panels backed by the Kairos API (K06 decisions, budget/caps, HITL approvals, demo recipients).
// Each panel falls back to the mock store when the API is unreachable and says so with its badge.
import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { Check, X, Lock, ChevronDown, ChevronUp, ShieldCheck, Phone, Plus, Trash2, ExternalLink } from 'lucide-react'
import { useKairos, useDerived } from '../lib/store'
import { useApi, useApiRole, useApiStatus, useHitlPermissions } from '../api/hooks'
import { CM03_CONSOLE_URL, getRoutes, addDemoRecipient, approve, getApprovals, getAudit, getCampaign, getCapBreaches, getDemoRecipients, reject, removeDemoRecipient } from '../api/kairos'
import { ApiError, OfflineError } from '../api/client'
import { mockApprovals, mockCampaign, mockDecisions } from '../api/mocks'
import { dayOf, fmtTs, paiseInr } from '../lib/format'
import { MockNote, RefreshButton, SourceBadge } from './live'
import { Bar, Button, Card, CardHead, ChannelTag, Chip, Empty, Input, ReasonDialog, Segmented, StatusChip, Td, Th, cx } from './ui'

const ACTIONS = ['SEND', 'WAIT', 'CANCEL', 'HANDOFF']
const knownCh = (ch) => (['sms', 'whatsapp', 'email', 'ivr', 'voicebot'].includes(ch) ? ch : null)

// ---------- today's decisions ----------
export function LiveDecisions({ className }) {
  const { state } = useKairos()
  const d = useDerived()
  const status = useApiStatus()
  const res = useApi((o) => getAudit({ limit: 1000 }, o), { fallback: () => mockDecisions(state, d.allQueue), deps: [status.state], poll: 15000 })
  const [f, setF] = useState('All')
  const [all, setAll] = useState(false)
  const { rows, day } = useMemo(() => {
    const dec = (res.data || []).filter((r) => r.type === 'DECISION' && r.decision)
    if (res.source !== 'live') return { rows: dec, day: `Day ${state.day}` }
    const last = dec.reduce((m, r) => (dayOf(r.ts) > m ? dayOf(r.ts) : m), '')
    return { rows: dec.filter((r) => dayOf(r.ts) === last), day: last || '—' }
  }, [res.data, res.source, state.day])
  const counts = Object.fromEntries(ACTIONS.map((a) => [a, rows.filter((r) => r.decision === a).length]))
  const view = rows.filter((r) => f === 'All' || r.decision === f)
  const shown = all ? view : view.slice(0, 10)
  const versions = [...new Set(rows.map((r) => r.policy_version))].join(', ')
  return (
    <Card className={className}>
      <CardHead title="Supervisor decisions · today" sub={`${day} · SEND / WAIT / CANCEL / HANDOFF with the rule that fired · policy ${versions || '—'}`}
        right={<><SourceBadge res={res} /><RefreshButton onClick={res.reload} /></>} />
      <MockNote res={res} />
      <div className="mx-5 mb-3 grid grid-cols-4 overflow-hidden rounded-lg border border-line">
        {ACTIONS.map((a, i) => (
          <button key={a} onClick={() => setF(f === a ? 'All' : a)} className={cx('px-4 py-2.5 text-left', i && 'border-l border-line', f === a && 'bg-raised')}>
            <div className="text-[12px] text-muted">{a}</div>
            <div className={cx('text-[20px] font-semibold tabular', { SEND: 'text-ok', WAIT: 'text-warn', CANCEL: 'text-bad', HANDOFF: 'text-violet' }[a])}>{counts[a]}</div>
          </button>
        ))}
      </div>
      <div className="mx-5 mb-2 flex items-center gap-2">
        <Segmented size="sm" value={f} onChange={setF} options={['All', ...ACTIONS]} />
        <span className="text-[12px] text-faint">{view.length} decisions</span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full">
          <thead><tr><Th>Time</Th><Th>Borrower</Th><Th>Action</Th><Th>Channel</Th><Th>Reason · rule</Th><Th>Policy</Th></tr></thead>
          <tbody>
            {shown.map((r) => (
              <tr key={r.id}>
                <Td className="font-mono text-[12px] whitespace-nowrap text-muted">{res.source === 'live' ? fmtTs(r.ts) : r.ts}</Td>
                <Td className="whitespace-nowrap">{r.borrower_id ? <Link to={`/crm/${r.borrower_id}`} className="font-mono text-[12px] hover:underline">{r.borrower_id}</Link> : '—'}</Td>
                <Td><StatusChip s={r.decision}>{r.decision}</StatusChip></Td>
                <Td className="whitespace-nowrap"><ChannelTag ch={knownCh(r.detail?.channel)} muted /></Td>
                <Td className="text-[12.5px]"><div className="line-clamp-2" title={r.reason}>{r.reason}</div><div className="font-mono text-[11px] text-faint">{r.rule}</div></Td>
                <Td className="font-mono text-[12px] text-muted">v{r.policy_version}</Td>
              </tr>
            ))}
          </tbody>
        </table>
        {!shown.length && <Empty>{res.loading ? 'Loading…' : 'No decisions yet.'}</Empty>}
      </div>
      {view.length > 10 && (
        <button onClick={() => setAll(!all)} className="flex w-full items-center justify-center gap-1 border-t border-line py-2.5 text-[12.5px] text-muted hover:text-fg">
          {all ? <><ChevronUp size={14} /> Show less</> : <><ChevronDown size={14} /> Show all {view.length}</>}
        </button>
      )}
    </Card>
  )
}

// ---------- budget, caps, cap breaches ----------
export function BudgetCaps({ campId, className }) {
  const { state } = useKairos()
  const d = useDerived()
  const status = useApiStatus()
  const camp = useApi((o) => getCampaign(campId, o), { fallback: () => mockCampaign(state, d, campId), deps: [campId, status.state], poll: 15000 })
  const br = useApi((o) => getCapBreaches(o), { fallback: () => ({ count: 0, rows: [] }), deps: [status.state], poll: 15000 })
  const c = camp.data
  const today = c?.days?.length ? c.days[c.days.length - 1] : null
  const poolUse = c && c.pool_paise ? c.committed_paise / c.pool_paise : 0
  const dayUse = today && today.allowance_paise ? today.committed_paise / today.allowance_paise : 0
  const n = br.data?.count ?? 0
  return (
    <Card className={className}>
      <CardHead title="Budget & caps" sub={c ? `${c.id} · ${c.name || 'campaign'} · ${c.allocated_cases} cases` : campId}
        right={<><SourceBadge res={camp} /><RefreshButton onClick={() => { camp.reload(); br.reload() }} /></>} />
      <MockNote res={camp}>{camp.offline ? '' : `Campaign ${campId} is not in the backend; set VITE_KAIROS_CAMPAIGN.`}</MockNote>
      {c && (
        <div className="space-y-4 px-5 pb-5">
          <div>
            <div className="mb-1.5 flex justify-between text-[12.5px]"><span className="text-muted">Campaign pool committed</span><span className="tabular">{paiseInr(c.committed_paise)} <span className="text-muted">/ {paiseInr(c.pool_paise, 0)}</span></span></div>
            <Bar value={poolUse} tone={poolUse > 0.95 ? 'bad' : 'fg'} />
          </div>
          {today && (
            <div>
              <div className="mb-1.5 flex justify-between text-[12.5px]"><span className="text-muted">Daily allowance · {today.day}</span><span className="tabular">{paiseInr(today.committed_paise)} <span className="text-muted">/ {paiseInr(today.allowance_paise)}</span></span></div>
              <Bar value={dayUse} tone={dayUse > 1 ? 'bad' : dayUse > 0.85 ? 'warn' : 'ok'} />
              <div className="mt-1 text-[11.5px] text-faint">Daily pacing is a hard limit: a send that would exceed it is a WAIT, never a breach.</div>
            </div>
          )}
          <div className="grid grid-cols-2 gap-3">
            <div className="rounded-lg border border-line p-3"><div className="text-[12px] text-muted">Verified collected</div><div className="text-[18px] font-semibold tabular">{paiseInr(c.collected_paise, 0)}</div><div className="text-[11.5px] text-faint">D7/LMS verified only</div></div>
            <div className={cx('rounded-lg border p-3', n ? 'border-bad/30 bg-bad/10' : 'border-ok/20 bg-ok/5')}>
              <div className="text-[12px] text-muted" title={br.source === 'live' ? 'GET /cap-breaches' : 'mock (API offline)'}>Cap breaches{br.source !== 'live' && ' · mock'}</div>
              <div className={cx('text-[18px] font-semibold tabular', n ? 'text-bad' : 'text-ok')}>{n}</div>
              <div className="text-[11.5px] text-faint">Independent recount of every live touch</div>
            </div>
          </div>
          {n > 0 && (
            <div className="rounded-lg border border-bad/30 p-2 text-[12px]">
              {br.data.rows.slice(0, 5).map((r, i) => <div key={i} className="font-mono text-bad">{r.borrower_id} · {r.day} · {r.channel} · {r.n}/{r.cap} ({r.band})</div>)}
            </div>
          )}
          <div className="flex items-start gap-2 rounded-lg border border-ok/20 bg-ok/5 p-3 text-[12px] text-muted">
            <ShieldCheck size={14} className="mt-0.5 shrink-0 text-ok" />
            Caps and budget are checked at approval (reserve) and again at send time.
          </div>
        </div>
      )}
    </Card>
  )
}

// ---------- HITL approvals queue ----------
const RULE_TEXT = { VOICE_CHANNEL: 'Voice call', STANDARD_INTENSITY_HIGH_DPD: 'Standard intensity at high DPD', MANUAL_DISPATCH: 'Manual send' }

export function ApprovalsQueue({ className }) {
  const { state, dispatch } = useKairos()
  const d = useDerived()
  const { role, actor, label } = useApiRole()
  const status = useApiStatus()
  const perms = useHitlPermissions()
  const res = useApi((o) => getApprovals({ status: 'PENDING' }, role, o), { fallback: () => mockApprovals(state, d.allQueue), deps: [role, status.state, state.heroOverrides, state.queue], poll: 15000 })
  const [dialog, setDialog] = useState(null)
  const [busy, setBusy] = useState(null)
  const rows = res.data || []
  const canApprove = perms.has('approve')
  const canReject = perms.has('reject')

  const run = async (row, verb, reason) => {
    if (res.source !== 'live') {
      dispatch({ type: 'QUEUE', ids: [row.instruction_id], action: verb === 'approve' ? 'approve' : 'cancel', reason })
      return
    }
    setBusy(row.instruction_id)
    try {
      const out = await (verb === 'approve' ? approve : reject)(row.instruction_id, { actor, reason }, role)
      dispatch({ type: 'TOAST', text: `${verb === 'approve' ? 'Approved' : 'Rejected'} ${row.instruction_id} · ${out?.status || out?.outcome || 'done'} (as ${label})`, tone: verb === 'approve' ? 'ok' : 'warn' })
    } catch (e) {
      const msg = e instanceof OfflineError ? 'Kairos API unreachable' : e instanceof ApiError ? `${e.status} ${e.code ? `${e.code}: ` : ''}${e.message}` : String(e)
      dispatch({ type: 'TOAST', text: `${verb === 'approve' ? 'Approve' : 'Reject'} refused · ${msg}`, tone: 'bad' })
    } finally {
      setBusy(null)
      res.reload()
    }
  }

  return (
    <Card className={className}>
      <CardHead title="Approvals queue · human in the loop" sub={`Sends held as PENDING_APPROVAL until a person decides · acting as ${label} (X-Kairos-Role: ${role})`}
        right={<><SourceBadge res={res} /><RefreshButton onClick={res.reload} /></>} />
      <MockNote res={res} />
      {!canApprove && <div className="mx-5 mb-3 flex items-center gap-2 text-[12px] text-muted"><Lock size={12} /> Approving needs the Supervisor or Policy owner role — switch in the sidebar. Operators may reject.</div>}
      <div className="overflow-x-auto">
        <table className="w-full">
          <thead><tr><Th>Borrower</Th><Th>Channel · when</Th><Th>Why a person</Th><Th>Cost · expires</Th><Th className="text-right">Decide</Th></tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.instruction_id}>
                <Td className="whitespace-nowrap"><Link to={`/crm/${r.borrower_id}`} className="font-mono text-[12.5px] font-semibold hover:underline">{r.borrower_id}</Link>
                  <div className="font-mono text-[11px] text-muted">{r.loan_id} · {r.phone}</div></Td>
                <Td className="whitespace-nowrap"><ChannelTag ch={knownCh(r.channel)} /><div className="text-[12px] text-muted">{res.source === 'live' ? fmtTs(r.scheduled_at) : r.scheduled_at} · <span className="font-mono">{r.template_id}</span></div></Td>
                <Td><div className="flex flex-wrap gap-1">{(r.rules || []).map((x) => <Chip key={x} tone="violet">{RULE_TEXT[x] || x}</Chip>)}</div></Td>
                <Td className="whitespace-nowrap"><span className="tabular">{paiseInr(r.est_cost_paise)}</span><div className="font-mono text-[11.5px] text-muted">{res.source === 'live' ? fmtTs(r.expires_at) : r.expires_at}</div></Td>
                <Td className="text-right whitespace-nowrap">
                  <div className="inline-flex gap-1.5">
                    <Button size="sm" icon={canApprove ? Check : Lock} disabled={!canApprove || busy === r.instruction_id} title={canApprove ? 'Approve — the Gate runs again now and at send time' : 'Needs Supervisor or Policy owner'}
                      onClick={() => setDialog({ row: r, verb: 'approve' })}>Approve</Button>
                    <Button size="sm" variant="danger" icon={X} title="Reject" disabled={!canReject || busy === r.instruction_id} onClick={() => setDialog({ row: r, verb: 'reject' })} />
                  </div>
                </Td>
              </tr>
            ))}
          </tbody>
        </table>
        {!rows.length && <Empty>{res.loading ? 'Loading…' : 'Nothing waiting for approval.'}</Empty>}
      </div>
      <ReasonDialog open={!!dialog} title={dialog ? `${dialog.verb === 'approve' ? 'Approve' : 'Reject'} ${dialog.row.instruction_id}` : ''}
        danger={dialog?.verb === 'reject'} confirmLabel={dialog?.verb === 'approve' ? 'Approve' : 'Reject'}
        effect={dialog?.verb === 'approve' ? 'The Gate re-checks consent, caps, budget and window now; the send still passes every guardrail again at send time.' : 'The held send is canceled and its reserved budget released.'}
        onClose={() => setDialog(null)} onConfirm={(reason) => run(dialog.row, dialog.verb, reason)} />
    </Card>
  )
}

// ---------- demo recipients (phone-demo allow-list; numbers never shown in full) ----------
export function DemoRecipients({ className }) {
  const { dispatch } = useKairos()
  const status = useApiStatus()
  const res = useApi((o) => getDemoRecipients(o), { fallback: () => ({ recipients: [], note: null }), deps: [status.state] })
  const [num, setNum] = useState('')
  const [removing, setRemoving] = useState(null) // masked entry being removed
  const [confirmNum, setConfirmNum] = useState('')
  const [busy, setBusy] = useState(false)
  const live = res.source === 'live'
  const list = res.data?.recipients || []

  const call = async (fn, okText) => {
    setBusy(true)
    try {
      const out = await fn()
      dispatch({ type: 'TOAST', text: okText(out), tone: 'ok' })
    } catch (e) {
      dispatch({ type: 'TOAST', text: e instanceof OfflineError ? 'Kairos API unreachable — nothing changed' : `Refused · ${e.message}`, tone: 'bad' })
    } finally {
      setBusy(false)
      setNum('')
      setConfirmNum('')
      setRemoving(null)
      res.reload()
    }
  }
  const add = (e) => { e.preventDefault(); if (num.trim()) call(() => addDemoRecipient(num.trim()), (o) => `Added ${o?.added ?? 'number'} to demo recipients`) }
  const remove = (e) => {
    e.preventDefault()
    if (!confirmNum.trim()) return
    call(() => removeDemoRecipient(confirmNum.trim()), (o) => (o?.removed ? `Removed ${removing}` : 'That number was not on the list'))
  }

  return (
    <Card className={className}>
      <CardHead title="Demo recipients" sub="Team phones the phone-demo adapters may message · stored only on the backend machine · always masked"
        right={<><SourceBadge res={res} /><RefreshButton onClick={res.reload} /></>} />
      <div className="space-y-3 px-5 pb-5">
        <div className="rounded-lg border border-warn/25 bg-warn/5 px-3 py-2 text-[12px] text-warn">
          Sending is dry-run unless enabled on the backend (KAIROS_DEMO_SMS / KAIROS_DEMO_WHATSAPP = 1, an approved template and dry_run off). Being listed never sends anything by itself.
        </div>
        {!live && <div className="text-[12.5px] text-muted">{res.loading ? 'Loading…' : 'The recipient list lives on the backend only — the API is unreachable, so nothing is shown and nothing is stored in the browser.'}</div>}
        {live && (
          <div className="rounded-lg border border-line">
            {list.length ? list.map((m) => (
              <div key={m} className="flex items-center gap-3 border-t border-line px-3 py-2 first:border-t-0">
                <Phone size={13} className="text-muted" />
                <span className="flex-1 font-mono text-[13px]">{m}</span>
                {removing === m ? (
                  <form onSubmit={remove} className="flex items-center gap-1.5">
                    <div className="w-44"><Input type="tel" autoComplete="off" autoFocus value={confirmNum} onChange={(e) => setConfirmNum(e.target.value)} placeholder="Re-type full number" className="h-7 text-[12.5px]" /></div>
                    <Button size="sm" variant="danger" type="submit" disabled={busy}>Remove</Button>
                    <Button size="sm" variant="ghost" type="button" onClick={() => { setRemoving(null); setConfirmNum('') }}>Cancel</Button>
                  </form>
                ) : <Button size="sm" variant="ghost" icon={Trash2} onClick={() => { setRemoving(m); setConfirmNum('') }}>Remove</Button>}
              </div>
            )) : <div className="px-3 py-3 text-[12.5px] text-muted">No demo recipients. Sends to any number stay blocked.</div>}
          </div>
        )}
        {live && (
          <form onSubmit={add} className="flex items-center gap-2">
            <Input type="tel" autoComplete="off" value={num} onChange={(e) => setNum(e.target.value)} placeholder="Add a team phone (+91 …)" />
            <Button type="submit" icon={Plus} disabled={busy || !num.trim()}>Add</Button>
          </form>
        )}
        {live && <div className="text-[11.5px] text-faint">The list only ever comes back masked, so removing asks you to re-type the number. Inputs are cleared after each call and never logged.</div>}
      </div>
    </Card>
  )
}

// ---------- CM-03 WhatsApp servicing console (cm03/whatsapp-servicing) ----------
export function Cm03Console({ className }) {
  const status = useApiStatus()
  const res = useApi((o) => getRoutes(o), { deps: [status.state], enabled: status.state === 'online' })
  const served = res.source === 'live' && res.data?.has('/cm03/console')
  return (
    <Card className={className}>
      <CardHead title="CM-03 · WhatsApp servicing console" sub="Inbound servicing: OTP before any disclosure, D7 check of every payment claim, fraud flags"
        right={served ? <a href={CM03_CONSOLE_URL} target="_blank" rel="noreferrer"><Button size="sm" icon={ExternalLink}>Open</Button></a> : <Chip tone="warn">{status.state === 'online' ? 'Not on this backend' : 'SIMULATED · offline'}</Chip>} />
      {served ? (
        <iframe title="CM-03 console" src={CM03_CONSOLE_URL} className="mx-5 mb-5 h-[480px] w-[calc(100%-2.5rem)] rounded-lg border border-line bg-bg" />
      ) : (
        <div className="px-5 pb-5 text-[12.5px] text-muted">
          {status.state === 'online' ? 'The running backend does not serve /cm03/console yet (it is being built on cm03/whatsapp-servicing). CM-03 events still appear in the live feed when they are written to the audit.' : 'Kairos API unreachable. The live feed shows simulated CM-03 messages, OTP state and fraud flags.'}
        </div>
      )}
    </Card>
  )
}
