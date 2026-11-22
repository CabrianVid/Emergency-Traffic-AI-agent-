import { useMemo } from 'react'
import { Mark } from './Agent'
import { logEntries } from './log'
import { clock, deadline, isStale, needsUnit, nextStop, openTasks, pickedUp, plannedArrival, priorityRank, proposedUnits, shortId, taskLabel, timeOfDay, unitColor, unitState } from './model'
import type { Geometry, Place, Proposal, Snapshot, Stop, Task } from './model'

type Menu = (event: React.MouseEvent, kind: 'unit' | 'task', id: string) => void
interface Common { state: Snapshot; geometry: Geometry | null; place: Place; proposal?: Proposal; selected: string | null; onSelect: (id: string) => void; onMenu: Menu }

export function CallQueue({ state, place, proposal, selected, onSelect, onMenu, onNew }: Common & { onNew: () => void }) {
  const world = state.world!
  const now = world.sim_time_s
  const suggested = proposedUnits(isStale(state) ? undefined : proposal, world)
  const tasks = openTasks(world)
  const groups: [string, Task[]][] = [
    ['Needs a unit', tasks.filter(needsUnit)],
    ['Assigned', tasks.filter((t) => !needsUnit(t) && !pickedUp(t))],
    ['On board', tasks.filter(pickedUp)],
  ]
  return <section className="pane queue">
    <header><h2>Calls and transfers</h2><button className="new" onClick={onNew}>New emergency<kbd>N</kbd></button></header>
    <div className="scroll">
      {tasks.length === 0 && <p className="empty">No open calls or transfers.</p>}
      {groups.map(([title, rows]) => rows.length > 0 && <div key={title} className={title === 'Needs a unit' ? 'group open' : 'group'}>
        <h3>{title}<span>{rows.length}</span></h3>
        {rows.map((task) => {
          const left = deadline(task) - now
          const arrival = plannedArrival(task, world)
          const late = arrival != null && Math.max(arrival, now) > deadline(task)
          return <button key={task.id} className={`call-row ${selected === task.id ? 'selected' : ''}`} onClick={() => onSelect(task.id)} onContextMenu={(event) => onMenu(event, 'task', task.id)}>
            <b className={`prio p${priorityRank(task)}`} title={`Priority ${priorityRank(task)}`}>{priorityRank(task)}</b>
            <span className="call-main"><strong>{task.id}<em>{taskLabel(task)}{task.required_capabilities.length ? `, ${task.required_capabilities.join(' ')}` : ''}</em></strong>
              <small>{pickedUp(task) ? `to ${place(task.dropoff_node)}` : place(task.pickup_node)}{state.notes[task.id] ? `, ${state.notes[task.id]}` : ''}</small></span>
            <span className="call-side">
              {task.assigned_vehicle_id ? <span className="callsign" style={{ background: unitColor(task.assigned_vehicle_id) }}>{task.assigned_vehicle_id}</span> : null}
              {suggested[task.id] && <span className="suggest" title="Unit in the plan you are viewing"><Mark size={9}/>{suggested[task.id]}</span>}
              {Number.isFinite(left) && <time className={left < 0 || late ? 'late' : left < 120 ? 'soon' : ''} title={`Due ${timeOfDay(deadline(task))}`}>{left < 0 ? `+${clock(-left)}` : clock(left)}</time>}
            </span>
          </button>
        })}
      </div>)}
    </div>
  </section>
}

