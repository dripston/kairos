import { NavLink, Outlet, useNavigate } from 'react-router-dom'
import {
  Gauge, Megaphone, Database, TrendingUp, Layers, Search, FlaskConical, ChevronRight, RotateCcw,
  CircleCheck, TriangleAlert, Info, CircleX, X, ShieldCheck, Pause, Play, Sun,
} from 'lucide-react'
import { useKairos, useDerived, fmtClock } from '../lib/store'
import { CAMPAIGN_DAYS, CHANNEL_META, HERO_ID } from '../lib/mock'
import { Button, Chip, Dot, cx, inr, Segmented } from './ui'

const NAV = [
  { group: 'Overview', items: [{ to: '/learning', label: 'Learning & Proof', icon: TrendingUp }] },
  { group: 'Operations', items: [{ to: '/control', label: 'Control Room', icon: Gauge }] },
  { group: 'Business', items: [
    { to: '/crm', label: 'Smart CRM', icon: Database },
    { to: '/cohorts', label: 'Cohorts', icon: Layers },
    { to: '/campaigns', label: 'Campaign Management', icon: Megaphone },
  ] },
]

function Logo() {
  return (
    <div className="flex items-center gap-2.5">
      <div className="grid h-7 w-7 place-items-center rounded-full bg-gradient-to-br from-sky-300 via-indigo-400 to-violet-500 text-[13px] font-bold text-white shadow">K</div>
      <span className="text-[17px] font-semibold tracking-tight">Kairos</span>
    </div>
  )
}

function Sidebar() {
  const { state, dispatch } = useKairos()
  return (
    <aside className="flex w-[248px] shrink-0 flex-col border-r border-line bg-bg">
      <div className="flex items-center justify-between px-5 pt-5 pb-4">
        <Logo />
        <span className="inline-flex items-center gap-1 rounded-md border border-line px-1.5 py-0.5 text-[11px] text-muted"><Search size={11} />⌘K</span>
      </div>
      <nav className="flex-1 overflow-y-auto px-3">
        {NAV.map((g) => (
          <div key={g.group} className="mb-4">
            <div className="px-2.5 pb-1.5 text-[12.5px] text-muted">{g.group}</div>
            {g.items.map((it) => (
              <NavLink key={it.to} to={it.to}
                className={({ isActive }) => cx('mb-0.5 flex items-center gap-3 rounded-lg border px-2.5 py-2 text-[14.5px] transition-colors',
                  isActive ? 'border-line bg-raised font-semibold text-fg' : 'border-transparent text-fg/85 hover:bg-raised')}>
                <it.icon size={16} strokeWidth={1.8} />
                {it.label}
              </NavLink>
            ))}
          </div>
        ))}
        <div className="mx-2.5 mt-2 rounded-lg border border-line bg-surface p-3 text-[12px] text-muted">
          <div className="mb-1 flex items-center gap-1.5 font-semibold text-fg"><ShieldCheck size={13} className="text-ok" /> Hard guardrails</div>
          Consent, STOP and contact caps can't be overridden by anyone.
        </div>
      </nav>
      <div className="border-t border-line p-3">
        <div className="mb-2 px-1 text-[11.5px] text-muted">Acting as</div>
        <Segmented size="sm" value={state.role} onChange={(role) => dispatch({ type: 'SET_ROLE', role })}
          options={[{ value: 'operator', label: 'Operator' }, { value: 'policy', label: 'Policy owner' }]} />
        <div className="mt-3 flex items-center gap-2.5 px-1">
          <div className="grid h-8 w-8 place-items-center rounded-full bg-info/20 text-[11px] font-bold text-info">{state.role === 'operator' ? 'OP' : 'PO'}</div>
          <div className="min-w-0 flex-1 leading-tight">
            <div className="truncate text-[13px] font-semibold">{state.role === 'operator' ? 'ops.desk' : 'policy.owner'}</div>
            <div className="text-[11.5px] text-muted">{state.role === 'operator' ? 'Campaign operator' : 'Policy owner'}</div>
          </div>
          <Sun size={15} className="text-muted" />
        </div>
      </div>
    </aside>
  )
}

const ENV_TONE = { LIVE_SANDBOX: 'ok', REPLAY: 'info', SIMULATED: 'warn', OFFICIAL_SCORE: 'violet' }

