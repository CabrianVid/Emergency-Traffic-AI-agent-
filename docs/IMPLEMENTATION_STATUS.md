# Implementation status — prompt 3/4

Updated 2026-11-21. Verified on Windows 11 with Python 3.13.9, Node 22
and Eclipse SUMO 1.28.0. The implementation checkpoints are
`c16e22e` (hospital capacity and plan equivalence), `9c5d3ce` (API and
network), and `c43c0a5` (workstation and demo). Run `git log -4 --oneline`
to see the handoff commit as well.

## What works

- The React workstation renders a live fleet, task deadlines, receiving
  sites, equipment custody, committed routes, proposed routes, selected
  road closures, traffic lights, a before/after vehicle timeline, and
  mock dispatch notifications. Its controls support scenario selection,
  simulation speed, pause, play, step, reset, and map zoom.
- FastAPI serializes simulator commands on one worker with exclusive
  TraCI ownership. The UI reads versioned snapshots over WebSocket or
  HTTP polling. Ordinary position updates advance `telemetry_seq`.
  Service completions and other planning changes advance `plan_revision`.
- The default network is Ljubljana's Tabor/UKC area, imported by
  `netconvert` from the committed OpenStreetMap road extract. The UI,
  optimizer, and simulator use its same junction and edge identifiers.
  The fictional 7×7 grid remains selectable and is the startup fallback.
- Crew reports validate against the authoritative vehicle and equipment
  state. Optional OpenAI structured extraction runs outside the SUMO
  worker. Without a key, the UI explicitly shows
  “Rule-based fallback — AI unavailable”. Scripted cases cover R2/K1,
  a new urgent patient, and fog plus a receiving-capacity reduction.
- Feasible B0 and A1 proposals include estimated ETAs, deadline misses,
  assignment changes, and infeasibility or unserved-task notes. The
  comparison groups alternatives only when their complete schedules
  match. The human dispatcher approves a revisioned, expiring proposal.
  Idempotent commit, physical route changes, and the mock dispatch
  feed are exercised together in the API test.
- A hospital blocked by a zero-capacity window holds arriving vehicles
  at the SUMO stop. Custody transfers after the dwell and a valid
  receiving slot. The live regression covers waiting across CLI
  save-state and resume. New estimated insertions start at or after
  the current simulation time.
- Results show observed dispatch-to-pickup samples, deadline delays,
  and civilian SUMO time loss. The comparison calculates average and
  nearest-rank P90 from the latest samples for each policy in separate
  replays of the same network/scenario. It keeps observations across
  resets during the current server session.

## Verified commands and runs

```powershell
.\.venv\Scripts\rescueflow.exe doctor
.\.venv\Scripts\python.exe -m pytest tests/ -v
npm --prefix frontend run build
.\.venv\Scripts\python.exe -m uvicorn rescueflow.dashboard.app:app --host 127.0.0.1 --port 8000
npm --prefix frontend run dev -- --port 5173
```

- Doctor passed and all 48 tests passed. The tests used real SUMO for
  Ljubljana report-to-recovery, the training grid, dwell/custody,
  road closure, and hospital handover verification. The Vite production
  build passed TypeScript checking and bundling.
- The browser displayed the live console, validated the example
  Slovenian crew report, and showed two computed repair cards. The
  actual approval, dispatch messages, simulation controls, K1 delivery,
  and replay were verified through the API. Browser input access
  became unavailable during the later visual checks.
- The original CLI breakdown workflow still froze R2, approved a
  feasible B0 plan, and completed K1 recovery on the synthetic grid.
  On Ljubljana, B0 and A1 replays each delivered K1 to H1 and populated
  measured comparison rows. The final proxy smoke run recorded three
  pickups per policy, with a 256.7s average and 403s P90 for each.
  The proxy smoke also exercised playback, road closure and timed
  reopening, approval retry, and reset. The urgent and fog scripts
  produced feasible choices. See the [demo script](DEMO_SCRIPT.md) and
  [API runbook](RUNBOOK.md) for reproduction.

## Boundaries and prompt 4

- Real OpenAI calls were untested without a supplied key. The provider
  SDK was installed and the no-key path was verified. Free text
  currently supports a vehicle breakdown and the same vehicle's
  stranded equipment in one report. Use typed triggers for the other
  scenarios.
- Road ETAs use free-flow estimates on the selected SUMO graph.
  Civilian time loss is the last observed SUMO value per sampled
  vehicle. Helicopter ETA is analytical. The dashboard asks the
  operator to advance past an internal junction edge before rerouting
  or freezing a vehicle there. Synthetic hospital sites are anchored
  to map junctions, and traffic demand is synthetic.
- The demo starts paused at a fixed warm-up point and pauses when
  choices are shown. The response comparison summarizes individual
  replays. Each result can have a different set of observed tasks.
  It does not measure a controlled policy effect.
- Prompt 4 should implement independent, matched-seed Suite L and T
  runs from serialized starting conditions and add Suite F when
  appropriate. Use identical demand and declared decision delays,
  check trip/stop completion and signal restoration, and derive CSVs,
  plots and honest comparisons from recorded results. OR-Tools and
  optional repositioning remain deferred. See the
  [development plan](DEVELOPMENT_PLAN.md).
