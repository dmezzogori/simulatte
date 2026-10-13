/** Welch moving average for warm-up inspection (D99), the algorithm of Python `simulatte.statistics.welch`. */
export type Point = readonly [number, number];

/** Compensated (Neumaier) sum, close to Python `math.fsum` for display purposes. */
function sum(values: readonly number[]): number {
  let total = 0, compensation = 0;
  for (const value of values) {
    const next = total + value;
    compensation += Math.abs(total) >= Math.abs(value) ? total - next + value : value - next + total;
    total = next;
  }
  return total + compensation;
}

/** Index of the first time greater than `time`, like `bisect.bisect_right`. */
function after(times: readonly number[], time: number): number {
  let low = 0, high = times.length;
  while (low < high) {
    const middle = (low + high) >>> 1;
    if (times[middle]! <= time) low = middle + 1; else high = middle;
  }
  return low;
}

/**
 * Averages replications on `grid` with previous-value sampling (a grid point before a replication's
 * first sample is skipped for it; a point no replication covers is dropped), then applies a centred
 * moving average of `window` points that uses the available points at the edges.
 */
export function welch(series: readonly (readonly Point[])[], {window, grid}: {window: number; grid: readonly number[]}): Point[] {
  if (!Number.isInteger(window) || window < 1 || window % 2 === 0) throw new RangeError(`window must be an odd positive integer, got ${window}`);
  const sampled = series.map(replication => {
    for (const [, value] of replication) if (!Number.isFinite(value)) throw new RangeError(`values must be finite numbers, got ${value}`);
    return {times: replication.map(([time]) => time), values: replication.map(([, value]) => value)};
  });
  const averages: Point[] = [];
  for (const time of grid) {
    const found: number[] = [];
    for (const {times, values} of sampled) {
      const position = after(times, time);
      if (position) found.push(values[position - 1]!);
    }
    if (found.length) averages.push([time, sum(found) / found.length]);
  }
  const half = window >> 1;
  return averages.map(([time], index) => {
    const span = averages.slice(Math.max(0, index - half), index + half + 1).map(([, value]) => value);
    return [time, sum(span) / span.length] as const;
  });
}

/** A replication's finite samples of `series.get` points (`[t, seq, value]`). A null (an undefined KPI,
 * such as a mean before the first completion) contributes nothing; with previous-value sampling the
 * replication enters the average at its first finite sample. */
export function finitePoints(points: readonly (readonly [number, number, number | null])[]): Point[] {
  return points.flatMap(([time, , value]) => value !== null && Number.isFinite(value) ? [[time, value] as const] : []);
}

/** `count` evenly spaced times from the earliest to the latest sample of any replication. */
export function uniformGrid(series: readonly (readonly Point[])[], count: number): number[] {
  const times = series.flatMap(replication => replication.length ? [replication[0]![0], replication.at(-1)![0]] : []);
  if (!times.length) return [];
  const start = Math.min(...times), end = Math.max(...times);
  if (start === end || count < 2) return [start];
  return Array.from({length: count}, (_, index) => index === count - 1 ? end : start + (end - start) * index / (count - 1));
}

/** Fetched replication series of one configuration; changing the window recomputes without refetching. */
export class WelchModel {
  readonly #series: readonly (readonly Point[])[];
  readonly #grid: readonly number[];
  constructor(series: readonly (readonly Point[])[], grid: readonly number[]) {this.#series = series; this.#grid = grid;}
  get replications(): number {return this.#series.length;}
  points(window: number): Point[] {return welch(this.#series, {window, grid: this.#grid});}
}
