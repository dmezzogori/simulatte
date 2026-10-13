import {describe, expect, it} from 'vitest';
import {MAX_OVERLAY, mergeSeries, seriesInputs} from '../src/overlay-charts';
import {WelchModel, uniformGrid, welch} from '../src/welch';

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
  it('builds a uniform grid over the replications time span', () => {
    expect(uniformGrid([a, [[1, 1], [3, 1]]], 4)).toEqual([0, 1, 2, 3]);
    expect(uniformGrid([[[5, 1]]], 10)).toEqual([5]);
    expect(uniformGrid([], 10)).toEqual([]);
  });
});
