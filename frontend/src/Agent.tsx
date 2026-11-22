import { useEffect, useMemo, useRef, useState } from 'react'
import { clock, isStale, needsUnit, nextStop, openTasks, plannedArrival, deadline, planActions, planHeadline, priorityRank, shortId, timeOfDay, unitColor, unitState, words } from './model'
import type { Geometry, Interpretation, Place, Proposal, Recommendation, Snapshot, World } from './model'
import { logEntries, triggerOf } from './log'

interface Props {
  state: Snapshot; geometry: Geometry | null; place: Place; plan?: Proposal; busy: string | null; offline: boolean
  onPlan: (id: string) => void; onApprove: (signals: boolean, plan: Proposal) => void; onReject: () => void; onAsk: () => void
  onInterpret: (message: string) => Promise<Interpretation | undefined>; onReport: (id: string) => Promise<boolean>
  onSelect: (id: string) => void
  onEvent: (kind: string, ref: string | null) => void; onNewCall: () => void; onCloseRoad: () => void; closing: boolean
}

/** The mark that identifies everything the AI proposes. */
export const Mark = ({ size = 14 }: { size?: number }) =>
  <svg className="mark" width={size} height={size} viewBox="0 0 14 14" aria-hidden="true"><path d="M7 0 14 7 7 14 0 7Z"/><path className="core" d="M7 4 10 7 7 10 4 7Z"/></svg>

const delta = (value: number) => clock(Math.abs(value))
const list = (ids: string[]) => ids.length < 2 ? ids.join('') : `${ids.slice(0, -1).join(', ')} and ${ids.at(-1)}`

/** Calculated reasons for a plan, compared with the best of the others. */
function reasons(plan: Proposal, others: Proposal[], world: World): string[] {
  const found: string[] = []
  const m = plan.metrics
  const worst = m.worst_critical_response_eta_s
  const ids = Object.keys(m.critical_response_eta_s)
  if (worst != null) {
    const rivals = others.map((o) => o.metrics.worst_critical_response_eta_s).filter((v): v is number => v != null)
    const gap = rivals.length ? Math.min(...rivals) - worst : 0
    const who = ids.length > 1 ? `Every priority 1 patient reached within ${clock(worst)}` : `${ids[0]} reached in ${clock(worst)}`
    found.push(gap > 5 ? `${who}, ${delta(gap)} sooner than the next plan` : gap < -5 ? `${who}, ${delta(gap)} later than the fastest plan` : who)
  }
  if (m.unserved_task_ids.length) found.push(`Leaves ${list(m.unserved_task_ids)} without a unit`)
  if (m.waiting_call_ids?.length) found.push(`${list(m.waiting_call_ids)} ${m.waiting_call_ids.length > 1 ? 'wait' : 'waits'} for the first unit that frees up`)
  if (m.estimated_deadline_misses.length) found.push(`${list(m.estimated_deadline_misses)} expected late`)
  else if (others.some((o) => o.metrics.estimated_deadline_misses.length)) found.push('All deadlines met')
  const moved = m.reassigned_task_ids
  if (moved.length) found.push(`Changes unit for ${list(moved)}`)
  else if (others.some((o) => o.metrics.reassigned_task_ids.length)) found.push('No crew loses a job it already has')
  if (!others.length && !planActions(plan, world).length) found.push('Every planning method arrives at the plan you already have')
  const late = plan.estimated_deadline_misses.filter((id) => !m.estimated_deadline_misses.includes(id) && world.tasks[id])
  if (late.length) found.push(`${list(late)} may arrive late`)
  return found
}

