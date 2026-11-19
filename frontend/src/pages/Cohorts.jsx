import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { Plus, X, Eye, Save, ArrowUp, ArrowDown, Trash2, Layers, ArrowUpRight } from 'lucide-react'
import { useKairos, useDerived } from '../lib/store'
import { COHORT_FIELDS, RISK_TAGS, matchCohort } from '../lib/mock'
import { Button, Card, CardHead, Chip, Empty, Input, PageHeader, Td, Th, Toggle, cx, inr, pct } from '../components/ui'

const OPS = { number: ['>', '≥', '<', '≤', '=', '≠'], enum: ['=', '≠'] }
const fieldOf = (key) => COHORT_FIELDS.find((f) => f.key === key)

export const ruleText = (rules) =>
  rules.map((r) => `${fieldOf(r.field)?.label ?? r.field} ${r.op} ${r.field === 'riskTag' ? RISK_TAGS[r.value] ?? r.value : r.value}`).join(' and ')

export default function Cohorts() {
  const { state, dispatch } = useKairos()
  const d = useDerived()
  const everyone = useMemo(() => [state.hero, ...state.borrowers], [state])
  const [rules, setRules] = useState([{ field: 'dpd', op: '>', value: '45' }, { field: 'lastResponse', op: '=', value: 'No response' }])
  const [preview, setPreview] = useState(null)
  const [name, setName] = useState('')

  const sorted = [...state.cohorts].sort((a, b) => a.priority - b.priority)
  const stats = Object.fromEntries(d.byCohort.map((c) => [c.key, c]))
  const orphans = everyone.filter((b) => !b.cohortId).length
  const runPreview = () => setPreview(everyone.filter((b) => matchCohort(b, { rules })))
  const setRule = (i, patch) => { setRules(rules.map((r, k) => (k === i ? { ...r, ...patch } : r))); setPreview(null) }
  const save = () => {
    if (!name.trim() || !rules.length) return
    const id = 'K' + (state.cohorts.filter((c) => c.id.startsWith('K')).length + 1)
    dispatch({ type: 'SAVE_COHORT', cohort: { id, name: name.trim(), priority: 0, source: 'Custom', rules } })
    // custom cohorts go to the top so they take precedence over the broad lender tags
    setName(''); setPreview(null)
  }

  return (
    <>
      <PageHeader title="Cohorts" sub="Slice the portfolio, then plug campaigns into each slice. A loan belongs to the first cohort it matches, in priority order."
        chip={<Chip tone="warn">Synthetic data</Chip>} />

      <div className="space-y-5">
        <Card>
          <CardHead title="Build a cohort" sub="Any portfolio field — risk tag, DPD, region, EMI, bounces, NACH, last response, language" />
          <div className="space-y-2 px-5">
            {rules.map((r, i) => {
              const f = fieldOf(r.field)
              return (
                <div key={i} className="flex items-center gap-2">
                  <span className="w-10 text-right text-[12px] text-faint">{i ? 'and' : 'where'}</span>
                  <select value={r.field} onChange={(e) => { const nf = fieldOf(e.target.value); setRule(i, { field: nf.key, op: OPS[nf.type][0], value: nf.type === 'enum' ? nf.options[0] : '0' }) }}
                    className="h-9 w-56 rounded-lg border border-line bg-bg px-3 text-[13px]">
                    {COHORT_FIELDS.map((x) => <option key={x.key} value={x.key}>{x.label}</option>)}
                  </select>
                  <select value={r.op} onChange={(e) => setRule(i, { op: e.target.value })} className="h-9 w-20 rounded-lg border border-line bg-bg px-2 text-[13px]">
                    {OPS[f.type].map((o) => <option key={o}>{o}</option>)}
                  </select>
                  {f.type === 'enum'
                    ? <select value={r.value} onChange={(e) => setRule(i, { value: e.target.value })} className="h-9 w-60 rounded-lg border border-line bg-bg px-3 text-[13px]">
                        {f.options.map((o) => <option key={o} value={o}>{f.key === 'riskTag' ? RISK_TAGS[o] : o}</option>)}
                      </select>
                    : <Input type="number" value={r.value} onChange={(e) => setRule(i, { value: e.target.value })} className="w-40" />}
                  <button onClick={() => { setRules(rules.filter((_, k) => k !== i)); setPreview(null) }} className="grid h-9 w-9 place-items-center rounded-lg border border-line text-muted hover:text-fg"><X size={14} /></button>
                </div>
              )
            })}
            <div className="flex gap-2 pt-1 pl-12">
              <Button size="sm" icon={Plus} onClick={() => setRules([...rules, { field: 'riskTag', op: '=', value: 'C3' }])}>Add condition</Button>
              <Button size="sm" variant="primary" icon={Eye} onClick={runPreview} disabled={!rules.length}>Preview match</Button>
            </div>
          </div>
          <div className="mx-5 mt-4 overflow-hidden rounded-lg border border-line">
            {preview == null ? <Empty>Add conditions and click Preview match.</Empty> : (
              <>
                <div className="border-b border-line bg-raised/40 px-4 py-2 text-[13px]"><b>{preview.length}</b> loans match · {ruleText(rules)}</div>
                <table className="w-full">
                  <thead><tr><Th>Loan ID</Th><Th>Name</Th><Th>DPD</Th><Th>Risk tag</Th><Th>Region</Th><Th>Bounces</Th><Th>Last response</Th></tr></thead>
                  <tbody>{preview.slice(0, 8).map((b) => (
                    <tr key={b.id}><Td className="font-mono text-[12px]">{b.loans[0].id}</Td><Td>{b.name}</Td><Td>{b.dpd}</Td>
                      <Td className="text-muted">{RISK_TAGS[b.riskTag]}</Td><Td className="text-muted">{b.region}</Td>
                      <Td className="font-mono text-[12px] text-muted">{b.bounceString}</Td><Td className="text-muted">{b.lastResponse}</Td></tr>
                  ))}</tbody>
                </table>
                {preview.length > 8 && <div className="border-t border-line px-4 py-2 text-[12px] text-muted">+ {preview.length - 8} more</div>}
              </>
            )}
          </div>
          <div className="flex gap-2 px-5 py-4">
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Cohort name, e.g. High DPD · silent 3 days" />
            <Button variant="primary" icon={Save} onClick={save} disabled={!name.trim() || !rules.length}>Save cohort</Button>
          </div>
        </Card>

        <Card>
          <CardHead title="Saved cohorts — priority order" sub="Top of the list wins when a loan matches more than one cohort"
            right={<span className={cx('text-[12.5px]', orphans ? 'text-warn' : 'text-ok')}>{orphans ? `${orphans} loans fall under no cohort` : 'Every loan is in a cohort'}</span>} />
          <table className="w-full">
            <thead><tr><Th className="w-16">#</Th><Th>Cohort</Th><Th>Loans</Th><Th>Overdue</Th><Th>Collected</Th><Th>Response rate</Th><Th>Campaigns</Th><Th className="text-right">Actions</Th></tr></thead>
            <tbody>
              {sorted.map((c, i) => {
                const s = stats[c.id]
                const camps = state.campaigns.filter((k) => k.cohortIds.includes(c.id))
                return (
                  <tr key={c.id}>
                    <Td>
                      <div className="flex items-center gap-1">
                        <span className="grid h-6 w-6 place-items-center rounded-md bg-raised text-[11px] font-bold text-muted">{c.priority}</span>
                        <div className="flex flex-col">
                          <button disabled={!i} onClick={() => dispatch({ type: 'MOVE_COHORT', id: c.id, dir: -1 })} className="text-faint hover:text-fg disabled:opacity-20"><ArrowUp size={11} /></button>
                          <button disabled={i === sorted.length - 1} onClick={() => dispatch({ type: 'MOVE_COHORT', id: c.id, dir: 1 })} className="text-faint hover:text-fg disabled:opacity-20"><ArrowDown size={11} /></button>
                        </div>
                      </div>
                    </Td>
                    <Td><div className="font-semibold">{c.name}</div><div className="text-[12px] text-muted">{ruleText(c.rules)} · <span className="text-faint">{c.source}</span></div></Td>
                    <Td className="tabular">{s?.loans ?? 0}</Td>
                    <Td className="tabular">{s ? inr(s.overdue) : '—'}</Td>
                    <Td className="tabular">{s ? <span>{inr(s.collected)} <span className="text-muted">· {pct(s.pct)}</span></span> : '—'}</Td>
                    <Td className="tabular">{s ? pct(s.responseRate, 0) : '—'}</Td>
                    <Td><div className="flex flex-wrap gap-1">{camps.length ? camps.map((k) => <Chip key={k.id} tone={k.mode === 'FIXED' ? 'neutral' : 'info'}>{k.name}</Chip>) : <span className="text-[12px] text-warn">No campaign yet</span>}</div></Td>
                    <Td className="text-right whitespace-nowrap">
                      <Link to={`/control?scope=cohort&id=${c.id}`} className="mr-3 inline-flex items-center gap-1 text-[12.5px] text-muted hover:text-fg">Control Room <ArrowUpRight size={12} /></Link>
                      <button onClick={() => dispatch({ type: 'DELETE_COHORT', id: c.id })} className="text-muted hover:text-bad"><Trash2 size={14} /></button>
                    </Td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </Card>

        <Card>
          <CardHead title="Campaigns plugged into cohorts" sub="One cohort can run several campaigns; a campaign can serve several cohorts. Tick to attach."
            right={<Layers size={15} className="text-muted" />} />
          <div className="overflow-x-auto pb-2">
            <table className="w-full">
              <thead><tr><Th>Cohort</Th>{state.campaigns.map((k) => <Th key={k.id} className="text-center"><div className="mx-auto max-w-[140px] leading-tight">{k.name}<div className="text-[11px] font-normal text-faint">{k.status.toLowerCase()} · {k.mode.toLowerCase()}</div></div></Th>)}</tr></thead>
              <tbody>
                {sorted.map((c) => (
                  <tr key={c.id}>
                    <Td className="font-medium whitespace-nowrap">{c.name}</Td>
                    {state.campaigns.map((k) => (
                      <Td key={k.id} className="text-center">
                        <div className="inline-flex"><Toggle on={k.cohortIds.includes(c.id)} onChange={() => dispatch({ type: 'ATTACH_COHORT', campaignId: k.id, cohortId: c.id, on: !k.cohortIds.includes(c.id) })} /></div>
                      </Td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      </div>
    </>
  )
}

