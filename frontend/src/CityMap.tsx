import { useEffect, useMemo, useRef, useState } from 'react'
import { isStale, needsUnit, openTasks, pickedUp, planActions, priorityRank, shortId, unitState } from './model'
import type { Geometry, Point, Proposal, Snapshot } from './model'

const line = (shape: Point[]) => shape.map(([x, y]) => `${x},${-y}`).join(' ')
// The bundled aerial covers this area, in metres of the Ljubljana network.
const AERIAL = { x: -700, y: -1450, width: 2685, height: 2000 }

interface Props {
  geometry: Geometry; state: Snapshot; proposal?: Proposal
  selected: string | null; onSelect: (id: string | null) => void
  edge: string | null; onEdge: (id: string | null) => void
  onMenu: (event: React.MouseEvent, kind: 'unit' | 'task' | 'road', id: string) => void
  satellite: boolean
}

/** One label per street, on its longest straight piece. */
function streetLabels(geometry: Geometry) {
  const best = new Map<string, { length: number; at: Point; angle: number }>()
  for (const road of Object.values(geometry.edges)) {
    if (!road.name) continue
    for (let i = 1; i < road.shape.length; i++) {
      const [a, b] = [road.shape[i - 1], road.shape[i]]
      const length = Math.hypot(b[0] - a[0], b[1] - a[1])
      if (length <= (best.get(road.name)?.length ?? 45)) continue
      let angle = Math.atan2(-(b[1] - a[1]), b[0] - a[0]) * 180 / Math.PI
      if (angle > 90) angle -= 180
      if (angle < -90) angle += 180
      best.set(road.name, { length, at: [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2], angle })
    }
  }
  return [...best].map(([name, value]) => ({ name, ...value }))
}

