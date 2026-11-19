// Learning & Proof panels backed by the API: K09 arm posteriors (from the CM-02 daily-segment report),
// channel switches, the per-segment table, and the K09 vs CN10 proof card (replay report JSON or the documented numbers).
import { useMemo, useState } from 'react'
import { Download, ArrowRight } from 'lucide-react'
import { useKairos } from '../lib/store'
import { useApi, useApiStatus } from '../api/hooks'
import { dailySegmentCsvUrl, getAudit, getDailySegment, getReplayReport } from '../api/kairos'
import { DEFAULT_CAMPAIGN } from '../api/config'
import { mockArms, mockDailySegment, mockSwitches } from '../api/mocks'
import { CHANNEL_META } from '../lib/mock'
import { dayOf, todayIso } from '../lib/format'
import { MockNote, ProvChip, RefreshButton, SourceBadge } from './live'
import { Button, Card, CardHead, ChannelTag, Chip, Empty, Input, Segmented, Td, Th, cx } from './ui'

// Documented K09 (k09/optimize default: decay 0.7 + value floor 0.5) vs CN10 on the three unseen seeds.
// Source: backend/README.md on k09/optimize. REPLAY on the DEV simulator — not a live result.
const DOCUMENTED_PROOF = [
  { seed: 31337, cn10: 60.93, k09: 90.1, lift: 0.479, replies: [178, 245], payments: [50, 57], spend: [2921, 2719], breaches: 0 },
  { seed: 8080, cn10: 60.95, k09: 98.23, lift: 0.612, replies: [179, 276], payments: [41, 65], spend: [2937, 2810], breaches: 0 },
  { seed: 4242, cn10: 61.95, k09: 96.67, lift: 0.56, replies: [182, 282], payments: [43, 81], spend: [2938, 2917], breaches: 0 },
]

const knownCh = (ch) => (CHANNEL_META[ch] ? ch : null)
const pctS = (x) => `${x >= 0 ? '+' : ''}${(x * 100).toFixed(0)}%`

/** Beta posterior mean and a 95% credible interval (normal approximation; fine for n ≥ ~10). */
function betaCi(a, b) {
  const n = a + b
  const m = a / n
  const sd = Math.sqrt((a * b) / (n * n * (n + 1)))
  return { m, lo: Math.max(0, m - 1.96 * sd), hi: Math.min(1, m + 1.96 * sd) }
}
function withAB(arm) {
  if (arm.alpha != null && arm.beta != null) return arm
  const n = Math.max(1, arm.n || arm.n_obs || 1)
  const m = arm.posterior_mean ?? arm.mean ?? 0
  return { ...arm, alpha: m * n + 1, beta: (1 - m) * n + 1 }
}

