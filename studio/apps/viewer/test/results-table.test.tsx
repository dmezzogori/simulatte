import {describe, expect, it} from 'vitest';
import {renderToStaticMarkup} from 'react-dom/server';
import {ResultsTable, intervalLabel, pairedLabel} from '../src/results-table';
import type {ExperimentReport} from '../src/studio-client';

const estimate = (n: number, mean: number | null, half_width: number | null, state: string, level = 0.95) =>
  ({n, mean, sd: half_width === null ? null : 1, half_width, lower: null, upper: null, state, level});
const report: ExperimentReport = {
  run_id: 'a'.repeat(64), name: 'sweep', status: 'completed', level: 0.95, effective_level: 0.95, pointwise: true, m: 3,
  configurations: [
    {index: 0, label: 'n=3/base', parameters: {n: 3}, counts: {succeeded: 3}, mixed_runtime: false},
    {index: 1, label: 'n=4/base', parameters: {n: 4}, counts: {succeeded: 2, failed: 1}, mixed_runtime: true},
  ],
  rows: [
    {configuration: 0, label: 'n=3/base', kpi: 'cell/count', n_succeeded: 3, estimate: estimate(3, 8.5, 1.27, 'ok')},
    {configuration: 1, label: 'n=4/base', kpi: 'cell/count', n_succeeded: 2, estimate: estimate(2, 9.25, 0, 'degenerate')},
    {configuration: 1, label: 'n=4/base', kpi: 'cell/other', n_succeeded: 2, estimate: estimate(1, 4, null, 'insufficient')},
  ],
  missing: [{configuration: 1, index: 2, status: 'failed', attempt: 'b'.repeat(32)}], missing_total: 1,
  replications: [], replications_total: 0,
  baseline: 'n=3/base', paired_m: 2, paired_effective_level: 0.95,
  paired: [{configuration: 1, label: 'n=4/base', kpi: 'cell/count', n: 2, estimate: estimate(2, 0.75, 0.5, 'ok'), missing: [2]}],
};
const options = {level: 0.95, bonferroni: false, baseline: 'n=3/base'};
const html = (value: ExperimentReport) => renderToStaticMarkup(<ResultsTable report={value} options={options} onOptions={() => {}} />);

describe('results table', () => {
  it('renders n, mean, half-width and a state badge per configuration and KPI', () => {
    const markup = html(report);
    for (const text of ['n=3/base', 'n=4/base', 'cell/count', 'cell/other', '8.5', '1.27', '9.25'])
      expect(markup).toContain(text);
    expect(markup).toMatch(/class="badge state-ok"[^>]*>ok</);
    expect(markup).toMatch(/class="badge state-degenerate"[^>]*>degenerate</);
    expect(markup).toMatch(/class="badge state-insufficient"[^>]*>insufficient</);
    expect(markup).toContain('mixed runtime');
  });
  it('labels pointwise and Bonferroni-adjusted intervals exactly', () => {
    expect(intervalLabel(report)).toBe('pointwise 95 % t intervals, no multiple-comparison correction');
    expect(intervalLabel({...report, level: 0.9, effective_level: 0.9})).toBe('pointwise 90 % t intervals, no multiple-comparison correction');
    expect(intervalLabel({...report, level: 0.999, effective_level: 0.999})).toBe('pointwise 99.9 % t intervals, no multiple-comparison correction');
    const adjusted = {...report, pointwise: false, effective_level: 1 - 0.05 / 3, paired_effective_level: 0.975};
    expect(intervalLabel(adjusted)).toBe('Bonferroni-adjusted (m = 3, effective level 0.983333)');
    expect(pairedLabel(adjusted)).toBe('Bonferroni-adjusted (m = 2, effective level 0.975)');
    expect(html(report)).toContain('pointwise 95 % t intervals, no multiple-comparison correction');
    expect(html(adjusted)).toContain('Bonferroni-adjusted (m = 3, effective level 0.983333)');
  });
  it('shows paired differences against the baseline and the missing pairs', () => {
    const markup = html(report);
    expect(markup).toContain('b − a');
    expect(markup).toContain('0.75');
    expect(markup).toContain('Missing pairs (1)');
    expect(markup).toMatch(/n=4\/base[^<]*<\/td><td>2<\/td><td>failed/);
  });
});
