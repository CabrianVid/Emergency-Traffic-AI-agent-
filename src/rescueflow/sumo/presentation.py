"""City-wide Ljubljana SUMO scene: baseline dispatch vs. RescueFlow upgrades.

Both runs start from the same frozen fleet, routine transfers, civilian
demand and emergency calls on a 5 x 4 km OpenStreetMap road network. Every
call is planned by the project's real planners and committed through the
real approval service (`rescueflow.benchmark.runner.LogisticsRun`):

* ``baseline`` (B0): nearest immediately free ambulance, normal signals.
* ``rescueflow`` (A4): the dashboard's decision engine -- feasible optimizer
  plans ranked by the AI, decided again when a unit frees up -- plus green-wave
  signal priority for critical legs. Recorded AI answers are reused.
* ``compare``: both windows side by side, kept on the same simulated second.

The optimized window compares itself with ``baseline-reference.json``, the
result of the baseline run on exactly the same inputs (``--save-reference``).

The GUI markers only *display* the run; they never change what SUMO does.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from statistics import mean

from rescueflow.approval.service import DefaultApprovalService
from rescueflow.benchmark.inputs import frozen_departures
from rescueflow.benchmark.runner import LogisticsRun, _trip_rows
from rescueflow.decision.agent import DecisionAgent
from rescueflow.decision.alternatives import generate_alternatives
from rescueflow.dispatch.notification import MockDispatchNotificationAdapter
from rescueflow.models.core import (
    ReceivingSite,
    Task,
    TaskKind,
    TaskPriority,
    TaskStatus,
    Vehicle,
    VehicleMode,
    VehicleStatus,
    WorldState,
)
from rescueflow.planning.queue_dispatch import FreeUnitTrigger
from rescueflow.scenario.loader import DefaultScenarioLoader, _two_stop_route
from rescueflow.sumo.controller import TraciSimulationController
from rescueflow.sumo.env import ensure_sumolib_importable
from rescueflow.sumo.network_gen import write_sumocfg
from rescueflow.travel import SumoTravelTimeProvider
from rescueflow.travel.city_travel_time import CityTravelTimeProvider

SEED = 42
HORIZON_S = 1800
DEMAND_UNTIL_S = 1500
APPROVAL_S = 15
MOBILIZATION_S = 10
RESPONSE_TARGET_S = 300  # same target and pickup window as the benchmark
NETCONVERT_ARGS = ("--tls.join", "--junctions.join", "--roundabouts.guess", "--ramps.guess",
                   "--tls.default-type", "actuated")
GUESS_SIGNALS = True
# Extra SUMO processing options for the scene (see docs/RUNBOOK.md).
# Without these, SUMO teleports vehicles (including ambulances) after
# collisions inside imported OSM junction clusters.
SUMO_PROCESSING: dict[str, str] = {
    "collision.action": "warn",
    "collision.check-junctions": "false",
    "ignore-junction-blocker": "30",
}

POLICIES = {
    "B0": "BASELINE  ·  nearest free unit  ·  normal traffic lights",
    "A1": "Optimized dispatch only  ·  normal traffic lights",
    "A2": "Nearest free unit  ·  green-wave signal priority",
    "A3": "Older upgrade  ·  fleet-wide urgent-first dispatch  +  green-wave signal priority",
    "A4": "RESCUEFLOW  ·  AI picks among optimizer plans  +  green-wave signal priority",
}
ALIASES = {"baseline": "B0", "rescueflow": "A4", "upgraded": "A4"}

# Named places (lat, lon). Each resolves to the nearest well-connected SUMO
# junction, so labels sit on real roads of the generated network.
PLACES = {
    "UKC": ("UKC Ljubljana", 46.0551, 14.5231),
    "ST_CENTER": ("Station Center", 46.0536, 14.5199),
    "ST_BEZIGRAD": ("Station Bežigrad", 46.0662, 14.5128),
    "ST_VIC": ("Station Vič", 46.0452, 14.4968),
    "ZD_MOSTE": ("ZD Moste", 46.0532, 14.5368),
    "ZD_BEZIGRAD": ("ZD Bežigrad", 46.0655, 14.5086),
    "SISKA": ("Šiška", 46.0628, 14.4975),
    "TRNOVO": ("Trnovo", 46.0428, 14.5042),
    "KODELJEVO": ("Kodeljevo", 46.0478, 14.5282),
    "TABOR": ("Tabor", 46.0572, 14.5168),
    "RAKOVNIK": ("Rakovnik", 46.0416, 14.5196),
    "BTC": ("BTC", 46.0640, 14.5385),
    "TIVOLI": ("Tivoli", 46.0560, 14.4985),
    "CENTER": ("Center", 46.0522, 14.5040),
    "POLJANE": ("Poljane", 46.0497, 14.5175),
    "ROZNA": ("Rožna dolina", 46.0470, 14.4935),
    "STEPANJA": ("Štepanja vas", 46.0470, 14.5400),
    "NOVE_JARSE": ("Nove Jarše", 46.0655, 14.5280),
    # Civilian demand gateways on arterials near the map edge.
    "G_DUNAJSKA": ("Dunajska", 46.0672, 14.5112),
    "G_CELOVSKA": ("Celovška", 46.0628, 14.4925),
    "G_TRZASKA": ("Tržaška", 46.0438, 14.4925),
    "G_ZALOSKA": ("Zaloška", 46.0560, 14.5425),
    "G_SMARTINSKA": ("Šmartinska", 46.0625, 14.5420),
    "G_DOLENJSKA": ("Dolenjska", 46.0420, 14.5150),
    "G_MASARYKOVA": ("Masarykova", 46.0583, 14.5105),
    "G_SLOVENSKA": ("Slovenska", 46.0530, 14.5030),
}
STATIONS = ("ST_CENTER", "ST_BEZIGRAD", "ST_VIC")


@dataclass(frozen=True)
class Call:
    id: str
    at_s: int
    place: str
    what: str


CALLS = (
    Call("CALL-1", 60, "SISKA", "cardiac arrest"),
    Call("CALL-2", 150, "TRNOVO", "traffic collision"),
    Call("CALL-3", 240, "STEPANJA", "stroke"),
    Call("CALL-4", 330, "NOVE_JARSE", "fall from height"),
    Call("CALL-5", 420, "TIVOLI", "breathing difficulty"),
    Call("CALL-6", 510, "RAKOVNIK", "cardiac arrest"),
    Call("CALL-7", 600, "BTC", "collapsed shopper"),
    Call("CALL-8", 690, "CENTER", "pedestrian struck"),
)

# Units on routine transfers at t=0 (no siren), then idle units at stations.
ROUTINE = (
    ("R4", "ZD_BEZIGRAD", "UKC", "TR-1", 1500),
    ("R5", "ROZNA", "UKC", "TR-2", 1600),
    ("R6", "UKC", "ZD_MOSTE", "TR-3", 1500),
    ("R7", "KODELJEVO", "ZD_BEZIGRAD", "TR-4", 1700),
    ("R8", "TABOR", "TRNOVO", "TR-5", 1600),
    ("R9", "UKC", "SISKA", "TR-6", 1700),
    ("R10", "POLJANE", "BTC", "TR-7", 1800),
)
IDLE = (("R1", "ST_CENTER"), ("R2", "ST_BEZIGRAD"), ("R3", "ST_VIC"))
CIVILIAN_FLOWS = (
    ("G_DUNAJSKA", "G_TRZASKA", 38), ("G_TRZASKA", "G_DUNAJSKA", 39),
    ("G_CELOVSKA", "G_ZALOSKA", 35), ("G_ZALOSKA", "G_CELOVSKA", 36),
    ("G_SMARTINSKA", "G_SLOVENSKA", 41), ("G_SLOVENSKA", "G_SMARTINSKA", 42),
    ("G_DOLENJSKA", "G_MASARYKOVA", 41), ("G_MASARYKOVA", "G_DOLENJSKA", 42),
    ("G_CELOVSKA", "G_DOLENJSKA", 49), ("G_DOLENJSKA", "G_CELOVSKA", 51),
    ("G_DUNAJSKA", "G_ZALOSKA", 48), ("G_ZALOSKA", "G_TRZASKA", 52),
)

# Marker colors (RGBA). Vehicles take the same color as their halo.
RED = (235, 40, 40, 255)
ORANGE = (255, 150, 20, 255)
BLUE = (70, 160, 255, 255)
GREY = (170, 175, 180, 255)
GREEN = (60, 210, 110, 255)
CIVILIAN = (235, 220, 170, 255)
BRAND = (255, 143, 0, 255)
PANEL = (12, 14, 16, 255)
REFERENCE = "baseline-reference.json"
HOLD_RESULT_S = 30


def input_digest(assets: Path) -> str:
    """What both runs must share: network, calls, fleet, demand and timing."""
    content = (assets / "ljubljana-wide.net.xml.gz").read_bytes() + repr(
        (SEED, HORIZON_S, APPROVAL_S, MOBILIZATION_S, PLACES, CALLS, ROUTINE, IDLE, CIVILIAN_FLOWS)).encode()
    return hashlib.sha256(content).hexdigest()


def load_reference(assets: Path) -> dict | None:
    path = assets / REFERENCE
    if not path.exists():
        return None
    reference = json.loads(path.read_text(encoding="utf-8"))
    return reference if reference["inputs"] == input_digest(assets) else None


def _write_xml(root: ET.Element, path: Path) -> None:
    ET.indent(root, space="    ")
    ET.ElementTree(root).write(path, encoding="UTF-8", xml_declaration=True)


def resolve_places(travel: SumoTravelTimeProvider) -> dict[str, str]:
    """Nearest junction to each named place with real two-way road access."""
    net = travel.net

    def usable(node) -> bool:
        ins = [e for e in node.getIncoming() if e.allows("passenger") and e.getLength() >= 25]
        outs = [e for e in node.getOutgoing() if e.allows("passenger") and e.getLength() >= 25]
        return bool(ins and outs) and len(node.getIncoming()) + len(node.getOutgoing()) >= 3

    nodes = [n for n in net.getNodes() if usable(n)]
    resolved: dict[str, str] = {}
    hub = None
    for key, (_label, lat, lon) in PLACES.items():
        x, y = net.convertLonLat2XY(lon, lat)
        for node in sorted(nodes, key=lambda n: (n.getCoord()[0] - x) ** 2 + (n.getCoord()[1] - y) ** 2):
            node_id = node.getID()
            if hub is None or (travel.road_route_exists(hub, node_id) and travel.road_route_exists(node_id, hub)):
                resolved[key] = node_id
                hub = hub or node_id
                break
        else:
            raise ValueError(f"No connected junction near {key}")
    return resolved


def build_world(travel: SumoTravelTimeProvider, nodes: dict[str, str]) -> WorldState:
    world = WorldState(sim_time_s=0.0)
    hospital = ReceivingSite(
        id="UKC", node_id=nodes["UKC"], service_handling_s=90.0,
        accepted_task_kinds=[TaskKind.patient_transport, TaskKind.urgent_response],
    )
    world.receiving_sites = {"UKC": hospital}

    def ambulance(unit: str, place: str, status=VehicleStatus.available) -> Vehicle:
        return Vehicle(id=unit, mode=VehicleMode.ambulance, status=status, node_id=nodes[place],
                       patient_capacity=1, equipment_capacity_units=1, capabilities=["ALS"])

    for unit, station in IDLE:
        world.vehicles[unit] = ambulance(unit, station)
    for unit, pickup, dropoff, task_id, deadline in ROUTINE:
        task = Task(
            id=task_id, kind=TaskKind.patient_transport, priority=TaskPriority.routine,
            pickup_node=nodes[pickup], dropoff_node=nodes[dropoff], created_at_s=0.0,
            latest_delivery_s=float(deadline), patient_slots=1, load_seconds=90.0, unload_seconds=60.0,
            status=TaskStatus.planned, assigned_vehicle_id=unit,
        )
        # Each routine unit starts one junction away from its pickup, already
        # rolling, so the fleet is visibly spread over the city at t=0.
        start = _neighbour_start(travel, nodes[pickup], nodes["UKC"])
        vehicle = ambulance(unit, pickup, VehicleStatus.assigned)
        vehicle.node_id = start
        vehicle.route_stops, _ = _two_stop_route(vehicle, task, travel, 0.0, world.receiving_sites.values())
        world.vehicles[unit] = vehicle
        world.tasks[task_id] = task
    feasibility = DefaultScenarioLoader().validate(world)
    if not feasibility.feasible:
        raise ValueError("Initial city-wide schedule is infeasible: " + "; ".join(i.message for i in feasibility.issues))
    return world


def _neighbour_start(travel: SumoTravelTimeProvider, node_id: str, hub: str) -> str:
    """A connected junction 400-900 m from `node_id`, so the unit visibly drives there."""
    net = travel.net
    origin = net.getNode(node_id).getCoord()
    ranked = sorted(
        net.getNodes(),
        key=lambda n: abs(((n.getCoord()[0] - origin[0]) ** 2 + (n.getCoord()[1] - origin[1]) ** 2) ** 0.5 - 650),
    )
    for node in ranked[:40]:
        candidate = node.getID()
        if any(e.getLength() >= 25 for e in node.getOutgoing()) and travel.road_route_exists(candidate, node_id) \
                and travel.road_route_exists(candidate, hub):
            return candidate
    return node_id


def write_routes(travel: SumoTravelTimeProvider, nodes: dict[str, str], path: Path) -> int:
    routes = ET.Element("routes")
    ET.SubElement(routes, "vType", id="civilian", vClass="passenger", maxSpeed="13.9", accel="2.0", decel="4.5",
                  speedFactor="1", color=",".join(map(str, CIVILIAN[:3])), guiShape="passenger")
    emergency = ET.SubElement(routes, "vType", id="ambulance", vClass="emergency", maxSpeed="20.0", accel="2.5",
                              decel="4.5", speedFactor="1", guiShape="emergency", color="235,40,40",
                              length="6.5", width="2.3")
    ET.SubElement(emergency, "param", key="has.bluelight.device", value="true")
    ET.SubElement(routes, "vType", id="ambulance_routine", vClass="passenger", maxSpeed="13.9", accel="2.0",
                  decel="4.5", speedFactor="1", guiShape="emergency", color="70,160,255", length="6.5", width="2.3")
    flows = tuple((nodes[a], nodes[b], period) for a, b, period in CIVILIAN_FLOWS)
    departures = frozen_departures(SEED, DEMAND_UNTIL_S, flows)
    for depart, identifier, start, end, speed in departures:
        vehicle = ET.SubElement(routes, "vehicle", id=identifier, type="civilian", depart=str(depart),
                                departLane="best", speedFactor=speed)
        ET.SubElement(vehicle, "route", edges=" ".join(travel.road_path_edges(start, end)))
    _write_xml(routes, path)
    return len(departures)


def prepare(root: Path, output: Path, gui: bool, delay_ms: int, policy: str = "B0") -> tuple[Path, Path, dict, WorldState, int]:
    assets = root / "simulation" / "presentation"
    # One frozen network: netconvert does not build the same junctions every time,
    # so two runs could otherwise be compared on slightly different roads.
    net = output / "ljubljana-wide.net.xml"
    net.write_bytes(gzip.decompress((assets / "ljubljana-wide.net.xml.gz").read_bytes()))
    travel = CityTravelTimeProvider(str(net))
    nodes = resolve_places(travel)
    world = build_world(travel, nodes)
    routes = output / "citywide.rou.xml"
    civilians = write_routes(travel, nodes, routes)
    cfg = output / "citywide.sumocfg"
    write_sumocfg(str(net), str(routes), str(cfg), seed=SEED, end_s=HORIZON_S)
    tree = ET.parse(cfg)
    processing = tree.getroot().find("processing")
    for option, value in SUMO_PROCESSING.items():
        ET.SubElement(processing, option, value=value)
    _write_xml(tree.getroot(), cfg)
    if gui:
        view = ET.parse(assets / "ljubljana-wide.view.xml").getroot()
        view.find("delay").set("value", str(delay_ms))
        decal = view.find("decal")
        decal.set("file", str(assets / decal.get("file")))
        _write_xml(view, output / "citywide.view.xml")
        tree = ET.parse(cfg)
        gui_only = ET.SubElement(tree.getroot(), "gui_only")
        ET.SubElement(gui_only, "gui-settings-file", value="citywide.view.xml")
        # Two windows fit next to each other on a 1920 px wide screen.
        ET.SubElement(gui_only, "window-size", value="955,1080")
        ET.SubElement(gui_only, "window-pos", value="0,0" if policy == "B0" else "960,0")
        _write_xml(tree.getroot(), cfg)
    return net, cfg, nodes, world, civilians


class CityRun(LogisticsRun):
    """`LogisticsRun` that retries a route installation blocked by a unit
    crossing a junction, one simulated second later, re-planning from the
    then-current state (the dashboard's "advance and retry" rule)."""

    MAX_JUNCTION_RETRIES = 30
    agent: DecisionAgent | None = None

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.trigger = FreeUnitTrigger()
        self.rankings: Counter = Counter()

    def _current_plan(self, freed: bool) -> None:
        """The dashboard's decision engine: feasible options, ranked by the AI."""
        world = self.world
        event = None if freed else world.events[-1]

        def route_check(current, proposal):
            self.controller.preview_routes(current, {p.vehicle_id: p.stops for p in proposal.vehicle_plans})

        options, notes = generate_alternatives(world, self.travel, event, budget_s=15, route_check=route_check,
                                               serving=self.controller.serving(world))
        if not options:
            self.failed.extend(notes[:2])
            return
        result = self.agent.rank(world, event, options)
        self.rankings[result.source] += 1
        plan = next(o for o in options if o.option_id == result.recommended).proposal
        candidate = world.model_copy(deep=True)
        committed = DefaultApprovalService(notifier=MockDispatchNotificationAdapter()).approve(
            candidate, plan, approved_by="simulated-operator", idempotency_key=f"c{len(self.events)}")
        if not committed.committed:
            self.failed.append(f"approval: {committed.rejection_reason}")
            return
        touched = sorted(p.vehicle_id for p in plan.vehicle_plans)
        self.controller.apply_committed_plan(candidate, touched)
        self.world = candidate
        self.trigger.mark(candidate)
        self.controller.green_wave.enabled = True
        self.log("plan_approved", policy=self.policy, revision=committed.new_plan_revision, vehicles=touched, freed=freed)

    def free_unit_check(self) -> None:
        delay = self.inputs["approval_delay_s"] + self.inputs["mobilization_delay_s"]
        if self.policy == "A4" and self.trigger.fires(
                self.world, lambda t: self.report_times.get(t.id, 0.0) + delay <= self.world.sim_time_s):
            self._current_plan(freed=True)

    def repair(self, affected):
        # Every policy plans with the traffic SUMO reports now.
        self.controller.use_live_traffic(self.travel)
        if self.policy == "A4":
            self._current_plan(freed=False)
            return None
        for _attempt in range(self.MAX_JUNCTION_RETRIES):
            try:
                return super().repair(affected)
            except (RuntimeError, ValueError) as exc:
                if "junction" not in str(exc):
                    raise
                if self.failed and self.failed[-1].startswith("route installation"):
                    self.failed.pop()
                self.log("approval_retried", reason=str(exc))
                self.controller.step(self.world, capture_positions=False)
                if self.on_step is not None:
                    self.on_step(self)
        raise RuntimeError("route installation kept hitting junctions")


def _clock(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


PANEL_TITLES = {
    "B0": ("BASELINE", "nearest free ambulance", "normal traffic lights"),
    "A1": ("OPTIMIZED DISPATCH ONLY", "fleet-wide urgent-first plan", "normal traffic lights"),
    "A2": ("GREEN WAVE ONLY", "nearest free ambulance", "green-wave signal priority"),
    "A3": ("OLDER UPGRADE", "fleet-wide urgent-first plan", "green-wave signal priority"),
    "A4": ("RESCUEFLOW", "AI picks among optimizer plans", "green-wave signal priority"),
}


class CityView:
    """Draws the run state into SUMO-GUI. Read-only with respect to the plan.

    The map fills the top of the window; a dark strip below it holds the legend
    on the left and the response times on the right. The optimized run frames
    every place where it differs from the recorded baseline."""

    STRIP_M = 1780.0
    LINE_M = 132.0
    # SUMO draws a label slightly above its point.
    TEXT_LIFT_M = 48.0

    def __init__(self, controller: TraciSimulationController, travel: SumoTravelTimeProvider,
                 nodes: dict[str, str], policy: str, assets: Path):
        self.controller, self.travel, self.nodes, self.policy = controller, travel, nodes, policy
        self.assets = assets
        self.reference = load_reference(assets) if policy != "B0" else None
        self.reported: dict[str, float] = {}
        self.reached: dict[str, tuple[float, str]] = {}
        self.delivered: set[str] = set()
        self.interventions = 0
        self.event_cursor = 0
        self.run_cursor = 0
        self.headline = ("Routine transfers under way.", "Waiting for the first emergency call.")
        self.signal_marker: tuple[str, float] | None = None
        self.started = False
        self.framed: set[str] = set()
        x0, y0, x1, y1 = travel.net.getBoundary()
        self.map = (x0 + 100, y0 + 20, x1 - 100, y1 - 350)
        self.frame = (self.map[0], self.map[1] - self.STRIP_M, self.map[2], self.map[3])
        self.middle = self.map[0] + (self.map[2] - self.map[0]) * 0.44

    def xy(self, node_id: str) -> tuple[float, float]:
        return self.travel.net.getNode(node_id).getCoord()

    def text(self, poi: str, x: float, row: float, text: str) -> None:
        y = self.map[1] - 175 - row * self.LINE_M
        self.controller.gui_marker(f"panel-{poi}", (x, y), label=text, color=(0, 0, 0, 0), layer=12)

    def panel(self, poi: str, row: float, text: str, dot: tuple | None = None) -> None:
        center = (self.map[0] + self.middle) / 2
        self.text(poi, center, row, text)
        if dot is not None:
            y = self.map[1] - 175 - row * self.LINE_M
            self.controller.gui_marker(f"panel-dot-{poi}", (center - 620, y + self.TEXT_LIFT_M), color=dot, size=80, layer=12)

    def static_layer(self) -> None:
        mark = self.controller.gui_marker
        self.controller.gui_frame(self.frame)
        left, _bottom, right, _top = self.frame
        edge = BRAND if self.policy != "B0" else (120, 125, 130, 255)
        self.controller.gui_box("strip", (left - 4000, self.map[1] - self.STRIP_M - 4000, right + 4000, self.map[1]),
                                PANEL, fill=True, layer=10)
        self.controller.gui_box("strip-edge", (left - 4000, self.map[1] - 24, right + 4000, self.map[1]), edge, fill=True, layer=11)
        self.box = (self.middle + 40, self.map[1] - self.STRIP_M + 60, right - 60, self.map[1] - 70)
        self.controller.gui_box("result-box", self.box, edge, line_width=22, layer=11)
        hx, hy = self.xy(self.nodes["UKC"])
        mark("hospital-zone", (hx, hy), color=(255, 255, 255, 70), size=300, layer=1)
        mark("hospital", (hx, hy), color=(255, 255, 255, 255), size=130, layer=8,
             image=str(self.assets / "hospital.png"))
        mark("hospital-label", (hx + 40, hy + 160), label="UKC LJUBLJANA  ·  HOSPITAL",
             color=(0, 0, 0, 0), layer=9)
        for key in STATIONS:
            sx, sy = self.xy(self.nodes[key])
            mark(f"station-{key}", (sx, sy), color=(255, 255, 255, 255), size=90, layer=8,
                 image=str(self.assets / "station.png"))
            mark(f"station-label-{key}", (sx, sy - 110), label=PLACES[key][0].upper(),
                 color=(0, 0, 0, 0), layer=9)
        title, dispatch, signals = PANEL_TITLES[self.policy]
        self.panel("title", 0, f"{title}")
        self.panel("dispatch", 1, f"Who goes: {dispatch}")
        self.panel("signals", 2, f"Lights: {signals}")
        legend = (
            ("legend-red", "Emergency run, blue lights", RED),
            ("legend-orange", "Patient going to UKC", ORANGE),
            ("legend-blue", "Routine transfer", BLUE),
            ("legend-grey", "Free ambulance", GREY),
        )
        for index, (poi, text, color) in enumerate(legend):
            self.panel(poi, 8.35 + index * 0.92, text, dot=color)
        columns = ("Call", "Response", "Unit") if self.policy == "B0" else ("Call", "Baseline", "RescueFlow", "Difference")
        self.columns = [self.box[0] + (self.box[2] - self.box[0]) * share for share in
                        ((0.2, 0.55, 0.82) if self.policy == "B0" else (0.16, 0.4, 0.62, 0.85))]
        heading = "RESPONSE TIMES" if self.policy == "B0" else "THE DIFFERENCE, LIVE"
        self.text("box-title", (self.box[0] + self.box[2]) / 2, 0.1, heading)
        for x, label in zip(self.columns, columns):
            self.text(f"box-{label}", x, 1.25, label)

    def __call__(self, run: LogisticsRun) -> None:
        if not self.started:
            self.static_layer()
            self.started = True
        world = run.world
        now = world.sim_time_s
        self._read_events(run, now)
        self._draw_calls(world, now)
        self._draw_fleet(world)
        self.transfers = sum(t.status == TaskStatus.completed for t in world.tasks.values() if t.priority == TaskPriority.routine)
        self._draw_status(now)
        self._draw_results(now)

    def _read_events(self, run: LogisticsRun, now: float) -> None:
        for event in run.events[self.run_cursor:]:
            if event["kind"] == "incident_validated":
                payload = event["incident"]["payload"]
                call = next(c for c in CALLS if c.id == payload["task_id"])
                self.reported[call.id] = event["sim_time_s"]
                self.headline = (f"{_clock(now)}  NEW {call.id}: {call.what}",
                                 f"in {PLACES[call.place][0]}. Dispatcher is planning...")
            elif event["kind"] == "plan_approved":
                assigned = [
                    f"{task.id} to {task.assigned_vehicle_id}" for task in run.world.tasks.values()
                    if task.id.startswith("CALL-") and task.status != TaskStatus.completed
                    and task.assigned_vehicle_id and task.id not in self.reached
                ]
                if assigned:
                    self.headline = (f"{_clock(now)}  Plan approved:", ", ".join(assigned[-3:]))
        self.run_cursor = len(run.events)
        for event in self.controller.event_log[self.event_cursor:]:
            kind, task_id = event["kind"], event.get("task_id")
            if kind == "stop_begin" and event.get("stop_kind") == "pickup" and task_id in self.reported \
                    and task_id not in self.reached:
                self.reached[task_id] = (event["sim_time_s"], event["vehicle_id"])
                waited = event["sim_time_s"] - self.reported[task_id]
                self.headline = (f"{_clock(now)}  {event['vehicle_id']} reached {task_id}",
                                 f"response time {_clock(waited)}")
            elif kind == "stop_completed" and event.get("stop_kind") == "dropoff" and task_id in self.reported:
                self.delivered.add(task_id)
            elif kind == "phase_shortened":
                self.interventions += 1
                self.signal_marker = (event["tls_id"], now)
                self.controller.gui_marker(
                    "signal", self.controller.gui_signal_position(event["tls_id"]),
                    label=f"GREEN WAVE for {event['vehicle_id']}", color=(80, 255, 140, 230), size=110, layer=7,
                )
        self.event_cursor = len(self.controller.event_log)
        if self.signal_marker and now - self.signal_marker[1] > 15:
            self.controller.gui_marker("signal", None, label="")
            self.signal_marker = None

    def _draw_calls(self, world: WorldState, now: float) -> None:
        for call in CALLS:
            if call.id not in self.reported:
                continue
            x, y = self.xy(self.nodes[call.place])
            task = world.tasks.get(call.id)
            if call.id in self.delivered:
                color, size = (150, 150, 150, 110), 90
                label = f"{call.id}  done, patient at UKC"
            elif call.id in self.reached:
                at, unit = self.reached[call.id]
                color, size = (60, 210, 110, 190), 140
                label = f"{call.id}  reached by {unit} in {_clock(at - self.reported[call.id])}"
            else:
                color, size = (255, 30, 30, 200), 190 + 60 * (int(now) % 2)
                unit = task.assigned_vehicle_id if task else None
                who = f"{unit} coming" if unit else "no unit yet"
                other = self._baseline(call.id)
                if unit and other and other["unit"] and other["unit"] != unit:
                    who += f" (baseline sends {other['unit']})"
                label = f"! {call.id} {call.what}  ·  {who}  ·  {_clock(now - self.reported[call.id])}"
            self.controller.gui_marker(f"call-{call.id}", (x, y), color=color, size=size, layer=3)
            # The frame's label replaces the call's while a difference is shown.
            self.controller.gui_marker(f"call-label-{call.id}", (x, y + 150), label="" if call.id in self.framed else label,
                                       color=(0, 0, 0, 0), layer=9)

    def _unit_state(self, world: WorldState, unit: Vehicle) -> tuple[tuple, str]:
        remaining = [s for s in unit.route_stops if s.service_completed_at_s is None]
        if not remaining:
            return GREY, "free"
        task = world.tasks.get(remaining[0].task_id)
        if task is None:
            return BLUE, "moving"
        if task.priority == TaskPriority.critical:
            if task.status == TaskStatus.en_route_dropoff:
                return ORANGE, f"{task.id} patient to UKC"
            return RED, f"to {task.id}"
        if task.status == TaskStatus.en_route_dropoff:
            return BLUE, f"{task.id} patient aboard"
        return BLUE, f"{task.id} transfer"

    def _draw_fleet(self, world: WorldState) -> None:
        states = {unit_id: self._unit_state(world, unit) for unit_id, unit in world.vehicles.items()}
        positions = self.controller.gui_vehicle_view({unit: color for unit, (color, _) in states.items()})
        parked: dict[str, int] = {}
        for unit_id, (color, text) in states.items():
            label_dy = 75
            if unit_id in positions:
                x, y = positions[unit_id]
            else:
                node = world.vehicles[unit_id].node_id
                slot = parked.get(node, 0)
                parked[node] = slot + 1
                x, y = self.xy(node)
                x += 110 * slot
                label_dy = 75 + 70 * slot
            active = color in (RED, ORANGE)
            halo = (*color[:3], 210 if active else 170)
            self.controller.gui_marker(f"unit-{unit_id}", (x, y), color=halo, size=120 if active else 80, layer=6)
            self.controller.gui_marker(f"unit-label-{unit_id}", (x, y - label_dy), label=f"{unit_id} {text}" if active else unit_id,
                                       color=(0, 0, 0, 0), layer=9)

    def _draw_status(self, now: float) -> None:
        waiting = [c for c in self.reported if c not in self.reached]
        responses = [at - self.reported[c] for c, (at, _unit) in self.reached.items()]
        average = _clock(mean(responses)) if responses else "--:--"
        lines = (
            f"TIME  {_clock(now)}",
            f"Calls {len(self.reported)}/{len(CALLS)}   waiting {len(waiting)}   average {average}",
            f"Routine transfers finished {self.transfers}/{len(ROUTINE)}",
            self.headline[0],
            self.headline[1],
        )
        for index, text in enumerate(lines):
            self.panel(f"status-{index}", 3.25 + index, text)

    # -- the window that shows the difference ----------------------------------

    def _baseline(self, call_id: str) -> dict | None:
        return self.reference["calls"].get(call_id) if self.reference else None

    def _difference(self, call_id: str, now: float) -> tuple[str, str, str, int]:
        """Baseline time, own time, difference text, and -1 slower / 0 open / 1 faster."""
        reported, other = self.reported[call_id], self._baseline(call_id)
        mine = self.reached[call_id][0] - reported if call_id in self.reached else None
        base = other["response_s"] if other else None
        base_known = base is not None and now - reported >= base
        left = _clock(base) if base_known else "waiting" if other else "?"
        right = _clock(mine) if mine is not None else "driving"
        if mine is None:
            return left, right, f"BEHIND by {_clock(now - reported - base)}" if base_known else "", -1 if base_known else 0
        if base is None or not base_known:
            return left, right, f"AHEAD by {_clock(now - reported - mine)}", 1
        gap = base - mine
        if abs(gap) < 10:
            return left, right, "same", 0
        return left, right, f"{_clock(abs(gap))} {'faster' if gap > 0 else 'slower'}", 1 if gap > 0 else -1

    def _draw_results(self, now: float) -> None:
        for row, call in enumerate(CALLS):
            y = 2.3 + row * 1.05
            name = f"{row + 1}  {PLACES[call.place][0]}"
            if call.id not in self.reported:
                cells = (name, *("" for _ in self.columns[1:]))
            elif self.policy == "B0":
                mine = self.reached.get(call.id)
                cells = (name, _clock(mine[0] - self.reported[call.id]) if mine else f"waiting {_clock(now - self.reported[call.id])}",
                         mine[1] if mine else "")
            elif self.reference is None:
                cells = (name, "?", _clock(self.reached[call.id][0] - self.reported[call.id]) if call.id in self.reached else "driving", "")
            else:
                left, right, text, verdict = self._difference(call.id, now)
                cells = (name, left, right, text)
                self._frame_call(call, now, text, verdict)
                middle = self.map[1] - 175 - y * self.LINE_M + self.TEXT_LIFT_M
                color = (30, 120, 60, 255) if verdict > 0 else (150, 35, 35, 255) if verdict < 0 else (45, 48, 52, 255)
                self.controller.gui_box(f"row-{call.id}", (self.columns[3] - 340, middle - 62, self.columns[3] + 340, middle + 62),
                                        color, fill=True, layer=11.5)
            for index, (x, value) in enumerate(zip(self.columns, cells)):
                self.text(f"cell-{call.id}-{index}", x, y, value)
        done = [c.id for c in CALLS if c.id in self.reached]
        own = [self.reached[c][0] - self.reported[c] for c in done]
        total = f"Average of {len(done)} reached: {_clock(mean(own))}" if own else ""
        both = [c for c in done if self._baseline(c) and self._baseline(c)["response_s"] is not None
                and now - self.reported[c] >= self._baseline(c)["response_s"]]
        if self.policy != "B0" and both:
            base = mean(self._baseline(c)["response_s"] for c in both)
            mine = mean(self.reached[c][0] - self.reported[c] for c in both)
            total = f"Average of {len(both)}:   baseline {_clock(base)}    RescueFlow {_clock(mine)}"
        self.text("box-total", (self.box[0] + self.box[2]) / 2, 2.3 + len(CALLS) * 1.05 + 0.25, total)

    def _frame_call(self, call: Call, now: float, text: str, verdict: int) -> None:
        """A square on the map around a call while its outcome differs."""
        x, y = self.xy(self.nodes[call.place])
        fresh = call.id in self.reached and now - max(
            self.reached[call.id][0], self.reported[call.id] + (self._baseline(call.id)["response_s"] or 0)) <= 60
        show = verdict != 0 and (fresh or call.id not in self.reached or "AHEAD" in text)
        color = BRAND if verdict > 0 else (235, 60, 60, 255)
        size = 300 + 25 * (int(now) % 2)
        self.controller.gui_box(f"frame-{call.id}", (x - size, y - size, x + size, y + size) if show else None, color,
                                line_width=26, layer=9)
        label = ""
        (self.framed.add if show and call.id in self.reached else self.framed.discard)(call.id)
        if show:
            label = ("AMBULANCE IS HERE, BASELINE STILL DRIVING" if "AHEAD" in text
                     else "BASELINE IS ALREADY HERE" if "BEHIND" in text else f"{text.upper()} THAN BASELINE")
        self.controller.gui_marker(f"frame-label-{call.id}", (x, y + size + 45), label=label, color=(0, 0, 0, 0), layer=12)


class Pace:
    """Keeps two windows on the same simulated second through small files."""

    def __init__(self, folder: Path, policy: str) -> None:
        self.mine, self.other = folder / policy, folder / ("A4" if policy == "B0" else "B0")

    def _write(self, value: float) -> None:
        temporary = self.mine.with_suffix(f".{os.getpid()}")
        temporary.write_text(str(value), encoding="utf-8")
        for _ in range(50):
            try:
                temporary.replace(self.mine)
                return
            except PermissionError:  # the other window is reading it
                time.sleep(0.002)

    def wait(self, now: float) -> None:
        self._write(now)
        deadline = time.monotonic() + 120  # the other window was closed: go on alone
        while time.monotonic() < deadline:
            try:
                if float(self.other.read_text(encoding="utf-8") or 0) >= now:
                    return
            except (OSError, ValueError):
                pass
            time.sleep(0.004)

    def finish(self) -> None:
        self._write(1e9)


def compare(delay_ms: int) -> None:
    with tempfile.TemporaryDirectory(prefix="rescueflow-sync-") as folder:
        runs = [subprocess.Popen([sys.executable, "-m", "rescueflow.sumo.presentation", policy, "--delay", str(delay_ms),
                                  "--sync", folder]) for policy in ("baseline", "rescueflow")]
        for process in runs:
            process.wait()


def run(root: Path, policy: str, *, gui: bool, output: Path, delay_ms: int, sync: Path | None = None) -> dict:
    ensure_sumolib_importable()
    output.mkdir(parents=True, exist_ok=True)
    net, cfg, nodes, world, civilians = prepare(root, output, gui, delay_ms, policy)
    manifest = {
        "scenario": "citywide", "horizon_s": HORIZON_S, "approval_delay_s": APPROVAL_S,
        "mobilization_delay_s": MOBILIZATION_S,
        "incidents": [
            {"time_s": call.at_s, "type": "new_urgent_task", "payload": {
                "task_id": call.id, "pickup_node": nodes[call.place], "dropoff_node": nodes["UKC"],
                "priority": "critical", "required_capabilities": ["ALS"],
                "latest_pickup_s": call.at_s + RESPONSE_TARGET_S, "latest_delivery_s": call.at_s + 1050,
            }}
            for call in CALLS
        ],
    }
    controller = TraciSimulationController(
        str(cfg), str(net), state_path=str(output / "unused.state.xml.gz"), seed=SEED, gui=gui,
        allow_teleport=False, observations_dir=output, junction_stops=True,
    )
    controller.defer_junction_reroutes = True
    controller.finish_service_first = controller.recover_stuck_units = True
    travel = CityTravelTimeProvider(str(net))
    runner = CityRun(manifest, controller, world, travel, policy, output, record=False)
    view = CityView(controller, travel, nodes, policy, root / "simulation" / "presentation") if gui else None
    if policy == "A4":
        from dotenv import load_dotenv  # noqa: PLC0415

        load_dotenv(root / ".env")
        runner.agent = DecisionAgent(cache_dir=root / "simulation" / "presentation" / "ai-answers", retries=2)

    pace = Pace(sync, policy) if sync else None

    def on_step(current) -> None:
        current.free_unit_check()
        if view is not None:
            view(current)
        if pace is not None:
            pace.wait(current.world.sim_time_s)

    runner.on_step = on_step
    print(f"RescueFlow | Ljubljana city-wide SUMO scene | {policy}: {POLICIES[policy]}", flush=True)
    print(f"  {len(world.vehicles)} ambulances, {len(ROUTINE)} routine transfers, {len(CALLS)} emergency calls, "
          f"{civilians} civilian trips", flush=True)
    try:
        runner.execute()
        if gui:
            time.sleep(HOLD_RESULT_S)  # keep the final result on screen
    finally:
        if pace is not None:
            pace.finish()
        controller.close()
    return summarize(runner, controller, output, policy, civilians)


def summarize(runner: LogisticsRun, controller, output: Path, policy: str, civilians: int) -> dict:
    events = controller.event_log
    reported = {e["incident"]["payload"]["task_id"]: e["sim_time_s"]
                for e in runner.events if e["kind"] == "incident_validated"}
    reached, delivered = {}, {}
    for event in events:
        task_id = event.get("task_id")
        if task_id in reported and event["kind"] == "stop_begin" and event.get("stop_kind") == "pickup":
            reached.setdefault(task_id, (event["sim_time_s"] - reported[task_id], event["vehicle_id"]))
        if task_id and event["kind"] == "stop_completed" and event.get("stop_kind") == "dropoff":
            delivered[task_id] = event["sim_time_s"]
    trips = _trip_rows(output / "tripinfo.xml")
    civilian = [t for t in trips if t["type"] == "civilian" and t["arrival_s"] >= 0 and not t["vaporized"]]
    routine_late = [
        t.id for t in runner.world.tasks.values()
        if t.priority == TaskPriority.routine and (t.id not in delivered or delivered[t.id] > (t.latest_delivery_s or 1e9))
    ]
    responses = [value for value, _unit in reached.values()]
    summary = {
        "policy": policy,
        "calls": {call.id: {"place": PLACES[call.place][0], "response_s": reached.get(call.id, (None,))[0],
                            "unit": reached.get(call.id, (None, None))[1], "at_ukc_s": delivered.get(call.id)}
                  for call in CALLS},
        "mean_response_s": mean(responses) if responses else None,
        "max_response_s": max(responses) if responses else None,
        "within_target": sum(r <= RESPONSE_TARGET_S for r in responses),
        "routine_transfers_late_or_unfinished": routine_late,
        "signal_phases_shortened": sum(e["kind"] == "phase_shortened" for e in events),
        "civilian_trips": civilians, "civilian_arrived": len(civilian),
        "civilian_mean_time_loss_s": mean(t["time_loss_s"] for t in civilian) if civilian else None,
        "collisions_or_teleports": sum(e["kind"] in ("collision", "teleport_started") for e in events),
        "run_failures": runner.failed, "decisions": dict(runner.rankings),
    }
    if runner.rankings:
        print(f"Decisions: {dict(runner.rankings)}")
    print("Emergency calls (report → first ambulance on scene):")
    for call_id, row in summary["calls"].items():
        response = _clock(row["response_s"]) if row["response_s"] is not None else "not reached"
        print(f"  {call_id:7} {row['place']:14} {response:>12}   unit {row['unit'] or '-'}")
    if responses:
        print(f"Average response {_clock(summary['mean_response_s'])}, worst {_clock(summary['max_response_s'])}, "
              f"{summary['within_target']}/{len(CALLS)} within {_clock(RESPONSE_TARGET_S)}.")
    print(f"Routine transfers late or unfinished: {len(routine_late)}/{len(ROUTINE)} {routine_late}")
    print(f"Green-wave phase shortenings: {summary['signal_phases_shortened']}. "
          f"Civilian trips finished {len(civilian)}/{civilians}, mean time loss "
          f"{summary['civilian_mean_time_loss_s'] or 0:.1f}s.")
    if summary["collisions_or_teleports"] or runner.failed:
        print(f"Audit notes: {summary['collisions_or_teleports']} collision/teleport events; {runner.failed}")
    (output / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    (output / "events.jsonl").write_text(
        "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in sorted(
            [*runner.events, *events], key=lambda e: e["sim_time_s"])), encoding="utf-8")
    return summary


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("policy", type=lambda v: ALIASES.get(v.lower(), v.upper()), choices=(*POLICIES, "COMPARE"),
                        help="baseline (B0), rescueflow (A4), or compare (both, in step)")
    parser.add_argument("--save-reference", action="store_true", help="Store this baseline run for the comparison")
    parser.add_argument("--sync", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--headless", action="store_true", help="Run without opening SUMO GUI")
    parser.add_argument("--delay", type=int, default=60, help="GUI delay per simulated second in ms (default 60)")
    parser.add_argument("--output", type=Path, help="Directory for SUMO run files. Defaults to a temporary directory.")
    args = parser.parse_args()
    root = Path.cwd()
    if not (root / "simulation" / "presentation").is_dir():
        parser.error("Run this command from the RescueFlow repository root.")
    if args.policy == "COMPARE":
        compare(args.delay)
        return
    if args.save_reference and args.policy != "B0":
        parser.error("Only the baseline can be saved as the reference.")
    options = {"gui": not args.headless, "delay_ms": args.delay, "sync": args.sync}
    if args.output:
        summary = run(root, args.policy, output=args.output.resolve(), **options)
    else:
        with tempfile.TemporaryDirectory(prefix="rescueflow-sumo-") as output:
            summary = run(root, args.policy, output=Path(output), **options)
    if args.save_reference:
        assets = root / "simulation" / "presentation"
        (assets / REFERENCE).write_text(json.dumps(
            {"inputs": input_digest(assets), "calls": summary["calls"], "mean_response_s": summary["mean_response_s"]},
            indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Saved {assets / REFERENCE}")


if __name__ == "__main__":
    main()