/** Why an option ranks below the first one, in one line. */
function tradeoff(plan: Proposal, best: Proposal) {
  const parts: string[] = []
  const [a, b] = [plan.metrics.worst_critical_response_eta_s, best.metrics.worst_critical_response_eta_s]
  if (a != null && b != null && Math.abs(a - b) > 5) parts.push(a > b ? `${delta(a - b)} slower to a priority 1 patient` : `${delta(a - b)} faster to the last priority 1 patient`)
  const moved = plan.metrics.reassigned_task_ids.length - best.metrics.reassigned_task_ids.length
  if (moved > 0) parts.push(`changes ${moved} more job${moved > 1 ? 's' : ''}`)
  const late = plan.metrics.estimated_deadline_misses.length - best.metrics.estimated_deadline_misses.length
  if (late > 0) parts.push(`${late} more expected late`)
  const open = plan.metrics.unserved_task_ids.length - best.metrics.unserved_task_ids.length
  if (open > 0) parts.push(`${open} more without a unit`)
  if (moved < 0) parts.push(`changes ${-moved} fewer job${moved < -1 ? 's' : ''}`)
  return parts.join(', ') || 'Similar result, different units'
}

/** Jobs worth showing for a decision: what triggered it, and what the plan moves or delays. */
function timeRows(plan: Proposal, state: Snapshot, focus: string[]) {
  const world = state.world!
  const keep = state.proposals.find((p) => p.keeps)
  const before = Object.keys(state.eta_before).length ? state.eta_before : keep?.arrivals ?? {}
  const actions = Object.fromEntries(planActions(plan, world).map((a) => [a.task, a]))
  const ids = new Set([...focus.flatMap((id) => [id, `${id}-RECOVERY`]), ...Object.keys(actions), ...plan.metrics.unserved_task_ids,
    ...Object.entries(plan.arrivals).filter(([id, a]) => before[id] && (Math.abs(a.eta_s - before[id].eta_s) > 15 || Math.abs((a.done_s ?? 0) - (before[id].done_s ?? 0)) > 15)).map(([id]) => id)])
  return [...ids].filter((id) => world.tasks[id] && !['completed', 'interrupted'].includes(world.tasks[id].status)).map((id) => {
    const now = plan.arrivals[id]
    const was = before[id] ?? before[id.replace(/-RECOVERY$/, '')]
    return { id, task: world.tasks[id], unit: now?.unit ?? null, eta: now?.eta_s ?? null, from: actions[id]?.from ?? null,
      change: now && was && was.unit !== null ? now.eta_s - was.eta_s : null, done: now?.stop === 'pickup' ? now.done_s : null,
      doneChange: now?.done_s != null && was?.done_s != null && was.unit !== null ? now.done_s - was.done_s : null, waits: Boolean(now && now.unit === null), focus: focus.includes(id) || focus.includes(id.replace(/-RECOVERY$/, '')) }
  }).sort((a, b) => Number(b.focus) - Number(a.focus) || priorityRank(a.task) - priorityRank(b.task) || (a.eta ?? 1e9) - (b.eta ?? 1e9)).slice(0, 6)
}

const signed = (value: number) => Math.abs(value) < 5 ? 'same' : `${value > 0 ? '+' : '−'}${clock(Math.abs(value))}`

function Times({ plan, state, focus, place, onSelect }: { plan: Proposal; state: Snapshot; focus: string[]; place: Place; onSelect: (id: string) => void }) {
  const rows = timeRows(plan, state, focus)
  if (!rows.length) return null
  return <table className="times"><thead><tr><th>Unit</th><th>Job</th><th>Arrives in</th><th>Change</th></tr></thead>
    <tbody>{rows.map((row) => <tr key={row.id} className={row.focus ? 'focus' : ''}>
      <td>{row.unit ? <button className="callsign" style={{ background: unitColor(row.unit) }} onClick={() => onSelect(row.unit!)}>{row.unit}</button>
        : <span className="nobody">{row.waits ? 'next free' : 'nobody'}</span>}</td>
      <td><button onClick={() => onSelect(row.id)}><b className={`prio p${priorityRank(row.task)}`}>{priorityRank(row.task)}</b>{shortId(row.id)}</button>
        <small>{place(plan.arrivals[row.id]?.stop === 'dropoff' ? row.task.dropoff_node : row.task.pickup_node)}{row.from ? `, was ${row.from}` : ''}</small></td>
      <td className="eta">{row.eta != null ? clock(row.eta) : '–'}{row.focus && row.done != null && <small>hospital {clock(row.done)}</small>}</td>
      <td className={`change ${row.change == null || Math.abs(row.change) < 5 ? '' : row.change > 0 ? 'worse' : 'better'}`}>
        {row.change == null ? '' : signed(row.change)}
        {row.focus && row.done != null && row.doneChange != null && <small className={row.doneChange > 5 ? 'worse' : ''}>{signed(row.doneChange)}</small>}</td>
    </tr>)}</tbody></table>
}

