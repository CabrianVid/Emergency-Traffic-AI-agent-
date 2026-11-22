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

import math
import uuid
from collections import Counter
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

    def __init__(
        self, sumocfg_path: str, net_path: str, state_path: str = DEFAULT_STATE_PATH,
        seed: int = 42, *, allow_teleport: bool = True,
        observations_dir: Path | None = None, junction_stops: bool = False, gui: bool = False,
    ) -> None:
        self.sumocfg_path = sumocfg_path
        self.net_path = net_path
        self.state_path = state_path
        self.seed = seed
        self.allow_teleport = allow_teleport
        self.observations_dir = observations_dir
        self.junction_stops = junction_stops
        self.gui = gui
        self.priority_probe_ids: set[str] = set()
        self._connected = False
        self._travel: SumoTravelTimeProvider | None = None
        self.green_wave = GreenWaveController()
        self.event_log: list[dict] = []
        self._green_wave_log_copied = 0
        self._stop_queue: dict[str, list[dict]] = {}
        self._next_stop_ptr: dict[str, int] = {}
        self._was_stopped: dict[str, bool] = {}
        self._civilian_loss_s: dict[str, float] = {}
        self._gui_markers: dict[str, dict] = {}
        self._spawn_origin: dict[str, tuple[str | None, dict]] = {}
        self._awaiting_insertion: dict[str, tuple] = {}
        # Opt-in (presentation): commit a plan while a unit is inside a
        # junction and install its route as soon as it reaches a road edge.
        self.defer_junction_reroutes = False
        self._deferred_reroutes: set[str] = set()
        self._holds: dict[str, float] = {}
        self._added_at: dict[str, float] = {}

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

        binary = env.sumo_binary
        if self.gui:
            binary = binary.with_stem("sumo-gui")
            if not binary.is_file():
                raise RuntimeError(f"SUMO GUI is missing at {binary}. Install the SUMO desktop tools.")
        args = [
            str(binary), "-c", self.sumocfg_path,
            "--no-step-log", "--duration-log.disable", "--seed", str(self.seed),
        ]
        if self.gui:
            args += ["--start", "--quit-on-end"]
        if resume and self.has_saved_state():
            args += ["--load-state", self.state_path]
        if not self.allow_teleport:
            args += ["--time-to-teleport", "-1"]
        if self.observations_dir is not None:
            output = self.observations_dir.resolve()
            args += [
                "--tripinfo-output", str(output / "tripinfo.xml"),
                "--tripinfo-output.write-unfinished", "true",
                "--vehroute-output", str(output / "routes.xml"),
                "--vehroute-output.exit-times", "true",
                "--vehroute-output.write-unfinished", "true",
                "--error-log", str(output / "warnings.log"),
            ]
        traci.start(args)
        self._connected = True
        self._gui_markers.clear()
        self._ensure_travel().set_closed_edges(world.closed_edges)
        if resume:
            self._rebuild_queues_from_world(world)
            self._set_road_permissions(world.closed_edges)

    def save_and_close(self) -> None:
        if not self._connected:
            return
        import traci  # noqa: PLC0415

        Path(self.state_path).parent.mkdir(parents=True, exist_ok=True)
        traci.simulation.saveState(self.state_path)
        traci.close()
        self._connected = False

    # -- presentation-only GUI drawing (never changes simulated behavior) ------

    def gui_marker(
        self, poi_id: str, xy: tuple[float, float] | None, *, label: str | None = None,
        color: tuple[int, ...] | None = None, size: float | None = None, layer: float = 5.0,
        image: str = "",
    ) -> None:
        """Create or update a SUMO-GUI point of interest. `xy=None` hides it."""
        import traci

        x, y = xy if xy is not None else (-10_000.0, -10_000.0)
        if poi_id not in self._gui_markers:
            traci.poi.add(poi_id, x, y, color or (255, 255, 255, 255), layer=layer, imgFile=image,
                          width=size or 1.0, height=size or 1.0)
            self._gui_markers[poi_id] = {}
        state = self._gui_markers[poi_id]
        if state.get("xy") != (x, y):
            traci.poi.setPosition(poi_id, x, y)
            state["xy"] = (x, y)
        if color is not None and state.get("color") != color:
            traci.poi.setColor(poi_id, color)
            state["color"] = color
        if size is not None and state.get("size") != size:
            traci.poi.setWidth(poi_id, size)
            traci.poi.setHeight(poi_id, size)
            state["size"] = size
        if label is not None and state.get("label") != label:
            traci.poi.setParameter(poi_id, "label", label)
            state["label"] = label

    def gui_vehicle_view(self, colors: dict[str, tuple[int, int, int, int]]) -> dict[str, tuple[float, float]]:
        """Recolor live fleet vehicles and return their actual SUMO positions."""
        import traci

        live = set(traci.vehicle.getIDList())
        positions = {}
        for vehicle_id, color in colors.items():
            if vehicle_id in live:
                traci.vehicle.setColor(vehicle_id, color)
                positions[vehicle_id] = traci.vehicle.getPosition(vehicle_id)
        return positions

    def gui_signal_position(self, tls_id: str) -> tuple[float, float]:
        """Position of a signal's first controlled stop line."""
        import traci

        lane = traci.trafficlight.getControlledLanes(tls_id)[0]
        return tuple(traci.lane.getShape(lane)[-1][:2])

    def gui_frame(self, bounds: tuple[float, float, float, float]) -> None:
        import traci

        traci.gui.setBoundary("View #0", *bounds)

    def gui_screenshot(self, path: Path) -> None:
        import traci

        traci.gui.screenshot("View #0", str(path))

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
        self._civilian_loss_s.clear()
        self._spawn_origin.clear()
        self._awaiting_insertion.clear()
        self._deferred_reroutes.clear()
        self._holds.clear()
        self._added_at.clear()

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
            departure_index = len(edges)
            at_origin = not edges
            if edges and edges[-1] == departure_edge:
                departure_index -= 1
                edges.extend(segment[1:])
            else:
                edges.extend(segment)
            for pend in pending:
                stop_plan.append({
                    "edge_id": departure_edge, "stop_id": pend.stop_id, "task_id": pend.task_id,
                    "stop_kind": pend.kind.value, "duration": self._stop_duration(world, pend),
                    "route_index": departure_index,
                    "at_origin": at_origin,
                })
            pending = []
            stop_plan.append({
                "edge_id": edges[-1], "stop_id": stop.stop_id, "task_id": stop.task_id,
                "stop_kind": stop.kind.value, "duration": self._stop_duration(world, stop),
                "route_index": len(edges) - 1,
            })
            current_node = stop.node_id

        if pending:
            fallback_edge = edges[-1] if edges else travel.any_edge_at(current_node)
            for pend in pending:
                stop_plan.append({
                    "edge_id": fallback_edge, "stop_id": pend.stop_id, "task_id": pend.task_id,
                    "stop_kind": pend.kind.value, "duration": self._stop_duration(world, pend),
                    "route_index": max(0, len(edges) - 1),
                    "at_origin": not edges,
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
        # A re-added vehicle can wait in SUMO's insertion queue while traffic
        # blocks its position. Replace that queued insertion from the same
        # origin instead of adding a second vehicle with the same ID.
        queued = not live and (
            vehicle.id in traci.simulation.getPendingVehicles()
            or self._added_at.get(vehicle.id) == traci.simulation.getTime()
        )
        start_edge, depart_kwargs = self._spawn_origin.get(vehicle.id, (None, {})) if queued else (None, {})
        depart_kwargs = dict(depart_kwargs)
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
        edges, stop_plan = self._build_route(world, vehicle, stops, start_node=vehicle.node_id, start_edge=start_edge)
        route_id = f"route_{vehicle.id}_{uuid.uuid4().hex[:8]}"
        traci.route.add(route_id, edges)
        placements = self._stop_placements(edges, stop_plan)
        type_id = self._vtype_for_stops(world, stops)
        resumed = "departSpeed" in depart_kwargs
        if resumed:
            # A unit rerouted while standing at (or just past) a stop on its
            # current edge: SUMO only accepts stops downstream of the
            # re-insertion point, so keep those stops just ahead of it.
            ahead = float(depart_kwargs["departPos"])
            adjusted = []
            for order, (lane, position, entry) in enumerate(placements):
                if entry["route_index"] == 0 and position <= ahead + 0.1 * order:
                    position = min(lane.getLength(), ahead + 0.1 * (order + 1))
                adjusted.append((lane, position, entry))
            placements = adjusted
        numeric = resumed and depart_kwargs["departSpeed"] != "max"
        if numeric and float(depart_kwargs["departSpeed"]) > traci.vehicletype.getMaxSpeed(type_id):
            # A unit that loses its critical leg leaves blue-light mode and
            # re-enters at the routine type's maximum speed.
            depart_kwargs["departSpeed"] = f"{traci.vehicletype.getMaxSpeed(type_id):.2f}"
        if numeric and placements and stop_plan[0]["route_index"] == 0:
            # Re-inserting a moving vehicle just before a stop on its current
            # edge: SUMO rejects the insertion ("too close to brake") and the
            # vehicle silently never departs. Cap the re-insertion speed so
            # SUMO's own insertion brake gap (v*tau + v^2/2d) fits, and record it.
            gap = placements[0][1] - float(depart_kwargs["departPos"])
            speed = float(depart_kwargs["departSpeed"])
            decel = traci.vehicletype.getDecel(type_id)
            tau = traci.vehicletype.getTau(type_id)
            usable = max(0.0, gap - 2.0)
            limit = decel * (math.sqrt(tau * tau + 2 * usable / decel) - tau) if gap >= 0 else speed
            if speed > limit:
                depart_kwargs["departSpeed"] = f"{limit:.2f}"
                self.event_log.append({
                    "sim_time_s": world.sim_time_s, "kind": "reroute_speed_capped", "vehicle_id": vehicle.id,
                    "edge_id": start_edge, "gap_m": round(gap, 2), "from_mps": round(speed, 2),
                    "to_mps": round(limit, 2),
                })
        if live or queued:
            traci.vehicle.remove(vehicle.id)
        self._insert_vehicle(vehicle.id, route_id, type_id, depart_kwargs, placements)
        self._added_at[vehicle.id] = traci.simulation.getTime()
        self._spawn_origin[vehicle.id] = (start_edge, dict(depart_kwargs))
        if numeric:
            self._awaiting_insertion[vehicle.id] = (route_id, type_id, dict(depart_kwargs), placements)

        self._stop_queue[vehicle.id] = [
            {"stop_id": e["stop_id"], "task_id": e["task_id"], "stop_kind": e["stop_kind"]} for e in stop_plan
        ]
        self._next_stop_ptr[vehicle.id] = 0
        self._was_stopped[vehicle.id] = False

    def _insert_vehicle(self, vehicle_id: str, route_id: str, type_id: str, depart_kwargs: dict,
                        placements: list[tuple]) -> None:
        import traci  # noqa: PLC0415

        traci.vehicle.add(
            vehicle_id, route_id, typeID=type_id,
            depart=str(traci.simulation.getTime()), departLane="best", **depart_kwargs
        )
        for stop_index, (lane, position, entry) in enumerate(placements):
            traci.vehicle.insertStop(
                vehicle_id, stop_index, entry["edge_id"], pos=position,
                laneIndex=lane.getIndex(), duration=entry["duration"], flags=0,
            )

    def _retry_blocked_insertions(self, world: WorldState, traci) -> None:
        """A resumed vehicle whose exact speed SUMO cannot insert (slow
        leader, red signal, nearby stop) would stay queued and invisible.
        Re-insert it once at SUMO's maximum safe speed and record that."""
        if not self._awaiting_insertion:
            return
        pending = set(traci.simulation.getPendingVehicles())
        for vehicle_id, (route_id, type_id, kwargs, placements) in list(self._awaiting_insertion.items()):
            del self._awaiting_insertion[vehicle_id]
            if vehicle_id not in pending:
                continue
            traci.vehicle.remove(vehicle_id)
            relaxed = {**kwargs, "departSpeed": "max"}
            self._insert_vehicle(vehicle_id, route_id, type_id, relaxed, placements)
            self._spawn_origin[vehicle_id] = (self._spawn_origin.get(vehicle_id, (None, {}))[0], relaxed)
            self.event_log.append({
                "sim_time_s": world.sim_time_s, "kind": "reinsertion_speed_relaxed", "vehicle_id": vehicle_id,
                "requested_mps": kwargs["departSpeed"],
            })

    def _stop_placements(self, edges: list[str], stop_plan: list[dict]) -> list[tuple]:
        """(lane, position, entry) for every planned stop, in route order."""
        net = self._ensure_travel().net
        occurrences = Counter(entry["edge_id"] for entry in stop_plan)
        placed: Counter = Counter()
        placements = []
        for entry in stop_plan:
            edge = net.getEdge(entry["edge_id"])
            index = entry["route_index"] + 1
            connections = edge.getOutgoing().get(net.getEdge(edges[index]), []) if index < len(edges) else []
            lane = connections[0].getFromLane() if connections else edge.getLane(0)
            position = min(1.0, lane.getLength() * 0.9)
            # SUMO treats an identical later setStop as an edit to the
            # existing stop, even if the route revisits this edge.
            edge_id = entry["edge_id"]
            count = occurrences[edge_id]
            spacing = min(0.5, lane.getLength() / (2 * count))
            if self.junction_stops and not entry.get("at_origin"):
                position = max(position, lane.getLength() - 1.0 - spacing * (count - placed[edge_id] - 1))
            else:
                position = min(lane.getLength() * 0.99, position + spacing * placed[edge_id])
            placed[edge_id] += 1
            placements.append((lane, position, entry))
        return placements

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

    def load_scenario(self, world: WorldState, *, keep_connected: bool = False) -> None:
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
        if not keep_connected:
            self.save_and_close()

    def apply_committed_plan(self, world: WorldState, vehicle_ids: list[str]) -> None:
        """Called after `ApprovalService.approve` commits new assignments:
        re-establishes SUMO routing for exactly the vehicles the approved
        proposal touched. A broken vehicle is skipped (it is frozen, not
        rerouted) -- see `freeze_vehicle_and_locate`."""
        standalone = not self._connected
        if standalone:
            self.connect(resume=True, world=world)
        self._ensure_travel().set_closed_edges(world.closed_edges)
        any_established = False
        for vehicle_id in vehicle_ids:
            vehicle = world.vehicles[vehicle_id]
            if vehicle.mode != VehicleMode.ambulance or vehicle.status == VehicleStatus.broken:
                continue
            served = sum(1 for s in vehicle.route_stops if s.service_completed_at_s is not None)
            remaining = vehicle.route_stops[served:]
            if remaining and self.defer_junction_reroutes and (vehicle.sumo_edge_id or "").startswith(":"):
                # Committed now; installed in SUMO once the unit leaves the junction.
                self._deferred_reroutes.add(vehicle_id)
                self.event_log.append({"sim_time_s": world.sim_time_s, "kind": "reroute_deferred_in_junction",
                                       "vehicle_id": vehicle_id, "edge_id": vehicle.sumo_edge_id})
            elif remaining:
                self._deferred_reroutes.discard(vehicle_id)
                self._establish_route(world, vehicle, remaining)
                any_established = True
            else:
                self._remove_vehicle(vehicle_id)
        if any_established and standalone:
            # See `load_scenario` -- a (re)spawned vehicle needs one real
            # step before saveState reliably captures it.
            self.step(world, n=1)
        if standalone:
            self.save_and_close()

    def edge_travel_times(self) -> dict[str, float]:
        """Current mean travel time of every road edge, as SUMO observes it."""
        import traci  # noqa: PLC0415

        return {e: traci.edge.getTraveltime(e) for e in traci.edge.getIDList() if not e.startswith(":")}

    def use_live_traffic(self, *providers) -> None:
        times = self.edge_travel_times()
        for provider in (self._ensure_travel(), *providers):
            provider.set_live_travel_times(times)

    def hold_vehicle(self, world: WorldState, vehicle_id: str, seconds: float) -> None:
        """Physically delay a unit: it stands still until the hold expires."""
        self._holds[vehicle_id] = max(self._holds.get(vehicle_id, 0.0), world.sim_time_s + seconds)
        self.event_log.append({"sim_time_s": world.sim_time_s, "kind": "vehicle_held",
                               "vehicle_id": vehicle_id, "seconds": seconds})

    def _apply_holds(self, world: WorldState, traci) -> None:
        live = set(traci.vehicle.getIDList()) if self._holds else set()
        for vehicle_id, until in list(self._holds.items()):
            release = world.sim_time_s >= until
            if release:
                del self._holds[vehicle_id]
            broken = vehicle_id in world.vehicles and world.vehicles[vehicle_id].status == VehicleStatus.broken
            if vehicle_id in live and not broken:
                traci.vehicle.setSpeed(vehicle_id, -1 if release else 0)

    def _edge_after_junction(self, vehicle_id: str) -> str:
        import traci  # noqa: PLC0415

        route = traci.vehicle.getRoute(vehicle_id)
        return route[min(traci.vehicle.getRouteIndex(vehicle_id) + 1, len(route) - 1)]

    def _apply_deferred_reroutes(self, world: WorldState, traci) -> None:
        if not self._deferred_reroutes:
            return
        live = set(traci.vehicle.getIDList())
        for vehicle_id in sorted(self._deferred_reroutes):
            if vehicle_id not in live:
                self._deferred_reroutes.discard(vehicle_id)
                continue
            if traci.vehicle.getRoadID(vehicle_id).startswith(":"):
                continue
            self._deferred_reroutes.discard(vehicle_id)
            vehicle = world.vehicles[vehicle_id]
            remaining = [s for s in vehicle.route_stops if s.service_completed_at_s is None]
            if remaining and vehicle.status != VehicleStatus.broken:
                self._establish_route(world, vehicle, remaining)

    def _remove_vehicle(self, vehicle_id: str) -> None:
        import traci

        if vehicle_id in traci.vehicle.getIDList() or vehicle_id in traci.simulation.getPendingVehicles():
            traci.vehicle.remove(vehicle_id)
        self._spawn_origin.pop(vehicle_id, None)
        self._awaiting_insertion.pop(vehicle_id, None)
        self._stop_queue.pop(vehicle_id, None)
        self._next_stop_ptr.pop(vehicle_id, None)
        self._was_stopped.pop(vehicle_id, None)

    def preview_routes(self, world: WorldState, overrides: dict) -> dict[str, list[str]]:
        """Use the same route stitching as execution, without changing SUMO."""
        self._ensure_travel().set_closed_edges(world.closed_edges)
        routes = {}
        for vehicle_id, stops in overrides.items():
            vehicle = world.vehicles[vehicle_id]
            if vehicle.mode != VehicleMode.ambulance or vehicle.status == VehicleStatus.broken:
                continue
            remaining = [stop for stop in stops if stop.service_completed_at_s is None]
            if not remaining:
                routes[vehicle_id] = []
                continue
            start_edge = vehicle.sumo_edge_id
            if start_edge and start_edge.startswith(":"):
                if not self.defer_junction_reroutes:
                    raise ValueError(f"{vehicle_id} is in a junction. Advance one step and retry.")
                start_edge = self._edge_after_junction(vehicle_id)
            routes[vehicle_id], _ = self._build_route(world, vehicle, remaining, vehicle.node_id, start_edge)
        return routes

    def checkpoint(self) -> None:
        import traci

        Path(self.state_path).parent.mkdir(parents=True, exist_ok=True)
        traci.simulation.saveState(self.state_path)

    def restore_checkpoint(self, world: WorldState) -> None:
        self.close()
        self._stop_queue.clear()
        self._next_stop_ptr.clear()
        self._was_stopped.clear()
        self.connect(resume=True, world=world)

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
        standalone = not self._connected
        if standalone:
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
        if standalone:
            self.save_and_close()
        return node_id

    def apply_road_closure_physically(self, world: WorldState, edge_id: str) -> None:
        """Disallows every lane of `edge_id` for all vehicle classes going
        forward (`traci.lane.setDisallowed`) -- vehicles already on the
        edge are not ejected or teleported; this only blocks new routing
        onto it, matching the "ahead of vehicles" closure semantics
        documented in docs/DECISIONS.md."""
        standalone = not self._connected
        if standalone:
            self.connect(resume=True, world=world)
        self._set_road_permissions({edge_id: None})
        self.event_log.append({"sim_time_s": world.sim_time_s, "kind": "edge_closed_physically", "edge_id": edge_id})
        if standalone:
            self.save_and_close()

    def _set_road_permissions(self, edges, *, reopen: bool = False) -> None:
        import traci  # noqa: PLC0415

        net = self._ensure_travel().net
        for edge_id in edges:
            for lane in net.getEdge(edge_id).getLanes():
                if reopen:
                    traci.lane.setAllowed(lane.getID(), sorted(lane.getPermissions()))
                else:
                    traci.lane.setDisallowed(lane.getID(), ["all"])

    def _edge_to_node(self, edge_id: str) -> str:
        if edge_id.startswith(":"):
            return edge_id.split("_")[0].lstrip(":")
        return self._ensure_travel().net.getEdge(edge_id).getToNode().getID()

    # -- stepping --------------------------------------------------------------

    def step(self, world: WorldState, n: int = 1, *, capture_positions: bool = True) -> SimulationSnapshot:
        import traci  # noqa: PLC0415

        self._enforce_frozen(world, traci)

        for _ in range(n):
            self._hold_hospital_dropoffs(world, traci)
            self._apply_holds(world, traci)
            traci.simulationStep()
            world.sim_time_s = traci.simulation.getTime()
            world.bump_telemetry()
            if self.observations_dir is not None:
                for ids, kind in (
                    (traci.simulation.getStartingTeleportIDList(), "teleport_started"),
                    (traci.simulation.getCollidingVehiclesIDList(), "collision"),
                ):
                    for vehicle_id in ids:
                        self.event_log.append({"kind": kind, "vehicle_id": vehicle_id, "sim_time_s": world.sim_time_s})
            self._process_arrivals_and_stops(world, traci)
            # After the arrival check: replacing a unit must not look like an arrival.
            self._retry_blocked_insertions(world, traci)
            self._apply_deferred_reroutes(world, traci)
            self._update_positions(world, traci)
            self._run_green_wave(world, traci)
            for restored in tick_scheduled_restorations(world):
                if restored.kind == "edge_reopen":
                    self._set_road_permissions([restored.target_id], reopen=True)
                self.event_log.append({
                    "sim_time_s": world.sim_time_s, "kind": "scheduled_restoration_applied",
                    "restoration_kind": restored.kind, "target_id": restored.target_id,
                })
            self._enforce_frozen(world, traci)

        positions = {
            vid: {"edge": traci.vehicle.getRoadID(vid), "pos": traci.vehicle.getLanePosition(vid)}
            for vid in traci.vehicle.getIDList()
        } if capture_positions else {}
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
                traci.vehicle.setStop(
                    vehicle_id, edge, pos=pos, laneIndex=traci.vehicle.getLaneIndex(vehicle_id),
                    duration=1_000_000.0, flags=1,
                )
            except Exception:  # noqa: BLE001 -- best-effort; setSpeed(0) above still applies
                pass

    def _process_arrivals_and_stops(self, world: WorldState, traci) -> None:
        arrived = set(traci.simulation.getArrivedIDList())
        live = set(traci.vehicle.getIDList())
        for vehicle_id, queue in self._stop_queue.items():
            if world.vehicles[vehicle_id].status == VehicleStatus.broken:
                continue
            ptr = self._next_stop_ptr.get(vehicle_id, 0)
            if ptr >= len(queue):
                continue
            was = self._was_stopped.get(vehicle_id, False)
            if vehicle_id in arrived:
                if was:
                    self._complete_stop(world, vehicle_id, queue[ptr])
                    ptr += 1
                self._next_stop_ptr[vehicle_id] = ptr
                if ptr != len(queue):
                    self.event_log.append({
                        "kind": "missing_sumo_stop", "vehicle_id": vehicle_id,
                        "sim_time_s": world.sim_time_s, "remaining": queue[ptr:],
                    })
                self._was_stopped[vehicle_id] = False
                continue
            if vehicle_id not in live:
                continue
            stopped_now = traci.vehicle.isStopped(vehicle_id)
            if was and len(traci.vehicle.getStops(vehicle_id)) < len(queue) - ptr:
                self._complete_stop(world, vehicle_id, queue[ptr])
                ptr += 1
                self._next_stop_ptr[vehicle_id] = ptr
                was = False
            if stopped_now and not was and ptr < len(queue):
                stop = next((s for s in world.vehicles[vehicle_id].route_stops if s.stop_id == queue[ptr]["stop_id"]), None)
                if stop is not None:
                    stop.service_start_s = world.sim_time_s
                self.event_log.append({
                    "sim_time_s": world.sim_time_s, "kind": "stop_begin", "vehicle_id": vehicle_id, **queue[ptr],
                    "node_id": stop.node_id if stop is not None else None,
                    "edge_id": traci.vehicle.getRoadID(vehicle_id),
                    "xy": traci.vehicle.getPosition(vehicle_id),
                })
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
            vehicle.status = VehicleStatus.carrying
            if stop_obj is not None:
                vehicle.node_id = stop_obj.node_id
            # Carrying a patient or equipment is a fixed commitment through
            # its drop-off. Keep that prefix even as new tasks are inserted.
            for index, stop in enumerate(vehicle.route_stops):
                if stop.task_id == task_id and stop.kind == StopKind.dropoff:
                    vehicle.locked_stop_index = max(vehicle.locked_stop_index or 0, index)
                    break
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
            vehicle.status = (
                VehicleStatus.assigned if any(s.service_completed_at_s is None for s in vehicle.route_stops)
                else VehicleStatus.available
            )
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
            else:
                vehicle.sumo_edge_id = None
                vehicle.sumo_lane_position = None

    def live_telemetry(self, world: WorldState) -> dict:
        """Dashboard telemetry read from the current TraCI frame."""
        import traci

        vehicle_ids = traci.vehicle.getIDList()
        fleet = {}
        civilian_count = 0
        for vehicle_id in vehicle_ids:
            if vehicle_id in world.vehicles:
                route_index = traci.vehicle.getRouteIndex(vehicle_id)
                fleet[vehicle_id] = {
                    "xy": traci.vehicle.getPosition(vehicle_id),
                    "edge": traci.vehicle.getRoadID(vehicle_id),
                    "speed_mps": traci.vehicle.getSpeed(vehicle_id),
                    "route": traci.vehicle.getRoute(vehicle_id)[max(0, route_index):],
                }
            elif traci.vehicle.getTypeID(vehicle_id) == "civilian":
                civilian_count += 1
                self._civilian_loss_s[vehicle_id] = traci.vehicle.getTimeLoss(vehicle_id)
        return {
            "fleet": fleet,
            "signals": {
                tls_id: {
                    "phase": traci.trafficlight.getPhase(tls_id),
                    "state": traci.trafficlight.getRedYellowGreenState(tls_id),
                }
                for tls_id in traci.trafficlight.getIDList()
            },
            "civilian_active": civilian_count,
            "civilian_observed": len(self._civilian_loss_s),
            "civilian_time_loss_s": round(sum(self._civilian_loss_s.values()), 1),
        }

    def _run_green_wave(self, world: WorldState, traci) -> None:
        self._ensure_travel().set_closed_edges(world.closed_edges)
        live = set(traci.vehicle.getIDList())
        eligible: dict[str, int] = dict.fromkeys(sorted(self.priority_probe_ids & live), 0)
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
