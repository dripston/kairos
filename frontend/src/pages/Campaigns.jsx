import { useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { Plus, Play, Pause, Square, Database, FileSpreadsheet, Users, Check, RefreshCw, Upload, Lock, ArrowUpRight } from 'lucide-react'
import { useKairos } from '../lib/store'
import { BANDS, CHANNEL_META, SERIES, TEMPLATES, sumTo } from '../lib/mock'
import {
  Button, Card, ChannelIcons, ChannelTag, Chip, Input, Modal, PageHeader, ReasonDialog, Segmented, StatusChip, Td, Th, Toggle, cx, inr,
} from '../components/ui'

export default function Campaigns() {
  const { state, dispatch } = useKairos()
  const [tab, setTab] = useState('All')
  const [open, setOpen] = useState(() => new URLSearchParams(window.location.search).has('new'))
  const [dialog, setDialog] = useState(null)
  const tabs = ['All', 'RUNNING', 'SCHEDULED', 'PAUSED', 'DRAFT', 'STOPPED']
  const list = state.campaigns.filter((c) => tab === 'All' || c.status === tab)
  const count = (s) => state.campaigns.filter((c) => s === 'All' || c.status === s).length

  return (
    <>
      <PageHeader title="Campaign Management" sub="Choose who to reach, on which channels, within what budget. Audiences come from Smart CRM."
        right={<Button variant="primary" size="lg" icon={Plus} onClick={() => setOpen(true)}>New campaign</Button>} />
      <div className="mb-5">
        <Segmented value={tab} onChange={setTab} options={tabs.map((t) => ({ value: t, label: `${t === 'All' ? 'All' : t[0] + t.slice(1).toLowerCase()} ${count(t)}` }))} />
      </div>
      <div className="grid grid-cols-3 gap-4">
        {list.map((c) => {
          const live = c.id === 'C-01' || c.id === 'C-02'
          const series = c.id === 'C-01' ? SERIES.adaptive : SERIES.fixed
          const spent = live ? sumTo(series.spend, state.day) : 0
          const rpr = live ? sumTo(series.replies, state.day) / spent : null
          return (
            <Card key={c.id} className="flex flex-col">
              <div className="flex-1 px-5 pt-4 pb-4">
                <div className="flex items-start justify-between gap-3">
                  <div className="text-[15.5px] font-semibold">{c.name}</div>
                  <StatusChip s={c.status} />
                </div>
                <div className="mt-1 line-clamp-2 text-[13px] text-muted">{c.purpose}</div>
                <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-[12.5px] text-muted">
                  <span className="inline-flex items-center gap-1.5">{c.source === 'CRM' ? <Database size={13} /> : <FileSpreadsheet size={13} />}{c.source}</span>
                  <span className="inline-flex items-center gap-1.5"><Users size={13} />{c.enrolled} enrolled</span>
                  <ChannelIcons channels={c.channels} />
                  <Chip tone={c.mode === 'ADAPTIVE' ? 'info' : c.mode === 'SHADOW' ? 'violet' : 'neutral'}>{c.mode[0] + c.mode.slice(1).toLowerCase()}</Chip>
                </div>
                <div className="mt-2.5 flex flex-wrap items-center gap-1.5 text-[12px]">
                  <span className="text-faint">Cohorts</span>
                  {c.cohortIds.length ? c.cohortIds.map((id) => <Chip key={id}>{state.cohorts.find((k) => k.id === id)?.name ?? id}</Chip>) : <span className="text-warn">none — attach in Cohorts</span>}
                </div>
                <div className="mt-3.5">
                  <div className="mb-1 flex justify-between text-[12px] text-muted">
                    <span>Budget {inr(spent)} / {inr(c.totalBudget)}</span>
                    <span>{rpr ? `${rpr.toFixed(3)} replies/₹` : `Day ${c.startDay}–${c.endDay}`}</span>
                  </div>
                  <div className="h-1.5 overflow-hidden rounded-full bg-raised"><div className="h-full rounded-full bg-fg/80" style={{ width: `${(spent / c.totalBudget) * 100}%` }} /></div>
                </div>
              </div>
              <div className="flex items-center gap-2 border-t border-line px-5 py-3">
                {c.status === 'RUNNING' && <Button size="sm" icon={Pause} onClick={() => setDialog({ c, action: 'pause', title: `Pause "${c.name}"`, effect: 'Pending sends are held and the audience is kept.' })}>Pause</Button>}
                {(c.status === 'PAUSED') && <Button size="sm" icon={Play} onClick={() => dispatch({ type: 'CAMPAIGN', id: c.id, action: 'resume', reason: 'Operator decision' })}>Resume</Button>}
                {(c.status === 'SCHEDULED' || c.status === 'DRAFT') && <Button size="sm" icon={Play} onClick={() => dispatch({ type: 'CAMPAIGN', id: c.id, action: 'start', reason: 'Operator decision' })}>Start</Button>}
                {['RUNNING', 'PAUSED', 'SCHEDULED'].includes(c.status) && <Button size="sm" icon={Square} onClick={() => setDialog({ c, action: 'stop', danger: true, title: `Stop "${c.name}"`, effect: 'Every pending reservation is canceled and its budget released.' })}>Stop</Button>}
                <div className="flex-1" />
                {!live && <span className="mr-2 text-[12.5px] text-muted">{c.status === 'PAUSED' ? 'Held — audience kept' : c.status === 'SCHEDULED' ? `Starts Day ${c.startDay}` : c.status === 'STOPPED' ? 'Stopped' : 'Not started'}</span>}
                <Link to={`/control?campaign=${c.id}`} className="inline-flex items-center gap-1 text-[12.5px] text-muted hover:text-fg">Control Room <ArrowUpRight size={13} /></Link>
              </div>
            </Card>
          )
        })}
      </div>
      {open && <NewCampaign onClose={() => setOpen(false)} />}
      <ReasonDialog open={!!dialog} title={dialog?.title} effect={dialog?.effect} danger={dialog?.danger} confirmLabel={dialog?.action === 'stop' ? 'Stop' : 'Pause'}
        onClose={() => setDialog(null)} onConfirm={(reason) => dispatch({ type: 'CAMPAIGN', id: dialog.c.id, action: dialog.action, reason })} />
    </>
  )
}

const STEPS = [
  ['Basics', 'Name and purpose'],
  ['Audience', 'Cohorts from Smart CRM, or a CSV'],
  ['Exclusions', 'DNC, consent, frequency'],
  ['Channels & templates', 'Where and what to say'],
  ['Budget & learning', 'Spend and policy mode'],
  ['Done rules', 'Goal and give-up rules'],
  ['Cohort', 'Who is in, person by person'],
  ['Review', 'Schedule and arm'],
]

function NewCampaign({ onClose }) {
  const { state, dispatch } = useKairos()
  const [step, setStep] = useState(() => Number(new URLSearchParams(window.location.search).get('step') || 0))
  const [f, setF] = useState({
    name: '', purpose: '', source: 'CRM', cohorts: new Set(['C2', 'C3']), bands: new Set(['1–29', '30–59', '60–89', '90+']), langs: 'All', minOut: 0,
    csv: null, ex: { hold: true, other: true, stale: true },
    capDay: 1, capWeek: 3, quiet: '20:00–08:00', gap: 24,
    channels: { sms: true, whatsapp: true, email: false, ivr: false, voicebot: false },
    daily: 120, total: 1200, mode: 'ADAPTIVE', paidDone: true, promiseHold: true, giveUp: 6,
    startDay: state.day + 1, endDay: state.day + 10,
  })
  const set = (patch) => setF((x) => ({ ...x, ...patch }))
  const fileRef = useRef()
  const [synced, setSynced] = useState(0)

  const pool = useMemo(() => [state.hero, ...state.borrowers], [state])
  const { accepted, excluded } = useMemo(() => {
    const acc = []
    const exc = []
    const enabled = Object.entries(f.channels).filter(([, v]) => v).map(([k]) => k)
    pool.forEach((b) => {
      if (!f.cohorts.has(b.cohortId)) return
      if (!f.bands.has(b.band)) return
      if (f.langs !== 'All' && b.language !== f.langs) return
      if (b.loans.reduce((a, l) => a + l.outstanding, 0) < f.minOut) return
      let reason = null
      if (b.flags.stop || b.flags.dnc) reason = 'STOP / opted out'
      else if (b.flags.hardship) reason = 'Hardship'
      else if (b.loans.every((l) => l.status === 'PAID')) reason = 'Already paid (D7 verified)'
      else if (!enabled.some((c) => b.consent[c])) reason = 'No consent on enabled channels'
      else if (f.ex.hold && b.flags.hold && !b.hero) reason = 'Active promise-to-pay hold'
      else if (f.ex.other && b.id.endsWith('7')) reason = 'Already in another campaign'
      else if (f.ex.stale && b.id.endsWith('13')) reason = 'Missing or stale DPD'
      reason ? exc.push({ ...b, reason }) : acc.push(b)
    })
    return { accepted: acc, excluded: exc }
  }, [pool, f])
  const csvExtra = f.source === 'CSV' && f.csv ? f.csv.added : 0
  const audience = accepted.length + csvExtra
  const enabledCh = Object.entries(f.channels).filter(([, v]) => v).map(([k]) => k)
  const ready = f.name.trim() && audience > 0 && enabledCh.length > 0

  const onCsv = async (file) => {
    if (!file) return
    const text = await file.text()
    const lines = text.split(/\r?\n/).filter((l) => l.trim())
    const rows = Math.max(0, lines.length - 1)
    const ids = new Set(pool.flatMap((b) => [b.id, ...b.loans.map((l) => l.id)]))
    const dupes = lines.slice(1).filter((l) => l.split(',').some((c) => ids.has(c.trim().replace(/"/g, '')))).length
    set({ csv: { name: file.name, rows, dupes, added: Math.max(0, rows - dupes) } })
  }

  const create = () => {
    const id = 'C-' + String(state.campaigns.length + 1).padStart(2, '0')
    dispatch({ type: 'CREATE_CAMPAIGN', campaign: {
      id, name: f.name.trim(), purpose: f.purpose || 'Created in Kairos Campaign Management.', status: f.startDay <= state.day ? 'RUNNING' : 'SCHEDULED',
      cohortIds: [...f.cohorts], source: f.source, enrolled: audience, excluded: excluded.length, mode: f.mode, channels: f.channels,
      dailyBudget: f.daily, totalBudget: f.total, startDay: f.startDay, endDay: f.endDay, caps: { day: f.capDay, week: f.capWeek }, frozen: false,
    } })
    onClose()
  }

  const toggleCohort = (id) => { const n = new Set(f.cohorts); n.has(id) ? n.delete(id) : n.add(id); set({ cohorts: n }) }
  const toggleBand = (b) => { const n = new Set(f.bands); n.has(b) ? n.delete(b) : n.add(b); set({ bands: n }) }

  return (
    <Modal open onClose={onClose} title="New campaign" width="max-w-6xl"
      right={<span className="text-[13px] text-muted">{audience ? <><b className="text-fg">{audience}</b> in audience · {excluded.length} excluded</> : 'No audience yet'}</span>}
      footer={<>
        <span className="text-[13px] text-muted">{ready ? <span className="text-ok">Ready to arm</span> : 'Not ready yet'}</span>
        <div className="flex gap-2">
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          {step < STEPS.length - 1 && <Button onClick={() => setStep(step + 1)}>Next</Button>}
          <Button variant="primary" icon={Check} disabled={!ready} onClick={create}>Create & arm</Button>
        </div>
      </>}>
      <div className="grid min-h-[560px] grid-cols-[260px_1fr]">
        <div className="border-r border-line p-3">
          {STEPS.map(([t, h], i) => (
            <button key={t} onClick={() => setStep(i)} className={cx('mb-1 w-full rounded-lg px-3 py-2.5 text-left', step === i ? 'bg-raised' : 'hover:bg-raised/50')}>
              <div className={cx('text-[14px] font-medium', step === i ? 'text-fg' : 'text-fg/85')}>{t}</div>
              <div className="text-[12.5px] text-muted">{h}</div>
            </button>
          ))}
        </div>

        <div className="space-y-5 p-7">
          {step === 0 && <>
            <Field label="Campaign name"><Input value={f.name} onChange={(e) => set({ name: e.target.value })} placeholder="e.g. December mid-DPD recovery" /></Field>
            <Field label="What is this campaign for?">
              <textarea value={f.purpose} onChange={(e) => set({ purpose: e.target.value })} rows={3} placeholder="One line, so the team knows why it exists."
                className="w-full rounded-lg border border-line bg-bg px-3 py-2.5 text-[13.5px] outline-none placeholder:text-faint focus:border-line-strong" />
            </Field>
          </>}

          {step === 1 && <>
            <Segmented value={f.source} onChange={(v) => set({ source: v })} options={[{ value: 'CRM', label: 'Smart CRM (default)' }, { value: 'CSV', label: 'Upload CSV (optional)' }]} />
            <div className="flex items-center justify-between rounded-lg border border-line bg-bg px-4 py-3">
              <div className="flex items-center gap-2.5 text-[13.5px]"><Database size={16} className="text-info" /> Fetched automatically from Smart CRM · <span className="text-muted">last synced {synced ? 'just now' : '4 min ago'}</span></div>
              <Button size="sm" icon={RefreshCw} onClick={() => setSynced(synced + 1)}>Re-sync</Button>
            </div>
            <Field label="Cohorts — which slices of the portfolio this campaign serves">
              <div className="flex flex-wrap gap-2">{[...state.cohorts].sort((a, b) => a.priority - b.priority).map((c) => {
                const n = pool.filter((b) => b.cohortId === c.id).length
                return <button key={c.id} onClick={() => toggleCohort(c.id)} className={cx('rounded-lg border px-3 py-1.5 text-[13px]', f.cohorts.has(c.id) ? 'border-fg bg-fg text-bg' : 'border-line text-muted')}>{c.name} <span className="opacity-60">· {n}</span></button>
              })}</div>
            </Field>
            <Field label="Refine by DPD band (optional)">
              <div className="flex gap-2">{BANDS.map((b) => (
                <button key={b} onClick={() => toggleBand(b)} className={cx('rounded-lg border px-3.5 py-1.5 text-[13px]', f.bands.has(b) ? 'border-fg bg-fg text-bg' : 'border-line text-muted')}>{b} DPD</button>
              ))}</div>
            </Field>
            <div className="grid grid-cols-2 gap-4">
              <Field label="Language">
                <select value={f.langs} onChange={(e) => set({ langs: e.target.value })} className="h-9 w-full rounded-lg border border-line bg-bg px-3 text-[13px]">
                  {['All', 'Hindi', 'English', 'Marathi', 'Tamil', 'Telugu', 'Kannada', 'Bengali'].map((l) => <option key={l}>{l}</option>)}
                </select>
              </Field>
              <Field label="Minimum outstanding (₹)"><Input type="number" value={f.minOut} onChange={(e) => set({ minOut: Number(e.target.value) })} /></Field>
            </div>
            <div className="rounded-lg border border-line bg-raised/40 px-4 py-3 text-[14px]"><b>{accepted.length}</b> borrowers match in Smart CRM <span className="text-muted">· {excluded.length} will be excluded (see Cohort)</span></div>
            {f.source === 'CSV' && (
              <div className="rounded-lg border border-dashed border-line-strong p-5 text-center">
                <Upload size={20} className="mx-auto text-muted" />
                <div className="mt-2 text-[13.5px]">Add accounts from a CSV — rows are merged into Smart CRM and de-duplicated by loan ID.</div>
                <input ref={fileRef} type="file" accept=".csv" className="hidden" onChange={(e) => onCsv(e.target.files?.[0])} />
                <Button size="sm" className="mt-3" onClick={() => fileRef.current?.click()}>Choose CSV</Button>
                {f.csv && <div className="mt-3 text-[13px]"><b>{f.csv.name}</b> · {f.csv.rows} rows · <span className="text-warn">{f.csv.dupes} duplicates skipped</span> · <span className="text-ok">{f.csv.added} new merged</span></div>}
              </div>
            )}
          </>}

          {step === 2 && <>
            <div className="text-[13.5px] text-muted">Who never gets contacted. Locked rules are hard guardrails.</div>
            <div className="space-y-2">
              {[['STOP / opted out — on every channel', true], ['Hardship flagged', true], ['Already paid (D7 verified)', true], ['No consent for the channel', true]].map(([l]) => (
                <div key={l} className="flex items-center justify-between rounded-lg border border-line px-4 py-2.5 text-[13.5px]"><span>{l}</span><span className="inline-flex items-center gap-1 text-[12px] text-muted"><Lock size={12} /> Always</span></div>
              ))}
              {[['hold', 'Active promise-to-pay hold'], ['other', 'Already in another running campaign'], ['stale', 'Missing or stale DPD']].map(([k, l]) => (
                <div key={k} className="flex items-center justify-between rounded-lg border border-line px-4 py-2.5 text-[13.5px]"><span>{l}</span><Toggle on={f.ex[k]} onChange={() => set({ ex: { ...f.ex, [k]: !f.ex[k] } })} /></div>
              ))}
            </div>
            <div className="grid grid-cols-4 gap-3">
              <Field label="Contacts / day"><Input type="number" min={1} max={2} value={f.capDay} onChange={(e) => set({ capDay: Number(e.target.value) })} /></Field>
              <Field label="Contacts / week"><Input type="number" min={1} max={4} value={f.capWeek} onChange={(e) => set({ capWeek: Number(e.target.value) })} /></Field>
              <Field label="Quiet hours"><Input value={f.quiet} onChange={(e) => set({ quiet: e.target.value })} /></Field>
              <Field label="Min gap (hours)"><Input type="number" value={f.gap} onChange={(e) => set({ gap: Number(e.target.value) })} /></Field>
            </div>
            <div className="text-[12px] text-faint">Caps apply across all channels combined, per borrower. Policy v3 (approved).</div>
          </>}

          {step === 3 && <>
            <div className="grid grid-cols-2 gap-3">
              {Object.keys(CHANNEL_META).map((ch) => (
                <div key={ch} className="flex items-center justify-between rounded-lg border border-line px-4 py-3">
                  <div><ChannelTag ch={ch} /><div className="text-[12px] text-muted">₹{CHANNEL_META[ch].cost.toFixed(2)} per attempt</div></div>
                  <Toggle on={f.channels[ch]} onChange={() => set({ channels: { ...f.channels, [ch]: !f.channels[ch] } })} />
                </div>
              ))}
            </div>
            <Card>
              <table className="w-full">
                <thead><tr><Th>Approved template</Th><Th>Channel</Th><Th>Language</Th><Th>Intensity</Th><Th>Purpose</Th></tr></thead>
                <tbody>{TEMPLATES.filter((t) => f.channels[t.channel]).map((t) => (
                  <tr key={t.id}><Td className="font-mono text-[12px]">{t.id}</Td><Td><ChannelTag ch={t.channel} /></Td><Td>{t.lang}</Td><Td><Chip>{t.intensity}</Chip></Td><Td className="text-muted">{t.purpose}</Td></tr>
                ))}</tbody>
              </table>
            </Card>
            <div className="inline-flex items-center gap-1.5 text-[12px] text-faint"><Lock size={12} /> Read-only from the template registry. Kairos never writes new templates; with no approved match it waits.</div>
          </>}

          {step === 4 && <>
            <div className="grid grid-cols-2 gap-4">
              <Field label="Daily budget (₹)"><Input type="number" value={f.daily} onChange={(e) => set({ daily: Number(e.target.value) })} /></Field>
              <Field label="Campaign budget (₹)"><Input type="number" value={f.total} onChange={(e) => set({ total: Number(e.target.value) })} /></Field>
            </div>
            <Field label="Policy mode">
              <div className="grid grid-cols-3 gap-3">
                {[['ADAPTIVE', 'Adaptive (Kairos)', 'Learns channel and time per borrower; ranks by replies per ₹; waits below threshold.'], ['FIXED', 'Fixed cadence', 'The baseline schedule — useful as a benchmark.'], ['SHADOW', 'Shadow', 'Kairos recommends, fixed cadence sends. Compare without risk.']].map(([v, t, d]) => (
                  <button key={v} onClick={() => set({ mode: v })} className={cx('rounded-lg border p-3.5 text-left', f.mode === v ? 'border-fg bg-raised' : 'border-line hover:bg-raised/50')}>
                    <div className="text-[13.5px] font-semibold">{t}</div><div className="mt-1 text-[12px] text-muted">{d}</div>
                  </button>
                ))}
              </div>
            </Field>
            <div className="rounded-lg border border-line bg-bg px-4 py-3 text-[13px] text-muted">Spend is reserved before each send and reconciled once against the actual charge. Parallel workers can never overspend the budget.</div>
          </>}

          {step === 5 && <div className="space-y-2">
            <div className="flex items-center justify-between rounded-lg border border-line px-4 py-3 text-[13.5px]"><span>Verified payment (D7 settled) completes the borrower for that loan</span><Toggle on={f.paidDone} onChange={() => set({ paidDone: !f.paidDone })} /></div>
            <div className="flex items-center justify-between rounded-lg border border-line px-4 py-3 text-[13.5px]"><span>A promise to pay creates a dated hold; payment is re-checked before outreach resumes</span><Toggle on={f.promiseHold} onChange={() => set({ promiseHold: !f.promiseHold })} /></div>
            <div className="flex items-center justify-between rounded-lg border border-line px-4 py-3 text-[13.5px]"><span>Give up after this many unanswered attempts</span><Input type="number" className="w-20" value={f.giveUp} onChange={(e) => set({ giveUp: Number(e.target.value) })} /></div>
            <div className="flex items-center justify-between rounded-lg border border-line px-4 py-3 text-[13.5px]"><span>Hardship, waiver and complaint replies go to a person</span><span className="inline-flex items-center gap-1 text-[12px] text-muted"><Lock size={12} /> Always</span></div>
          </div>}

          {step === 6 && <div className="grid grid-cols-2 gap-5">
            <Card>
              <div className="px-4 pt-3.5 pb-2 text-[13.5px] font-semibold">Accepted · {audience}</div>
              <div className="max-h-[400px] overflow-y-auto">
                <table className="w-full"><tbody>{accepted.slice(0, 40).map((b) => (
                  <tr key={b.id}><Td className="font-mono text-[12px]">{b.id}</Td><Td>{b.name}</Td><Td className="text-muted">{b.dpd} DPD</Td><Td className="text-muted">{b.language}</Td></tr>
                ))}</tbody></table>
              </div>
            </Card>
            <Card>
              <div className="px-4 pt-3.5 pb-2 text-[13.5px] font-semibold">Excluded · {excluded.length}</div>
              <div className="max-h-[400px] overflow-y-auto">
                <table className="w-full"><tbody>{excluded.map((b) => (
                  <tr key={b.id}><Td className="font-mono text-[12px]">{b.id}</Td><Td>{b.name}</Td><Td><Chip tone="warn">{b.reason}</Chip></Td></tr>
                ))}</tbody></table>
              </div>
            </Card>
          </div>}

          {step === 7 && <>
            <div className="grid grid-cols-2 gap-4">
              <Field label="Start (campaign day)"><Input type="number" value={f.startDay} onChange={(e) => set({ startDay: Number(e.target.value) })} /></Field>
              <Field label="End (campaign day)"><Input type="number" value={f.endDay} onChange={(e) => set({ endDay: Number(e.target.value) })} /></Field>
            </div>
            <Card className="divide-y divide-line">
              {[
                ['Name', f.name || <span className="text-bad">Missing</span>],
                ['Audience', `${audience} from ${f.cohorts.size} cohort${f.cohorts.size === 1 ? '' : 's'}${f.source === 'CSV' ? ' + CSV' : ''} · ${excluded.length} excluded`],
                ['Channels', enabledCh.length ? enabledCh.map((c) => CHANNEL_META[c].label).join(' · ') : <span className="text-bad">None</span>],
                ['Caps', `${f.capDay}/day · ${f.capWeek}/week · quiet ${f.quiet} · ${f.gap}h gap`],
                ['Budget', `${inr(f.daily)} / day · ${inr(f.total)} total`],
                ['Policy', f.mode[0] + f.mode.slice(1).toLowerCase()],
                ['Schedule', `Day ${f.startDay} → Day ${f.endDay} · starts automatically · no new sends after the end`],
              ].map(([k, v]) => (
                <div key={k} className="flex justify-between gap-6 px-4 py-2.5 text-[13.5px]"><span className="text-muted">{k}</span><span className="text-right">{v}</span></div>
              ))}
            </Card>
            <div className="text-[12px] text-faint">Re-syncing from Smart CRM never duplicates an enrolment. Changing policy later creates a new version.</div>
          </>}
        </div>
      </div>
    </Modal>
  )
}

function Field({ label, children }) {
  return <div><div className="mb-1.5 text-[13px] font-medium">{label}</div>{children}</div>
}
