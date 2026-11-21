import { useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import {
  Activity, AlertTriangle, Ambulance, ArrowDownRight, ArrowRight, Check, ChevronDown, ClipboardCheck,
  Clock3, LocateFixed, Pause, Play, Radio, RotateCcw, Send, SkipForward, TrafficCone, Truck,
} from 'lucide-react'
import { api, useSnapshot } from './connection'
import { CityMap } from './CityMap'
import { ActivityLog, Fleet, Results, Schedule } from './Workstation'
import { clock, profileLabels, scenarioLabels, terminalStatuses, vehicleStatus, words } from './model'
import type { Geometry, Interpretation, Network, Proposal, Scenario, Snapshot, World } from './model'
import './style.css'

const example = 'Vozilo R2 je obstalo. Komplet K1 je ostal v vozilu.'

function Overview({ world, state }: { world: World; state: Snapshot }) {
  const active = Object.values(world.tasks).filter((task) => !terminalStatuses.includes(task.status))
  const available = Object.values(world.vehicles).filter((unit) => unit.mode === 'ambulance' && vehicleStatus(unit, world) === 'available').length
  const kit = world.assets.K1
  const kitLocation = kit?.location.kind === 'hospital'
    ? Object.values(world.receiving_sites).find((site) => site.node_id === kit.location.ref_id)?.id
    : kit?.location.kind === 'roadside' ? 'Roadside' : kit?.location.ref_id
  const description = kit?.location.kind === 'hospital' ? 'Delivered' : kit?.location.kind === 'roadside' ? 'Awaiting recovery' : 'Tracked custody'
  return <div className="overview">
    <div className="overview-card"><span className="metric-icon teal"><Activity size={17}/></span><div><small>Active tasks</small><strong>{active.length} <em>{active.filter((task) => task.priority === 'critical').length} critical</em></strong></div></div>
    <div className="overview-card"><span className="metric-icon blue"><Truck size={17}/></span><div><small>Available units</small><strong>{available}<em> / 5 ambulances</em></strong></div></div>
    <div className="overview-card"><span className="metric-icon amber"><LocateFixed size={17}/></span><div><small>Equipment K1</small><strong>{kitLocation ?? '—'} <em>{description}</em></strong></div></div>
    <div className="overview-card"><span className="metric-icon violet"><ClipboardCheck size={17}/></span><div><small>Committed plan</small><strong>r{world.plan_revision} <em>{state.proposals.length ? `${state.proposals.length} choices` : 'Human approval'}</em></strong></div></div>
  </div>
}

function PlanCard({ plan, selected, index, onSelect }: { plan: Proposal; selected: boolean; index: number; onSelect: () => void }) {
  const reassignments = plan.assignments.filter((task) => task.previous_vehicle_id && task.previous_vehicle_id !== task.vehicle_id).length
  const recovery = plan.assignments.find((task) => task.task_id.includes('RECOVERY'))
  return <button className={`plan-card ${selected ? 'selected' : ''}`} onClick={onSelect} aria-pressed={selected}>
    <span className="plan-card-head"><span className="plan-letter">{String.fromCharCode(65 + index)}</span>
      <strong>{profileLabels[plan.policy === 'b0' ? 'baseline' : plan.profile]}</strong>{selected && <Check size={15}/>}</span>
    <span className="plan-summary">{recovery ? `${recovery.vehicle_id} recovers K1 at ${clock(recovery.pickup_s)}`
      : `${plan.assignments.length} projected assignments · ${plan.unserved_task_ids.length} unserved`}</span>
    <span className="plan-metrics"><span>{plan.estimated_deadline_misses.length} deadline misses</span><span>{reassignments} reassigned</span></span>
    {plan.equivalent_policies.length > 0 && <span className="plan-equivalent">Same schedule as {plan.equivalent_policies.map((profile) => profileLabels[profile]).join(', ')}</span>}
  </button>
}

function Dispatch({ state, selection, selectPlan, busy, error, command, refresh, road }: {
  state: Snapshot; selection?: Proposal; selectPlan: (plan: string) => void; busy: boolean; error: string
  command: (path: string, body?: object) => Promise<Snapshot | undefined>
  refresh: (path: string, body: object) => Promise<Interpretation | undefined>; road: string | null
}) {
  const [message, setMessage] = useState('')
  const [interpretation, setInterpretation] = useState<Interpretation | null>(null)
  const ready = state.simulator !== 'unavailable' && Boolean(state.world)
  const canApprove = selection && selection.based_on_plan_revision === state.world?.plan_revision
  const impacted = state.activity.findLast((event) => event.impacted_tasks?.length)
  const kit = state.world?.assets.K1
  const breakdownAvailable = state.scenario !== 'breakdown' || (
    state.world?.vehicles.R2?.status !== 'broken' && kit?.location.kind === 'vehicle' && kit.location.ref_id === 'R2'
  )

  async function interpret() {
    const result = await refresh('/api/events/interpret', { message })
    setInterpretation(result ?? null)
  }
  async function applyReport() {
    if (!interpretation?.report_id) return
    const result = await command('/api/events', { trigger: 'report', report_id: interpretation.report_id })
    if (result) setInterpretation(null)
  }

  return <aside className="panel dispatch-panel" aria-label="Dispatcher assistant">
    <header className="panel-head"><div className="section-title"><Radio size={16}/><h2>Dispatcher</h2></div><span className="tag">OPERATOR</span></header>
    <div className="dispatch-body">
      <div className="dispatch-label"><label htmlFor="crew-report">Crew report</label><button className="text-button" onClick={() => { setMessage(example); setInterpretation(null) }}>Use example</button></div>
      <textarea id="crew-report" value={message} maxLength={1200} placeholder="Paste a crew report in Slovenian…" onChange={(event) => { setMessage(event.target.value); setInterpretation(null) }}/>
      <div className="agent-mode"><i/>{interpretation?.source ?? state.agent_mode}</div>
      <button className="primary-button" disabled={!ready || busy || !message.trim()} onClick={interpret}><Send size={14}/> Interpret report</button>
      {interpretation && <div className={`interpretation ${interpretation.valid ? '' : 'invalid'}`}>
        <strong>{interpretation.valid ? <Check size={14}/> : <AlertTriangle size={14}/>}
          {interpretation.valid ? 'Validated report' : 'Report needs attention'}</strong>
        <p>{interpretation.summary ?? interpretation.error}</p>
        {interpretation.valid && <>
          <small>{words(interpretation.draft?.type ?? '')} · {interpretation.draft?.vehicle_id}
            {interpretation.draft?.equipment_ids_reported_stranded.length ? ` · ${interpretation.draft.equipment_ids_reported_stranded.join(', ')}` : ''}</small>
          <button disabled={busy} onClick={applyReport}>Apply incident and compute choices <ArrowRight size={14}/></button>
        </>}
      </div>}
      <div className="scenario-actions">
        <button disabled={!ready || busy || state.script_applied || !breakdownAvailable} onClick={() => void command('/api/events', { trigger: 'scenario' })}>
          <AlertTriangle size={14}/>{state.script_applied || !breakdownAvailable ? 'Reset to replay scripted incident' : 'Trigger scripted incident'}
        </button>
        {road && <button disabled={!ready || busy || Boolean(state.world && road in state.world.closed_edges)}
          onClick={() => void command('/api/events', { trigger: 'road_closure', edge_id: road, duration_s: 240 })}><TrafficCone size={14}/> Close selected road for 4 min</button>}
      </div>
      {error && <p className="error-box" role="alert"><AlertTriangle size={14}/>{error}</p>}
      <div className="plans-head">
        <div><h3>Repair choices</h3><small>Estimated from SUMO road ETAs</small></div>
        <button className="icon-button" title="Regenerate choices" aria-label="Regenerate choices" disabled={!ready || busy}
          onClick={() => void command('/api/plans/propose', {})}><RotateCcw size={15}/></button>
      </div>
      {impacted?.impacted_tasks && <p className="impacted">Affected tasks · {impacted.impacted_tasks.join(', ')}</p>}
      {state.proposals.length === 0 && state.proposal_issues.length === 0 && <p className="proposal-empty">Interpret a report or trigger an incident to compare feasible schedules.</p>}
      {state.proposal_issues.length > 0 && <details className="plan-issues"><summary>{state.proposal_issues.length} feasibility notes</summary>
        <ul>{[...new Set(state.proposal_issues)].map((issue) => <li key={issue}>{issue}</li>)}</ul>
      </details>}
      {state.proposals.map((plan, i) => <PlanCard key={plan.proposal_id} index={i} plan={plan}
        onSelect={() => selectPlan(plan.proposal_id)} selected={plan.proposal_id === selection?.proposal_id}/>)}
      {selection && <div className="approve-block">
        <details className="assignment-details"><summary>Assignments and estimated ETAs</summary>
          <ul>{selection.assignments.map((assignment) => <li key={assignment.task_id}>
            <strong>{assignment.task_id}</strong> · {assignment.vehicle_id} · {clock(assignment.pickup_s)} → {clock(assignment.delivery_s)}
          </li>)}</ul>
          {selection.unserved_task_ids.length > 0 && <p>Unserved · {selection.unserved_task_ids.join(', ')}</p>}
        </details>
        {!canApprove && <p className="error-box">The plan version changed. Regenerate choices.</p>}
        <button className="primary-button" disabled={busy || !ready || !canApprove} onClick={() => void command(
          `/api/plans/${selection.proposal_id}/approve`,
          { expected_plan_revision: selection.based_on_plan_revision, idempotency_key: selection.proposal_id },
        )}><Check size={16}/> Approve selected plan</button>
      </div>}
    </div>
  </aside>
}

function App() {
  const { state, connection, accept } = useSnapshot()
  const [geometry, setGeometry] = useState<Geometry | null>(null)
  const [selected, setSelected] = useState<string | null>(null)
  const [edge, setEdge] = useState<string | null>(null)
  const [selectedPlan, selectPlan] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [results, setResults] = useState(false)
  const run = state?.run_id
  const map = state?.network
  const selection = state?.proposals?.find((plan) => plan.proposal_id === selectedPlan) ?? state?.proposals?.[0]
  const live = Boolean(state?.world && state.simulator !== 'unavailable' && connection !== 'offline')
  const playing = state?.simulator === 'running'

  useEffect(() => {
    setSelected(null)
    setEdge(null)
    selectPlan(null)
    setGeometry(null)
    if (!run || !map) return
    let active = true
    api<Geometry>('/api/network').then((value) => {
      if (active && value.network === map) setGeometry(value)
    }).catch((failure: Error) => { if (active) setError(failure.message) })
    return () => { active = false }
  }, [run, map])

  async function send<T>(path: string, body: object = {}): Promise<T | undefined> {
    setBusy(true)
    setError('')
    try {
      return await api<T>(path, body)
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : 'Command failed')
    } finally {
      setBusy(false)
    }
  }
  async function command(path: string, body: object = {}) {
    const result = await send<Snapshot | { committed: boolean }>(path, body)
    if (!result) return undefined
    const next = 'sequence' in result ? result : await api<Snapshot>('/api/state').catch(() => undefined)
    if (next) accept(next)
    return next
  }
  const load = (network: Network, scenario: Scenario) => void command('/api/simulation/load', { network, scenario })
  const control = (operation: string, body?: object) => void command(`/api/simulation/${operation}`, body)
  const select = (id: string) => setSelected((current) => current === id ? null : id)

  return <div className="app">
    <header className="topbar">
      <div className="brand"><span className="brand-icon"><Ambulance size={23}/></span><div><h1>RescueFlow <span>AI</span></h1><small>DISPATCH WORKSTATION</small></div></div>
      <div className="top-controls">
        <span className="simulation-tag"><i/> SIMULATION</span>
        <div className="sim-clock"><Clock3 size={17}/><time>{clock(state?.world?.sim_time_s)}</time></div>
        <div className="playback">
          <button className="play-button" disabled={!live || busy} title={playing ? 'Pause' : 'Play'} aria-label={playing ? 'Pause' : 'Play'}
            onClick={() => control(playing ? 'pause' : 'play')}>{playing ? <Pause size={17}/> : <Play size={17}/>}</button>
          <button disabled={!live || busy || playing} title="Advance 30 seconds" aria-label="Advance 30 seconds" onClick={() => control('step', { steps: 30 })}><SkipForward size={17}/></button>
          <button disabled={!state?.run_id || busy} title="Reset run" aria-label="Reset run" onClick={() => control('reset')}><RotateCcw size={16}/></button>
          <select aria-label="Simulation speed" disabled={!live || busy} value={state?.speed ?? 1} onChange={(event) => control('speed', { speed: Number(event.target.value) })}>
            {[1, 5, 15, 30].map((s) => <option key={s} value={s}>{s}×</option>)}
          </select>
        </div>
      </div>
      <button className={`results-toggle ${results ? 'selected' : ''}`} aria-expanded={results} onClick={() => setResults(!results)}><Activity size={16}/> Results <ChevronDown size={14}/></button>
    </header>
    <main>
      <div className="command-bar">
        <div className="view-title"><h2>Operations overview</h2>
          <span className={`connection ${connection}`}><i/>{connection === 'live' ? 'Live connection' : connection === 'polling' ? 'Polling' : connection === 'offline' ? 'Disconnected' : 'Connecting'}</span>
          {state && <span className="run-state">{state.demo_pause ? 'Demo pause · compare choices' : words(state.simulator)}</span>}
        </div>
        <div className="scenario-picker">
          <label>Network <select aria-label="Network" value={map ?? 'ljubljana'} disabled={busy || !state}
            onChange={(event) => load(event.target.value as Network, state?.scenario ?? 'breakdown')}>
            <option value="ljubljana">Ljubljana</option><option value="grid">Training grid</option>
          </select></label>
          <label>Scenario <select aria-label="Scenario" value={state?.scenario ?? 'breakdown'} disabled={busy || !state}
            onChange={(event) => load(map ?? 'ljubljana', event.target.value as Scenario)}>
            {Object.entries(scenarioLabels).map(([id, label]) => <option value={id} key={id}>{label}</option>)}
          </select></label>
        </div>
      </div>
      {state?.error && <p role="alert" className="error-box"><AlertTriangle size={16}/>{state.error}</p>}
      {!state?.world && <section className="panel empty-state"><Activity size={25}/><h2>{state?.error ? 'Simulator unavailable' : 'Connecting to the simulator'}</h2>
        <p>{connection === 'offline' ? 'Start the local FastAPI server to connect.' : 'The workstation will display the SUMO session when it is ready.'}</p></section>}
      {state?.world && <>
        <Overview world={state.world} state={state}/>
        {results && <Results metrics={state.metrics} disabled={!live || busy} onPolicy={(baseline) => void command('/api/plans/propose', { profiles: baseline ? ['baseline'] : ['urgent_first', 'minimum_disruption'] })}/>}
        <div className="workspace">
          <Fleet world={state.world} selected={selected} onSelect={select}/>
          <CityMap key={run} geometry={geometry} snapshot={state} proposal={selection}
            selected={selected} onSelect={select} edge={edge} onEdge={setEdge}/>
          <Dispatch key={run} state={state} selection={selection} selectPlan={selectPlan} busy={busy || connection === 'offline'}
            error={error} command={command} refresh={send} road={edge}/>
        </div>
        <div className="bottom-grid"><Schedule snapshot={state} proposal={selection} selected={selected} onSelect={select}/><ActivityLog snapshot={state}/></div>
      </>}
      <footer><span>RescueFlow AI · AdaHack 2026</span><span><ArrowDownRight size={13}/> Synthetic incidents · Human dispatcher approval</span></footer>
    </main>
  </div>
}

createRoot(document.getElementById('root')!).render(<App/>)