function Source({ recommendation, count }: { recommendation: Recommendation | null; count: number }) {
  if (!recommendation) return null
  const { source, model, latency_ms: ms, cache_hit: cached, error } = recommendation
  if (source === 'openai-decisions') return <p className="source">{count} feasible plans computed and checked, ranked by {model}{ms ? ` in ${(ms / 1000).toFixed(1)} s` : ''}{cached ? ' (stored answer)' : ''}</p>
  if (source === 'single-feasible-option') return <p className="source">Only one feasible plan exists, so there is nothing to rank</p>
  return <p className="source warn">AI unavailable{error ? ` (${error})` : ''}. Plans ranked by fixed rules</p>
}

function Decision(props: Props & { since: number }) {
  const { state, plan, busy, place, onPlan, onApprove, onReject, onAsk, onSelect, since } = props
  const world = state.world!
  const [askLights, setAskLights] = useState(false)
  const plans = state.proposals
  const best = plans.find((p) => p.recommended) ?? plans[0]
  const chosen = plan ?? best
  const single = plans.length === 1
  const byAI = state.recommendation?.source !== 'deterministic-fallback'
  const seconds = useTicker(since)
  const stale = isStale(state)
  const trigger = triggerOf(state)
  const road = trigger.kind === 'road_closure'
  const keep = plans.find((p) => p.keeps)
  // What the times in the list are about: the trigger, or every open priority 1 patient.
  const named = trigger.tasks.filter((id) => world.tasks[id] || world.tasks[`${id}-RECOVERY`])
  const carried = trigger.units.flatMap((unit) => Object.entries(state.eta_before).filter(([, a]) => a.unit === unit).map(([id]) => id))
  const focus = named.length ? named : carried.length ? carried.slice(0, 3) : Object.keys(best.metrics.critical_response_eta_s)
  const etaOf = (option: Proposal) => focus.flatMap((id) => [id, `${id}-RECOVERY`]).filter((id) => option.arrivals[id])
    .sort((a, b) => priorityRank(world.tasks[a]) - priorityRank(world.tasks[b])).slice(0, 2).map((id) => {
      const [now, was] = [option.arrivals[id], state.eta_before[id]]
      // A closed road beyond the patient changes the time to hospital, not to the patient.
      const later = road && was && now.done_s != null && Math.abs(now.eta_s - was.eta_s) < 5
      return { id, label: later && now.stop === 'pickup' ? `${shortId(id)} to hospital` : shortId(id), eta_s: later ? now.done_s! : now.eta_s }
    })
  const newLights = chosen.signal_priority_units.filter((unit) => chosen.lights[unit] > 0)
  const blocked = Boolean(busy) || stale || props.offline

  function decide(option: Proposal) {
    if (option.signal_priority_units.some((unit) => option.lights[unit] > 0)) { onPlan(option.proposal_id); setAskLights(true) }
    else onApprove(false, option)
  }
  if (askLights) return <section className="decision" aria-live="polite">
    <p className="trigger"><b>Plan chosen</b><span>{planHeadline(chosen, world)}</span></p>
    <p className="eyebrow"><span>One more question</span></p>
    <h2>Turn the traffic lights green for {list(newLights)}?</h2>
    <table className="times"><thead><tr><th>Unit</th><th>Going to</th><th>Lights</th><th>Arrives in</th></tr></thead><tbody>
      {newLights.map((unit) => { const [id, next] = Object.entries(chosen.arrivals).find(([, a]) => a.unit === unit) ?? ['', null]; return <tr key={unit} className="focus">
        <td><span className="callsign" style={{ background: unitColor(unit) }}>{unit}</span></td>
        <td>{id && world.tasks[id] ? <><b className={`prio p${priorityRank(world.tasks[id])}`}>{priorityRank(world.tasks[id])}</b> {shortId(id)}</> : ''}</td>
        <td className="eta">{chosen.lights[unit]}</td><td className="change">{next ? clock(next.eta_s) : ''}</td></tr> })}</tbody></table>
    <p className="hint">Each light turns green shortly before the ambulance arrives and returns to normal after it passes. Cross traffic waits meanwhile.</p>
    <div className="decide">
      <button className="approve" disabled={blocked} onClick={() => onApprove(true, chosen)}>Yes, turn green<kbd>Enter</kbd></button>
      <button data-key="x" disabled={blocked} onClick={() => onApprove(false, chosen)}>No, normal lights<kbd>X</kbd></button>
      <button disabled={Boolean(busy)} onClick={() => setAskLights(false)}>Back</button>
    </div>
  </section>
  return <section className="decision" aria-live="polite">
    {trigger.title && <p className="trigger"><b>{trigger.title}</b><span>{named.map((id) => world.tasks[id] ? `${id}, ${place(world.tasks[id].pickup_node)}${state.notes[id] ? `, ${state.notes[id]}` : ''}` : id).join(' · ')}{road && !named.length ? `on the path of ${list(trigger.units)}` : ''}</span></p>}
    <p className="eyebrow"><span>{trigger.question}</span><time title="Time since the question">{clock(seconds)}</time></p>
    <h2>{planHeadline(chosen, world)}</h2>
    <Times plan={chosen} state={state} focus={focus} place={place} onSelect={onSelect}/>
    <ul className="why">{reasons(chosen, plans.filter((p) => p !== chosen), world).map((r) => <li key={r}>{r}</li>)}</ul>
    {!single && <div className="options" role="radiogroup" aria-label="Options">
      {plans.map((option, i) => { const etas = etaOf(option); return <button key={option.proposal_id} role="radio" aria-checked={option === chosen}
        className={`${option === chosen ? 'chosen' : ''} ${option === best ? 'best' : ''}`} onClick={() => onPlan(option.proposal_id)}>
        <kbd>{i + 1}</kbd>
        <span className="option-text"><strong>{planHeadline(option, world)}</strong>
          <small>{option === best ? (byAI ? `AI’s choice${option.ai_probability != null ? `, ${Math.round(option.ai_probability * 100)}%` : ''}` : 'First by the fixed rules') : tradeoff(option, best)}</small></span>
        <span className="option-eta">{etas.length ? etas.map((eta) => <span key={eta.id}><b>{clock(eta.eta_s)}</b><small>{eta.label}</small></span>) : <span><b>–</b><small>no unit</small></span>}</span>
      </button> })}
      <p className="options-key"><span>Number key views an option</span><span>Expected arrival</span></p>
    </div>}
    {stale && <p className="notice">The situation changed. Updating the options…</p>}
    <div className="decide">
      <button className="approve" disabled={blocked} onClick={() => decide(chosen)}>
        {chosen.keeps ? (chosen.reroutes.length ? 'Reroute' : 'Keep the plan') : single ? 'Confirm' : chosen === best ? 'Approve' : `Approve option ${plans.indexOf(chosen) + 1}`}<kbd>Enter</kbd></button>
      {keep && keep !== chosen ? <button data-key="x" disabled={blocked} onClick={() => decide(keep)}>{keep.reroutes.length ? 'Reroute only' : 'Keep plan'}<kbd>X</kbd></button>
        : !keep && <button data-key="x" disabled={Boolean(busy)} onClick={onReject}>Decide later<kbd>X</kbd></button>}
      <button disabled={Boolean(busy)} onClick={onAsk}>Recalculate<kbd>R</kbd></button>
    </div>
    <Source recommendation={state.recommendation} count={plans.length}/>
  </section>
}

