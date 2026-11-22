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
  network: Network; label: string; bounds: [number, number, number, number]
  nodes: Record<string, Point>; signals: Record<string, Point>
  edges: Record<string, { shape: Point[]; name: string; lanes: number }>
}
export interface Telemetry {
  fleet?: Record<string, { xy: Point; edge: string; speed_mps: number; route: string[] }>
  signals?: Record<string, { phase: number; state: string }>
}
export interface Activity {
  kind: string; sim_time_s: number; message?: string; vehicle_id?: string; task_id?: string
  stop_kind?: string; tls_id?: string; outcome?: string; impacted_tasks?: string[]
}
export interface Assignment {
  task_id: string; vehicle_id: string; previous_vehicle_id: string | null
  pickup_s: number | null; delivery_s: number | null; asset_ids: string[]
}
export interface Proposal {
  proposal_id: string; policy: string; profile: string; explanation: string; based_on_plan_revision: number
  vehicle_plans: { vehicle_id: string; stops: Stop[] }[]; unserved_task_ids: string[]
  assignments: Assignment[]; routes: Record<string, string[]>; estimated_deadline_misses: string[]
  equivalent_policies: string[]
  score: { critical_tardiness_s: number; other_weighted_lateness_s: number; assignment_churn: number }
  option_id: string; strategy: string; description: string; rank: number; recommended: boolean
  ai_probability: number | null; signal_priority_units: string[]; warnings: string[]
  metrics: {
    critical_response_eta_s: Record<string, number>; worst_critical_response_eta_s: number | null
    unserved_task_ids: string[]; estimated_deadline_misses: string[]; reassigned_task_ids: string[]
    equipment_delivery_eta_s: Record<string, number | null>; assignments: Record<string, string>
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
}
export interface Interpretation {
  source: string; valid: boolean; report_id: string | null; summary?: string; error?: string
  draft?: { type: string; vehicle_id: string; equipment_ids_reported_stranded: string[] }
  impacted_tasks?: { task_id: string; impact: string }[]
}

export const scenarioLabels: Record<Scenario, string> = {
  breakdown: 'R2 breakdown · K1 recovery', urgent: 'New urgent patient',
}
export const strategyLabels: Record<string, string> = {
  urgent_first: 'Urgent first', minimum_disruption: 'Least disruption', nearest_free_unit: 'Nearest free unit',
  waiting_queue: 'Waiting queue',
}
export const sourceLabels: Record<string, string> = {
  'openai-decisions': 'Ranked by AI', 'single-feasible-option': 'Only feasible option',
  'deterministic-fallback': 'Deterministic fallback · not AI',
}
export const vehicleColors: Record<string, string> = {
  R1: '#56e2bc', R2: '#70aefa', R3: '#b89af5', R4: '#f4c76a', R5: '#61d6ed',
}
export const terminalStatuses = ['completed', 'interrupted', 'unserved']

export function clock(value: number | null | undefined) {
  if (value == null || !Number.isFinite(value)) return '—'
  const seconds = Math.max(0, Math.round(value))
  return [Math.floor(seconds / 60), seconds % 60].map((n) => String(n).padStart(2, '0')).join(':')
}
export function words(value: string) {
  return value.replaceAll('_', ' ')
}
export function taskLabel(task: Task) {
  if (task.kind === 'equipment_recovery') return 'Equipment recovery'
  if (task.kind === 'equipment_delivery') return 'Equipment delivery'
  if (task.kind === 'urgent_response') return 'Urgent response'
  return 'Patient transfer'
}
export function vehicleStatus(vehicle: Vehicle, world: World) {
  if (['broken', 'weather_grounded', 'delayed'].includes(vehicle.status)) return vehicle.status
  const taskId = vehicle.route_stops.find((s) => s.service_completed_at_s == null)?.task_id
  if (!taskId) return 'available'
  return world.tasks[taskId]?.status === 'en_route_dropoff' ? 'carrying' : 'assigned'
}
