# Known limitations

- The benchmark uses the training grid. Ljubljana uses real road
  geometry with fictional facilities, traffic and incidents. Findings
  apply to the synthetic measurements.
- Civilian demand uses fixed trips. Runs with SUMO teleportation off
  can leave queues or tasks unfinished at cutoff. Both are reported.
- Helicopter travel is analytical and hospital receiving is an
  abstraction. Suite F, repositioning and OR-Tools are deferred.
- Free text handles breakdown and equipment reports. Other events have
  structured controls. OpenAI is optional. SUMO, the benchmark and the
  offline replay run without provider credentials.
- Actual signal timing is audited in the synthetic grid. Conflict
  ranking is unit-tested. An empirical multi-probe traffic run was
  outside the selected evaluation.
- ETA predictions use free-flow routing. Response and completion
  measurements use SUMO observations. A live route change while a
  unit is on an internal junction edge can require advancing first.