export function UnitBoard({ state, geometry, place, proposal, selected, onSelect, onMenu }: Common) {
  const world = state.world!
  const suggested = Object.entries(proposedUnits(isStale(state) ? undefined : proposal, world))
  return <section className="pane units">
    <header><h2>Units</h2><span>{Object.values(world.vehicles).filter((v) => unitState(v, state, geometry).key === 'free').length} available</span></header>
    <div className="scroll"><table><tbody>
      {Object.values(world.vehicles).map((vehicle) => {
        const status = unitState(vehicle, state, geometry)
        const stop = nextStop(vehicle)
        const plan = suggested.filter(([, unit]) => unit === vehicle.id).map(([task]) => task)
        return <tr key={vehicle.id} className={`${status.key} ${selected === vehicle.id ? 'selected' : ''}`} onClick={() => onSelect(vehicle.id)} onContextMenu={(event) => onMenu(event, 'unit', vehicle.id)}>
          <th scope="row"><button className="callsign" style={{ background: unitColor(vehicle.id) }}>{vehicle.id}</button></th>
          <td className="crew">{vehicle.capabilities.join(' ')}{vehicle.equipment_aboard.length > 0 && <em> {vehicle.equipment_aboard.join(' ')}</em>}</td>
          <td><span className={`state ${status.key}`}>{status.label}</span></td>
          <td className="job">{status.key !== 'out' && stop?.task_id ? <>{stop.task_id}<small>{place(stop.node_id)}</small></> : <small>{state.telemetry.fleet?.[vehicle.id] ? '' : place(vehicle.node_id)}</small>}</td>
          <td className="eta">{plan.length > 0 ? <span className="suggest"><Mark size={9}/>{shortId(plan[0])}</span>
            : status.key !== 'out' && stop?.arrival_s != null && ['enroute', 'transport'].includes(status.key) ? <time title="Estimated arrival">{timeOfDay(Math.max(stop.arrival_s, world.sim_time_s))}</time> : null}</td>
        </tr>
      })}
    </tbody></table></div>
  </section>
}

interface Job { id: string; start: number; end: number; done: boolean }
function jobs(stops: Stop[]): Job[] {
  const found = new Map<string, Job>()
  for (const stop of stops) {
    if (!stop.task_id || stop.arrival_s == null) continue
    const start = (stop.service_completed_at_s != null ? stop.service_start_s : stop.arrival_s) ?? stop.arrival_s
    const end = stop.service_completed_at_s ?? stop.departure_s ?? start
    const job = found.get(stop.task_id)
    if (job) { job.end = Math.max(job.end, end); job.done = stop.service_completed_at_s != null }
    else found.set(stop.task_id, { id: stop.task_id, start, end, done: stop.service_completed_at_s != null })
  }
  return [...found.values()]
}

export function Timeline({ state, proposal, selected, onSelect }: Common) {
  const world = state.world!
  const now = world.sim_time_s
  const [from, to] = [now - 300, now + 1500]
  const at = (value: number) => Math.max(0, Math.min(100, (value - from) / (to - from) * 100))
  const preview = proposal && !isStale(state) ? proposal : undefined
  const ticks = Array.from({ length: 7 }, (_, i) => Math.ceil(from / 300) * 300 + i * 300).filter((t) => t < to)
  const bars = (stops: Stop[], ghost = false) => jobs(stops).filter((job) => job.end > from && job.start < to).map((job) =>
    <button key={job.id} className={`bar p${world.tasks[job.id] ? priorityRank(world.tasks[job.id]) : 3} ${job.done ? 'done' : ''} ${ghost ? 'ghost' : ''} ${selected === job.id ? 'selected' : ''}`}
      style={{ left: `${at(job.start)}%`, width: `${Math.max(1.6, at(job.end) - at(job.start))}%` }} onClick={() => onSelect(job.id)}
      title={`${job.id}, ${timeOfDay(job.start)} to ${timeOfDay(job.end)}${ghost ? ' (suggested)' : ''}`}>{job.id}</button>)
  return <div className="timeline">
    <div className="ticks"><span/><div>{ticks.map((t) => <time key={t} style={{ left: `${at(t)}%` }}>{timeOfDay(t)}</time>)}</div></div>
    {Object.values(world.vehicles).map((vehicle) => {
      const change = preview?.vehicle_plans.find((entry) => entry.vehicle_id === vehicle.id)
      const open = change && change.stops.filter((s) => s.service_completed_at_s == null)
      return <div key={vehicle.id} className={`lane ${change ? 'changing' : ''}`}>
        <button className="callsign" style={{ color: unitColor(vehicle.id) }} onClick={() => onSelect(vehicle.id)}>{vehicle.id}</button>
        <div className="track">{ticks.map((t) => <i key={t} style={{ left: `${at(t)}%` }}/>)}
          <div className="bars">{bars(vehicle.route_stops)}</div>
          {open && <div className="bars next">{bars(open, true)}</div>}
        </div></div>
    })}
    <div className="now" style={{ left: `calc(52px + (100% - 64px) * ${at(now) / 100})` }}/>
  </div>
}