function StatusBar() {
  const { state, dispatch } = useKairos()
  const d = useDerived()
  const spentPct = d.spend / d.budgetTotal
  return (
    <div className="sticky top-0 z-30 flex h-12 shrink-0 items-center gap-3 border-b border-line bg-bg/90 px-8 backdrop-blur">
      <Chip tone={ENV_TONE[state.env]}>{state.env.replace('_', ' ')}</Chip>
      <Dot tone={state.globalPause ? 'bad' : 'ok'} live={!state.globalPause} label={state.globalPause ? 'Outbound paused' : 'Live · updated just now'} />
      <div className="mx-1 h-5 w-px bg-line" />
      <span className="text-[13px] tabular text-muted">Day <span className="font-semibold text-fg">{state.day}</span> / {CAMPAIGN_DAYS} · {fmtClock(state.minute)}</span>
      <Button size="sm" icon={ChevronRight} onClick={() => dispatch({ type: 'NEXT_DAY' })} disabled={state.day >= CAMPAIGN_DAYS}>Next day</Button>
      <div className="flex-1" />
      <div className="hidden items-center gap-2 lg:flex">
        <span className="text-[12.5px] text-muted">Spend</span>
        <div className="h-1.5 w-28 overflow-hidden rounded-full bg-raised"><div className="h-full rounded-full bg-fg" style={{ width: `${spentPct * 100}%` }} /></div>
        <span className="text-[12.5px] tabular">{inr(d.spend)} <span className="text-muted">/ {inr(d.budgetTotal)}</span></span>
        <span className="text-[12px] text-muted">+ {inr(d.reserved, 2)} reserved</span>
      </div>
      <div className="mx-1 h-5 w-px bg-line" />
      <span className="inline-flex items-center gap-1.5 rounded-full border border-ok/25 bg-ok/10 px-2.5 py-0.5 text-[12px] font-semibold text-ok">
        <ShieldCheck size={12} /> Cap breaches: 0
      </span>
      <Button size="sm" variant="ghost" icon={FlaskConical} onClick={() => dispatch({ type: 'TOGGLE_SIM' })}>Simulation <span className="text-faint">⇧S</span></Button>
    </div>
  )
}

function SimButton({ children, onClick, tone }) {
  return (
    <button onClick={onClick}
      className={cx('w-full rounded-lg border px-3 py-2 text-left text-[13px] transition-colors',
        tone === 'bad' ? 'border-bad/30 text-bad hover:bg-bad/10' : tone === 'ok' ? 'border-ok/30 text-ok hover:bg-ok/10' : 'border-line text-fg hover:bg-raised')}>
      {children}
    </button>
  )
}

