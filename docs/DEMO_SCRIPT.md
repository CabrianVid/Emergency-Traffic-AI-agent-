# RescueFlow dispatcher demo

Start the backend and frontend using the [runbook](RUNBOOK.md). Open the
console with **Ljubljana** selected. The road network comes from
OpenStreetMap; facilities, demand and incidents are simulated. Moving
markers come from TraCI.

## Four-minute walkthrough

1. **Start state.** Point out the fleet, tasks and R2 carrying kit K1.
2. **Event.** Select **Use example**, **Interpret report**, then apply it;
   or press **New emergency**. The simulation pauses.
3. **Options.** Up to three feasible options appear, ranked. The badge says
   whether the AI ranked them, only one option existed, or the labeled
   deterministic fallback ranked them. Compare ETAs, changed routes,
   warnings and equipment effects.
4. **Decision.** Approve an option, reject all, or recalculate. Tick
   **Authorize traffic-light priority** to allow a green wave for the listed
   units. Point out the plan revision and mock crew messages.
5. **Execution.** Play at 30×. Follow the unit on the map and the signal
   requests and restorations in **Run activity**.
6. **Other events.** Click a unit (breakdown, 2 min delay), a task (priority,
   hospital) or a road (close 4 min), or request equipment.
7. **Evidence.** Open **Results**. Read the paired differences and intervals,
   including those that show no gain, then replay a recorded scenario.

Say aloud that the benchmark is synthetic, that the dispatcher always decides
in the dashboard, and what the results do *not* show
([plain-English guide](SIMPLE_SYSTEM_EXPLANATION.md)).

If live startup fails, open the [offline SUMO replay](../experiments/published/replay.html)
and say that it is a recording of one older training-grid run.

If a unit is inside a junction when you report a breakdown, advance a step
and retry. Recalculate if the plan version changed.
