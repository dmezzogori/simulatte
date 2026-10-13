import {useState} from 'react';
import type {Estimate, ExperimentReport} from './studio-client';

export interface ResultsOptions {level: number; bonferroni: boolean; baseline: string | null;}
const MIN_LEVEL = 0.8, MAX_LEVEL = 0.999;
const number = (value: number | null | undefined) => value === null || value === undefined ? '—' : String(Number(value.toPrecision(6)));
const percent = (level: number) => String(Number((level * 100).toFixed(4)));
const label = (pointwise: boolean, level: number, m: number, effective: number) => pointwise
  ? `pointwise ${percent(level)} % t intervals, no multiple-comparison correction`
  : `Bonferroni-adjusted (m = ${m}, effective level ${number(effective)})`;
/** The interval family of the configuration table (D98). */
export const intervalLabel = (report: ExperimentReport) => label(report.pointwise, report.level, report.m, report.effective_level);
/** The interval family of the paired differences against the baseline. */
export const pairedLabel = (report: ExperimentReport) =>
  label(report.pointwise, report.level, report.paired_m ?? 0, report.paired_effective_level ?? report.level);
const Badge = ({state}: {state: string}) => <span className={`badge state-${state}`} title={state === 'degenerate'
  ? 'All replications equal: zero-width interval' : state === 'insufficient' ? 'Fewer than two replications: no interval' : undefined}>{state}</span>;
const cells = (estimate: Estimate) => <><td>{number(estimate.mean)}</td><td>{estimate.half_width === null ? '—' : `± ${number(estimate.half_width)}`}</td></>;

/** Configuration × KPI estimates with paired differences against a chosen baseline (design 5). */
export function ResultsTable({report, options, onOptions, busy = false}: {report: ExperimentReport; options: ResultsOptions; onOptions(options: ResultsOptions): void; busy?: boolean}) {
  const [level, setLevel] = useState(String(options.level));
  const parsed = Number(level);
  const levelValid = /^0\.[0-9]+$/.test(level) && parsed >= MIN_LEVEL && parsed <= MAX_LEVEL;
  const paired = new Map((report.paired ?? []).map(item => [`${item.configuration}\u0000${item.kpi}`, item]));
  const comparing = report.baseline !== undefined;
  const labels = report.configurations.map(item => item.label);
  return <div className="results-table">
    <div className="row">
      <label>Level <input value={level} inputMode="decimal" aria-invalid={!levelValid} onChange={event => setLevel(event.target.value)}
        onBlur={() => {if (levelValid && parsed !== options.level) onOptions({...options, level: parsed});}} /></label>
      <label><input type="checkbox" checked={options.bonferroni} disabled={busy} onChange={event => onOptions({...options, bonferroni: event.target.checked})} /> Bonferroni</label>
      <label>Baseline <select value={options.baseline ?? ''} disabled={busy} onChange={event => onOptions({...options, baseline: event.target.value || null})}>
        <option value="">None</option>
        {labels.map(item => <option key={item} value={item}>{item}</option>)}
      </select></label>
      {!levelValid && <small role="alert">Level must be between {MIN_LEVEL} and {MAX_LEVEL}</small>}
    </div>
    <p className="interval-label">{intervalLabel(report)}</p>
    {comparing && <p className="interval-label">Differences b − a against {report.baseline}: {pairedLabel(report)}</p>}
    <table>
      <thead><tr><th>Configuration</th><th>KPI</th><th>n</th><th>Mean</th><th>Half-width</th><th>State</th>
        {comparing && <><th>b − a</th><th>Half-width</th><th>n</th><th>State</th><th>Missing</th></>}</tr></thead>
      <tbody>{report.rows.map(row => {
        const difference = paired.get(`${row.configuration}\u0000${row.kpi}`);
        return <tr key={`${row.configuration}\u0000${row.kpi}`}>
          <td>{row.label}{report.configurations[row.configuration]?.mixed_runtime && <span className="badge mixed" title="Replications resolved different dependency sets">mixed runtime</span>}</td>
          <td>{row.kpi}</td><td>{row.estimate.n}</td>{cells(row.estimate)}<td><Badge state={row.estimate.state} /></td>
          {comparing && (difference
            ? <>{cells(difference.estimate)}<td>{difference.n}</td><td><Badge state={difference.estimate.state} /></td><td>{difference.missing.length}</td></>
            : <td colSpan={5}>{row.label === report.baseline ? 'baseline' : '—'}</td>)}
        </tr>;
      })}</tbody>
    </table>
    {report.rows.length === 0 && <p>No replication has reported a KPI yet.</p>}
    <details open={report.missing_total > 0}><summary>Missing pairs ({report.missing_total})</summary>
      <p>Replications without a result are excluded from every interval and difference.</p>
      <table><thead><tr><th>Configuration</th><th>Replication</th><th>Status</th></tr></thead>
        <tbody>{report.missing.map(item => <tr key={`${item.configuration}\u0000${item.index}`}>
          <td>{labels[item.configuration] ?? item.configuration}</td><td>{item.index}</td><td>{item.status}</td></tr>)}</tbody></table>
      {report.missing.length < report.missing_total && <small>Showing the first {report.missing.length} of {report.missing_total}.</small>}
    </details>
  </div>;
}