function SimPanel() {
  const { state, dispatch } = useKairos()
  const nav = useNavigate()
  if (!state.simOpen) return null
  const run = (kind, go = true) => { dispatch({ type: 'SIM', kind }); if (go) nav(`/crm/${HERO_ID}`) }
  return (
    <div className="fixed inset-0 z-40" onMouseDown={() => dispatch({ type: 'TOGGLE_SIM', open: false })}>
      <div className="anim-slide absolute top-0 right-0 flex h-full w-[340px] flex-col border-l border-line bg-surface shadow-2xl" onMouseDown={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between border-b border-line px-5 py-4">
          <div>
            <div className="flex items-center gap-2 text-[15px] font-semibold"><FlaskConical size={15} /> Simulation</div>
            <div className="text-[12px] text-muted">Presenter only · every event is labelled SIMULATED</div>
          </div>
          <button onClick={() => dispatch({ type: 'TOGGLE_SIM', open: false })} className="text-muted hover:text-fg"><X size={17} /></button>
        </div>
        <div className="flex-1 space-y-5 overflow-y-auto px-5 py-4">
          <section>
            <div className="mb-2 text-[12px] font-semibold tracking-wide text-muted uppercase">Clock</div>
            <div className="grid grid-cols-2 gap-2">
              <Button icon={ChevronRight} onClick={() => dispatch({ type: 'NEXT_DAY' })}>Next day</Button>
              <Button icon={RotateCcw} onClick={() => dispatch({ type: 'RESET' })}>Reset demo</Button>
            </div>
          </section>
          <section>
            <div className="mb-2 text-[12px] font-semibold tracking-wide text-muted uppercase">Borrower {HERO_ID} · phone stand-ins</div>
            <p className="mb-2 text-[12px] text-muted">Use these if a real phone is unavailable. They write the same events the phones would.</p>
            <div className="space-y-1.5">
              <SimButton onClick={() => run('need_time')}>💬 Replies “need time, will pay on the 25th”</SimButton>
              <SimButton onClick={() => run('callback')}>📞 Asks for a callback</SimButton>
              <SimButton onClick={() => run('hardship')}>🩹 Reports hardship (accident)</SimButton>
              <SimButton onClick={() => run('paid_claim')}>🧾 Sends voice note + UPI screenshot (“already paid”)</SimButton>
              <SimButton onClick={() => run('wrong_otp')}>🔒 Enters a wrong OTP</SimButton>
              <SimButton tone="ok" onClick={() => run('post_payment')}>✅ Post payment to D7 (pending → settled)</SimButton>
              <SimButton tone="bad" onClick={() => run('edited_screenshot')}>🖼️ Sends an edited screenshot</SimButton>
              <SimButton tone="bad" onClick={() => run('stop')}>⛔ Sends STOP</SimButton>
              <SimButton onClick={() => run('duplicate')}>⚔️ Second campaign races for the same borrower</SimButton>
            </div>
          </section>
          <section>
            <div className="mb-2 text-[12px] font-semibold tracking-wide text-muted uppercase">Providers</div>
            <div className="space-y-1.5">
              {['whatsapp', 'sms', 'email', 'ivr'].map((ch) => (
                <SimButton key={ch} tone={state.outage[ch] ? 'bad' : undefined} onClick={() => dispatch({ type: 'OUTAGE', channel: ch })}>
                  {state.outage[ch] ? <Play size={12} className="mr-1.5 inline" /> : <Pause size={12} className="mr-1.5 inline" />}
                  {state.outage[ch] ? `Restore ${CHANNEL_META[ch].label}` : `Simulate ${CHANNEL_META[ch].label} outage`}
                </SimButton>
              ))}
            </div>
          </section>
          <section>
            <div className="mb-2 text-[12px] font-semibold tracking-wide text-muted uppercase">Environment label</div>
            <div className="flex flex-wrap gap-1.5">
              {['LIVE_SANDBOX', 'REPLAY', 'SIMULATED'].map((e) => (
                <button key={e} onClick={() => dispatch({ type: 'SET_ENV', env: e })}
                  className={cx('rounded-full border px-2.5 py-1 text-[11.5px] font-semibold', state.env === e ? 'border-fg bg-fg text-bg' : 'border-line text-muted')}>{e.replace('_', ' ')}</button>
              ))}
            </div>
          </section>
        </div>
      </div>
    </div>
  )
}

function Toasts() {
  const { state, dispatch } = useKairos()
  const icon = { ok: CircleCheck, warn: TriangleAlert, bad: CircleX, info: Info }
  const color = { ok: 'text-ok', warn: 'text-warn', bad: 'text-bad', info: 'text-info' }
  return (
    <div className="fixed right-6 bottom-6 z-50 flex w-[380px] flex-col gap-2">
      {state.toasts.slice(-3).map((t) => {
        const I = icon[t.tone] || CircleCheck
        return (
          <div key={t.id} className="anim-slide flex items-start gap-2.5 rounded-xl border border-line bg-raised px-4 py-3 shadow-2xl">
            <I size={16} className={cx('mt-0.5 shrink-0', color[t.tone])} />
            <div className="flex-1 text-[13px]">{t.text}</div>
            <button onClick={() => dispatch({ type: 'DISMISS_TOAST', id: t.id })} className="text-faint hover:text-fg"><X size={14} /></button>
          </div>
        )
      })}
    </div>
  )
}

export default function Shell() {
  return (
    <div className="flex h-full">
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        <StatusBar />
        <main className="min-h-0 flex-1 overflow-y-auto">
          <div className="mx-auto max-w-[1440px] px-8 py-7">
            <Outlet />
          </div>
        </main>
      </div>
      <SimPanel />
      <Toasts />
    </div>
  )
}
