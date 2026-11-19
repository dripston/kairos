import { useEffect, useState } from 'react'
import { MessageSquare, MessageCircle, Mail, Phone, Bot, X } from 'lucide-react'
import { CHANNEL_META } from '../lib/mock'

export const cx = (...a) => a.filter(Boolean).join(' ')
export const inr = (n, d = 0) => '₹' + Number(n).toLocaleString('en-IN', { minimumFractionDigits: d, maximumFractionDigits: d })
export const pct = (n, d = 1) => `${(n * 100).toFixed(d)}%`

export function Card({ className, children, ...p }) {
  return <div className={cx('rounded-xl border border-line bg-surface', className)} {...p}>{children}</div>
}

export function CardHead({ title, sub, right, className }) {
  return (
    <div className={cx('flex items-start justify-between gap-4 px-5 pt-4 pb-3', className)}>
      <div className="min-w-0">
        <div className="text-[15px] font-semibold text-fg">{title}</div>
        {sub && <div className="mt-0.5 text-[13px] text-muted">{sub}</div>}
      </div>
      {right && <div className="flex shrink-0 items-center gap-2">{right}</div>}
    </div>
  )
}

export function PageHeader({ title, sub, right, chip }) {
  return (
    <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
      <div>
        <div className="flex items-center gap-3">
          <h1 className="text-[28px] font-semibold tracking-tight">{title}</h1>
          {chip}
        </div>
        {sub && <p className="mt-1 text-[15px] text-muted">{sub}</p>}
      </div>
      {right && <div className="flex flex-wrap items-center gap-2">{right}</div>}
    </div>
  )
}

const TONES = {
  neutral: 'bg-white/[0.06] text-muted border-white/[0.06]',
  ok: 'bg-ok/10 text-ok border-ok/20',
  warn: 'bg-warn/10 text-warn border-warn/25',
  bad: 'bg-bad/10 text-bad border-bad/25',
  info: 'bg-info/10 text-info border-info/25',
  violet: 'bg-violet/10 text-violet border-violet/25',
  solid: 'bg-fg text-bg border-fg',
}
export function Chip({ tone = 'neutral', children, className, dot }) {
  return (
    <span className={cx('inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border px-2 py-0.5 text-[11.5px] font-semibold', TONES[tone], className)}>
      {dot && <span className="h-1.5 w-1.5 rounded-full bg-current" />}
      {children}
    </span>
  )
}

export function Dot({ tone = 'ok', live, label }) {
  const c = { ok: 'bg-ok', warn: 'bg-warn', bad: 'bg-bad', info: 'bg-info', neutral: 'bg-faint' }[tone]
  return (
    <span className="inline-flex items-center gap-1.5 text-[12.5px] text-muted">
      <span className={cx('h-1.5 w-1.5 rounded-full', c, live && 'live-dot')} />
      {label}
    </span>
  )
}

export const STATUS_TONE = {
  RUNNING: 'ok', SCHEDULED: 'info', PAUSED: 'warn', STOPPED: 'bad', DRAFT: 'neutral', COMPLETED: 'neutral',
  RESERVED: 'ok', APPROVED: 'ok', NEEDS_APPROVAL: 'violet', HELD: 'warn', WAITING: 'warn', CANCELED: 'bad', SENT: 'neutral',
  SEND: 'ok', WAIT: 'warn', CANCEL: 'bad', HANDOFF: 'violet', STOP: 'bad', DONE: 'neutral',
  NEEDS_HUMAN: 'bad', CLAIMED: 'info', RESOLVED: 'ok', APPROVAL: 'violet',
}
export function StatusChip({ s, children }) {
  return <Chip tone={STATUS_TONE[s] || 'neutral'}>{children || s.replace('_', ' ').toLowerCase().replace(/^\w/, (c) => c.toUpperCase())}</Chip>
}

