"""Intelligent green-wave signal priority -- simulation-only
(docs/DEVELOPMENT_PLAN.md section 8).

**Safety model (read this before changing anything here):**

- We never write an arbitrary phase/state string. Every intervention
  selects an *existing* phase index from the junction's own
  `getAllProgramLogics` and either does nothing (already green) or calls
  `traci.trafficlight.setPhaseDuration` to shorten the *current* phase's
  remaining time.
- We only ever shorten a phase that is itself a green phase for some
  movement (its state string contains 'G' or 'g') and that is *not* the
  phase the ambulance needs. We never touch a yellow or all-red clearance
  phase -- those always run their full configured duration. This is what
  "compatible phase transitions with minimum green, yellow and all-red
  clearance" means in practice: we ride the junction's own safe cyclic
  sequence faster, we never skip a step in it.
- A shortened green phase is floored at `MIN_GREEN_OVERRIDE_S`, applied at
  most once per phase *instance* (tracked per (junction, phase index)) so
  repeated calls across simulation steps don't keep resetting the
  countdown and freezing the phase forever.
- `getNextTLS` gives us the TraCI *link index* for the vehicle's actual
  intended movement directly -- we never infer it by hand-matching lane
  IDs, which would be fragile.
- Restoration is logged explicitly once a request completes or times out.
  `setPhaseDuration`'s own documented semantics are "no effect on
  subsequent repetitions of this phase" -- our one-time shortening is
  therefore already self-limiting; "restore" deliberately does *not* call
  `setProgram` (which would reset the junction to phase index 0 and lose
  where it actually is in its cycle -- a worse disruption than the
  intervention itself). We only ever touch one phase instance once, so
  there is nothing persistent left to undo; the logged event records that
  intervention has stopped, and a test confirms the junction's own cyclic
  program continues unmodified afterward.
- Two simultaneous requests at the same junction are resolved
  deterministically (priority rank, then ETA) in `_rank_requests`; only the
  winner actively intervenes, so we never attempt to prove two different
  phases are simultaneously "compatible" -- we simply never act on behalf
  of more than one request per junction at a time.

This module never connects to a real traffic controller; it only ever
calls TraCI against the local SUMO process this process itself started.
"""

from __future__ import annotations

from dataclasses import dataclass, field

TRIGGER_WINDOW_S = (10.0, 25.0)
MIN_GREEN_OVERRIDE_S = 6.0
MAX_ACTIVE_TLS_PER_VEHICLE = 3
REQUEST_TIMEOUT_S = 90.0


@dataclass
class _Request:
    vehicle_id: str
    tls_id: str
    link_index: int
    priority_rank: int
    requested_at_s: float
    eta_s: float
    state: str = "REQUEST_RECEIVED"
    shortened_phase: tuple[int, bool] | None = None  # (phase_index, already_applied)


