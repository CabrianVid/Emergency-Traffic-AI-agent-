import { strategyLabels, words } from './model'
import type { Activity, Snapshot } from './model'

export interface Entry { key: string; at: number; by: 'ai' | 'you' | 'event' | 'unit'; text: string }

const eventNames: Record<string, string> = {
  new_urgent_task: 'New emergency call', vehicle_breakdown: 'Unit breakdown', vehicle_delay: 'Unit delayed',
  road_closure: 'Road closed', priority_changed: 'Priority changed', destination_changed: 'Hospital changed',
  equipment_request: 'Equipment requested',
}
/** The event's name, with the unit when the report names one. */
const named = (event: Activity) => {
  const unit = event.message?.match(/^(R\d+)/)?.[1]
  return unit ? `${eventNames[event.kind]}, ${unit}` : eventNames[event.kind]
}
const hidden = ['scenario_loaded', 'vehicle_held', 'vehicle_frozen', 'edge_closed_physically', 'reinsertion_speed_relaxed',
  'reroute_speed_capped', 'reroute_deferred_in_junction', 'lane_change_forced', 'missing_sumo_stop', 'collision', 'teleport_started']

function describe(event: Activity): { by: Entry['by']; text: string } | null {
  const message = event.message ?? ''
  if (hidden.includes(event.kind)) return null
  if (event.kind === 'proposals_computed') {
    const count = Number(message.match(/^(\d+)/)?.[1] ?? 0)
    if (!count) return { by: 'ai', text: 'Found no feasible plan' }
    const how = message.includes('AI ranked') ? `Compared ${count} plans and suggested one` : message.includes('single option')
      ? 'Found one feasible plan' : `Offered ${count} plans by fixed rules (AI unavailable)`
    return { by: 'ai', text: how }
  }
  if (event.kind === 'unit_available') return { by: 'ai', text: 'A unit became free for a waiting call, plans recalculated' }
  if (event.kind === 'plan_approved') {
    const strategy = message.match(/\((\w+)\)/)?.[1] ?? ''
    const label = (strategyLabels[strategy] ?? words(strategy)).toLowerCase()
    return { by: 'you', text: message.includes('as recommended') ? `Approved the suggestion: ${label}` : `Chose another plan: ${label}` }
  }
  if (event.kind === 'proposals_rejected') return { by: 'you', text: 'Rejected the suggestion' }
  if (event.kind === 'signal_priority_authorised') return { by: 'you', text: `Cleared traffic lights for ${message.split(' for ').at(-1)}` }
  if (event.kind === 'execution_failed') return { by: 'event', text: 'The plan could not be carried out' }
  if (event.kind in eventNames) {
    const affected = event.impacted_tasks?.length ? `: ${event.impacted_tasks.join(', ')}` : ''
    return { by: 'event', text: `${named(event)}${affected}` }
  }
  if (event.kind === 'stop_begin') return { by: 'unit', text: `${event.vehicle_id} ${event.stop_kind === 'pickup' ? 'on scene' : 'at hospital'}, ${event.task_id}` }
  if (event.kind === 'stop_completed') return { by: 'unit', text: `${event.vehicle_id} ${event.stop_kind === 'pickup' ? 'left scene with' : 'handed over'} ${event.task_id}` }
  if (event.kind === 'phase_shortened') return { by: 'unit', text: `Traffic light shortened for ${event.vehicle_id}` }
  if (event.kind === 'scheduled_restoration_applied') return { by: 'event', text: 'Closed road reopened' }
  return null
}

/** What made the AI speak up, for the suggestion now on screen. */
export function triggerOf(state: Snapshot): { title: string; tasks: string[] } | null {
  const last = state.activity.findLastIndex((event) => event.kind === 'proposals_computed')
  const event = last > 0 ? state.activity[last - 1] : undefined
  if (!event) return null
  if (event.kind === 'unit_available') return { title: 'A unit is free again', tasks: [] }
  return event.kind in eventNames ? { title: named(event), tasks: event.impacted_tasks ?? [] } : null
}

/** Newest first: what happened, who decided it, and what units report. */
export function logEntries(state: Snapshot): Entry[] {
  const entries: Entry[] = []
  state.activity.forEach((event, i) => {
    const entry = describe(event)
    if (entry) entries.push({ key: `${event.sim_time_s}-${event.kind}-${event.vehicle_id ?? ''}-${event.tls_id ?? ''}-${i}`, at: event.sim_time_s, ...entry })
  })
  for (const order of state.world?.dispatch_messages ?? [])
    entries.push({ key: order.message_id, at: order.sent_at_sim_s, by: 'unit', text: `Order sent to ${order.unit_id}: ${order.task_id}` })
  return entries.reverse().sort((a, b) => b.at - a.at)
}