export function CityMap({ geometry, state, proposal, selected, onSelect, edge, onEdge, onMenu, satellite }: Props) {
  const box = useRef<HTMLDivElement>(null)
  const [size, setSize] = useState<Point>([800, 500])
  const [view, setView] = useState<{ zoom: number; center: Point } | null>(null)
  const drag = useRef<{ x: number; y: number; moved: boolean } | null>(null)
  const heading = useRef<Record<string, { xy: Point; angle: number }>>({})
  const world = state.world!
  const fleet = state.telemetry.fleet ?? {}

  useEffect(() => {
    const observer = new ResizeObserver(([entry]) => {
      if (entry.contentRect.width > 0) setSize([entry.contentRect.width, entry.contentRect.height])
    })
    if (box.current) observer.observe(box.current)
    return () => observer.disconnect()
  }, [])

  const [left, bottom, right, top] = geometry.bounds
  const fit = Math.max((right - left) * 1.04 / size[0], (top - bottom) * 1.06 / size[1]) // metres per pixel
  const zoom = view?.zoom ?? 1
  const center = view?.center ?? [(left + right) / 2, -(bottom + top) / 2] as Point
  const u = fit / zoom
  const origin: Point = [center[0] - size[0] * u / 2, center[1] - size[1] * u / 2]

  function zoomTo(factor: number, at: Point = center) {
    const next = Math.max(0.8, Math.min(6, zoom * factor))
    const ratio = zoom / next
    setView({ zoom: next, center: [at[0] + (center[0] - at[0]) * ratio, at[1] + (center[1] - at[1]) * ratio] })
  }
  const now = useRef({ zoomTo, origin, u })
  now.current = { zoomTo, origin, u }
  useEffect(() => {
    const element = box.current
    if (!element) return
    const onWheel = (event: WheelEvent) => {
      event.preventDefault()
      const rect = element.getBoundingClientRect()
      const { origin: o, u: scale } = now.current
      now.current.zoomTo(event.deltaY < 0 ? 1.18 : 1 / 1.18,
        [o[0] + (event.clientX - rect.left) * scale, o[1] + (event.clientY - rect.top) * scale])
    }
    element.addEventListener('wheel', onWheel, { passive: false })
    return () => element.removeEventListener('wheel', onWheel)
  }, [])

  const roads = useMemo(() => Object.entries(geometry.edges).map(([id, road]) => ({
    id, name: road.name, points: line(road.shape), width: 3 + road.lanes * 2.1,
  })), [geometry])
  const labels = useMemo(() => streetLabels(geometry), [geometry])
  const tasks = openTasks(world)
  const selectedTask = selected ? world.tasks[selected] : undefined
  const focusUnit = selectedTask?.assigned_vehicle_id ?? (selected && world.vehicles[selected] ? selected : null)
  const preview = proposal && !isStale(state) ? proposal : undefined
  const actions = preview ? planActions(preview, world) : []
  const latestSignal = new Map(state.activity.filter((e) => e.tls_id).map((e) => [e.tls_id, e.kind]))
  const cleared = (id: string) => ['priority_requested', 'phase_shortened', 'priority_green_reached'].includes(latestSignal.get(id) ?? '')
  const clicked = () => !drag.current?.moved

  function angleOf(id: string, xy: Point) {
    const last = heading.current[id]
    if (!last) { heading.current[id] = { xy, angle: 0 }; return 0 }
    const [dx, dy] = [xy[0] - last.xy[0], xy[1] - last.xy[1]]
    if (Math.hypot(dx, dy) > 0.8) {
      const turn = ((Math.atan2(dx, dy) * 180 / Math.PI - last.angle + 540) % 360 + 360) % 360 - 180
      heading.current[id] = { xy, angle: last.angle + turn } // turn the short way round
    }
    return heading.current[id].angle
  }

  return <div className={`map ${satellite ? 'satellite' : ''}`} ref={box}>
    <svg viewBox={`${origin[0]} ${origin[1]} ${size[0] * u} ${size[1] * u}`} role="img" aria-label="Live map of units, calls and hospitals"
      onPointerDown={(event) => { if (event.button === 0) drag.current = { x: event.clientX, y: event.clientY, moved: false } }}
      onPointerMove={(event) => {
        const start = drag.current
        if (!start) return
        const [dx, dy] = [event.clientX - start.x, event.clientY - start.y]
        if (!start.moved && Math.hypot(dx, dy) < 4) return
        drag.current = { x: event.clientX, y: event.clientY, moved: true }
        setView({ zoom, center: [center[0] - dx * u, center[1] - dy * u] })
      }}
      onPointerUp={() => { window.setTimeout(() => { drag.current = null }) }}
      onPointerLeave={() => { drag.current = null }}
      onClick={(event) => {
        const target = event.target as Element
        if (clicked() && (target === event.currentTarget || target.tagName === 'image')) { onSelect(null); onEdge(null) }
      }}
      onContextMenu={(event) => event.preventDefault()}>
      {geometry.network === 'ljubljana' && <image className="aerial" href="/ljubljana-aerial.jpg" {...AERIAL} preserveAspectRatio="none"/>}
      <g className="road-casing">{roads.map((r) => <polyline key={r.id} points={r.points} strokeWidth={r.width + 2 * u}/>)}</g>
      <g className="road">{roads.map((r) => <polyline key={r.id} points={r.points} strokeWidth={r.width} className={r.id === edge ? 'picked' : ''}
        onClick={() => { if (clicked()) { onSelect(null); onEdge(r.id === edge ? null : r.id) } }}
        onContextMenu={(event) => onMenu(event, 'road', r.id)}><title>{r.name || 'Unnamed road'}</title></polyline>)}</g>

      {Object.entries(fleet).map(([id, live]) => world.vehicles[id] && world.vehicles[id].status !== 'broken' && <g key={id}
        className={`route ${unitState(world.vehicles[id], state, geometry).key}`} opacity={focusUnit && focusUnit !== id ? 0.2 : 0.9}>
        {live.route.map((road, i) => geometry.edges[road] && <polyline key={i} points={line(geometry.edges[road].shape)} strokeWidth={3.5 * u}/>)}
      </g>)}
      {preview && Object.entries(preview.routes).map(([id, route]) => <g key={id} className="route proposal">
        {route.map((road, i) => geometry.edges[road] && <polyline key={i} points={line(geometry.edges[road].shape)}
          strokeWidth={4.5 * u} strokeDasharray={`${9 * u} ${6 * u}`}/>)}
      </g>)}
      {Object.keys(world.closed_edges).map((id) => geometry.edges[id] &&
        <polyline key={id} className="closed" points={line(geometry.edges[id].shape)} strokeWidth={7 * u} strokeDasharray={`${7 * u} ${5 * u}`}/>)}
      <g className="street-names">{labels.filter((l) => l.length / u > l.name.length * 3.6).map((l) =>
        <text key={l.name} transform={`translate(${l.at[0]},${-l.at[1]}) rotate(${l.angle})`} fontSize={11 * u} strokeWidth={3 * u} dy={3.6 * u}>{l.name}</text>)}</g>
      {Object.entries(geometry.signals).map(([id, [x, y]]) => cleared(id) && <g key={id} className="signal" transform={`translate(${x},${-y}) scale(${u})`}>
        <circle r={9}/><circle r={4} className="lamp"/><title>Traffic light cleared for an ambulance</title></g>)}

      {Object.values(world.receiving_sites).map((site) => {
        const p = geometry.nodes[site.node_id]
        return p && <g key={site.id} className="hospital" transform={`translate(${p[0]},${-p[1]}) scale(${u})`}>
          <rect x={-11} y={-11} width={22} height={22}/><path d="M-6,0H6M0,-6V6"/><text x={16} y={5}>{site.id}</text><title>Receiving hospital {site.id}</title></g>
      })}
      {Object.values(world.assets).filter((asset) => asset.location.kind === 'roadside').map((asset) => {
        const p = geometry.nodes[asset.location.ref_id]
        return p && <g key={asset.id} className="stranded" transform={`translate(${p[0]},${-p[1]}) scale(${u})`}>
          <rect x={-8} y={-8} width={16} height={16}/><text x={12} y={18}>{asset.id}</text><title>Equipment {asset.id} at the roadside</title></g>
      })}
      {selectedTask && geometry.nodes[selectedTask.pickup_node] && geometry.nodes[selectedTask.dropoff_node] && <line className="leg"
        x1={geometry.nodes[selectedTask.pickup_node][0]} y1={-geometry.nodes[selectedTask.pickup_node][1]}
        x2={geometry.nodes[selectedTask.dropoff_node][0]} y2={-geometry.nodes[selectedTask.dropoff_node][1]}
        strokeWidth={1.5 * u} strokeDasharray={`${4 * u} ${4 * u}`}/>}
      {tasks.filter((t) => !pickedUp(t)).map((task) => {
        const p = geometry.nodes[task.pickup_node]
        if (!p) return null
        return <g key={task.id} className={`call p${priorityRank(task)} ${needsUnit(task) ? 'open' : ''} ${selected === task.id ? 'selected' : ''}`}
          transform={`translate(${p[0]},${-p[1]}) scale(${u})`} onClick={() => { if (clicked()) onSelect(task.id) }}
          onContextMenu={(event) => onMenu(event, 'task', task.id)}>
          {needsUnit(task) && task.priority === 'critical' && <circle className="pulse" r={17}/>}
          <path className="pin" d="M0,-13 L11,0 L0,13 L-11,0 Z"/><text className="pin-text" y={4.2}>{priorityRank(task)}</text>
          <text className="tag-text" x={15} y={4 + 14 * tasks.filter((t) => !pickedUp(t) && t.pickup_node === task.pickup_node).indexOf(task)}>{shortId(task.id)}</text></g>
      })}
      {Object.values(world.vehicles).map((vehicle) => {
        const live = fleet[vehicle.id]
        const p = live?.xy ?? geometry.nodes[vehicle.node_id]
        if (!p) return null
        const status = unitState(vehicle, state, geometry)
        const action = actions.find((a) => a.unit === vehicle.id)
        return <g key={vehicle.id} className={`unit ${status.key} ${selected === vehicle.id ? 'selected' : ''}`}
          style={{ transform: `translate(${p[0]}px,${-p[1]}px)` }} onClick={() => { if (clicked()) onSelect(vehicle.id) }}
          onContextMenu={(event) => onMenu(event, 'unit', vehicle.id)}>
          <g transform={`scale(${u})`}>
            {selected === vehicle.id && <circle className="halo" r={17}/>}
            <g className="arrow" style={{ transform: `rotate(${angleOf(vehicle.id, p)}deg)` }}><path d="M0,-12 L9,9 L0,5 L-9,9 Z"/></g>
            <g transform="translate(-15,-33)"><rect className="plate" width={30} height={17}/><text className="callsign" x={15} y={12.8}>{vehicle.id}</text>
              {action && <><rect className="plate-ai" x={30} width={shortId(action.task).length * 8 + 24} height={17}/><text className="to" x={36} y={12.8}>▸ {shortId(action.task)}</text></>}</g>
            <title>{vehicle.id}, {status.label.toLowerCase()}{live ? `, ${Math.round(live.speed_mps * 3.6)} km/h` : ''}</title>
          </g>
        </g>
      })}
    </svg>
    <div className="map-tools">
      <button title="Zoom in" aria-label="Zoom in" onClick={() => zoomTo(1.35)}>+</button>
      <button title="Zoom out" aria-label="Zoom out" onClick={() => zoomTo(1 / 1.35)}>−</button>
      <button title="Show the whole area" aria-label="Show the whole area" onClick={() => setView(null)}>⤢</button>
    </div>
    {geometry.network === 'ljubljana' && <p className="attribution">© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">OpenStreetMap</a> contributors · Orthophoto 2024 GURS, CC BY 4.0</p>}
  </div>
}