function Watching({ state, geometry, place, busy, onAsk, onSelect }: Props) {
  const world = state.world!
  const now = world.sim_time_s
  const open = openTasks(world).filter((task) => needsUnit(task))
  const risks = openTasks(world).flatMap((task) => {
    const arrival = plannedArrival(task, world)
    const late = arrival == null ? 0 : Math.max(arrival, now) - deadline(task)
    return late > 0 && Number.isFinite(late) ? [{ task, late }] : []
  })
  const next = Object.values(world.vehicles).flatMap((unit) => {
    const stop = nextStop(unit)
    return stop?.task_id && unit.status !== 'broken' ? [{ unit, stop, at: Math.max(stop.arrival_s ?? Infinity, now) }] : []
  }).sort((a, b) => a.at - b.at).slice(0, 3)
  const free = Object.values(world.vehicles).filter((unit) => unitState(unit, state, geometry).key === 'free')
  return <section className="watching">
    {open.length > 0 ? <>
      <h2>{open.length === 1 ? `${open[0].id} has no unit` : `${open.length} jobs have no unit`}</h2>
      <p>{free.length ? `${list(free.map((u) => u.id))} ${free.length > 1 ? 'are' : 'is'} free. I can compare plans now.` : 'No unit is free. I will suggest a plan as soon as one is.'}</p>
      <button className="approve" disabled={Boolean(busy)} onClick={onAsk}>Suggest a plan<kbd>R</kbd></button>
    </> : <>
      <h2>{risks.length ? `${list(risks.map((r) => r.task.id))} running late` : 'Every job has a unit'}</h2>
      <p>{risks.length ? 'A different plan may help.' : 'I will speak up when something changes.'}</p>
      {risks.length > 0 && <button className="approve" disabled={Boolean(busy)} onClick={onAsk}>Look for a better plan<kbd>R</kbd></button>}
    </>}
    <ul className="watchlist">
      {Object.entries(state.review_at).map(([id, at]) => <li key={`review-${id}`} className="checking"><button onClick={() => onSelect(id)}>{shortId(id)}</button>
        <span>{world.tasks[id]?.assigned_vehicle_id ? `${world.tasks[id].assigned_vehicle_id} on the way, ` : 'no unit, '}I look again in</span><time>{clock(Math.max(0, at - now))}</time></li>)}
      {risks.map(({ task, late }) => <li key={task.id} className="risk"><button onClick={() => onSelect(task.id)}>{task.id}</button><span>expected {clock(late)} after its deadline</span></li>)}
      {next.map(({ unit, stop, at }) => <li key={unit.id}><button onClick={() => onSelect(unit.id)}>{unit.id}</button>
        <span>{unitState(unit, state, geometry).key === 'hospital' ? 'handing over at ' : unitState(unit, state, geometry).key === 'scene' ? 'on scene, ' : stop.kind === 'pickup' ? `to ${stop.task_id}, ` : `brings ${stop.task_id} to `}{place(stop.node_id)}</span>
        <time>{timeOfDay(at)}</time></li>)}
    </ul>
  </section>
}