// ---------- proof card ----------
export function ProofCard() {
  const rep = useApi((o) => getReplayReport(o), { deps: [] })
  const r = rep.source === 'live' ? rep.data : null
  const k = r?.kairos
  const base = r?.baselines?.cn10 || r?.baseline
  return (
    <Card>
      <CardHead title="K09 vs CN10 · proof" sub={r ? `Replay report · seed ${r.matched?.seed} · ${r.matched?.borrowers} borrowers · ${r.matched?.days} days · same budget and caps` : 'Documented run on unseen seeds · same seed, portfolio, budget and caps for both arms'}
        right={<ProvChip prov="REPLAY" title="Replay on the DEV simulator (SIMULATED world). Not a live sandbox result." />} />
      {r && k && base ? (
        <div className="grid grid-cols-5 gap-3 px-5 pb-5">
          {[
            ['Replies per ₹1k', base.replies_per_1000_inr, k.replies_per_1000_inr, true],
            ['Verified payments', base.verified_payments, k.verified_payments, true],
            ['Spend ₹', base.spend_inr, k.spend_inr, false],
            ['STOP opt-outs', base.opt_outs, k.opt_outs, false],
            ['K01 cap breaches', base.cap_breaches_k01, k.cap_breaches_k01, false],
          ].map(([l, b, v, up]) => {
            const ch = b ? v / b - 1 : 0
            return (
              <div key={l} className="rounded-lg border border-line p-3">
                <div className="text-[12px] text-muted">{l}</div>
                <div className="text-[20px] font-semibold tabular">{v ?? '—'}</div>
                <div className="text-[12px] text-muted">CN10 {b ?? '—'} {b ? <span className={cx((ch >= 0) === up ? 'text-ok' : 'text-bad')}>{pctS(ch)}</span> : null}</div>
              </div>
            )
          })}
        </div>
      ) : (
        <div className="px-5 pb-5">
          <div className="mb-3 grid grid-cols-3 gap-3">
            {DOCUMENTED_PROOF.map((p) => (
              <div key={p.seed} className="rounded-lg border border-ok/25 bg-ok/5 p-3">
                <div className="text-[12px] text-muted">Unseen seed {p.seed}</div>
                <div className="text-[26px] font-semibold text-ok tabular">{pctS(p.lift)}</div>
                <div className="text-[12px] text-muted">replies per ₹ · {p.k09} vs {p.cn10} per ₹1k</div>
              </div>
            ))}
          </div>
          <table className="w-full">
            <thead><tr><Th>Seed</Th><Th>Replies CN10 → K09</Th><Th>Verified payments</Th><Th>Spend ₹</Th><Th>Cap breaches</Th></tr></thead>
            <tbody>
              {DOCUMENTED_PROOF.map((p) => (
                <tr key={p.seed}>
                  <Td className="font-mono">{p.seed}</Td>
                  <Td className="tabular">{p.replies[0]} → <b>{p.replies[1]}</b></Td>
                  <Td className="tabular">{p.payments[0]} → <b>{p.payments[1]}</b></Td>
                  <Td className="tabular">{p.spend[0].toLocaleString('en-IN')} → {p.spend[1].toLocaleString('en-IN')}</Td>
                  <Td className="font-semibold text-ok">{p.breaches}</Td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="mt-2 text-[12px] text-muted">
            Tuned on seeds 7/42/99/2026, validated on 11/123/555, then run once on these unseen seeds. STOP opt-outs are higher than CN10 — an open follow-up.
            No replay report is served right now; drop <span className="font-mono">k09_replay_report.json</span> into <span className="font-mono">frontend/public/</span> to show a fresh run.
          </div>
        </div>
      )}
    </Card>
  )
}

// ---------- arm posteriors ----------
export function ArmScores({ campaignId = DEFAULT_CAMPAIGN }) {
  const { state } = useKairos()
  const status = useApiStatus()
  const audit = useApi((o) => getAudit({ limit: 1 }, o), { deps: [status.state] })
  const day = audit.data?.[0]?.ts ? dayOf(audit.data[0].ts) : todayIso()
  const res = useApi((o) => getDailySegment({ campaignId, day }, o), { fallback: () => mockDailySegment(state.day), deps: [campaignId, day, status.state, state.day], poll: 5000 })
  const rep = useApi((o) => getReplayReport(o), { deps: [] })
  let arms = res.data?.learning?.arms || []
  let src = res
  let note = null
  if (!arms.length && rep.source === 'live' && rep.data?.scores_after) {
    arms = Object.values(rep.data.scores_after).flat().map((x) => ({ ...x, n: x.n_obs, posterior_mean: x.mean }))
    src = rep
    note = 'From the replay report (REPLAY)'
  } else if (!arms.length) {
    arms = mockArms(state.day)
    note = res.source === 'live' ? 'Learner tables empty for this day — showing the built-in mock (SIMULATED)' : null
  }
  const bands = [...new Set(arms.map((a) => a.band))].sort()
  const [band, setBand] = useState('All')
  const view = arms.map(withAB).filter((a) => band === 'All' || a.band === band).sort((a, b) => (b.posterior_mean ?? 0) - (a.posterior_mean ?? 0))
  const max = Math.max(0.05, ...view.map((a) => betaCi(a.alpha, a.beta).hi))
  return (
    <Card>
      <CardHead title="K09 arm posteriors" sub="Reply probability per band × channel × slot · bar = posterior mean, whisker = 95% credible interval · updates as reply windows close"
        right={<><SourceBadge res={src} prov={src === rep ? 'REPLAY' : undefined} /><RefreshButton onClick={res.reload} /></>} />
      <MockNote res={res} />
      {note && <div className="mx-5 mb-2 text-[12px] text-muted">{note}</div>}
      <div className="mx-5 mb-2"><Segmented size="sm" value={band} onChange={setBand} options={['All', ...bands]} /></div>
      <div className="px-5 pb-4">
        {view.slice(0, 16).map((a) => {
          const ci = betaCi(a.alpha, a.beta)
          return (
            <div key={`${a.band}-${a.channel}-${a.slot}`} className="grid grid-cols-[200px_minmax(0,1fr)_170px] items-center gap-3 py-1">
              <div className="flex items-center gap-1.5 text-[12.5px]"><span className="w-6 font-mono text-faint">{a.band}</span><ChannelTag ch={knownCh(a.channel)} /><span className="text-faint">{a.slot}</span></div>
              <div className="relative h-3 rounded-full bg-raised">
                <div className="absolute top-0 h-3 rounded-full" style={{ width: `${(ci.m / max) * 100}%`, background: CHANNEL_META[a.channel]?.color || '#888', opacity: 0.85 }} />
                <div className="absolute top-1/2 h-px bg-fg" style={{ left: `${(ci.lo / max) * 100}%`, width: `${((ci.hi - ci.lo) / max) * 100}%` }} />
                <div className="absolute top-0 h-3 w-px bg-fg" style={{ left: `${(ci.lo / max) * 100}%` }} />
                <div className="absolute top-0 h-3 w-px bg-fg" style={{ left: `${(ci.hi / max) * 100}%` }} />
              </div>
              <div className="text-right font-mono text-[12px]">{(ci.m * 100).toFixed(1)}% <span className="text-faint">± {((ci.hi - ci.lo) * 50).toFixed(1)} · n={a.n ?? a.n_obs ?? '—'}</span></div>
            </div>
          )
        })}
        {!view.length && <Empty>No arms yet.</Empty>}
      </div>
    </Card>
  )
}

// ---------- channel switches ----------
export function ChannelSwitches() {
  const { state } = useKairos()
  const rep = useApi((o) => getReplayReport(o), { deps: [] })
  const live = rep.source === 'live' && rep.data?.preference_switches
  const list = live ? Object.values(rep.data.preference_switches).flat() : mockSwitches(state.day)
  return (
    <Card>
      <CardHead title="Channel switches" sub="When the learner's preferred channel for a band changed inside the campaign (e.g. after the WhatsApp drift)"
        right={live ? <ProvChip prov="REPLAY" /> : <span title="Built-in mock"><Chip tone="warn">SIMULATED · mock</Chip></span>} />
      <div className="px-5 pb-4">
        {list.length ? list.map((x, i) => (
          <div key={i} className="flex items-center gap-3 border-t border-line py-2 text-[13px] first:border-t-0">
            <span className="w-8 font-mono text-muted">{x.band}</span>
            <span className="w-28 text-muted">Day {x.day}{x.date && x.date !== `Day ${x.day}` ? ` · ${x.date}` : ''}</span>
            <ChannelTag ch={knownCh(x.from)} /><ArrowRight size={13} className="text-muted" /><ChannelTag ch={knownCh(x.to)} />
          </div>
        )) : <Empty>No switch yet — advance the clock past Day 4.</Empty>}
      </div>
    </Card>
  )
}

// ---------- CM-02 daily per-segment report ----------
export function DailySegmentTable() {
  const { state } = useKairos()
  const status = useApiStatus()
  const [campaignId, setCampaignId] = useState(DEFAULT_CAMPAIGN)
  const [day, setDay] = useState(todayIso())
  // jump to the latest day the ledger has decisions for
  const audit = useApi((o) => getAudit({ limit: 1 }, o), { deps: [status.state] })
  const latest = audit.data?.[0]?.ts ? dayOf(audit.data[0].ts) : null
  const res = useApi((o) => getDailySegment({ campaignId, day }, o), { fallback: () => mockDailySegment(state.day), deps: [campaignId, day, status.state, state.day], poll: 10000 })
  const r = res.data
  const segs = useMemo(() => r?.segments || [], [r])
  const t = r?.totals
  return (
    <Card>
      <CardHead title="Daily report · per segment (CM-02)" sub={r ? `${r.campaign_id} · ${r.day} · policy v${r.policy_version} · spend, response and learning per DPD band × channel` : 'GET /reports/daily-segment'}
        right={<><SourceBadge res={res} /><RefreshButton onClick={res.reload} /></>} />
      <div className="mx-5 mb-3 flex flex-wrap items-center gap-2">
        <div className="w-32"><Input value={campaignId} onChange={(e) => setCampaignId(e.target.value)} className="h-8 text-[12.5px]" /></div>
        <div className="w-40"><Input type="date" value={day} onChange={(e) => setDay(e.target.value)} className="h-8 text-[12.5px]" /></div>
        {latest && latest !== day && <Button size="sm" onClick={() => setDay(latest)}>Latest ({latest})</Button>}
        <div className="flex-1" />
        {res.source === 'live' && <a href={dailySegmentCsvUrl({ campaignId, day })} className="inline-flex"><Button size="sm" icon={Download}>CSV</Button></a>}
      </div>
      <MockNote res={res} />
      <div className="overflow-x-auto">
        <table className="w-full">
          <thead><tr><Th>Band</Th><Th>Channel</Th><Th>Attempts</Th><Th>Reach</Th><Th>Replies</Th><Th>Spend ₹</Th><Th>Replies / ₹1k</Th><Th>Cap breaches</Th></tr></thead>
          <tbody>
            {segs.map((s, i) => (
              <tr key={i}>
                <Td className="font-mono font-semibold">{s.band}</Td><Td><ChannelTag ch={knownCh(s.channel)} /></Td>
                <Td className="tabular">{s.attempts}</Td><Td className="tabular">{s.reach}</Td><Td className="tabular">{s.meaningful_replies}</Td>
                <Td className="tabular">{Number(s.spend_committed_inr).toFixed(2)}</Td><Td className="tabular">{s.replies_per_1000_inr}</Td>
                <Td className={cx('tabular', s.cap_breaches ? 'text-bad' : 'text-ok')}>{s.cap_breaches}</Td>
              </tr>
            ))}
            {t && (
              <tr className="bg-raised/40 font-semibold">
                <Td colSpan={2}>Total</Td><Td className="tabular">{t.attempts}</Td><Td className="tabular">{t.reach ?? '—'}</Td><Td className="tabular">{t.meaningful_replies}</Td>
                <Td className="tabular">{Number(t.spend_committed_inr).toFixed(2)}</Td><Td className="tabular">{t.replies_per_1000_inr}</Td><Td className={cx('tabular', t.cap_breaches ? 'text-bad' : 'text-ok')}>{t.cap_breaches}</Td>
              </tr>
            )}
          </tbody>
        </table>
        {!segs.length && <Empty>{res.loading ? 'Loading…' : 'No touches for this campaign day.'}</Empty>}
      </div>
      {!!r?.notes?.length && <div className="px-5 py-3 text-[11.5px] text-faint">{r.notes.join(' ')}</div>}
    </Card>
  )
}
