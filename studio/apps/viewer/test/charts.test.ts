import {describe, expect, it} from 'vitest';
import {MAX_OVERLAY, mergeSeries, seriesInputs} from '../src/overlay-charts';
import {WelchModel, finitePoints, uniformGrid, welch} from '../src/welch';
import {welchAttempts} from '../src/experiment-charts';
import type {ExperimentReport, ReplicationEntry} from '../src/studio-client';

describe('overlay chart data', () => {
  it('merges two recordings on one shared x-scale with previous-value steps inside each range', () => {
    const merged = mergeSeries([
      {label: 'a', points: [[0, 1], [2, 3]], truncated: false},
      {label: 'b', points: [[1, 5], [2, 6], [3, 7]], truncated: false},
    ]);
    expect(merged.x).toEqual([0, 1, 2, 3]);
    expect(merged.y).toEqual([[1, 1, 3, null], [null, 5, 6, 7]]);
  });
  it('keeps the post-event value of duplicate times and gaps for missing values', () => {
    expect(mergeSeries([{label: 'a', points: [[0, 1], [0, 2], [1, null], [2, 4]], truncated: false}]).y).toEqual([[2, null, 4]]);
  });
  it('caps the overlay at eight series', () => {
    const one = {label: 'x', points: [[0, 1]] as [number, number][], truncated: false};
    expect(() => mergeSeries(Array.from({length: MAX_OVERLAY + 1}, () => one))).toThrow(/8/);
  });
  it('labels series.get results by attempt and key and surfaces truncation', () => {
    const inputs = seriesInputs([{attempt: 'c'.repeat(32), series: {
      'cell/count': {points: [[0, 1, 1], [0, 2, 2], [1, 3, null]], truncated: true},
    }}], ['cell/count'], attempt => attempt === 'c'.repeat(32) ? 'n=3/base #0' : attempt);
    expect(inputs).toEqual([{label: 'n=3/base #0 · cell/count (truncated)', points: [[0, 1], [0, 2], [1, null]], truncated: true}]);
  });
});

describe('Welch moving average', () => {
  const a: [number, number][] = [[0, 0], [2, 10]], b: [number, number][] = [[0, 2], [2, 4]];
  const grid = [0, 1, 2, 3, 4];
  it('averages replications with previous values on the grid, then smooths with a centred window', () => {
    // Previous-value samples: a = 0 0 10 10 10, b = 2 2 4 4 4, mean = 1 1 7 7 7 (as the Python test).
    expect(welch([a, b], {window: 1, grid})).toEqual([[0, 1], [1, 1], [2, 7], [3, 7], [4, 7]]);
    expect(welch([a, b], {window: 3, grid})).toEqual([[0, 1], [1, 3], [2, 5], [3, 7], [4, 7]]);
    expect(welch([[[2, 8]], [[0, 2]]], {window: 1, grid: [0, 1, 2]})).toEqual([[0, 2], [1, 2], [2, 5]]);
    expect(welch([], {window: 1, grid: [0]})).toEqual([]);
    for (const window of [0, 2, -1, 1.5]) expect(() => welch([a], {window, grid})).toThrow(/window/);
    expect(() => welch([[[0, Number.NaN]]], {window: 1, grid})).toThrow(/finite/);
  });
  it('recomputes from the fetched series when the window changes', () => {
    const model = new WelchModel([a, b], grid);
    expect(model.points(1).map(([, value]) => value)).toEqual([1, 1, 7, 7, 7]);
    expect(model.points(3).map(([, value]) => value)).toEqual([1, 3, 5, 7, 7]);
    expect(model.replications).toBe(2);
  });
  it('drops null samples so a KPI undefined before its first observation still plots', () => {
    // A mean over zero completions is null until the first completion at t = 1.
    const early = finitePoints([[0, 1, null], [1, 2, 4], [2, 3, null], [3, 4, 8]]);
    expect(early).toEqual([[1, 4], [3, 8]]);
    const model = new WelchModel([early, finitePoints([[0, 1, 2], [2, 2, 6]])], [0, 1, 2, 3]);
    expect(model.points(1)).toEqual([[0, 2], [1, 3], [2, 5], [3, 7]]);
  });
  it('names the configuration and the listing cap when no committed replication is listed', () => {
    const entry = (configuration: number, status: string, attempt: string | null): ReplicationEntry => ({configuration, index: 0, status, attempt, trace: null});
    const report = (replications: ReplicationEntry[], total = replications.length) => ({
      configurations: [{index: 0, label: 'n=3/base'}, {index: 1, label: 'n=4/base'}], replications, replications_total: total,
    }) as unknown as ExperimentReport;
    expect(welchAttempts(report([entry(1, 'succeeded', 'a'), entry(1, 'failed', 'b')]), 1)).toEqual({attempts: ['a'], total: 1});
    expect(welchAttempts(report([entry(1, 'pending', null)]), 1))
      .toEqual({attempts: [], total: 0, message: 'n=4/base has no listed committed replication yet'});
    expect(welchAttempts(report([entry(1, 'pending', null)], 1500), 1))
      .toEqual({attempts: [], total: 0, message: 'n=4/base has no committed replication among the first 1 of 1500 listed replications (the report lists at most 1000)'});
  });
  it('builds a uniform grid over the replications time span', () => {
    expect(uniformGrid([a, [[1, 1], [3, 1]]], 4)).toEqual([0, 1, 2, 3]);
    expect(uniformGrid([[[5, 1]]], 10)).toEqual([5]);
    expect(uniformGrid([], 10)).toEqual([]);
  });
});
