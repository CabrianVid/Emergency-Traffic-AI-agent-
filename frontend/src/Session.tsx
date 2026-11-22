import { useState } from 'react'
import { BenchmarkResults } from './BenchmarkResults'
import { MonteCarloResults } from './MonteCarloResults'
import { clock, scenarioLabels } from './model'
import type { Network, Scenario, Snapshot } from './model'

/** Training and simulation controls. Out of the way: status bar or F9. */
export function SessionPanel({ state, busy, onControl, onLoad, onInject, onClose, onEvaluation }: {
  state: Snapshot; busy: boolean; onControl: (operation: string, body?: object) => void
  onLoad: (network: Network, scenario: Scenario) => void; onInject: (kind: string) => void; onClose: () => void; onEvaluation: () => void
}) {
  const playing = state.simulator === 'running'
  const script = state.scenario === 'breakdown' ? 'Scripted: R2 breaks down' : 'Scripted: urgent patient'
  const [network, setNetwork] = useState<Network>(state.network)
  const [scenario, setScenario] = useState<Scenario>(state.scenario)
  return <section className="session" aria-label="Training session">
    <header><h2>Training session</h2><button aria-label="Close" onClick={onClose}>×</button></header>
    <p>Simulated traffic, units and calls. Elapsed {clock(state.world?.sim_time_s)}.</p>
    <div className="session-row">
      <button disabled={busy} onClick={() => onControl(playing ? 'pause' : 'play')}>{playing ? 'Pause' : 'Run'}<kbd>Space</kbd></button>
      <button disabled={busy || playing} onClick={() => onControl('step', { steps: 30 })}>+30 s</button>
      <button disabled={busy} onClick={() => onControl('reset')}>Restart</button>
      <select aria-label="Speed" value={state.speed} disabled={busy} onChange={(event) => onControl('speed', { speed: Number(event.target.value) })}>
        {[1, 5, 15, 30].map((s) => <option key={s} value={s}>{s}× speed</option>)}</select>
    </div>
    <h3>Send an incident</h3>
    <div className="session-col">
      <button disabled={busy} onClick={() => onInject('new_emergency')}>Emergency call<kbd>F2</kbd></button>
      <button disabled={busy} onClick={() => onInject('equipment_request')}>Equipment request<kbd>F4</kbd></button>
      <button disabled={busy || state.script_applied} onClick={() => onInject('scenario')}>{script}<kbd>F8</kbd></button>
    </div>
    <h3>Exercise</h3>
    <div className="session-col">
      <label>Area <select value={network} onChange={(event) => setNetwork(event.target.value as Network)}>
        <option value="ljubljana">Ljubljana</option><option value="grid">Training grid</option></select></label>
      <label>Script <select value={scenario} onChange={(event) => setScenario(event.target.value as Scenario)}>
        {Object.entries(scenarioLabels).map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></label>
      <button disabled={busy} onClick={() => onLoad(network, scenario)}>Load exercise</button>
    </div>
    <div className="session-col"><button onClick={onEvaluation}>Evaluation results…</button></div>
  </section>
}

export function Evaluation({ state, onClose }: { state: Snapshot; onClose: () => void }) {
  const { metrics } = state
  return <div className="dialog-back" onClick={(event) => { if (event.target === event.currentTarget) onClose() }}>
    <section className="dialog" role="dialog" aria-label="Evaluation results">
      <header><h2>Evaluation results</h2><button aria-label="Close" onClick={onClose}>×</button></header>
      <div className="dialog-body">
        <MonteCarloResults/>
        <h3>This training session</h3>
        <p>{metrics.civilian_observed} civilian vehicles observed, {clock(metrics.civilian_time_loss_s)} total time loss.
          {Object.entries(metrics.responses_s).map(([task, time]) => ` ${task} reached ${clock(time)} after the order.`)}
          {Object.entries(metrics.deadline_delays_s).map(([task, time]) => ` ${task} ${clock(time)} late.`)}</p>
        <details><summary>Older benchmark (policy names differ: A2 was signals only, A3 combined)</summary><BenchmarkResults/></details>
      </div>
    </section>
  </div>
}
