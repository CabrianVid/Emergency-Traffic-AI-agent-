import { useEffect, useState } from 'react'
import { api } from './connection'

type Response = {
  policy: string; scenario: string; valid_runs: number; emergencies: number
  response_mean_s: number | null; response_median_s: number | null; response_p90_s: number | null
  within_target_pct: number | null; unserved_emergencies: number
}
type Traffic = {
  scenario: string; policy: string; mean_civilian_time_loss_s: number | null
  mean_civilian_p90_loss_s: number | null; civilian_unfinished: number; preemptions: number
}
type Effect = {
  suite: string; scenario: string; reference: string; candidate: string; label: string
  response_improvement_s: number | null; response_improvement_pct: number | null
  matched_seeds: number; matched_calls: number
}
type BenchmarkReport = {
  label: string; response_target_s: number; run_count: number; invalid_runs: unknown[]
  response: Response[]; traffic: Traffic[]; effects: Effect[]
}
const number = (value: number | null, suffix = '') => value == null ? '—' : `${value.toFixed(1)}${suffix}`
const signed = (value: number | null, suffix = '') => value == null ? '—' : `${value > 0 ? '+' : ''}${number(value, suffix)}`
const policyNames: Record<string, string> = {
  B0: 'B0 · Baseline',
  A1: 'A1 · Optimization',
  A2: 'A2 · Signal priority',
  A3: 'A3 · Combined',
}

export function BenchmarkResults() {
  const [report, setReport] = useState<BenchmarkReport | null>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    let open = true
    api<BenchmarkReport>('/api/benchmarks')
      .then((result) => { if (open) setReport(result) })
      .catch((failure: Error) => { if (open) setError(failure.message) })
    return () => { open = false }
  }, [])
  if (!report) return <p className="bench-note">{error || 'Loading recorded benchmarks…'}</p>
  return <div className="bench-results">
    <h3>Matched ambulance benchmark</h3>
    <p>{report.label} · Training grid · {report.run_count} runs · {report.invalid_runs.length} invalid</p>
    <div className="bench-scroll"><table>
      <caption>Observed response from call report to eligible ambulance arrival</caption>
      <thead><tr><th>Policy</th><th>Valid seeds</th><th>Mean</th><th>Median</th><th>P90</th><th>Within {report.response_target_s}s</th><th>Unserved</th></tr></thead>
      <tbody>{report.response.filter(row => row.scenario === 'urgent').sort((a, b) =>
        ['B0', 'A1', 'A2', 'A3'].indexOf(a.policy) - ['B0', 'A1', 'A2', 'A3'].indexOf(b.policy)).map(row =>
        <tr key={row.policy}><th scope="row">{policyNames[row.policy] ?? row.policy}</th><td>{row.valid_runs}</td>
          <td>{number(row.response_mean_s, ' s')}</td><td>{number(row.response_median_s, ' s')}</td>
          <td>{number(row.response_p90_s, ' s')}</td><td>{number(row.within_target_pct, '%')}</td>
          <td>{row.unserved_emergencies}/{row.emergencies}</td></tr>)}</tbody>
    </table></div>
    <h3>Contribution by feature</h3>
    <p>Positive values are seconds saved. Comparisons use calls reached by both policies.</p>
    <div className="bench-scroll"><table>
      <thead><tr><th>Comparison</th><th>Mean saved</th><th>Relative</th><th>Matched seeds / calls</th></tr></thead>
      <tbody>{report.effects.filter(row => row.suite === 'logistics' && row.scenario === 'urgent').map(row =>
        <tr key={row.label}><th scope="row">{row.label} · {row.reference} → {row.candidate}</th>
          <td>{signed(row.response_improvement_s, ' s')}</td><td>{signed(row.response_improvement_pct, '%')}</td>
          <td>{row.matched_seeds} / {row.matched_calls}</td></tr>)}</tbody>
    </table></div>
    <h3>Civilian effects in the frozen-route experiment</h3>
    <div className="bench-scroll"><table><thead>
      <tr><th>Condition</th><th>Policy</th><th>Mean time loss</th><th>Mean run P90</th><th>Unfinished</th><th>Phases shortened</th></tr>
    </thead><tbody>{report.traffic.map(row =>
      <tr key={`${row.scenario}-${row.policy}`}><th scope="row">{row.scenario.replace('-', ' ')}</th><td>{row.policy}</td>
        <td>{number(row.mean_civilian_time_loss_s, ' s')}</td><td>{number(row.mean_civilian_p90_loss_s, ' s')}</td>
        <td>{row.civilian_unfinished}</td><td>{row.preemptions}</td></tr>)}</tbody></table></div>
  </div>
}
