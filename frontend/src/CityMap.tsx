import { useMemo, useState } from 'react'
import { Activity, Crosshair, MapPin, Minus, Plus, Route, TrafficCone, Truck } from 'lucide-react'
import { clock, vehicleColors, vehicleStatus } from './model'
import type { Geometry, Point, Proposal, Snapshot } from './model'

const path = (shape: Point[]) => shape.map(([x, y]) => `${x},${-y}`).join(' ')

interface Props {
  geometry: Geometry | null; snapshot: Snapshot; proposal?: Proposal
  selected: string | null; onSelect: (id: string) => void
  edge: string | null; onEdge: (id: string | null) => void
}

export function CityMap({ geometry, snapshot, proposal, selected, onSelect, edge, onEdge }: Props) {
  const [zoom, setZoom] = useState(1)
  const [offset, setOffset] = useState<Point>([0, 0])
  const [drag, setDrag] = useState<Point | null>(null)
  const [signal, setSignal] = useState<string | null>(null)
  const streets = useMemo(() => Object.entries(geometry?.edges ?? {}).map(([id, road]) => ({
    id, line: path(road.shape), name: road.name, lanes: road.lanes,
  })), [geometry])
  const world = snapshot.world
  const position = snapshot.telemetry.fleet ?? {}
  const phases = snapshot.telemetry.signals ?? {}
  const latestSignal = new Map(snapshot.activity.filter((event) => event.tls_id).map((event) => [event.tls_id, event]))
  const activeSignal = (id: string) => ['priority_requested', 'phase_shortened', 'priority_green_reached'].includes(latestSignal.get(id)?.kind ?? '')

  if (!geometry || !world) return <section className="panel map-loading"><Activity size={24} /> Waiting for SUMO street geometry</section>
  const [left, bottom, right, top] = geometry.bounds
  const width = (right - left) * 1.08
  const height = (top - bottom) * 1.11
  const size = Math.max(width, height) / 90 / Math.sqrt(zoom)
  const center = [(left + right) / 2 + offset[0], -(bottom + top) / 2 + offset[1]]
  const bounds = `${center[0] - width / zoom / 2} ${center[1] - height / zoom / 2} ${width / zoom} ${height / zoom}`
  const changeZoom = (factor: number) => setZoom((current) => Math.max(1, Math.min(4, current * factor)))
  const task = selected ? world.tasks[selected] : undefined
  const selectedVehicle = selected ? world.vehicles[selected] : undefined
  const focusVehicle = task?.assigned_vehicle_id ?? selectedVehicle?.id
  const selectedRoad = edge ? geometry.edges[edge] : undefined
  const selectedPhase = signal ? phases[signal] : undefined

  return <section className="panel map-panel" aria-label="Live SUMO map">
    <header className="panel-head">
      <div className="section-title"><MapPin size={16} /><h2>{geometry.label}</h2></div>
      <span className="tag live-tag"><i /> SUMO telemetry</span>
    </header>
    <div className="map-window">
      <div className="map-coordinate"><span>ROAD NETWORK</span><strong>{Object.keys(geometry.edges).length} directed edges</strong></div>
      <svg className="city-map" viewBox={bounds} role="img" aria-label="SUMO roads, hospitals, vehicles and signals"
        onPointerDown={(event) => { if (event.target === event.currentTarget) setDrag([event.clientX, event.clientY]) }}
        onPointerUp={() => setDrag(null)} onPointerLeave={() => setDrag(null)}
        onPointerMove={(event) => {
          if (!drag) return
          const scale = width / zoom / event.currentTarget.clientWidth
          setOffset(([x, y]) => [x - (event.clientX - drag[0]) * scale, y - (event.clientY - drag[1]) * scale])
          setDrag([event.clientX, event.clientY])
        }}>
        {streets.map((road) => <polyline key={road.id} points={road.line} fill="none"
          className={`street ${road.lanes > 1 ? 'main-road' : ''} ${road.id === edge ? 'street-selected' : ''}`}
          onClick={() => { onEdge(road.id === edge ? null : road.id); setSignal(null) }}>
          <title>{road.name || road.id}</title>
        </polyline>)}
        {Object.entries(position).map(([id, telemetry]) => world.vehicles[id]?.status !== 'broken' &&
          <g key={id} className="route-overlay" opacity={focusVehicle && focusVehicle !== id ? '0.28' : '0.95'}>
            {telemetry.route.map((idEdge, index) => geometry.edges[idEdge] &&
              <polyline key={index} points={path(geometry.edges[idEdge].shape)} stroke={vehicleColors[id]} className="committed-route" />)}
          </g>)}
        {proposal && Object.entries(proposal.routes).map(([id, route]) =>
          <g key={id} className="route-overlay">
            {route.map((idEdge, index) => geometry.edges[idEdge] &&
              <polyline key={index} points={path(geometry.edges[idEdge].shape)} className="proposed-route" />)}
          </g>)}
        {Object.keys(world.closed_edges).map((id) => geometry.edges[id] &&
          <polyline key={id} points={path(geometry.edges[id].shape)} className="closed-road" />)}
        {Object.entries(geometry.signals).map(([id, [x, y]]) => <circle key={id}
          cx={x} cy={-y} r={size * (activeSignal(id) ? 0.58 : 0.23)}
          className={`signal ${activeSignal(id) ? 'priority-signal' : ''} ${signal === id ? 'selected-signal' : ''}`}
          onClick={() => { setSignal(id); onEdge(null) }}>
          <title>{`Signal ${id} · phase ${phases[id]?.phase ?? '—'}${activeSignal(id) ? ' · priority in progress' : ''}`}</title>
        </circle>)}
        {Object.values(world.receiving_sites).map((hospital) => {
          const point = geometry.nodes[hospital.node_id]
          if (!point) return null
          const current = hospital.slots.find((slot) => slot.start_s <= world.sim_time_s && world.sim_time_s < slot.end_s)
          const capacity = hospital.slots.length ? `${current?.capacity ?? 0} slots this window` : 'Open receiving'
          const upcoming = hospital.slots.find((slot) => slot.start_s > world.sim_time_s)
          return <g className="hospital-marker" key={hospital.id} transform={`translate(${point[0]},${-point[1]})`}>
            <rect x={-size * .7} y={-size * .7} width={size * 1.4} height={size * 1.4} rx={size * .28} />
            <path className="hospital-cross" d={`M${-size * .33},0h${size * .66}M0,${-size * .33}v${size * .66}`}/>
            <text x={size * .98} y={size * .25} fontSize={size * .83}>{hospital.id}</text>
            <title>{`${hospital.id} · ${capacity}${upcoming ? ` · ${upcoming.capacity} slots from ${clock(upcoming.start_s)}` : ''}`}</title>
          </g>
        })}
        {Object.values(world.assets).filter((asset) => asset.location.kind === 'roadside').map((asset) => {
          const point = geometry.nodes[asset.location.ref_id]
          return point && <g className="incident-marker" key={asset.id} transform={`translate(${point[0]},${-point[1]})`}>
            <circle r={size * 1.25} /><text x={size * .25} y={-size * 1.45} fontSize={size}>{asset.id}</text>
          </g>
        })}
        {task && [task.pickup_node, task.dropoff_node].map((node, index) => {
          const point = geometry.nodes[node]
          return point && <circle key={index} cx={point[0]} cy={-point[1]} r={size * 1.3} className="task-marker"><title>{index === 0 ? 'Pickup' : 'Drop-off'}</title></circle>
        })}
        {Object.values(world.vehicles).map((vehicle) => {
          const live = position[vehicle.id]
          const point = live?.xy ?? geometry.nodes[vehicle.node_id]
          const broken = vehicle.status === 'broken'
          if (!point) return null
          const [x, y] = point
          const fill = broken ? '#ff857e' : vehicleColors[vehicle.id]
          return <g key={vehicle.id} className="vehicle-marker" transform={`translate(${x},${-y})`}
            onClick={() => onSelect(vehicle.id)} aria-label={vehicle.id}>
            {(focusVehicle === vehicle.id || broken) && <circle className="vehicle-ring" r={size * 1.1} style={{ stroke: fill }} />}
            <circle r={size * .53} fill={fill} opacity={live || broken ? 1 : 0.65} />
            <text x={size * .78} y={-size * .58} fill={fill} fontSize={size * .79}>{vehicle.id === 'HELI_1' ? 'HELI' : vehicle.id}</text>
            <title>{`${vehicle.id} · ${vehicleStatus(vehicle, world)}${live ? ` · ${Math.round(live.speed_mps * 3.6)} km/h` : ''}`}</title>
          </g>
        })}
      </svg>
      <div className="map-zoom">
        <button title="Zoom in" aria-label="Zoom in" onClick={() => changeZoom(1.3)} disabled={zoom >= 4}><Plus size={16} /></button>
        <button title="Zoom out" aria-label="Zoom out" onClick={() => changeZoom(1 / 1.3)} disabled={zoom <= 1}><Minus size={16} /></button>
        <button title="Reset map view" aria-label="Reset map view" onClick={() => { setZoom(1); setOffset([0, 0]) }}><Crosshair size={15} /></button>
      </div>
      <div className="map-attribution">
        {snapshot.network === 'ljubljana' ? <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">© OpenStreetMap contributors</a> : 'Fictional 7×7 grid'}
      </div>
      {(selectedRoad || selectedPhase) && <div className="map-info">
        {selectedRoad ? <><TrafficCone size={15} /><span>{selectedRoad.name || edge} · {edge && edge in world.closed_edges ? 'Closed' : 'Selected road'}</span></>
          : <><Activity size={15} /><span title={`SUMO link states: ${selectedPhase?.state}`}>Signal {signal} · phase {selectedPhase?.phase} · {activeSignal(signal!) ? 'Priority active' : 'Normal program'}</span></>}
        <button aria-label="Clear map detail" onClick={() => { onEdge(null); setSignal(null) }}>×</button>
      </div>}
    </div>
    <div className="map-legend"><span><Truck size={14} /> Unit</span><span><i className="route-key" /> Active route</span>
      <span><Route size={14} /> Proposed</span><span><i className="closure-key" /> Closed road</span>
      <span><i className="signal-key" /> Signal priority</span>
      <span className="map-time">{clock(world.sim_time_s)} SIM</span></div>
  </section>
}