export function Log({ state }: { state: Snapshot }) {
  const entries = useMemo(() => logEntries(state), [state])
  return <ul className="log">{entries.map((entry) => <li key={entry.key} className={entry.by}>
    <time>{timeOfDay(entry.at, true)}</time><b>{{ ai: 'AI', you: 'You', event: 'Event', unit: 'Unit' }[entry.by]}</b><span>{entry.text}</span></li>)}</ul>
}

/** Details and actions for what is selected, over the map. */
export function Inspector({ state, geometry, place, selected, edge, busy, onEvent, onClose }: {
  state: Snapshot; geometry: Geometry | null; place: Place; selected: string | null; edge: string | null; busy: boolean
  onEvent: (kind: string, ref: string) => void; onClose: () => void
}) {
  const world = state.world!
  const unit = selected ? world.vehicles[selected] : undefined
  const task = selected ? world.tasks[selected] : undefined
  const road = edge ? geometry?.edges[edge] : undefined
  if (!unit && !task && !road) return null
  const rows: [string, React.ReactNode][] = []
  const actions: [string, string, string][] = []
  let title = '', subtitle = ''
  if (unit) {
    const status = unitState(unit, state, geometry)
    const stop = nextStop(unit)
    const live = state.telemetry.fleet?.[unit.id]
    title = unit.id; subtitle = `${unit.capabilities.join(' ')} ambulance`
    rows.push(['Status', <span key="s" className={`state ${status.key}`}>{status.label}</span>])
    if (live) rows.push(['Position', `${geometry?.edges[live.edge]?.name || 'In a junction'}, ${Math.round(live.speed_mps * 3.6)} km/h`])
    else rows.push(['Position', place(unit.node_id)])
    if (stop?.task_id && status.key !== 'out') rows.push(['Next', `${stop.task_id}, ${place(stop.node_id)}, ${timeOfDay(stop.arrival_s)}`])
    rows.push(['Equipment', unit.equipment_aboard.join(', ') || 'Standard'])
    if (status.key !== 'out') actions.push(['Report breakdown', 'breakdown', unit.id], ['Report 2 min delay', 'vehicle_delay', unit.id])
  } else if (task) {
    title = task.id; subtitle = taskLabel(task)
    rows.push(['Priority', <span key="p"><b className={`prio p${priorityRank(task)}`}>{priorityRank(task)}</b> {task.priority}</span>],
      ['From', place(task.pickup_node)], ['To', place(task.dropoff_node)],
      ['Unit', task.assigned_vehicle_id ?? 'None assigned'])
    if (state.notes[task.id]) rows.push(['Report', state.notes[task.id]])
    if (task.required_capabilities.length) rows.push(['Needs', task.required_capabilities.join(', ')])
    if (task.latest_pickup_s != null) rows.push(['Reach by', timeOfDay(task.latest_pickup_s)])
    if (task.latest_delivery_s != null) rows.push(['Deliver by', timeOfDay(task.latest_delivery_s)])
    if (!['completed', 'interrupted'].includes(task.status)) actions.push(
      [task.priority === 'critical' ? 'Lower to priority 2' : 'Raise to priority 1', 'priority_change', task.id],
      ['Change hospital', 'destination_change', task.id])
  } else if (road && edge) {
    title = road.name || 'Unnamed road'; subtitle = `${road.lanes} lane${road.lanes > 1 ? 's' : ''}`
    const closed = edge in world.closed_edges
    rows.push(['Status', closed ? `Closed until ${timeOfDay(world.closed_edges[edge])}` : 'Open'])
    if (!closed) actions.push(['Close for 4 min', 'road_closure', edge])
  }
  return <section className="inspector" aria-label={`Details of ${title}`}>
    <header><div><h2>{title}</h2><small>{subtitle}</small></div><button aria-label="Close details" onClick={onClose}>×</button></header>
    <dl>{rows.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
    {actions.length > 0 && <div className="inspector-actions">{actions.map(([label, kind, ref]) => <button key={kind} disabled={busy} onClick={() => onEvent(kind, ref)}>{label}</button>)}</div>}
  </section>
}
