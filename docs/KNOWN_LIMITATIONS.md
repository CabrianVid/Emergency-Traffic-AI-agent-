# Known limitations

- The final benchmark uses both the training grid and Ljubljana roads
  from OpenStreetMap. Facilities, signals, traffic and incidents are
  modeled. Findings apply to the simulated conditions.
- Civilian demand uses fixed trips. Runs with SUMO teleportation off
  can leave queues or tasks unfinished at cutoff. Both are reported.
- Helicopter and hospital-capacity features were removed from the active
  app. Repositioning and OR-Tools are not implemented.
- Free text handles breakdown and equipment reports. Other events have
  structured controls. The console, rule-based benchmarks and offline
  replay can run without OpenAI. Regenerating AI-ranked benchmark
  results requires provider access or cached answers to identical inputs.
- Actual signal timing is audited in the synthetic grid. Conflict
  ranking is unit-tested. An empirical multi-probe traffic run was
  outside the selected evaluation.
- Planner ETAs use the edge travel times SUMO reports now (dashboard and
  Monte Carlo) or free-flow times (older tools). Both can be optimistic.
  Response and completion measurements use SUMO observations. A breakdown
  report while a unit is inside a junction requires advancing first.
