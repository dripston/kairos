// "Explain this decision": GET /ai/explain (deepseek, labelled AI-generated, from the audit trail).
// Offline, or when the backend has nothing recorded for a demo borrower, it shows the caller's plain
// explanation instead, labelled SIMULATED. It never changes anything.
import { useState } from 'react'
import { Sparkles } from 'lucide-react'
import { explainDecision } from '../api/kairos'
import { useApiRole } from '../api/hooks'
import { Button, Chip, Modal } from './ui'

export function ExplainButton({ borrowerId, instructionId, fallback, size = 'sm', iconOnly = false, label = 'Explain' }) {
  const { role } = useApiRole()
  const [open, setOpen] = useState(false)
  const [res, setRes] = useState(null)

  const run = async () => {
    setOpen(true)
    setRes({ loading: true })
    try {
      const d = await explainDecision({ borrowerId, instructionId }, role)
      if (d?.outcome === 'NO_FACTS' && fallback) setRes({ text: fallback, source: 'mock', note: 'Nothing recorded in the backend for this borrower yet — showing the demo explanation.' })
      else setRes({ ...d, source: 'live' })
    } catch (e) {
      setRes(fallback ? { text: fallback, source: 'mock', note: e?.status ? `API answered ${e.status}` : 'Kairos API unreachable' } : { error: e?.message || 'Could not explain' })
    }
  }

  return (
    <>
      {iconOnly
        ? <button type="button" onClick={run} title="Explain this decision" className="grid h-7 w-7 place-items-center rounded-md text-muted hover:bg-raised hover:text-fg"><Sparkles size={14} /></button>
        : <Button size={size} icon={Sparkles} onClick={run}>{label}</Button>}
      <Modal open={open} onClose={() => setOpen(false)} width="max-w-xl"
        title={<span className="inline-flex items-center gap-2"><Sparkles size={16} className="text-violet" /> Why Aladdin decided this</span>}
        right={res && !res.loading && (res.source === 'live'
          ? <Chip tone={res.ai_generated ? 'violet' : 'neutral'}>{res.ai_generated ? 'AI-generated' : 'Deterministic'}</Chip>
          : res.source === 'mock' && <Chip tone="warn">SIMULATED · demo</Chip>)}>
        <div className="space-y-4 p-6">
          {res?.loading && <div className="animate-pulse text-[14px] text-muted">Reading the audit trail…</div>}
          {res?.error && <div className="text-[14px] text-bad">{res.error}</div>}
          {res?.text && <p className="text-[15px] leading-relaxed">{res.text}</p>}
          {res?.source === 'live' && (
            <div className="rounded-lg border border-line bg-raised/50 px-4 py-3 text-[12.5px] text-muted">
              {res.label}
              {res.model?.model && <div className="mt-1 font-mono text-[11.5px] text-faint">{res.model.model} · {res.model.latency_ms} ms{res.model.cached ? ' · cached' : ''}</div>}
            </div>
          )}
          {res?.note && <div className="text-[12.5px] text-muted">{res.note}</div>}
          <div className="text-[12px] text-faint">Explanations are written for people from the recorded facts. They never change a decision, a guardrail or a send.</div>
        </div>
      </Modal>
    </>
  )
}
