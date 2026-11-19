// K17/K18 voice: Sarvam bot registry (use case → agent, voice, languages), "pick bot for borrower", and every
// call with its bot, voice, language, disposition, confidence, the threshold rule that fired and the action taken.
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { AudioLines } from 'lucide-react'
import { useKairos } from '../lib/store'
import { useApi, useApiStatus } from '../api/hooks'
import { getAudit, getVoiceCall, getVoiceRegistry, selectVoiceBot } from '../api/kairos'
import { OfflineError } from '../api/client'
import { MOCK_VOICE_REGISTRY, mockVoiceSelect } from '../api/mocks'
import { fmtTs } from '../lib/format'
import { MockNote, RefreshButton, SourceBadge } from '../components/live'
import { Button, Card, CardHead, Chip, Empty, Input, PageHeader, Td, Th } from '../components/ui'
import { BotResult } from './CrmLive'

const MOCK_CALLS = [
  { call_id: 'sv-mock-1', borrower_id: 'B-1042', ts: 'Day 3 11:04', use_case: 'MID_BUCKET_RESOLUTION', disposition: 'PTP', effective: 'PTP', confidence: 0.86, rule: 'K17.PTP.ACCEPT', action: 'PTP_HOLD', language: 'Hindi' },
  { call_id: 'sv-mock-2', borrower_id: 'B-0213', ts: 'Day 3 11:20', use_case: 'EMI_REMINDER_EARLY', disposition: 'HARDSHIP', effective: 'HARDSHIP', confidence: 0.91, rule: 'K17.HARDSHIP.ACCEPT', action: 'HANDOFF_HARDSHIP', language: 'Marathi' },
  { call_id: 'sv-mock-3', borrower_id: 'B-0388', ts: 'Day 3 12:02', use_case: 'EMI_REMINDER_EARLY', disposition: 'PTP', effective: 'UNCLEAR', confidence: 0.41, rule: 'K17.LOW_CONFIDENCE.DOWNGRADE', action: 'NO_STATE_CHANGE', language: 'Hindi' },
]

