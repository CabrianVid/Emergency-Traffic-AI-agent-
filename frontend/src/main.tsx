import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { api, useSnapshot } from './connection'
import { Agent } from './Agent'
import { CallQueue, Inspector, Log, Timeline, UnitBoard } from './Boards'
import { CityMap } from './CityMap'
import { Evaluation, SessionPanel } from './Session'
import { buildPlaces, isStale, needsUnit, openTasks, planActions, setAnchor, timeOfDay, unitState } from './model'
import type { Geometry, Interpretation, Network, Scenario, Snapshot } from './model'
import './style.css'

function useStored<T>(key: string, initial: T): [T, (value: T) => void] {
  const [value, setValue] = useState<T>(() => {
    try { return JSON.parse(localStorage.getItem(key) ?? 'null') ?? initial } catch { return initial }
  })
  return [value, (next) => {
    setValue(next)
    try { localStorage.setItem(key, JSON.stringify(next)) } catch { /* not remembered */ }
  }]
}

function Splitter({ axis, value, min, max, invert, onChange }: { axis: 'x' | 'y'; value: number; min: number; max: number; invert?: boolean; onChange: (value: number) => void }) {
  const start = useRef<{ at: number; value: number } | null>(null)
  const move = (delta: number, from = value) => onChange(Math.max(min, Math.min(max, from + (invert ? -delta : delta))))
  return <div className={`splitter ${axis}`} role="separator" tabIndex={0} aria-label="Resize pane" aria-orientation={axis === 'x' ? 'vertical' : 'horizontal'}
    aria-valuenow={value} aria-valuemin={min} aria-valuemax={max}
    onPointerDown={(event) => { event.currentTarget.setPointerCapture(event.pointerId); start.current = { at: axis === 'x' ? event.clientX : event.clientY, value } }}
    onPointerMove={(event) => { if (start.current) move((axis === 'x' ? event.clientX : event.clientY) - start.current.at, start.current.value) }}
    onPointerUp={() => { start.current = null }}
    onKeyDown={(event) => {
      const step = { ArrowLeft: -16, ArrowUp: -16, ArrowRight: 16, ArrowDown: 16 }[event.key]
      if (step) { event.preventDefault(); move(step) }
    }}/>
}

type MenuItem = { label: string; run: () => void; disabled?: boolean }
const typing = (target: EventTarget | null) => target instanceof HTMLElement && ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName)