export function Button({ variant = 'outline', size = 'md', className, children, icon: Icon, ...p }) {
  const v = {
    primary: 'bg-fg text-bg hover:bg-white/85 border-fg',
    outline: 'border-line bg-transparent text-fg hover:bg-raised',
    ghost: 'border-transparent bg-transparent text-muted hover:text-fg hover:bg-raised',
    danger: 'border-bad/40 bg-bad/10 text-bad hover:bg-bad/20',
    warn: 'border-warn/40 bg-warn/10 text-warn hover:bg-warn/20',
  }[variant]
  const s = { sm: 'h-7 px-2.5 text-[12.5px] gap-1.5', md: 'h-9 px-3.5 text-[13.5px] gap-2', lg: 'h-10 px-4 text-[14px] gap-2' }[size]
  return (
    <button className={cx('inline-flex items-center justify-center rounded-lg border font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-40', v, s, className)} {...p}>
      {Icon && <Icon size={size === 'sm' ? 13 : 15} strokeWidth={2} />}
      {children}
    </button>
  )
}

export function Segmented({ options, value, onChange, size = 'md' }) {
  return (
    <div className="inline-flex rounded-lg border border-line bg-bg p-0.5">
      {options.map((o) => {
        const val = typeof o === 'string' ? o : o.value
        const label = typeof o === 'string' ? o : o.label
        return (
          <button key={val} onClick={() => onChange(val)}
            className={cx('rounded-md font-medium transition-colors', size === 'sm' ? 'px-2.5 py-1 text-[12px]' : 'px-3 py-1.5 text-[13px]', value === val ? 'bg-raised text-fg shadow-sm' : 'text-muted hover:text-fg')}>
            {label}
          </button>
        )
      })}
    </div>
  )
}

export function Select({ value, onChange, options, className }) {
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)}
      className={cx('h-9 rounded-lg border border-line bg-surface px-3 pr-8 text-[13px] text-fg outline-none focus:border-line-strong', className)}>
      {options.map((o) => (typeof o === 'string' ? <option key={o} value={o}>{o}</option> : <option key={o.value} value={o.value}>{o.label}</option>))}
    </select>
  )
}

export function Input({ className, ...p }) {
  return <input className={cx('h-9 w-full rounded-lg border border-line bg-bg px-3 text-[13.5px] text-fg outline-none placeholder:text-faint focus:border-line-strong', className)} {...p} />
}

export function Toggle({ on, onChange, disabled }) {
  return (
    <button disabled={disabled} onClick={onChange}
      className={cx('relative h-5 w-9 shrink-0 rounded-full border transition-colors disabled:opacity-40', on ? 'border-ok/50 bg-ok/30' : 'border-line bg-raised')}>
      <span className={cx('absolute top-0.5 h-3.5 w-3.5 rounded-full transition-all', on ? 'left-[18px] bg-ok' : 'left-0.5 bg-muted')} />
    </button>
  )
}

export function Ring({ value, tone = 'ok', size = 34 }) {
  const r = (size - 6) / 2
  const c = 2 * Math.PI * r
  const color = { ok: '#22c55e', warn: '#f59e0b', bad: '#ef4444', info: '#3b82f6', orange: '#f97316', fg: '#fafafa' }[tone]
  return (
    <svg width={size} height={size} className="-rotate-90">
      <circle cx={size / 2} cy={size / 2} r={r} stroke="#262626" strokeWidth="4" fill="none" />
      <circle cx={size / 2} cy={size / 2} r={r} stroke={color} strokeWidth="4" fill="none" strokeLinecap="round"
        strokeDasharray={c} strokeDashoffset={c * (1 - Math.max(0, Math.min(1, value)))} />
    </svg>
  )
}

export function KpiStrip({ items }) {
  return (
    <Card className="grid overflow-hidden" style={{ gridTemplateColumns: `repeat(${items.length}, minmax(0, 1fr))` }}>
      {items.map((k, i) => (
        <div key={k.label} className={cx('flex items-center justify-between gap-2 px-5 py-4', i && 'border-l border-line')}>
          <div className="min-w-0">
            <div className="text-[12.5px] text-muted">{k.label}</div>
            <div className={cx('mt-1 text-[24px] font-semibold tabular tracking-tight', k.valueClass)}>{k.value}</div>
            {k.sub && <div className="mt-0.5 truncate text-[12px] text-muted">{k.sub}</div>}
          </div>
          {k.ring != null && <Ring value={k.ring} tone={k.ringTone} />}
        </div>
      ))}
    </Card>
  )
}

