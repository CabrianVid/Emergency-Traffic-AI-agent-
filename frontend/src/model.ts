export type Point = [number, number]
export type Network = 'grid' | 'ljubljana'
export type Scenario = 'breakdown' | 'urgent'
export type Status = 'available' | 'assigned' | 'carrying' | 'broken' | 'weather_grounded' | string

export interface Stop {
  stop_id: string; task_id: string | null; node_id: string; kind: string
  arrival_s: number | null; departure_s: number | null
  service_start_s: number | null; service_completed_at_s: number | null
}
export interface Vehicle {
  id: string; node_id: string; status: Status; mode: string
  capabilities: string[]; equipment_aboard: string[]; route_stops: Stop[]
}
export interface Task {
  id: string; kind: string; priority: string; status: string; pickup_node: string; dropoff_node: string
  created_at_s: number; required_capabilities: string[]; required_asset_ids: string[]
  latest_pickup_s: number | null; latest_delivery_s: number | null; assigned_vehicle_id: string | null
}
export interface Asset {
  id: string; status: string; location: { kind: string; ref_id: string }
}
export interface Hospital {
  id: string; node_id: string; slots?: { start_s: number; end_s: number; capacity: number }[]
}
export interface Notification {
  message_id: string; unit_id: string; task_id: string; destination_label: string; sent_at_sim_s: number
}
export interface World {
  sim_time_s: number; plan_revision: number; telemetry_seq: number
  vehicles: Record<string, Vehicle>; tasks: Record<string, Task>; assets: Record<string, Asset>
  receiving_sites: Record<string, Hospital>; closed_edges: Record<string, number | null>
  dispatch_messages: Notification[]
}
export interface Geometry {
  network: Network; label: string; bounds: [number, number, number, number]; utm_offset: Point
  nodes: Record<string, Point>; signals: Record<string, Point>
  edges: Record<string, { shape: Point[]; name: string; lanes: number }>
}
export interface Telemetry {
  fleet?: Record<string, { xy: Point; edge: string; speed_mps: number; route: string[] }>
  signals?: Record<string, { phase: number; state: string }>
  serving?: string[]
}
export interface Activity {
  kind: string; sim_time_s: number; message?: string; vehicle_id?: string; task_id?: string
  stop_kind?: string; tls_id?: string; outcome?: string; impacted_tasks?: string[]; units?: string[]
}
export interface Assignment {
  task_id: string; vehicle_id: string; previous_vehicle_id: string | null
  pickup_s: number | null; delivery_s: number | null; asset_ids: string[]
}
/** Per open job: its unit (none while it waits), next stop, and seconds until arrival. */
export type Arrivals = Record<string, { unit: string | null; stop: string; eta_s: number; done_s: number | null }>
export interface Proposal {
  proposal_id: string; policy: string; profile: string; explanation: string; based_on_plan_revision: number
  vehicle_plans: { vehicle_id: string; stops: Stop[] }[]; unserved_task_ids: string[]
  assignments: Assignment[]; routes: Record<string, string[]>; estimated_deadline_misses: string[]
  equivalent_policies: string[]
  option_id: string; strategy: string; description: string; rank: number; recommended: boolean
  ai_probability: number | null; signal_priority_units: string[]; warnings: string[]
  arrivals: Arrivals; keeps: boolean; reroutes: string[]; lights: Record<string, number>
  metrics: {
    critical_response_eta_s: Record<string, number>; worst_critical_response_eta_s: number | null
    unserved_task_ids: string[]; estimated_deadline_misses: string[]; reassigned_task_ids: string[]
    equipment_delivery_eta_s: Record<string, number | null>; assignments: Record<string, string>
    waiting_call_ids?: string[]; mean_call_response_s?: number | null
  }
}
export interface Recommendation {
  source: string; ranking: string[]; recommended: string | null; probabilities: Record<string, number>
  model: string | null; latency_ms: number | null; cache_hit: boolean; error: string | null; explanation: string
}
export interface Metrics {
  civilian_active: number; civilian_observed: number; civilian_time_loss_s: number
  responses_s: Record<string, number>
  deadline_delays_s: Record<string, number>
  comparison: Record<string, { n: number; average_s: number; p90_s: number }>
}
export interface Snapshot {
  sequence: number; run_id: string; simulator: string; demo_pause: boolean; error?: string; agent_mode: string
  network: Network; scenario: Scenario; speed: number; script_applied: boolean
  world: World | null; telemetry: Telemetry; proposals: Proposal[]; proposal_issues: string[]
  before: Record<string, Stop[]> | null; activity: Activity[]; metrics: Metrics
  recommendation: Recommendation | null; decision_mode: string; signal_priority_active: boolean
  notes: Record<string, string>; eta_before: Arrivals; signal_units: string[]; review_at: Record<string, number>
}
export interface Interpretation {
  source: string; valid: boolean; report_id: string | null; summary?: string; error?: string
  draft?: { type: string; vehicle_id: string; equipment_ids_reported_stranded: string[] }
  impacted_tasks?: { task_id: string; impact: string }[]
}

