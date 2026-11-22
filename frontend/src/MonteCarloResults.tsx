import { useEffect, useState } from 'react'
import { api } from './connection'

type Policy = {
  completed_runs: number; valid_runs: number; calls: number; reached: number
  mean_capped_s: number; critical_mean_capped_s: number; within_target_pct: number; p90_reached_s: number
  critical_unreached: number; civilian_mean_time_loss_s: number
}
type Pair = {
  reference: string; candidate: string; valid_pairs: number; mean_seconds_saved: number
  ci95: [number, number]; better: number; same: number; worse: number
}
type Scenario = {
  seed: number; density: string; events: string[]
  policies: Record<string, { valid: boolean; mean_capped_s: number; unreached: number; calls: number }>
}
type Report = { response_cap_s: number; policies: Record<string, Policy>; pairs: Pair[]; scenarios: Scenario[] }

const NAMES: Record<string, string> = {
  B0: 'B0 · Nearest unit', B1: 'B1 · Central queue', R1: 'R1 · Optimizer, no AI',
  A1: 'A1 · AI dispatch', A2: 'A2 · AI + signals',
}
const ORDER = Object.keys(NAMES)
const SORTS = { seed: 'Scenario number', gain: 'AI dispatch best vs B1', loss: 'AI dispatch worst vs B1' }

function gain(scenario: Scenario) {
  const { A1, B1 } = scenario.policies
  return A1?.valid && B1?.valid ? B1.mean_capped_s - A1.mean_capped_s : 0
}

export function MonteCarloResults() {
  const [report, setReport] = useState<Report | null>(null)
  const [error, setError] = useState('')
  const [sort, setSort] = useState<keyof typeof SORTS>('seed')
  const [first, setFirst] = useState(0)
  const [status, setStatus] = useState('')
  useEffect(() => {
    let open = true
    api<Report>('/api/benchmarks/montecarlo')
      .then((result) => { if (open) setReport(result) })
      .catch((failure: Error) => { if (open) setError(failure.message) })
    return () => { open = false }
  }, [])
  if (!report) return <p className="bench-note">{error || 'Loading benchmark…'}</p>
  const policies = ORDER.filter((name) => report.policies[name]?.completed_runs)
  const listed = report.scenarios.slice().sort((a, b) =>
    sort === 'gain' ? gain(b) - gain(a) : sort === 'loss' ? gain(a) - gain(b) : a.seed - b.seed)
  const shown = sort === 'seed' ? listed.filter((s) => s.seed >= first).slice(0, 8) : listed.slice(0, 8)

  async function replay(seed: number, policy: string) {
    setStatus(`Opening scenario ${seed} ${policy} in SUMO…`)
    try {
      await api('/api/benchmarks/montecarlo/replay', { seed, policy })
      setStatus(`Scenario ${seed} ${policy} is open in the SUMO window.`)
    } catch (failure) { setStatus((failure as Error).message) }
  }

  return <div className="bench-results">
    <h3>Matched Monte Carlo benchmark</h3>
    <p>Synthetic grid · identical frozen inputs per scenario · response capped at {report.response_cap_s} s; a call nobody reaches counts as {report.response_cap_s} s</p>
    <div className="bench-scroll"><table>
      <thead><tr><th>Policy</th><th>Valid runs</th><th>Mean</th><th>Critical mean</th><th>P90 reached</th><th>Within 300 s</th><th>Not reached</th></tr></thead>
      <tbody>{policies.map((name) => { const row = report.policies[name]; return <tr key={name}>
        <th scope="row">{NAMES[name]}</th><td>{row.valid_runs}/{row.completed_runs}</td>
        <td>{row.mean_capped_s.toFixed(0)} s</td><td>{row.critical_mean_capped_s.toFixed(0)} s</td>
        <td>{row.p90_reached_s.toFixed(0)} s</td><td>{row.within_target_pct.toFixed(1)}%</td>
        <td>{row.calls - row.reached}/{row.calls}</td></tr> })}</tbody>
    </table></div>
    <h3>Paired differences</h3>
    <p>Positive = the second policy is faster. 95% bootstrap interval over scenarios.</p>
    <div className="bench-scroll"><table>
      <thead><tr><th>Comparison</th><th>Seconds saved</th><th>95% interval</th><th>Better / same / worse</th></tr></thead>
      <tbody>{report.pairs.map((row) => <tr key={row.reference + row.candidate}>
        <th scope="row">{row.reference} → {row.candidate}</th><td>{row.mean_seconds_saved.toFixed(1)} s</td>
        <td>{row.ci95[0].toFixed(1)} to {row.ci95[1].toFixed(1)}</td><td>{row.better} / {row.same} / {row.worse}</td></tr>)}</tbody>
    </table></div>
    <h3>Replay a recorded scenario</h3>
    <p className="bench-controls">
      <label>List by <select value={sort} onChange={(e) => setSort(e.target.value as keyof typeof SORTS)}>
        {Object.entries(SORTS).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
      </select></label>
      {sort === 'seed' && <label>From <input type="number" min={0} max={report.scenarios.length - 1} value={first}
        onChange={(e) => setFirst(Number(e.target.value) || 0)}/></label>}
    </p>
    <div className="bench-scroll"><table>
      <thead><tr><th>Scenario</th>{policies.map((name) => <th key={name}>{name}</th>)}</tr></thead>
      <tbody>{shown.map((s) => <tr key={s.seed}><th scope="row">#{s.seed} · {s.density}</th>
        {policies.map((name) => { const run = s.policies[name]; return <td key={name}>
          {run && <button className="replay-button" disabled={!run.valid} title={run.valid ? `Open scenario ${s.seed} ${name} in SUMO` : 'This run failed'}
            onClick={() => replay(s.seed, name)}>{run.valid ? `▶ ${run.mean_capped_s.toFixed(0)} s` : 'failed'}</button>}</td> })}</tr>)}</tbody>
    </table></div>
    <p className="bench-note" role="status">{status || 'Opens a SUMO window on this computer. Replays recorded AI answers; no AI call is made.'}</p>
  </div>
}