function useTicker(since: number) {
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [])
  return Math.max(0, Math.round((now - since) / 1000))
}

/** What the dispatcher can tell the AI. */
function Tell({ state, geometry, busy, offline, onEvent, onNewCall, onCloseRoad, closing }: Props) {
  const [asking, setAsking] = useState<'breakdown' | 'vehicle_delay' | null>(null)
  const units = Object.values(state.world!.vehicles).filter((unit) => unit.status !== 'broken')
  const blocked = Boolean(busy) || offline
  return <div className="tell">
    {asking ? <><span>{asking === 'breakdown' ? 'Which unit broke down?' : 'Which unit is 2 min late?'}</span>
      {units.map((unit) => <button key={unit.id} className="callsign" style={{ background: unitColor(unit.id) }} disabled={blocked}
        title={unitState(unit, state, geometry).label} onClick={() => { setAsking(null); onEvent(asking, unit.id) }}>{unit.id}</button>)}
      <button onClick={() => setAsking(null)}>Cancel</button></>
    : closing ? <><span>Click the closed road on the map</span><button onClick={onCloseRoad}>Cancel</button></>
    : <><button disabled={blocked} onClick={onNewCall}>New emergency</button><button disabled={blocked} onClick={() => setAsking('breakdown')}>Breakdown</button>
      <button disabled={blocked} onClick={() => setAsking('vehicle_delay')}>Delay</button><button disabled={blocked} onClick={onCloseRoad}>Road closed</button></>}
  </div>
}