export const scenarioLabels: Record<Scenario, string> = {
  breakdown: 'R2 breakdown, K1 recovery', urgent: 'New urgent patient',
}
export const strategyLabels: Record<string, string> = {
  urgent_first: 'Most urgent first', minimum_disruption: 'Fewest changes', nearest_free_unit: 'Nearest free unit',
  waiting_queue: 'Free units now, rest wait', keep: 'No change',
}
const priorityOrder = ['critical', 'high', 'routine']
export const priorityRank = (task: Task) => priorityOrder.indexOf(task.priority) + 1

// -- time -----------------------------------------------------------------------
// The desk shows time of day. A run's clock is anchored once to the wall clock.
let anchor = Date.now()
export function setAnchor(run: string, now: number) {
  const key = `anchor:${run}`
  anchor = Date.now() - now * 1000
  try {
    anchor = Number(localStorage.getItem(key)) || anchor
    localStorage.setItem(key, String(anchor))
  } catch { /* storage is blocked: the anchor lives in memory */ }
}
const two = (n: number) => String(n).padStart(2, '0')
export function timeOfDay(value: number | null | undefined, seconds = false) {
  if (value == null || !Number.isFinite(value)) return '–'
  const date = new Date(anchor + value * 1000)
  return `${two(date.getHours())}:${two(date.getMinutes())}${seconds ? `:${two(date.getSeconds())}` : ''}`
}
/** A duration as m:ss. */
export function clock(value: number | null | undefined) {
  if (value == null || !Number.isFinite(value)) return '–'
  const total = Math.round(Math.abs(value))
  return `${value < -0.5 ? '−' : ''}${Math.floor(total / 60)}:${two(total % 60)}`
}
/** Compact form of a job ID, for chips and map plates. */
export const shortId = (id: string) => id.replace('-RECOVERY', ' rec')
export const words = (value: string) => value.replaceAll('_', ' ')

// -- tasks and units ------------------------------------------------------------
export function taskLabel(task: Task) {
  if (task.id.endsWith('-RECOVERY') && task.kind !== 'equipment_recovery') return 'Patient in broken unit'
  if (task.kind === 'equipment_recovery') return 'Equipment recovery'
  if (task.kind === 'equipment_delivery') return 'Equipment delivery'
  if (task.kind === 'urgent_response') return 'Emergency'
  return 'Patient transfer'
}
export const nextStop = (vehicle: Vehicle) => vehicle.route_stops.find((stop) => stop.service_completed_at_s == null)
export const pickedUp = (task: Task) => ['picked_up', 'en_route_dropoff'].includes(task.status)
/** The next deadline that still applies to the task. */
export const deadline = (task: Task) =>
  (pickedUp(task) ? task.latest_delivery_s : task.latest_pickup_s ?? task.latest_delivery_s) ?? Infinity
export const needsUnit = (task: Task) => ['pending', 'unserved'].includes(task.status)
export const openTasks = (world: World) => Object.values(world.tasks)
  .filter((task) => !['completed', 'interrupted'].includes(task.status))
  .sort((a, b) => priorityRank(a) - priorityRank(b) || deadline(a) - deadline(b))
export function plannedArrival(task: Task, world: World) {
  const stops = task.assigned_vehicle_id ? world.vehicles[task.assigned_vehicle_id]?.route_stops ?? [] : []
  return stops.find((s) => s.task_id === task.id && s.service_completed_at_s == null)?.arrival_s ?? null
}

// One colour per unit, everywhere. Orange is the AI's, red means priority 1 or a problem.
const unitColors = ['#1565d8', '#8e24aa', '#0b8a3e', '#d81b78', '#0090a8', '#7a4a1e', '#5c6b00', '#3f3fb5']
export const unitColor = (id: string) => unitColors[(Number(id.replace(/\D/g, '')) + unitColors.length - 1) % unitColors.length]
export interface UnitState { key: 'free' | 'enroute' | 'scene' | 'transport' | 'hospital' | 'out'; label: string }
export function unitState(vehicle: Vehicle, state: Snapshot, geometry: Geometry | null): UnitState {
  if (['broken', 'weather_grounded', 'offline'].includes(vehicle.status)) return { key: 'out', label: 'Out of service' }
  const stop = nextStop(vehicle)
  if (!stop?.task_id) return { key: 'free', label: 'Available' }
  const live = state.telemetry.fleet?.[vehicle.id]
  const node = geometry?.nodes[stop.node_id]
  const there = state.telemetry.serving?.includes(vehicle.id)
    ?? (live && node && live.speed_mps < 0.3 && Math.hypot(live.xy[0] - node[0], live.xy[1] - node[1]) < 40)
  if (stop.kind === 'pickup') return there ? { key: 'scene', label: 'On scene' } : { key: 'enroute', label: 'En route' }
  return there ? { key: 'hospital', label: 'Handing over' } : { key: 'transport', label: 'Transporting' }
}