export default function Voice() {
  const { state } = useKairos()
  const status = useApiStatus()
  const reg = useApi((o) => getVoiceRegistry(o), { fallback: () => MOCK_VOICE_REGISTRY, deps: [status.state] })
  const calls = useApi(async (o) => {
    const rows = (await getAudit({ limit: 1000 }, o)).filter((r) => r.type === 'VOICE_DISPOSITION').slice(0, 25)
    const det = await Promise.all(rows.map((r) => getVoiceCall(r.detail?.call_id, o).catch(() => null)))
    return rows.map((r, i) => ({
      call_id: r.detail?.call_id, borrower_id: r.borrower_id, ts: fmtTs(r.ts), use_case: det[i]?.use_case, disposition: r.detail?.disposition, effective: r.detail?.effective,
      confidence: r.detail?.confidence, rule: r.reason, action: r.detail?.action, language: det[i]?.detail?.language_used || det[i]?.language_used, duration: det[i]?.duration_seconds, label: det[i]?.report_label,
    }))
  }, { fallback: () => MOCK_CALLS, deps: [status.state], poll: 5000 })
  const [bid, setBid] = useState('')
  const [pick, setPick] = useState(null)
  const r = reg.data?.registry || {}
  const useCases = Object.entries(r.use_cases || {})
  const voiceOf = (uc) => r.use_cases?.[uc]?.voice
  const thresholds = reg.data?.thresholds

  const run = async (e) => {
    e.preventDefault()
    const id = bid.trim()
    if (!id) return
    setPick({ loading: true })
    try {
      setPick({ data: await selectVoiceBot({ borrower_id: id }), live: true })
    } catch (err) {
      const b = id === state.hero.id ? state.hero : state.borrowers.find((x) => x.id === id)
      setPick({ data: mockVoiceSelect(b), live: false, why: err instanceof OfflineError ? 'API offline' : err.message })
    }
  }

  return (
    <>
      <PageHeader title="Voice bots" sub="Kairos decides whether a call may happen (K01 Gate); the registry picks which Sarvam bot, voice and language. Kairos, not the bot, decides what a disposition does."
        chip={<SourceBadge res={reg} />} />
      <div className="space-y-5">
        <div className="grid grid-cols-3 gap-5">
          <Card className="col-span-2">
            <CardHead title="Bot registry" sub={`Use case → Sarvam agent · registry v${r.registry_version ?? '—'} · ${r.status || ''}`} right={<RefreshButton onClick={reg.reload} />} />
            <MockNote res={reg} />
            <table className="w-full">
              <thead><tr><Th>Use case</Th><Th>DPD bands</Th><Th>Agent</Th><Th>Voice</Th><Th>Human approval</Th></tr></thead>
              <tbody>
                {useCases.map(([uc, u]) => (
                  <tr key={uc}>
                    <Td className="font-semibold">{uc.replace(/_/g, ' ').toLowerCase().replace(/^\w/, (c) => c.toUpperCase())}</Td>
                    <Td className="font-mono text-[12px] text-muted">{Array.isArray(u.bands) ? u.bands.join(', ') : u.bands === '*' ? 'any' : String(u.bands)}</Td>
                    <Td className="font-mono text-[12px]">{u.agent_id}</Td>
                    <Td><Chip tone={u.voice === 'Ritu' ? 'violet' : 'info'}>{u.voice}</Chip> <span className="text-[11.5px] text-faint">{r.voices?.[u.voice]?.gender}</span></Td>
                    <Td>{u.requires_human_approval ? <Chip tone="violet">Required</Chip> : <span className="text-muted">—</span>}</Td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="px-5 py-3 text-[12.5px] text-muted">Languages: {(r.supported_languages || []).join(' · ')}{r.default_language ? ` · default ${r.default_language}` : ''}</div>
          </Card>
          <Card className="p-5">
            <div className="mb-2 flex items-center gap-1.5 text-[13.5px] font-semibold"><AudioLines size={14} /> Pick bot for borrower</div>
            <form onSubmit={run} className="flex gap-2">
              <Input value={bid} onChange={(e) => setBid(e.target.value)} placeholder="Borrower id" />
              <Button type="submit" disabled={pick?.loading}>Pick</Button>
            </form>
            {pick?.data && (
              <>
                <div className="mt-2">{pick.live ? <Chip tone="warn" dot>SIMULATED · API</Chip> : <span title={pick.why}><Chip tone="warn">SIMULATED · offline mock</Chip></span>}</div>
                <BotResult r={pick.data} />
              </>
            )}
            {thresholds && (
              <div className="mt-4 border-t border-line pt-3 text-[12px] text-muted">
                Disposition thresholds v{thresholds.policy_version} ({thresholds.status}){thresholds.min_confidence_any != null ? ` · below ${thresholds.min_confidence_any} confidence nothing changes state` : ''}.
              </div>
            )}
          </Card>
        </div>

        <Card>
          <CardHead title="Calls" sub="Each post-call webhook: bot, voice, language, disposition, confidence, the rule that fired and the action Kairos took" right={<><SourceBadge res={calls} /><RefreshButton onClick={calls.reload} /></>} />
          <MockNote res={calls} />
          <table className="w-full">
            <thead><tr><Th>Time</Th><Th>Borrower</Th><Th>Bot · voice</Th><Th>Language</Th><Th>Disposition</Th><Th>Confidence</Th><Th>Rule fired</Th><Th>Action</Th></tr></thead>
            <tbody>
              {(calls.data || []).map((c) => (
                <tr key={c.call_id}>
                  <Td className="font-mono text-[12px] text-muted">{c.ts}</Td>
                  <Td><Link to={`/crm/${c.borrower_id}`} className="font-mono text-[12px] hover:underline">{c.borrower_id}</Link></Td>
                  <Td className="text-[12.5px]">{c.use_case || '—'}<div className="text-[11.5px] text-muted">{voiceOf(c.use_case) || '—'}</div></Td>
                  <Td>{c.language || '—'}</Td>
                  <Td>{c.disposition}{c.effective && c.effective !== c.disposition && <span className="text-warn"> → {c.effective}</span>}</Td>
                  <Td><Chip tone={c.confidence >= 0.8 ? 'ok' : c.confidence >= 0.6 ? 'warn' : 'bad'}>{c.confidence != null ? Number(c.confidence).toFixed(2) : '—'}</Chip></Td>
                  <Td className="font-mono text-[12px] text-info">{c.rule}</Td>
                  <Td className="font-mono text-[12px]">{c.action}</Td>
                </tr>
              ))}
            </tbody>
          </table>
          {!(calls.data || []).length && <Empty>{calls.loading ? 'Loading…' : 'No calls yet. Calls arrive through POST /webhooks/sarvam/call-end.'}</Empty>}
        </Card>
      </div>
    </>
  )
}