function App() {
  const { state, connection, accept } = useSnapshot()
  const [geometry, setGeometry] = useState<Geometry | null>(null)
  const [selected, setSelected] = useState<string | null>(null)
  const [edge, setEdge] = useState<string | null>(null)
  const [selectedPlan, selectPlan] = useState<string | null>(null)
  const [busy, setBusy] = useState<'plan' | 'report' | 'other' | null>(null)
  const [error, setError] = useState('')
  const [session, setSession] = useState(false)
  const [evaluation, setEvaluation] = useState(false)
  const [menu, setMenu] = useState<{ x: number; y: number; items: MenuItem[] } | null>(null)
  const [dock, setDock] = useStored<'timeline' | 'log'>('dock', 'timeline')
  const [night, setNight] = useStored('night', false)
  const [satellite, setSatellite] = useStored('satellite', false)
  const [leftWidth, setLeftWidth] = useStored('left', 376)
  const [rightWidth, setRightWidth] = useStored('right', 432)
  const [dockHeight, setDockHeight] = useStored('dock-height', 234)
  const [unitHeight, setUnitHeight] = useStored('unit-height', 242)
  const heldByHand = useRef(false)
  const shownAt = useRef(0)

  const run = state?.run_id
  const world = state?.world ?? null
  const offline = connection === 'offline' || !state?.world || state.simulator === 'unavailable'
  const playing = state?.simulator === 'running'
  const plan = state?.proposals.find((p) => p.proposal_id === selectedPlan) ?? state?.proposals[0]
  const place = useMemo(() => buildPlaces(geometry, world), [geometry, run]) // eslint-disable-line

  useEffect(() => { document.documentElement.dataset.theme = night ? 'night' : 'day' }, [night])
  useEffect(() => {
    setSelected(null); setEdge(null); selectPlan(null); setGeometry(null); setError('')
    heldByHand.current = false
    if (!run) return
    let active = true
    api<Geometry>('/api/network').then((value) => { if (active) setGeometry(value) })
      .catch((failure: Error) => { if (active) setError(failure.message) })
    return () => { active = false }
  }, [run])
  const anchored = useRef('')
  if (run && world && anchored.current !== run) { anchored.current = run; setAnchor(run, world.sim_time_s) }
  useEffect(() => {
    if (!error) return
    const timer = window.setTimeout(() => setError(''), 9000)
    return () => window.clearTimeout(timer)
  }, [error])

  const send = useCallback(async <T,>(path: string, body: object = {}, kind: 'plan' | 'report' | 'other' = 'other'): Promise<T | undefined> => {
    setBusy(kind); setError('')
    try { return await api<T>(path, body) } catch (failure) {
      setError(failure instanceof Error ? failure.message : 'The command failed')
    } finally { setBusy(null) }
  }, [])
  const command = useCallback(async (path: string, body: object = {}, kind: 'plan' | 'report' | 'other' = 'other') => {
    const result = await send<Snapshot | { committed: boolean }>(path, body, kind)
    if (!result) return undefined
    const next = 'sequence' in result ? result : await api<Snapshot>('/api/state').catch(() => undefined)
    if (next) accept(next)
    return next
  }, [send, accept])

  function control(operation: string, body?: object) {
    if (operation === 'pause') heldByHand.current = true
    if (operation === 'play') heldByHand.current = false
    void command(`/api/simulation/${operation}`, body)
  }
  const ask = useCallback(async () => {
    await command('/api/plans/propose', {}, 'plan')
  }, [command])
  async function inject(kind: string, ref: string | null = null) {
    const body = kind === 'scenario' ? { trigger: 'scenario' } : kind === 'road_closure' ? { trigger: 'road_closure', edge_id: ref, duration_s: 240 }
      : { trigger: 'event', kind, ref }
    const next = await command('/api/events', body, 'plan')
    if (next) { setEdge(null); if (next.proposals.length === 0) await goOn() }
  }
  /** Operations go on once the dispatcher has decided, unless a trainer paused them. */
  async function goOn() { if (!heldByHand.current) await command('/api/simulation/play') }
  async function approve(signals: boolean) {
    if (!plan) return
    const done = await command(`/api/plans/${plan.proposal_id}/approve`, { expected_plan_revision: plan.based_on_plan_revision,
      idempotency_key: plan.proposal_id, signal_priority: signals })
    if (done) await goOn()
  }
  async function reject() { if (await command('/api/plans/reject')) await goOn() }

  const pending = Boolean(state?.proposals.length)
  useEffect(() => {
    if (pending) shownAt.current = Date.now()
    else selectPlan(null)
  }, [pending])
  useEffect(() => { if (state?.proposals[0]) shownAt.current = Date.now() }, [state?.proposals[0]?.proposal_id]) // eslint-disable-line

  // Options computed for an earlier situation are refreshed, never approved.
  const refreshed = useRef('')
  useEffect(() => {
    if (!state || !isStale(state) || busy) return
    const key = `${run}-${state.world?.plan_revision}`
    if (refreshed.current === key) return
    refreshed.current = key
    void command('/api/plans/propose', {}, 'plan')
  }, [state, busy, run, command])

  // A shift that starts with unassigned work gets a first suggestion unasked.
  useEffect(() => {
    if (!state?.world || !run || offline || busy || playing || state.proposals.length) return
    if (state.activity.some((e) => ['proposals_computed', 'plan_approved', 'proposals_rejected'].includes(e.kind))) return
    if (!openTasks(state.world).some(needsUnit)) return
    try { if (sessionStorage.getItem('greeted') === run) return; sessionStorage.setItem('greeted', run) } catch { return }
    void command('/api/plans/propose', {}, 'plan')
  }, [state, run, offline, busy, playing, command])

  const select = (id: string | null) => { setEdge(null); setSelected((current) => current === id ? null : id) }
  function openMenu(event: React.MouseEvent, kind: 'unit' | 'task' | 'road', id: string) {
    event.preventDefault(); event.stopPropagation()
    if (!world) return
    const items: MenuItem[] = []
    if (kind === 'unit') {
      const out = world.vehicles[id].status === 'broken'
      items.push({ label: 'Report breakdown', run: () => void inject('breakdown', id), disabled: out },
        { label: 'Report 2 min delay', run: () => void inject('vehicle_delay', id), disabled: out })
    } else if (kind === 'task') {
      items.push({ label: world.tasks[id].priority === 'critical' ? 'Lower to priority 2' : 'Raise to priority 1', run: () => void inject('priority_change', id) },
        { label: 'Change hospital', run: () => void inject('destination_change', id) })
    } else items.push({ label: 'Close road for 4 min', run: () => void inject('road_closure', id), disabled: id in world.closed_edges })
    if (kind !== 'road') items.push({ label: 'Show details', run: () => { setEdge(null); setSelected(id) } })
    setMenu({ x: Math.min(event.clientX, window.innerWidth - 200), y: Math.min(event.clientY, window.innerHeight - 130), items })
  }

  const keys = useRef<(event: KeyboardEvent) => void>(() => {})
  keys.current = (event) => {
    if (event.key === 'F9') { event.preventDefault(); setSession(!session); return }
    if (event.key === 'Escape') { setMenu(null); setEvaluation(false); setSession(false); setSelected(null); setEdge(null); return }
    if (typing(event.target) || event.ctrlKey || event.altKey || event.metaKey || !state || offline || busy) return
    const injected = { F2: 'new_emergency', F4: 'equipment_request', F8: 'scenario' }[event.key]
    if (injected) { event.preventDefault(); if (!event.repeat) void inject(injected); return }
    const onControl = event.target instanceof HTMLElement && event.target.closest('button, a, [role=separator], summary')
    if (event.repeat || evaluation) return
    if (event.key === ' ' && !onControl) { event.preventDefault(); control(playing ? 'pause' : 'play') }
    else if (event.key.toLowerCase() === 'r') void ask()
    else if (pending && event.key >= '1' && event.key <= '3') { const option = state.proposals[Number(event.key) - 1]; if (option) selectPlan(option.proposal_id) }
    else if (pending && event.key.toLowerCase() === 'x') void reject()
    else if (pending && event.key === 'Enter' && !onControl && !isStale(state) && Date.now() - shownAt.current > 800)
      document.querySelector<HTMLButtonElement>('.decision .approve')?.click()
  }
  useEffect(() => {
    const down = (event: KeyboardEvent) => keys.current(event)
    const close = () => setMenu(null)
    window.addEventListener('keydown', down)
    window.addEventListener('pointerdown', close)
    return () => { window.removeEventListener('keydown', down); window.removeEventListener('pointerdown', close) }
  }, [])

  if (!state?.world) return <div className="offline"><h1>{state?.error ? 'The operations server has a problem' : connection === 'offline' ? 'No connection to the operations server' : 'Connecting'}</h1>
    <p>{state?.error ?? (connection === 'offline' ? 'Trying again every second. Check that the server is running.' : 'Loading units and calls.')}</p>
    {state?.error && <button onClick={() => control('reset')}>Restart the session</button>}</div>

  const tasks = openTasks(state.world)
  const open = tasks.filter(needsUnit)
  const units = Object.values(state.world.vehicles)
  const count = (key: string) => units.filter((v) => unitState(v, state, geometry).key === key).length
  const common = { state, geometry, place, proposal: plan, selected, onSelect: select, onMenu: openMenu }
  return <div className="desk" style={{ gridTemplateColumns: `${leftWidth}px 4px minmax(0, 1fr) 4px ${rightWidth}px` }}>
    <header className="strip">
      <dl>
        <div className={open.length ? 'alert' : ''}><dt>Without a unit</dt><dd>{open.length}</dd></div>
        <div><dt>Priority 1 open</dt><dd>{tasks.filter((t) => t.priority === 'critical').length}</dd></div>
        <div><dt>Open in total</dt><dd>{tasks.length}</dd></div>
        <div><dt>Units available</dt><dd>{count('free')}<small>/{units.length}</small></dd></div>
        {count('out') > 0 && <div className="alert"><dt>Out of service</dt><dd>{count('out')}</dd></div>}
        {Object.keys(state.world.closed_edges).length > 0 && <div><dt>Roads closed</dt><dd>{Object.keys(state.world.closed_edges).length}</dd></div>}
        {state.signal_priority_active && <div className="on"><dt>Traffic lights</dt><dd>Priority on</dd></div>}
      </dl>
      <time className="clock">{timeOfDay(state.world.sim_time_s, true)}</time>
    </header>
    <div className="left" style={{ gridTemplateRows: `minmax(0, 1fr) 4px ${unitHeight}px` }}>
      <CallQueue {...common}/>
      <Splitter axis="y" value={unitHeight} min={120} max={520} invert onChange={setUnitHeight}/>
      <UnitBoard {...common}/>
    </div>
    <Splitter axis="x" value={leftWidth} min={280} max={520} onChange={setLeftWidth}/>
    <main className="center" style={{ gridTemplateRows: `minmax(0, 1fr) 4px ${dockHeight}px` }}>
      <div className="map-pane">
        {geometry && geometry.network === state.network ? <CityMap key={run} geometry={geometry} state={state} proposal={plan} selected={selected} onSelect={(id) => { if (id) select(id); else setSelected(null) }}
          edge={edge} onEdge={setEdge} onMenu={openMenu} satellite={satellite}/> : <p className="empty">Loading the map</p>}
        <Inspector state={state} geometry={geometry} place={place} selected={selected} edge={edge} busy={Boolean(busy)} onEvent={(kind, ref) => void inject(kind, ref)} onClose={() => { setSelected(null); setEdge(null) }}/>
        {plan && !isStale(state) && planActions(plan, state.world).length > 0 && <p className="map-key"><i/>Suggested route</p>}
      </div>
      <Splitter axis="y" value={dockHeight} min={110} max={480} invert onChange={setDockHeight}/>
      <section className="pane dock">
        <header><div className="tabs" role="tablist">
          <button role="tab" aria-selected={dock === 'timeline'} onClick={() => setDock('timeline')}>Timeline</button>
          <button role="tab" aria-selected={dock === 'log'} onClick={() => setDock('log')}>Log</button></div></header>
        <div className="scroll">{dock === 'timeline' ? <Timeline {...common}/> : <Log state={state}/>}</div>
      </section>
    </main>
    <Splitter axis="x" value={rightWidth} min={360} max={620} invert onChange={setRightWidth}/>
    <Agent key={run} state={state} geometry={geometry} place={place} plan={plan} busy={busy === 'other' ? null : busy} offline={offline}
      onPlan={selectPlan} onApprove={(signals) => void approve(signals)} onReject={() => void reject()} onAsk={() => void ask()} onSelect={(id) => { setEdge(null); setSelected(id) }}
      onInterpret={(message) => send<Interpretation>('/api/events/interpret', { message }, 'report')}
      onReport={async (id) => Boolean(await command('/api/events', { trigger: 'report', report_id: id }, 'plan'))}/>
    <footer className="status">
      <span className={`link ${connection}`}>{connection === 'live' ? 'Connected' : connection === 'polling' ? 'Connected (slow)' : connection === 'offline' ? 'Connection lost' : 'Reconnecting'}</span>
      <span>{state.network === 'ljubljana' ? 'Ljubljana, Tabor and UKC' : 'Training grid'}</span>
      <span>Plan {state.world.plan_revision}</span>
      <span className="grow"/>
      {geometry?.network === 'ljubljana' && <button aria-pressed={satellite} onClick={() => setSatellite(!satellite)}>Aerial photo</button>}
      <button aria-pressed={night} onClick={() => setNight(!night)}>Night</button>
      <button aria-pressed={session} title="F9" onClick={() => setSession(!session)}>Training</button>
    </footer>
    {error && <p className="toast" role="alert">{error}<button aria-label="Dismiss" onClick={() => setError('')}>×</button></p>}
    {state.error && <p className="toast" role="alert">{state.error}</p>}
    {session && <SessionPanel key={`${run}-session`} state={state} busy={Boolean(busy)} onControl={control} onInject={(kind) => void inject(kind)} onClose={() => setSession(false)}
      onLoad={(network: Network, scenario: Scenario) => void command('/api/simulation/load', { network, scenario })} onEvaluation={() => { setEvaluation(true); setSession(false) }}/>}
    {evaluation && <Evaluation state={state} onClose={() => setEvaluation(false)}/>}
    {menu && <ul className="menu" role="menu" style={{ left: menu.x, top: menu.y }} onPointerDown={(event) => event.stopPropagation()}>
      {menu.items.map((item) => <li key={item.label}><button role="menuitem" disabled={item.disabled || Boolean(busy)} onClick={() => { setMenu(null); item.run() }}>{item.label}</button></li>)}</ul>}
  </div>
}

createRoot(document.getElementById('root')!).render(<App/>)
