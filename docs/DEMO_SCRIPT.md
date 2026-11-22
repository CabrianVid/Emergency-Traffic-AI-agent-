# RescueFlow dispatcher demo

Start the backend and frontend using the [runbook](RUNBOOK.md). Open the
console with **Ljubljana** selected. The road network comes from
OpenStreetMap; facilities, demand and incidents are simulated. Moving
markers come from TraCI.

## Four-minute walkthrough

1. **Start state.** The AI co-dispatcher already suggests who should take the
   unassigned work. Point out the magenta marks, then press `Enter`.
2. **Emergency call.** Press `F2`. The call appears as "Needs a unit" and the
   AI compares plans. The panel says whether the AI ranked them, only one plan
   exists, or fixed rules ranked them because the AI is unavailable.
3. **Decide together.** Press `2` and `1` to view options on the map and
   timeline. Read the reasons and trade-offs. Approve, or choose another plan.
   Keep **Clear traffic lights** ticked for a green wave.
4. **Crew report.** Paste `Vozilo R2 je obstalo. Komplet K1 je ostal v vozilu.`
   into the box at the bottom right and confirm what the AI understood.
5. **Other events.** Right-click a unit, a call or a road. `F4` requests
   equipment, `F8` sends the scripted incident.
6. **A unit frees up.** With a call waiting, the AI speaks up by itself.
7. **Evidence.** `F9`, **Evaluation results**. Read the paired differences and
   intervals, including those that show no gain.

Set the speed in the Training panel (`F9`); `Space` pauses.

Say aloud that the benchmark is synthetic, that the dispatcher always decides
in the dashboard, and what the results do *not* show
([plain-English guide](SIMPLE_SYSTEM_EXPLANATION.md)).

If live startup fails, open the [offline SUMO replay](../experiments/published/replay.html)
and say that it is a recording of one older training-grid run.

If a unit is inside a junction when you report a breakdown, advance a step
and retry. Recalculate if the plan version changed.
