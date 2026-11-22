import { Activity as ActivityIcon, ArrowRight, Check, ClipboardList, Radio, Signal, Timer, Truck } from 'lucide-react'
import { BenchmarkResults } from './BenchmarkResults'
import { MonteCarloResults } from './MonteCarloResults'
import { clock, taskLabel, terminalStatuses, vehicleColors, vehicleStatus, words } from './model'
import type { Activity, Metrics, Proposal, Snapshot, Stop, World } from './model'

interface FleetProps { world: World; selected: string | null; onSelect: (id: string) => void }

export function Fleet({ world, selected, onSelect }: FleetProps) {
  const active = Object.values(world.tasks).filter((task) => !terminalStatuses.includes(task.status))
  const priority = ['critical', 'high', 'routine']
  active.sort((a, b) => priority.indexOf(a.priority) - priority.indexOf(b.priority) ||
    (a.latest_pickup_s ?? a.latest_delivery_s ?? Infinity) - (b.latest_pickup_s ?? b.latest_delivery_s ?? Infinity))
  return <aside className="panel fleet-panel">
    <header className="panel-head"><div className="section-title"><Truck size={16}/><h2>Fleet</h2></div><span className="count">{Object.keys(world.vehicles).length}</span></header>
    <div className="fleet-list">
      {Object.values(world.vehicles).map((vehicle) => {
        const current = vehicle.route_stops.find((stop) => stop.service_completed_at_s == null)
        const status = vehicleStatus(vehicle, world)
        return <button className={`fleet-row ${selected === vehicle.id ? 'selected' : ''}`} key={vehicle.id} onClick={() => onSelect(vehicle.id)}>
          <span className="unit-icon" style={{ color: vehicleColors[vehicle.id] }}><Truck size={15}/></span>
          <span className="fleet-name"><strong>{vehicle.id}</strong>
            <small>{vehicle.capabilities.join(', ')}{current?.task_id && status !== 'broken' ? ` · ${current.task_id}` : ''}</small></span>
          <span className={`fleet-status status-${status}`}>{words(status)}</span>
        </button>
      })}
    </div>
    <section className="task-list">
      <header className="panel-head"><div className="section-title"><ClipboardList size={16}/><h2>Active tasks</h2></div><span className="count">{active.length}</span></header>
      {active.length === 0 && <p className="empty-inline">No active tasks.</p>}
      {active.map((task) => <button key={task.id} className={`task-row ${selected === task.id ? 'selected' : ''}`} onClick={() => onSelect(task.id)}>
        <i className={`priority-dot ${task.priority}`} title={task.priority}/>
        <span><strong>{task.id}</strong><small>{taskLabel(task)}</small></span>
        <time title="Next scheduled deadline">{clock(task.latest_pickup_s ?? task.latest_delivery_s)}</time>
      </button>)}
    </section>
  </aside>
}

function ScheduleBars({ stops, min, span, changed, focus }: { stops: Stop[]; min: number; span: number; changed?: boolean; focus?: string | null }) {
  return <div className={`schedule-track ${changed ? 'after' : ''}`}>
    {stops.filter((stop) => stop.arrival_s != null).map((stop) => {
      const start = (stop.service_completed_at_s != null ? stop.service_start_s : stop.arrival_s) ?? min
      const end = stop.service_completed_at_s ?? stop.departure_s ?? start
      const left = (start - min) / span * 100
      const width = Math.min(100 - left, Math.max(2.5, (end - start) / span * 100))
      return <div key={stop.stop_id} className={`schedule-bar ${stop.kind} ${stop.service_completed_at_s != null ? 'complete' : ''} ${stop.task_id === focus ? 'focused' : ''}`}
        style={{ left: `${left}%`, width: `${width}%` }}
        title={`${stop.task_id ?? ''} ${stop.kind} · ${clock(start)}–${clock(end)}`}>
        {stop.service_completed_at_s != null && <Check size={10}/>}<span>{stop.task_id?.replace('T3-RECOVERY', 'K1')}</span>
      </div>
    })}
  </div>
}

