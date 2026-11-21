"""Hard-constraint feasibility checking shared by B0, A1 and the approval gate.

Every candidate plan -- whether produced by the greedy baseline or the
fleet-wide optimizer -- passes through the *same* validator before it can be
shown to the operator or committed. See docs/CONTRACTS.md "Operator/safety
invariants" for the list this implements.
"""

from __future__ import annotations

from rescueflow.models.core import (
    FeasibilityIssue,
    FeasibilityReport,
    PlanProposal,
    Stop,
    VehicleStatus,
    WorldState,
)


def merged_vehicle_stops(world: WorldState, proposal: PlanProposal) -> dict[str, list[Stop]]:
    """Every vehicle's stop list as it would be *after* committing `proposal`:
    the proposal's stops for vehicles it touches, the current world's stops
    (unchanged) for every other vehicle."""
    overrides = {vp.vehicle_id: vp.stops for vp in proposal.vehicle_plans}
    merged: dict[str, list[Stop]] = {}
    for vid, vehicle in world.vehicles.items():
        merged[vid] = overrides.get(vid, vehicle.route_stops)
    return merged


class DefaultPlanValidator:
    """Concrete `PlanValidator`."""

    def validate(self, world: WorldState, proposal: PlanProposal) -> FeasibilityReport:
        issues: list[FeasibilityIssue] = []
        merged = merged_vehicle_stops(world, proposal)
        touched_vehicle_ids = {vp.vehicle_id for vp in proposal.vehicle_plans}

        for vehicle_id, stops in merged.items():
            vehicle = world.vehicles[vehicle_id]
            # The locked-prefix and broken/grounded checks only make sense
            # against stops the *proposal itself* is assigning -- an
            # untouched vehicle's pre-existing stops (including leftover
            # history from before a breakdown) are not something this
            # validation pass is being asked to approve.
            if vehicle_id in touched_vehicle_ids:
                issues.extend(self._check_locked_prefix_unchanged(vehicle, stops))
                issues.extend(self._check_grounded_or_broken(vehicle, stops))
            issues.extend(self._check_capacity_and_ordering(world, vehicle, stops))
            issues.extend(self._check_eligibility(world, vehicle, stops))
            issues.extend(self._check_time_windows(world, stops))

        issues.extend(self._check_asset_custody(world, merged))
        issues.extend(self._check_receiving_capacity(world, merged))

        return FeasibilityReport(feasible=len(issues) == 0, issues=issues)

    # -- individual checks --------------------------------------------------

    def _check_locked_prefix_unchanged(self, vehicle, stops: list[Stop]) -> list[FeasibilityIssue]:
        issues = []
        if vehicle.locked_stop_index is None:
            return issues
        existing_prefix = vehicle.route_stops[: vehicle.locked_stop_index + 1]
        new_prefix = stops[: vehicle.locked_stop_index + 1]
        if len(new_prefix) < len(existing_prefix) or [s.stop_id for s in new_prefix] != [
            s.stop_id for s in existing_prefix
        ]:
            issues.append(
                FeasibilityIssue(
                    code="locked_stop_reassigned",
                    message=f"vehicle {vehicle.id} has a locked/in-progress leg that a candidate plan "
                    "attempted to reorder or remove",
                    vehicle_id=vehicle.id,
                )
            )
        return issues

    def _check_grounded_or_broken(self, vehicle, stops: list[Stop]) -> list[FeasibilityIssue]:
        issues = []
        if vehicle.status in (VehicleStatus.broken, VehicleStatus.weather_grounded):
            start = 0 if vehicle.locked_stop_index is None else vehicle.locked_stop_index + 1
            if len(stops) > start:
                issues.append(
                    FeasibilityIssue(
                        code="unavailable_vehicle_assigned",
                        message=f"vehicle {vehicle.id} is {vehicle.status.value} and cannot be given new stops",
                        vehicle_id=vehicle.id,
                    )
                )
        return issues

    def _check_capacity_and_ordering(self, world: WorldState, vehicle, stops: list[Stop]) -> list[FeasibilityIssue]:
        issues = []
        onboard: set[str] = set()
        patient_count = 0
        equipment_count = 0
        last_departure = float("-inf")
        for stop in stops:
            # Observations can overrun an old estimate; compare only the
            # pending estimates. History still supplies the running loads.
            if stop.service_completed_at_s is None and stop.arrival_s is not None and stop.arrival_s < last_departure - 1e-6:
                issues.append(
                    FeasibilityIssue(
                        code="non_monotonic_schedule",
                        message=f"vehicle {vehicle.id} stop {stop.stop_id} arrives before the previous "
                        "stop's departure",
                        vehicle_id=vehicle.id,
                    )
                )
            if stop.service_completed_at_s is None and stop.departure_s is not None:
                last_departure = stop.departure_s

            if stop.task_id is None:
                continue
            task = world.tasks.get(stop.task_id)
            if task is None:
                issues.append(
                    FeasibilityIssue(
                        code="unknown_task", message=f"stop references unknown task {stop.task_id!r}",
                        vehicle_id=vehicle.id, task_id=stop.task_id,
                    )
                )
                continue

            if stop.kind.value == "pickup":
                if stop.task_id in onboard:
                    issues.append(
                        FeasibilityIssue(
                            code="double_pickup", message=f"task {task.id} picked up twice",
                            vehicle_id=vehicle.id, task_id=task.id,
                        )
                    )
                else:
                    onboard.add(stop.task_id)
                    patient_count += task.patient_slots
                    equipment_count += task.equipment_units
            elif stop.kind.value == "dropoff":
                if stop.task_id not in onboard:
                    issues.append(
                        FeasibilityIssue(
                            code="dropoff_without_pickup",
                            message=f"task {task.id} delivered without a preceding pickup on vehicle {vehicle.id}",
                            vehicle_id=vehicle.id, task_id=task.id,
                        )
                    )
                else:
                    onboard.discard(stop.task_id)
                    patient_count -= task.patient_slots
                    equipment_count -= task.equipment_units

            if patient_count > vehicle.patient_capacity:
                issues.append(
                    FeasibilityIssue(
                        code="patient_capacity_exceeded",
                        message=f"vehicle {vehicle.id} exceeds patient capacity at stop {stop.stop_id}",
                        vehicle_id=vehicle.id,
                    )
                )
            if equipment_count > vehicle.equipment_capacity_units:
                issues.append(
                    FeasibilityIssue(
                        code="equipment_capacity_exceeded",
                        message=f"vehicle {vehicle.id} exceeds equipment capacity at stop {stop.stop_id}",
                        vehicle_id=vehicle.id,
                    )
                )
        return issues

    def _check_eligibility(self, world: WorldState, vehicle, stops: list[Stop]) -> list[FeasibilityIssue]:
        issues = []
        seen_tasks: set[str] = set()
        for stop in stops:
            if stop.task_id is None or stop.task_id in seen_tasks:
                continue
            seen_tasks.add(stop.task_id)
            task = world.tasks.get(stop.task_id)
            if task is None:
                continue
            if vehicle.mode not in task.allowed_modes:
                issues.append(
                    FeasibilityIssue(
                        code="mode_not_allowed",
                        message=f"task {task.id} does not allow vehicle mode {vehicle.mode.value}",
                        vehicle_id=vehicle.id, task_id=task.id,
                    )
                )
            missing = set(task.required_capabilities) - set(vehicle.capabilities)
            if missing:
                issues.append(
                    FeasibilityIssue(
                        code="missing_capability",
                        message=f"vehicle {vehicle.id} lacks required capability {sorted(missing)} for task {task.id}",
                        vehicle_id=vehicle.id, task_id=task.id,
                    )
                )
        return issues

    def _check_time_windows(self, world: WorldState, stops: list[Stop]) -> list[FeasibilityIssue]:
        issues = []
        for stop in stops:
            if stop.task_id is None or stop.arrival_s is None:
                continue
            if stop.service_completed_at_s is not None:
                # Already happened -- the past cannot be made infeasible.
                continue
            task = world.tasks.get(stop.task_id)
            if task is None or not task.time_window_hard:
                continue
            deadline = task.latest_pickup_s if stop.kind.value == "pickup" else task.latest_delivery_s
            if deadline is not None and stop.arrival_s > deadline + 1e-6:
                issues.append(
                    FeasibilityIssue(
                        code="hard_deadline_violated",
                        message=f"task {task.id} {stop.kind.value} at {stop.arrival_s}s misses hard deadline {deadline}s",
                        task_id=task.id,
                    )
                )
        return issues

    def _check_asset_custody(self, world: WorldState, merged: dict[str, list[Stop]]) -> list[FeasibilityIssue]:
        issues = []
        for vehicle_id, stops in merged.items():
            for stop in stops:
                if stop.task_id is None or stop.kind.value != "pickup":
                    continue
                if stop.service_completed_at_s is not None:
                    # Already physically happened -- checking it against
                    # *current* (since-moved-on) asset location would check
                    # the wrong time slice. See docs/CONTRACTS.md.
                    continue
                task = world.tasks.get(stop.task_id)
                if task is None:
                    continue
                for asset_id in task.required_asset_ids:
                    asset = world.assets.get(asset_id)
                    if asset is None:
                        issues.append(
                            FeasibilityIssue(
                                code="unknown_asset", message=f"task {task.id} requires unknown asset {asset_id!r}",
                                task_id=task.id,
                            )
                        )
                        continue
                    loc = asset.location
                    if loc.kind == "vehicle":
                        if loc.ref_id != vehicle_id:
                            issues.append(
                                FeasibilityIssue(
                                    code="asset_not_at_pickup",
                                    message=f"asset {asset_id} is aboard vehicle {loc.ref_id}, not available for "
                                    f"pickup by {vehicle_id}",
                                    vehicle_id=vehicle_id, task_id=task.id,
                                )
                            )
                    else:
                        if loc.ref_id != stop.node_id:
                            issues.append(
                                FeasibilityIssue(
                                    code="asset_not_at_pickup",
                                    message=f"asset {asset_id} is physically at {loc.kind}:{loc.ref_id}, not at "
                                    f"pickup node {stop.node_id} for task {task.id}",
                                    vehicle_id=vehicle_id, task_id=task.id,
                                )
                            )
        return issues

    def _check_receiving_capacity(self, world: WorldState, merged: dict[str, list[Stop]]) -> list[FeasibilityIssue]:
        issues = []
        dropoffs_by_node: dict[str, list[float]] = {}
        for stops in merged.values():
            for stop in stops:
                if stop.kind.value != "dropoff":
                    continue
                # Completed handovers still consume an interval slot.
                # Count them at their actual completion, even if SUMO had to wait.
                handover_s = stop.service_completed_at_s
                if handover_s is None:
                    handover_s = stop.arrival_s
                if handover_s is not None:
                    dropoffs_by_node.setdefault(stop.node_id, []).append(handover_s)

        for site in world.receiving_sites.values():
            relevant = dropoffs_by_node.get(site.node_id, [])
            for slot in site.slots:
                count = sum(1 for t in relevant if slot.start_s <= t < slot.end_s)
                if count > slot.capacity:
                    issues.append(
                        FeasibilityIssue(
                            code="receiving_capacity_exceeded",
                            message=f"hospital {site.id} has {count} deliveries between "
                            f"{slot.start_s}s-{slot.end_s}s but only {slot.capacity} slots",
                        )
                    )
        return issues
