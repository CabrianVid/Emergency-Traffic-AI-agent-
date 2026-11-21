# RescueFlow dispatcher demo

Start the backend and frontend using the [runbook](RUNBOOK.md). Open
the console and leave **Ljubljana** and **R2 breakdown · K1 recovery**
selected. The map uses SUMO’s OpenStreetMap-derived road network and
simulated demand. A marker for an idle ambulance shows its assigned
staging node. Moving markers come from TraCI.

## Main walkthrough

1. Start paused. Point out R2 carrying kit K1, the active tasks and the
   committed timeline.
2. Select **Use example**, then **Interpret report**. Review the parsed
   vehicle and kit, the validation result, and the parser label. Select
   **Apply incident and compute choices**. **Trigger scripted incident**
   reaches the same validated event workflow directly.
3. Review the selected card’s estimates and compare the dashed proposed
   routes and lower timeline with the committed schedule. Select a
   distinct card to see a different route and task order.
4. Approve **Urgent first**. Look for the plan revision and the mock
   message for the kit recovery. Select **30×** and **Play**, or use the
   advance button when paused. Follow K1 from the roadside into a unit
   and then to H1 after its real transfer and receiving dwell.
5. Pause and review **Results** and **Run activity**. Signal priority
   requests and restorations are logged. The results show the observed
   dispatch-to-pickup and late handovers, plus SUMO civilian time loss.
6. Reset to replay. To populate both response-time rows, replay the same
   incident once with **Compute B0 only**, then again with **Compute A1
   only**, approving each plan and advancing sufficiently for pickups.
   The panel displays the latest observations from each policy for the
   current network and scenario. It keeps these across run resets in one
   server session. Formal matched benchmark suites are planned for
   prompt 4.

## Other scenarios

- **New urgent patient** adds an urgent request through the
  same planner. Select a road on the map to close that directed edge for
  four simulated minutes through the validated closure control.
- **Fog · receiving capacity** grounds HELI 1 for an hour and reduces
  H1’s available handovers in a future receiving window. Helicopter ETAs
  are analytical. The road network still drives ambulance ETAs and
  movement. Use **Training grid** as the offline fallback network.

If a position is inside a junction when you apply a breakdown or
reroute, advance past the junction and try the action again. Regenerate
its proposal if the world plan version changed.