export function Schedule({ snapshot, proposal, selected, onSelect }: { snapshot: Snapshot; proposal?: Proposal; selected: string | null; onSelect: (id: string) => void }) {
  const world = snapshot.world
  if (!world) return null
  const committed = Object.fromEntries(Object.values(world.vehicles).map((vehicle) => [vehicle.id, vehicle.route_stops]))
  const before = snapshot.before ?? committed
  const after = proposal ? { ...committed, ...Object.fromEntries(proposal.vehicle_plans.map((entry) => [entry.vehicle_id, entry.stops])) } : committed
  const times = [...Object.values(before).flat(), ...Object.values(after).flat()].flatMap((stop) => [stop.arrival_s, stop.departure_s])
    .filter((value): value is number => value != null && Number.isFinite(value))
  const min = Math.min(0, ...times)
  const span = Math.max(600, Math.ceil(Math.max(world.sim_time_s, ...times) / 300) * 300) - min
  const compare = Boolean(proposal || snapshot.before)
  return <section className="panel schedule-panel">
    <header className="panel-head"><div className="section-title"><Timer size={17}/><h2>Vehicle schedule</h2></div>
      <div className="schedule-legend"><span><i/> {compare ? 'Before incident' : 'Committed'}</span>{compare && <span><i/> {proposal ? 'Selected proposal · estimated' : 'Committed after incident'}</span>}</div>
    </header>
    <div className="schedule-content">
      <div className="time-scale"><span>UNIT</span><div>{[0, .25, .5, .75, 1].map((part) => <time key={part}>{clock(min + span * part)}</time>)}</div></div>
      {Object.values(world.vehicles).filter((v) => v.mode === 'ambulance').map((vehicle) => <div key={vehicle.id} className="schedule-row">
        <button className={selected === vehicle.id ? 'selected' : ''} onClick={() => onSelect(vehicle.id)}><i style={{ background: vehicleColors[vehicle.id] }}/>{vehicle.id}</button>
        <div><ScheduleBars stops={before[vehicle.id] ?? []} min={min} span={span} focus={selected}/>
          {compare && <ScheduleBars stops={after[vehicle.id] ?? []} min={min} span={span} changed focus={selected}/>}</div>
      </div>)}
    </div>
  </section>
}

export function eventText(event: Activity) {
  if (event.message) return event.message
  if (event.kind === 'stop_begin') return `${event.vehicle_id} began ${event.stop_kind} · ${event.task_id}`
  if (event.kind === 'stop_completed') return `${event.vehicle_id} completed ${event.stop_kind} · ${event.task_id}`
  if (event.kind === 'restored') return `Signal ${event.tls_id} restored normal program`
  if (event.kind === 'phase_shortened') return `Signal ${event.tls_id} shortened phase for ${event.vehicle_id}`
  if (event.tls_id) return `${words(event.kind)} · signal ${event.tls_id} · ${event.vehicle_id ?? ''}`
  return `${words(event.kind)}${event.vehicle_id ? ` · ${event.vehicle_id}` : ''}`
}

export function ActivityLog({ snapshot }: { snapshot: Snapshot }) {
  const messages = snapshot.world?.dispatch_messages ?? []
  const events = snapshot.activity.slice(-10).reverse()
  return <section className="panel activity-panel">
    <header className="panel-head"><div className="section-title"><ActivityIcon size={16}/><h2>Run activity</h2></div></header>
    <div className="activity-list">
      {messages.slice().reverse().map((message) => <div className="dispatch-event" key={message.message_id}>
        <Radio size={16}/><div><strong>Mock message sent to {message.unit_id}</strong>
          <small>{message.task_id} <ArrowRight size={11}/> {Object.values(snapshot.world?.receiving_sites ?? {}).find((site) => site.node_id === message.destination_label)?.id ?? message.destination_label}</small>
        </div><time>{clock(message.sent_at_sim_s)}</time>
      </div>)}
      {events.map((event, index) => <div className="activity-row" key={index}>
        {event.tls_id ? <Signal size={14}/> : <i/>}<span>{eventText(event)}</span><time>{clock(event.sim_time_s)}</time>
      </div>)}
    </div>
  </section>
}

export function Results({ metrics }: { metrics: Metrics }) {
  return <section className="panel results-panel">
    <header className="panel-head"><div className="section-title"><ActivityIcon size={16}/><h2>Run results</h2></div><span className="tag">Observed · simulated</span></header>
    <div className="results-body">
      <MonteCarloResults/>
      <details><summary>Historical benchmark (older policy definitions: A2 was signals only, A3 combined)</summary><BenchmarkResults/></details>
      <h3>Current live simulator session</h3>
      <div className="result-stats">
        <div><small>Civilian vehicles observed</small><strong>{metrics.civilian_observed}</strong></div>
        <div><small>Observed civilian time loss</small><strong>{clock(metrics.civilian_time_loss_s)}</strong></div>
      </div>
      {Object.entries(metrics.responses_s).length > 0 && <div className="response-list">
        {Object.entries(metrics.responses_s).map(([task, time]) => <span key={task}>{task} response <strong>{clock(time)}</strong></span>)}
      </div>}
      {Object.entries(metrics.deadline_delays_s).length > 0 && <div className="response-list">
        {Object.entries(metrics.deadline_delays_s).map(([task, time]) => <span key={task}>{task} late <strong>+{clock(time)}</strong></span>)}
      </div>}
    </div>
  </section>
}
