"""The one process that owns a TraCI connection (docs/CONTRACTS.md
"Ownership and process layout"). No other module calls `traci` directly.

**Process model.** Each CLI invocation (`rescueflow sim ...`) is a separate
OS process; TraCI cannot hold a connection open across them. Persistence
across invocations uses SUMO's own `simulation.saveState`/`--load-state`
(a real, documented SUMO feature for exactly this), *not* a reimplemented
serialization of simulator internals -- see docs/ARCHITECTURE.md
"Why save-state, not a long-lived process". Each command: connects (fresh
or resumed), reconstructs its own bookkeeping purely from `WorldState`
(never trusts leftover Python-side state from a previous process), does
its one job, mirrors real positions/completions back into `WorldState`,
saves state, and closes.

**Rerouting.** When a vehicle needs a new itinerary (freshly spawned, or an
approved plan changes its remaining stops), we `traci.vehicle.remove` it
and `traci.vehicle.add` it back immediately at the same edge/position/
speed with a freshly built route + `setStop` queue. This sidesteps the
ambiguity of whether `setRoute` preserves, drops, or re-triggers previously
scheduled stops -- removing custody-equivalent SUMO state entirely (custody
itself lives in `WorldState`/`Asset`, never in the SUMO vehicle object) and
re-adding is unambiguous and testable.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from rescueflow.events.apply import tick_scheduled_restorations
from rescueflow.interfaces.protocols import SmokeRunResult
from rescueflow.models.core import (
    SimulationSnapshot,
    StopKind,
    Task,
    TaskPriority,
    VehicleMode,
    VehicleStatus,
    WorldState,
)
from rescueflow.sumo.env import ensure_sumolib_importable, resolve_sumo_env
from rescueflow.sumo.green_wave import GreenWaveController
from rescueflow.travel.sumo_travel_time import SumoTravelTimeProvider

DEFAULT_STATE_PATH = "simulation/state/sumo.state.xml.gz"
HOSPITAL_HOLD_S = 2_000_000.0


class TraciSimulationController:
    """Concrete `SimulationController`."""

    def __init__(self, sumocfg_path: str, net_path: str, state_path: str = DEFAULT_STATE_PATH, seed: int = 42) -> None:
        self.sumocfg_path = sumocfg_path
        self.net_path = net_path
        self.state_path = state_path
        self.seed = seed
        self._connected = False
        self._travel: SumoTravelTimeProvider | None = None
        self.green_wave = GreenWaveController()
        self.event_log: list[dict] = []
        self._green_wave_log_copied = 0
        self._stop_queue: dict[str, list[dict]] = {}
        self._next_stop_ptr: dict[str, int] = {}
        self._was_stopped: dict[str, bool] = {}

    # -- connection lifecycle -------------------------------------------------

    def _ensure_travel(self) -> SumoTravelTimeProvider:
        if self._travel is None:
            self._travel = SumoTravelTimeProvider(self.net_path)
        return self._travel

    def has_saved_state(self) -> bool:
        return Path(self.state_path).exists()

    def connect(self, resume: bool, world: WorldState) -> None:
        env = resolve_sumo_env()
        if env is None:
            raise RuntimeError("SUMO not found; cannot start a simulation session. See docs/RUNBOOK.md.")
        ensure_sumolib_importable()
        import traci  # noqa: PLC0415

        args = [
            str(env.sumo_binary), "-c", self.sumocfg_path,
            "--no-step-log", "--duration-log.disable", "--seed", str(self.seed),
        ]
        if resume and self.has_saved_state():
            args += ["--load-state", self.state_path]
        traci.start(args)
        self._connected = True
        self._ensure_travel().set_closed_edges(world.closed_edges)
        if resume:
            self._rebuild_queues_from_world(world)

    def save_and_close(self) -> None:
        if not self._connected:
            return
        import traci  # noqa: PLC0415

        Path(self.state_path).parent.mkdir(parents=True, exist_ok=True)
        traci.simulation.saveState(self.state_path)
        traci.close()
        self._connected = False

    def close(self) -> None:
        if not self._connected:
            return
        import traci  # noqa: PLC0415

        traci.close()
        self._connected = False

    def reset(self) -> None:
        self.close()
        Path(self.state_path).unlink(missing_ok=True)
        self._stop_queue.clear()
        self._next_stop_ptr.clear()
        self._was_stopped.clear()
        self.event_log.clear()

    # -- route/stop bookkeeping ------------------------------------------------

    def _dwell_seconds(self, world: WorldState, stop) -> float:
        if stop.task_id is None:
            return 1.0
        task = world.tasks.get(stop.task_id)
        if task is None:
            return 1.0
        if stop.kind == StopKind.pickup:
            return task.load_seconds
        handling = 0.0
        for site in world.receiving_sites.values():
            if site.node_id == stop.node_id:
                handling = site.service_handling_s
                break
        return task.unload_seconds + handling

    def _stop_duration(self, world: WorldState, stop) -> float:
        if stop.kind == StopKind.dropoff and any(
            site.node_id == stop.node_id for site in world.receiving_sites.values()
        ):
            # Release explicitly after the real dwell and a receiving-slot check.
            return HOSPITAL_HOLD_S
        return self._dwell_seconds(world, stop)

    def _build_route(self, world: WorldState, vehicle, stops, start_node: str | None, start_edge: str | None):
        """Builds one real edge-path through every stop in order.

        A stop whose node equals the vehicle's *current* position (no
        travel needed yet, e.g. a pickup at the depot it starts at) cannot
        be assigned an edge until we know which edge the vehicle will
        actually depart on next -- picking an arbitrary incident edge here
        produced a real, observed bug: a disconnected route ("No connection
        between edge X and edge Y") whenever that arbitrary edge happened
        to differ from the real departure edge computed by the next real
        segment. Such stops are queued in `pending` and only resolved once
        the next real segment's *first* edge is known."""
        travel = self._ensure_travel()
        if start_edge is not None:
            edges = [start_edge]
            current_node = travel.net.getEdge(start_edge).getToNode().getID()
        else:
            edges = []
            current_node = start_node

        stop_plan = []
        pending: list = []
        for stop in stops:
            if stop.node_id == current_node:
                pending.append(stop)
                continue
            if edges:
                # Continue from the *specific* edge we already committed
                # to, not from any edge touching `current_node` -- a
                # junction need not have a connection from every incoming
                # edge to every outgoing one (e.g. no U-turn), so picking
                # departure edges independently per leg can silently chain
                # two edges that aren't actually connected (a real bug this
                # fixed -- see docs/DECISIONS.md).
                segment = travel.path_edges_from_edge(edges[-1], stop.node_id)
            else:
                segment = travel.road_path_edges(current_node, stop.node_id)
            if segment is None:
                raise ValueError(
                    f"vehicle {vehicle.id}: no open road route from {current_node} to {stop.node_id} "
                    f"(closed edges: {sorted(world.closed_edges)})"
                )
            departure_edge = segment[0]
            if edges and edges[-1] == departure_edge:
                edges.extend(segment[1:])
            else:
                edges.extend(segment)
            for pend in pending:
                stop_plan.append({
                    "edge_id": departure_edge, "stop_id": pend.stop_id, "task_id": pend.task_id,
                    "stop_kind": pend.kind.value, "duration": self._stop_duration(world, pend),
                })
            pending = []
            stop_plan.append({
                "edge_id": edges[-1], "stop_id": stop.stop_id, "task_id": stop.task_id,
                "stop_kind": stop.kind.value, "duration": self._stop_duration(world, stop),
            })
            current_node = stop.node_id

        if pending:
            fallback_edge = edges[-1] if edges else travel.any_edge_at(current_node)
            for pend in pending:
                stop_plan.append({
                    "edge_id": fallback_edge, "stop_id": pend.stop_id, "task_id": pend.task_id,
                    "stop_kind": pend.kind.value, "duration": self._stop_duration(world, pend),
                })
            if not edges:
                edges = [fallback_edge]
        return edges, stop_plan

    def _establish_route(self, world: WorldState, vehicle, stops: list) -> None:
        """(Re)spawns `vehicle` in SUMO with exactly `stops` as its
        remaining itinerary, continuing from its current live edge/position
        if it already exists, or from `vehicle.node_id` if this is its
        first appearance."""
        import traci  # noqa: PLC0415

        if not stops:
            return
        live = vehicle.id in traci.vehicle.getIDList()
        start_edge = None
        depart_kwargs = {}
        if live:
            start_edge = traci.vehicle.getRoadID(vehicle.id)
            if start_edge.startswith(":"):
                # Mid-junction -- cannot reroute from an internal edge; the
                # caller should retry on the next step once it clears.
                raise RuntimeError(f"vehicle {vehicle.id} is mid-junction; retry rerouting next step")
            depart_kwargs = {
                "departPos": str(traci.vehicle.getLanePosition(vehicle.id)),
                "departSpeed": str(traci.vehicle.getSpeed(vehicle.id)),
            }
            traci.vehicle.remove(vehicle.id)

        edges, stop_plan = self._build_route(world, vehicle, stops, start_node=vehicle.node_id, start_edge=start_edge)
        route_id = f"route_{vehicle.id}_{uuid.uuid4().hex[:8]}"
        traci.route.add(route_id, edges)
        traci.vehicle.add(
            vehicle.id, route_id, typeID=self._vtype_for_stops(world, stops),
            depart=str(traci.simulation.getTime()), **depart_kwargs
        )
        for entry in stop_plan:
            traci.vehicle.setStop(vehicle.id, entry["edge_id"], duration=entry["duration"], flags=0)

        self._stop_queue[vehicle.id] = [
            {"stop_id": e["stop_id"], "task_id": e["task_id"], "stop_kind": e["stop_kind"]} for e in stop_plan
        ]
        self._next_stop_ptr[vehicle.id] = 0
        self._was_stopped[vehicle.id] = False

    def _vtype_for_stops(self, world: WorldState, stops: list) -> str:
        """`ambulance` (real vClass=emergency privileges) iff any stop in
        this itinerary serves a critical-priority task, else
        `ambulance_routine` (no privileges) -- see the fairness note in
        `sumo/network_gen.py`. A single spawn's type is fixed for its whole
        itinerary; switching mid-route exactly at a leg boundary would need
        splitting the route and re-adding there, not justified in this
        scope since green-wave eligibility is independently recomputed
        every step from the *current* active task regardless of vType."""
        for stop in stops:
            if stop.task_id is None:
                continue
            task = world.tasks.get(stop.task_id)
            if task is not None and task.priority == TaskPriority.critical:
                return "ambulance"
        return "ambulance_routine"

    def _rebuild_queues_from_world(self, world: WorldState) -> None:
        import traci  # noqa: PLC0415

        live_ids = set(traci.vehicle.getIDList())
        for vehicle_id, vehicle in world.vehicles.items():
            if vehicle_id not in live_ids:
                continue
            served = 0
            for stop in vehicle.route_stops:
                if stop.service_completed_at_s is not None:
                    served += 1
                else:
                    break
            self._stop_queue[vehicle_id] = [
                {"stop_id": s.stop_id, "task_id": s.task_id, "stop_kind": s.kind.value} for s in vehicle.route_stops
            ]
            self._next_stop_ptr[vehicle_id] = served
            self._was_stopped[vehicle_id] = traci.vehicle.isStopped(vehicle_id)

    # -- scenario setup / plan application --------------------------------------

    def load_scenario(self, world: WorldState) -> None:
        """Fresh start (ignores any saved state): spawns every ambulance
        that already has stops with its full itinerary from `vehicle.node_id`.
        Helicopters are never injected into SUMO (analytical model only)."""
        self.reset()
        self.connect(resume=False, world=world)
        for vehicle in world.vehicles.values():
            if vehicle.mode != VehicleMode.ambulance or not vehicle.route_stops:
                continue
            self._establish_route(world, vehicle, vehicle.route_stops)
        # A vehicle `traci.vehicle.add`-ed in the same step it is saved is
        # not yet fully realized internally and is silently dropped by
        # `--load-state` on the next process's reconnect (observed, not
        # theoretical -- see docs/DECISIONS.md). One real step after
        # spawning, before the first save, fixes this.
        self.step(world, n=1)
        self.save_and_close()

    def apply_committed_plan(self, world: WorldState, vehicle_ids: list[str]) -> None:
        """Called after `ApprovalService.approve` commits new assignments:
        re-establishes SUMO routing for exactly the vehicles the approved
        proposal touched. A broken vehicle is skipped (it is frozen, not
        rerouted) -- see `freeze_vehicle_and_locate`."""
        self.connect(resume=True, world=world)
        any_established = False
        for vehicle_id in vehicle_ids:
            vehicle = world.vehicles[vehicle_id]
            if vehicle.mode != VehicleMode.ambulance or vehicle.status == VehicleStatus.broken:
                continue
            served = sum(1 for s in vehicle.route_stops if s.service_completed_at_s is not None)
            remaining = vehicle.route_stops[served:]
            if remaining:
                self._establish_route(world, vehicle, remaining)
                any_established = True
        if any_established:
            # See `load_scenario` -- a (re)spawned vehicle needs one real
            # step before saveState reliably captures it.
            self.step(world, n=1)
        self.save_and_close()

    def freeze_vehicle_and_locate(self, world: WorldState, vehicle_id: str) -> str:
        """Call *before* applying a `vehicle_breakdown` event (while `status`
        is still whatever it was): forces the vehicle to a dead stop at its
        real live position and returns its real node id, so the event
        payload's recovery-task pickup location is grounded in reality
        (see docs/DECISIONS.md -- this makes the prompt-1 "last confirmed
        node" approximation exact once a live session exists). Does not
        itself set `status = broken`; the caller still runs the normal
        `events.apply.apply_event` for that, unchanged.
        Connects and closes its own session; caller does not need to."""
        self.connect(resume=True, world=world)
        import traci  # noqa: PLC0415

        vehicle = world.vehicles[vehicle_id]
        node_id = vehicle.node_id
        if vehicle_id in traci.vehicle.getIDList():
            edge = traci.vehicle.getRoadID(vehicle_id)
            pos = traci.vehicle.getLanePosition(vehicle_id)
            traci.vehicle.setSpeed(vehicle_id, 0)
            vehicle.sumo_edge_id = edge
            vehicle.sumo_lane_position = pos
            node_id = self._edge_to_node(edge)
            vehicle.node_id = node_id
            self.event_log.append({
                "sim_time_s": world.sim_time_s, "kind": "vehicle_frozen", "vehicle_id": vehicle_id,
                "edge_id": edge, "position": pos, "node_id": node_id,
            })
        self.save_and_close()
        return node_id

    def apply_road_closure_physically(self, world: WorldState, edge_id: str) -> None:
        """Disallows every lane of `edge_id` for all vehicle classes going
        forward (`traci.lane.setDisallowed`) -- vehicles already on the
        edge are not ejected or teleported; this only blocks new routing
        onto it, matching the "ahead of vehicles" closure semantics
        documented in docs/DECISIONS.md."""
        self.connect(resume=True, world=world)
        import traci  # noqa: PLC0415

        net = self._ensure_travel().net
        edge = net.getEdge(edge_id)
        for lane in edge.getLanes():
            traci.lane.setDisallowed(lane.getID(), ["all"])
        self.event_log.append({"sim_time_s": world.sim_time_s, "kind": "edge_closed_physically", "edge_id": edge_id})
        self.save_and_close()

    def _edge_to_node(self, edge_id: str) -> str:
        if edge_id.startswith(":"):
            return edge_id.split("_")[0].lstrip(":")
        return self._ensure_travel().net.getEdge(edge_id).getToNode().getID()

    # -- stepping --------------------------------------------------------------

    def step(self, world: WorldState, n: int = 1) -> SimulationSnapshot:
        import traci  # noqa: PLC0415

        self._enforce_frozen(world, traci)

        for _ in range(n):
            self._hold_hospital_dropoffs(world, traci)
            traci.simulationStep()
            world.sim_time_s = traci.simulation.getTime()
            world.bump_telemetry()
            self._process_arrivals_and_stops(world, traci)
            self._update_positions(world, traci)
            self._run_green_wave(world, traci)
            for restored in tick_scheduled_restorations(world):
                self.event_log.append({
                    "sim_time_s": world.sim_time_s, "kind": "scheduled_restoration_applied",
                    "restoration_kind": restored.kind, "target_id": restored.target_id,
                })
            self._enforce_frozen(world, traci)

        positions = {
            vid: {"edge": traci.vehicle.getRoadID(vid), "pos": traci.vehicle.getLanePosition(vid)}
            for vid in traci.vehicle.getIDList()
        }
        return SimulationSnapshot(telemetry_seq=world.telemetry_seq, sim_time_s=world.sim_time_s, vehicle_positions=positions)

    def _hold_hospital_dropoffs(self, world: WorldState, traci) -> None:
        """Leave due handovers in their physical SUMO stop until a slot is available."""
        next_time = traci.simulation.getTime() + traci.simulation.getDeltaT()
        sites = {site.node_id: site for site in world.receiving_sites.values()}
        if not sites:
            return
        remaining = {}
        for node, site in sites.items():
            if not site.slots:
                remaining[node] = float("inf")
                continue
            slot = next((slot for slot in site.slots if slot.start_s <= next_time < slot.end_s), None)
            remaining[node] = 0 if slot is None else slot.capacity - sum(
                stop.kind == StopKind.dropoff and stop.node_id == node
                and stop.service_completed_at_s is not None
                and slot.start_s <= stop.service_completed_at_s < slot.end_s
                for vehicle in world.vehicles.values() for stop in vehicle.route_stops
            )

        live = set(traci.vehicle.getIDList())
        for vehicle_id in sorted(live.intersection(self._stop_queue)):
            vehicle = world.vehicles[vehicle_id]
            if vehicle.status == VehicleStatus.broken or not self._was_stopped.get(vehicle_id):
                continue
            queue = self._stop_queue[vehicle_id]
            index = self._next_stop_ptr[vehicle_id]
            if index >= len(queue) or queue[index]["stop_kind"] != "dropoff":
                continue
            stop_id = queue[index]["stop_id"]
            stop = next((s for s in vehicle.route_stops if s.stop_id == stop_id), None)
            if stop is None or stop.node_id not in remaining:
                continue
            if stop.service_start_s is None or next_time < stop.service_start_s + self._dwell_seconds(world, stop):
                continue
            if remaining[stop.node_id] > 0:
                traci.vehicle.resume(vehicle_id)
                remaining[stop.node_id] -= 1

    def _enforce_frozen(self, world: WorldState, traci) -> None:
        """A broken vehicle must never move again. `setSpeed(0)` alone left
        a small (few-meter) residual drift over many steps in practice (a
        real, observed discrepancy, not theoretical); registering an actual
        long `setStop` at its current position is SUMO's own "this vehicle
        is genuinely stationary" mechanism and is exact. Falls back to
        `setSpeed(0)` alone if the vehicle is mid-junction (an internal
        edge, where `setStop` is not valid) until it clears onto a real edge."""
        live = set(traci.vehicle.getIDList())
        for vehicle_id, vehicle in world.vehicles.items():
            if vehicle.status != VehicleStatus.broken or vehicle_id not in live:
                continue
            traci.vehicle.setSpeed(vehicle_id, 0)
            if traci.vehicle.isStopped(vehicle_id):
                continue
            edge = traci.vehicle.getRoadID(vehicle_id)
            # setStop fails ("too close to brake") while still in motion;
            # only register the exact-position stop once setSpeed(0) has
            # actually brought it to a standstill (usually the next step).
            if edge.startswith(":") or traci.vehicle.getSpeed(vehicle_id) > 0.1:
                continue
            pos = traci.vehicle.getLanePosition(vehicle_id)
            try:
                traci.vehicle.setStop(vehicle_id, edge, pos=pos, duration=1e6, flags=0)
            except Exception:  # noqa: BLE001 -- best-effort; setSpeed(0) above still applies
                pass

        positions = {
            vid: {"edge": traci.vehicle.getRoadID(vid), "pos": traci.vehicle.getLanePosition(vid)}
            for vid in traci.vehicle.getIDList()
        }
        return SimulationSnapshot(telemetry_seq=world.telemetry_seq, sim_time_s=world.sim_time_s, vehicle_positions=positions)

    def _process_arrivals_and_stops(self, world: WorldState, traci) -> None:
        arrived = set(traci.simulation.getArrivedIDList())
        live = set(traci.vehicle.getIDList())
        for vehicle_id, queue in self._stop_queue.items():
            if world.vehicles[vehicle_id].status == VehicleStatus.broken:
                continue
            ptr = self._next_stop_ptr.get(vehicle_id, 0)
            if ptr >= len(queue):
                continue
            if vehicle_id in arrived:
                # Reached the end of its route -- every remaining queued
                # stop must have already been serviced for SUMO to allow
                # that (a route cannot pass a pending `setStop`).
                while ptr < len(queue):
                    self._complete_stop(world, vehicle_id, queue[ptr])
                    ptr += 1
                self._next_stop_ptr[vehicle_id] = ptr
                continue
            if vehicle_id not in live:
                continue
            stopped_now = traci.vehicle.isStopped(vehicle_id)
            was = self._was_stopped.get(vehicle_id, False)
            if stopped_now and not was:
                stop = next((s for s in world.vehicles[vehicle_id].route_stops if s.stop_id == queue[ptr]["stop_id"]), None)
                if stop is not None:
                    stop.service_start_s = world.sim_time_s
                self.event_log.append({
                    "sim_time_s": world.sim_time_s, "kind": "stop_begin", "vehicle_id": vehicle_id, **queue[ptr],
                })
            if was and not stopped_now:
                self._complete_stop(world, vehicle_id, queue[ptr])
                self._next_stop_ptr[vehicle_id] = ptr + 1
            self._was_stopped[vehicle_id] = stopped_now

    def _complete_stop(self, world: WorldState, vehicle_id: str, entry: dict) -> None:
        vehicle = world.vehicles[vehicle_id]
        stop_obj = next((s for s in vehicle.route_stops if s.stop_id == entry["stop_id"]), None)
        if stop_obj is not None:
            stop_obj.service_completed_at_s = world.sim_time_s
            stop_obj.departure_s = world.sim_time_s
            stop_obj.arrival_s = min(stop_obj.arrival_s, world.sim_time_s) if stop_obj.arrival_s is not None else world.sim_time_s
            if stop_obj.kind == StopKind.dropoff:
                vehicle.node_id = stop_obj.node_id

        self.event_log.append({"sim_time_s": world.sim_time_s, "kind": "stop_completed", "vehicle_id": vehicle_id, **entry})

        task_id = entry["task_id"]
        if task_id is None:
            return
        task = world.tasks.get(task_id)
        if task is None:
            return

        if entry["stop_kind"] == "pickup":
            from rescueflow.models.core import AssetLocation, TaskStatus  # noqa: PLC0415

            task.status = TaskStatus.en_route_dropoff
            for asset_id in task.required_asset_ids:
                asset = world.assets.get(asset_id)
                if asset is not None:
                    asset.location = AssetLocation(kind="vehicle", ref_id=vehicle_id)
                    asset.status = "in_transit"
                if asset_id not in vehicle.equipment_aboard:
                    vehicle.equipment_aboard.append(asset_id)
        elif entry["stop_kind"] == "dropoff":
            from rescueflow.models.core import AssetLocation, TaskStatus  # noqa: PLC0415

            task.status = TaskStatus.completed
            for asset_id in task.required_asset_ids:
                asset = world.assets.get(asset_id)
                if asset is not None:
                    site = next((s for s in world.receiving_sites.values() if s.node_id == stop_obj.node_id), None) if stop_obj else None
                    asset.location = AssetLocation(kind="hospital" if site else "depot", ref_id=stop_obj.node_id if stop_obj else task.dropoff_node)
                    asset.status = "available"
                if asset_id in vehicle.equipment_aboard:
                    vehicle.equipment_aboard.remove(asset_id)
        world.bump_plan_revision()

    def _update_positions(self, world: WorldState, traci) -> None:
        live = set(traci.vehicle.getIDList())
        for vehicle_id, vehicle in world.vehicles.items():
            if vehicle_id in live:
                vehicle.sumo_edge_id = traci.vehicle.getRoadID(vehicle_id)
                vehicle.sumo_lane_position = traci.vehicle.getLanePosition(vehicle_id)

    def _run_green_wave(self, world: WorldState, traci) -> None:
        self._ensure_travel().set_closed_edges(world.closed_edges)
        live = set(traci.vehicle.getIDList())
        eligible: dict[str, int] = {}
        for vehicle_id, vehicle in world.vehicles.items():
            if vehicle_id not in live or vehicle.status == VehicleStatus.broken:
                continue
            active_task = _current_task(world, vehicle)
            if active_task is not None and active_task.priority == TaskPriority.critical:
                eligible[vehicle_id] = 0
        self.green_wave.step(traci, world, world.sim_time_s, eligible)
        self.event_log.extend(self.green_wave.log[self._green_wave_log_copied:])
        self._green_wave_log_copied = len(self.green_wave.log)

    # -- smoke test (prompt 1, unchanged behavior) ------------------------------

    def run_smoke(self, duration_s: int) -> SmokeRunResult:
        import time

        env = resolve_sumo_env()
        if env is None:
            return SmokeRunResult(
                sumo_version="unknown", duration_wall_ms=0.0, sim_time_elapsed_s=0.0,
                vehicles_arrived=0, ok=False, detail="SUMO not found; see docs/RUNBOOK.md",
            )
        ensure_sumolib_importable()
        import traci  # noqa: PLC0415

        sumo_version = self._read_sumo_version(env)
        wall_start = time.perf_counter()
        traci.start([str(env.sumo_binary), "-c", self.sumocfg_path, "--no-step-log", "--duration-log.disable"])
        self._connected = True
        try:
            arrived = 0
            observed_positions: list[tuple[str, float]] = []
            elapsed_s = 0.0
            step = 0
            while step < duration_s:
                traci.simulationStep()
                step += 1
                elapsed_s = traci.simulation.getTime()
                if "smoke_amb" in traci.vehicle.getIDList():
                    observed_positions.append((traci.vehicle.getRoadID("smoke_amb"), traci.vehicle.getLanePosition("smoke_amb")))
                arrived += traci.simulation.getArrivedNumber()
                if "smoke_amb" not in traci.vehicle.getIDList() and observed_positions:
                    break
            moved = len({p[0] for p in observed_positions}) > 1
            ambulance_arrived = len(observed_positions) > 0 and "smoke_amb" not in traci.vehicle.getIDList()
            wall_ms = (time.perf_counter() - wall_start) * 1000
            ok = moved and ambulance_arrived
            detail = (
                f"ambulance visited {len({p[0] for p in observed_positions})} distinct edges, "
                f"arrived={ambulance_arrived}, total arrivals (all vehicles)={arrived}"
            )
            return SmokeRunResult(
                sumo_version=sumo_version, duration_wall_ms=wall_ms, sim_time_elapsed_s=elapsed_s,
                vehicles_arrived=arrived, ok=ok, detail=detail,
            )
        finally:
            traci.close()
            self._connected = False

    @staticmethod
    def _read_sumo_version(env) -> str:
        import subprocess

        result = subprocess.run([str(env.sumo_binary), "--version"], capture_output=True, text=True, check=True)
        first_line = result.stdout.splitlines()[0] if result.stdout else "unknown"
        return first_line.strip()


def _current_task(world: WorldState, vehicle) -> Task | None:
    for stop in vehicle.route_stops:
        if stop.task_id and stop.service_completed_at_s is None:
            return world.tasks.get(stop.task_id)
    return None