@dataclass
class GreenWaveController:
    """Owns per-junction priority state for one TraCI session. Call
    `step(traci_module, world, sim_time_s)` once per simulation step."""

    enabled: bool = True
    log: list[dict] = field(default_factory=list)
    _requests: dict[tuple[str, str], _Request] = field(default_factory=dict)  # (vehicle, tls) -> Request
    _eligible: dict[str, int] = field(default_factory=dict)  # vehicle_id -> priority_rank, refreshed every step

    def _emit(self, sim_time_s: float, kind: str, **fields) -> None:
        self.log.append({"sim_time_s": sim_time_s, "kind": kind, **fields})

    def step(self, traci, world, sim_time_s: float, eligible_vehicles: dict[str, int]) -> None:
        """`eligible_vehicles` is the *complete, current* set of vehicle_id
        -> priority_rank for vehicles authorized for green-wave priority
        right now (the controller recomputes this every step from live task
        status -- see docs/DECISIONS.md "who is eligible for green-wave
        priority"); anything not in it is never touched, and anything that
        drops out of it (task delivered, vehicle broken) stops being
        requested for on the very next step."""
        if not self.enabled:
            return
        self._eligible = eligible_vehicles
        for vehicle_id, priority_rank in list(self._eligible.items()):
            if vehicle_id not in traci.vehicle.getIDList():
                continue
            for tls_id, link_index, distance_m, _state in traci.vehicle.getNextTLS(vehicle_id)[:MAX_ACTIVE_TLS_PER_VEHICLE]:
                speed = max(traci.vehicle.getSpeed(vehicle_id), 1.0)
                eta_s = distance_m / speed
                key = (vehicle_id, tls_id)
                if key not in self._requests:
                    if TRIGGER_WINDOW_S[0] <= eta_s <= TRIGGER_WINDOW_S[1]:
                        self._requests[key] = _Request(
                            vehicle_id=vehicle_id, tls_id=tls_id, link_index=link_index,
                            priority_rank=priority_rank, requested_at_s=sim_time_s, eta_s=eta_s,
                        )
                        self._emit(sim_time_s, "priority_requested", vehicle_id=vehicle_id, tls_id=tls_id,
                                   link_index=link_index, eta_s=round(eta_s, 1))
                else:
                    self._requests[key].eta_s = eta_s

        self._advance_requests(traci, sim_time_s)

    def _advance_requests(self, traci, sim_time_s: float) -> None:
        for key, req in list(self._requests.items()):
            if req.vehicle_id not in self._eligible:
                self._emit(sim_time_s, "priority_cancelled", vehicle_id=req.vehicle_id, tls_id=req.tls_id,
                           reason="vehicle no longer eligible (task delivered, broken, or rerouted)")
                req.state = "CANCELLED"
                self._restore(traci, req.tls_id, sim_time_s, req)
                self._requests.pop(key, None)

        by_tls: dict[str, list[_Request]] = {}
        for req in self._requests.values():
            by_tls.setdefault(req.tls_id, []).append(req)

        done_keys: list[tuple[str, str]] = []
        for tls_id, requests in by_tls.items():
            winner = self._rank_requests(requests)[0]
            for req in requests:
                if req.vehicle_id not in traci.vehicle.getIDList():
                    req.state = "CANCELLED"
                    self._restore(traci, tls_id, sim_time_s, req)
                    done_keys.append((req.vehicle_id, req.tls_id))
                    continue
                if (req.tls_id, req.link_index) not in [(tls, link) for tls, link, *_ in traci.vehicle.getNextTLS(req.vehicle_id)]:
                    req.state = "PASSAGE_CONFIRMED" if req.state == "COMPATIBLE_PRIORITY_GREEN" else "LEFT_APPROACH"
                    if req.state == "PASSAGE_CONFIRMED":
                        self._emit(sim_time_s, "priority_passage_confirmed", vehicle_id=req.vehicle_id, tls_id=req.tls_id)
                    self._restore(traci, tls_id, sim_time_s, req)
                    done_keys.append((req.vehicle_id, req.tls_id))
                    continue
                if req is not winner:
                    if req.state != "DEFERRED":
                        req.state = "DEFERRED"
                        self._emit(sim_time_s, "priority_deferred", vehicle_id=req.vehicle_id, tls_id=tls_id,
                                    reason="lower priority/eta than concurrent request")
                    continue
                self._service_request(traci, req, sim_time_s)
                if req.state in ("PASSAGE_CONFIRMED", "TIMEOUT"):
                    self._restore(traci, tls_id, sim_time_s, req)
                    done_keys.append((req.vehicle_id, req.tls_id))

        for key in done_keys:
            self._requests.pop(key, None)

    @staticmethod
    def _rank_requests(requests: list[_Request]) -> list[_Request]:
        return sorted(requests, key=lambda r: (r.priority_rank, r.eta_s))

    def _service_request(self, traci, req: _Request, sim_time_s: float) -> None:
        if sim_time_s - req.requested_at_s > REQUEST_TIMEOUT_S:
            req.state = "TIMEOUT"
            self._emit(sim_time_s, "priority_timeout", vehicle_id=req.vehicle_id, tls_id=req.tls_id)
            return

        logic = traci.trafficlight.getAllProgramLogics(req.tls_id)[0]
        phases = logic.getPhases()
        current_idx = traci.trafficlight.getPhase(req.tls_id)
        current_state_char = phases[current_idx].state[req.link_index]

        if current_state_char in ("G", "g"):
            if req.state != "COMPATIBLE_PRIORITY_GREEN":
                req.state = "COMPATIBLE_PRIORITY_GREEN"
                self._emit(sim_time_s, "priority_green_reached", vehicle_id=req.vehicle_id, tls_id=req.tls_id,
                           phase=current_idx)
            # Vehicle still approaching under green -- nothing to shorten.
            # Confirm passage once it has actually left this TLS's approach.
            if req.tls_id not in [t for t, *_ in traci.vehicle.getNextTLS(req.vehicle_id)]:
                req.state = "PASSAGE_CONFIRMED"
                self._emit(sim_time_s, "priority_passage_confirmed", vehicle_id=req.vehicle_id, tls_id=req.tls_id)
            return

        phase_state = phases[current_idx].state
        if "y" in phase_state.lower() or not any(light in phase_state for light in "Gg"):
            # Yellow/all-red clearance phase -- never shortened, let it run.
            return

        if req.shortened_phase is None or req.shortened_phase[0] != current_idx:
            remaining = traci.trafficlight.getNextSwitch(req.tls_id) - sim_time_s
            new_duration = min(remaining, MIN_GREEN_OVERRIDE_S) if remaining > MIN_GREEN_OVERRIDE_S else None
            if new_duration is not None:
                traci.trafficlight.setPhaseDuration(req.tls_id, new_duration)
                self._emit(sim_time_s, "phase_shortened", vehicle_id=req.vehicle_id, tls_id=req.tls_id,
                           phase=current_idx, new_duration_s=new_duration,
                           shortened_by_s=round(remaining - new_duration, 3))
            req.shortened_phase = (current_idx, True)
            req.state = "WAIT_FOR_MIN_GREEN"

    def _restore(self, traci, tls_id: str, sim_time_s: float, req: _Request) -> None:
        # Nothing to undo -- see the module docstring. This purely marks
        # (and logs) that we have stopped intervening for this request.
        self._emit(sim_time_s, "restored", tls_id=tls_id, vehicle_id=req.vehicle_id, outcome=req.state)