// -- places ---------------------------------------------------------------------
/** The road closest to a point: its edge, name and distance in metres. */
export function nearestRoad(geometry: Geometry, [x, y]: Point) {
  let best = { id: '', name: '', distance: Infinity }
  for (const [id, road] of Object.entries(geometry.edges)) for (let i = 1; i < road.shape.length; i++) {
    const [[ax, ay], [bx, by]] = [road.shape[i - 1], road.shape[i]]
    const along = Math.max(0, Math.min(1, ((x - ax) * (bx - ax) + (y - ay) * (by - ay)) / ((bx - ax) ** 2 + (by - ay) ** 2 || 1)))
    const distance = Math.hypot(x - ax - along * (bx - ax), y - ay - along * (by - ay))
    if (distance < best.distance) best = { id, name: road.name, distance }
  }
  return best
}
export type Place = (node: string) => string
export function buildPlaces(geometry: Geometry | null, world: World | null): Place {
  const hospitals = new Map(Object.values(world?.receiving_sites ?? {}).map((site) => [site.node_id, `Hospital ${site.id}`]))
  const cache = new Map<string, string>()
  return (node) => {
    const known = hospitals.get(node) ?? cache.get(node)
    if (known) return known
    const point = geometry?.nodes[node]
    const names = new Set<string>()
    if (point && geometry) for (const road of Object.values(geometry.edges)) {
      if (!road.name) continue
      for (const end of [road.shape[0], road.shape[road.shape.length - 1]])
        if (Math.hypot(end[0] - point[0], end[1] - point[1]) < 20) names.add(road.name)
    }
    if (!names.size && point && geometry) names.add(nearestRoad(geometry, point).name)
    const label = [...names].filter(Boolean).slice(0, 2).join(' / ') || 'Unnamed road'
    cache.set(node, label)
    return label
  }
}

// -- AI proposals ----------------------------------------------------------------
export interface Action { task: string; unit: string; from: string | null; arrival: number | null }
/** What would actually change if this plan were approved. */
export function planActions(plan: Proposal, world: World): Action[] {
  const rank = (id: string) => (world.tasks[id] ? priorityRank(world.tasks[id]) : 9)
  return plan.assignments
    .filter((a) => a.previous_vehicle_id !== a.vehicle_id)
    .map((a) => ({ task: a.task_id, unit: a.vehicle_id, from: a.previous_vehicle_id, arrival: a.pickup_s ?? a.delivery_s }))
    .sort((a, b) => rank(a.task) - rank(b.task) || (a.arrival ?? Infinity) - (b.arrival ?? Infinity))
}
export function planHeadline(plan: Proposal, world: World) {
  const actions = planActions(plan, world)
  const waiting = plan.metrics.waiting_call_ids ?? []
  const open = plan.metrics.unserved_task_ids.filter((id) => world.tasks[id]?.priority === 'critical')
  if (!actions.length && plan.reroutes.length) return `Drive ${plan.reroutes.join(', ')} around, keep all jobs`
  if (!actions.length) return open.length ? `Send nobody to ${open.join(', ')}` : waiting.length ? `Keep the plan, ${waiting.join(', ')} waits` : 'Keep the current plan'
  const [first, second] = actions
  const left = [...open, ...waiting].filter((id) => world.tasks[id]?.priority === 'critical')
  const rest = left.length ? `, ${left.map(shortId).join(', ')} waits` : ''
  const lead = `${first.from ? 'Move' : 'Send'} ${first.unit} to ${first.task}`
  if (actions.length === 1) return lead + rest
  if (actions.length === 2) return `${lead}, ${second.unit} to ${second.task}${rest}`
  return `${lead}, plus ${actions.length - 1} more${rest}`
}
/** The situation moved on (a stop was completed) after these options were computed. */
export const isStale = (state: Snapshot) =>
  state.proposals.length > 0 && state.proposals[0].based_on_plan_revision !== state.world?.plan_revision
/** Unit the previewed plan gives each task, to mark it across the console. */
export function proposedUnits(plan: Proposal | undefined, world: World): Record<string, string> {
  return Object.fromEntries((plan ? planActions(plan, world) : []).map((a) => [a.task, a.unit]))
}