const CH_ICON = { sms: MessageSquare, whatsapp: MessageCircle, email: Mail, ivr: Phone, voicebot: Bot }
export function ChannelTag({ ch, muted }) {
  if (!ch) return <span className="text-faint">—</span>
  const I = CH_ICON[ch]
  return (
    <span className={cx('inline-flex items-center gap-1.5 text-[13px]', muted ? 'text-muted' : 'text-fg')}>
      <I size={13} style={{ color: CHANNEL_META[ch].color }} />
      {CHANNEL_META[ch].label}
    </span>
  )
}
export function ChannelIcons({ channels }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      {Object.entries(channels).filter(([, on]) => on).map(([ch]) => {
        const I = CH_ICON[ch]
        return <I key={ch} size={13} style={{ color: CHANNEL_META[ch].color }} title={CHANNEL_META[ch].label} />
      })}
    </span>
  )
}

export function Th({ children, className }) {
  return <th className={cx('px-4 py-2.5 text-left text-[12px] font-medium text-muted', className)}>{children}</th>
}
export function Td({ children, className, ...p }) {
  return <td className={cx('border-t border-line px-4 py-2.5 text-[13px]', className)} {...p}>{children}</td>
}

export function Modal({ open, onClose, title, right, children, footer, width = 'max-w-5xl' }) {
  useEffect(() => {
    if (!open) return
    const k = (e) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', k)
    return () => window.removeEventListener('keydown', k)
  }, [open, onClose])
  if (!open) return null
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-6" onMouseDown={onClose}>
      <div className={cx('anim-fade flex max-h-[92vh] w-full flex-col overflow-hidden rounded-xl border border-line bg-surface shadow-2xl', width)} onMouseDown={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between border-b border-line px-6 py-4">
          <div className="text-[17px] font-semibold">{title}</div>
          <div className="flex items-center gap-4">
            {right}
            <button onClick={onClose} className="text-muted hover:text-fg"><X size={18} /></button>
          </div>
        </div>
        <div className="min-h-0 flex-1 overflow-auto">{children}</div>
        {footer && <div className="flex items-center justify-between border-t border-line px-6 py-3.5">{footer}</div>}
      </div>
    </div>
  )
}

const REASONS = ['Operator decision', 'Customer request', 'Compliance review', 'Provider issue', 'Budget control', 'Demo / test']
// Every human action asks for a reason and shows its effect before applying.
export function ReasonDialog({ open, title, effect, danger, confirmLabel = 'Apply', onConfirm, onClose, children }) {
  const [reason, setReason] = useState(REASONS[0])
  if (!open) return null
  return (
    <Modal open={open} onClose={onClose} title={title} width="max-w-md"
      footer={<>
        <span className="text-[12px] text-muted">Logged to the audit trail with your role.</span>
        <div className="flex gap-2">
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button variant={danger ? 'danger' : 'primary'} onClick={() => { onConfirm(reason); onClose() }}>{confirmLabel}</Button>
        </div>
      </>}>
      <div className="space-y-4 px-6 py-5">
        {effect && <div className="rounded-lg border border-line bg-bg px-3.5 py-3 text-[13px] text-muted">{effect}</div>}
        {children}
        <div>
          <div className="mb-1.5 text-[12.5px] text-muted">Reason (required)</div>
          <div className="flex flex-wrap gap-1.5">
            {REASONS.map((r) => (
              <button key={r} onClick={() => setReason(r)} className={cx('rounded-full border px-2.5 py-1 text-[12px]', reason === r ? 'border-fg bg-fg text-bg' : 'border-line text-muted hover:text-fg')}>{r}</button>
            ))}
          </div>
        </div>
      </div>
    </Modal>
  )
}

export function Empty({ children }) {
  return <div className="px-5 py-10 text-center text-[13px] text-muted">{children}</div>
}

export function Bar({ value, tone = 'ok' }) {
  const c = { ok: 'bg-ok', warn: 'bg-warn', bad: 'bg-bad', info: 'bg-info', fg: 'bg-fg' }[tone]
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-raised">
      <div className={cx('h-full rounded-full', c)} style={{ width: `${Math.max(2, Math.min(100, value * 100))}%` }} />
    </div>
  )
}