export function Agent(props: Props) {
  const { state, busy, offline } = props
  const [message, setMessage] = useState('')
  const [report, setReport] = useState<{ text: string; result: Interpretation } | null>(null)
  const deciding = state.proposals.length > 0
  const key = state.proposals[0]?.proposal_id ?? ''
  const since = useRef({ key, at: Date.now() })
  if (since.current.key !== key) since.current = { key, at: Date.now() }
  const rulesOnly = state.decision_mode.startsWith('Deterministic')
  const entries = useMemo(() => logEntries(state).filter((entry) => entry.by !== 'unit').slice(0, 14), [state])
  const decided = state.activity.filter((e) => e.kind === 'plan_approved')
  const followed = decided.filter((e) => e.message?.includes('as recommended')).length

  async function send() {
    const text = message.trim()
    if (!text) return
    const result = await props.onInterpret(text)
    if (result) { setReport({ text, result }); setMessage('') }
  }
  async function apply() {
    if (report?.result.report_id && await props.onReport(report.result.report_id)) setReport(null)
  }

  const status = offline ? 'Disconnected' : busy === 'plan' ? 'Comparing plans' : busy === 'report' ? 'Reading the report'
    : deciding ? 'Needs your decision' : 'Watching'
  return <aside className={`agent ${deciding ? 'deciding' : ''}`} aria-label="AI co-dispatcher">
    <header><Mark size={18}/><div><h1>AI co-dispatcher</h1>
      <small>{rulesOnly ? 'AI offline, fixed rules only' : state.decision_mode.split('·').at(-1)?.trim()}</small></div>
      <span className={`agent-status ${busy ? 'working' : ''}`}>{status}</span></header>
    {busy && <div className="progress" role="progressbar" aria-label={status}/>}
    <div className="agent-body">
      {report && <section className={`report ${report.result.valid ? '' : 'invalid'}`}>
        <p className="eyebrow"><span>Crew report</span></p>
        <blockquote>{report.text}</blockquote>
        {report.result.valid ? <>
          <h2>{words(report.result.draft?.type ?? '')}, {report.result.draft?.vehicle_id}</h2>
          <ul className="why">
            {report.result.draft?.equipment_ids_reported_stranded.map((id) => <li key={id}>{id} stranded in the vehicle</li>)}
            {report.result.impacted_tasks?.map((t) => <li key={t.task_id}>Affects {t.task_id}</li>)}
          </ul>
          <div className="decide"><button className="approve" disabled={Boolean(busy)} onClick={apply}>Correct, find a response</button>
            <button onClick={() => setReport(null)}>Discard</button></div>
        </> : <><p className="notice">{report.result.error ?? 'I could not turn this into an event.'}</p>
          <div className="decide"><button onClick={() => { setMessage(report.text); setReport(null) }}>Edit report</button></div></>}
      </section>}
      {deciding ? <Decision key={key} {...props} since={since.current.at}/> : !report && <Watching {...props}/>}
      {!deciding && state.proposal_issues.length > 0 && <p className="notice">No feasible plan. {[...new Set(state.proposal_issues)][0]}</p>}
      <section className="shared">
        <p className="eyebrow"><span>Shift log</span>{decided.length > 0 && <span>{followed} of {decided.length} suggestions taken as given</span>}</p>
        <ul>{entries.map((entry) => <li key={entry.key} className={entry.by}>
          <time>{timeOfDay(entry.at)}</time><b>{entry.by === 'ai' ? <Mark size={10}/> : entry.by === 'you' ? 'You' : 'Event'}</b><span>{entry.text}</span></li>)}</ul>
      </section>
    </div>
    <Tell {...props}/>
    <form className="composer" onSubmit={(event) => { event.preventDefault(); void send() }}>
      <textarea id="crew-report" rows={2} maxLength={1200} value={message} placeholder="Paste or type a crew report in Slovenian"
        aria-label="Crew report" onChange={(event) => setMessage(event.target.value)}
        onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); void send() } }}/>
      <button disabled={!message.trim() || Boolean(busy) || offline}>Read</button>
    </form>
  </aside>
}
