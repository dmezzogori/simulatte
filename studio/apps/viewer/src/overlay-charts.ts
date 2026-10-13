/** Overlaid KPI series of up to eight recordings on one uPlot with a shared x-scale (design 7).
 * Imperative like `Controller.charts()`: React owns only the host element. */
import uPlot from "uplot";
import type {SeriesEntry} from "./studio-client";

export const MAX_OVERLAY = 8;
export interface OverlayInput {label: string; points: readonly (readonly [number, number | null])[]; truncated: boolean;}
export interface OverlayData {x: number[]; y: (number | null)[][]; labels: string[];}
const COLORS = ["#537461", "#b0563d", "#3d6db0", "#8a5ab0", "#b09a3d", "#3da7b0", "#b03d7e", "#5e5e5e"];

/**
 * Aligns series on the sorted union of their sample times. Inside its own time range a series keeps
 * its previous value (the KPI display rule); outside it, and after a missing value, it has a gap.
 * Of several samples at one time the last (post-event) value wins.
 */
export function mergeSeries(inputs: readonly OverlayInput[]): OverlayData {
  if (inputs.length > MAX_OVERLAY) throw new RangeError(`An overlay shows at most ${MAX_OVERLAY} series`);
  const x = [...new Set(inputs.flatMap(input => input.points.map(([time]) => time)))].sort((a, b) => a - b);
  const y = inputs.map(({points}) => {
    const values: (number | null)[] = new Array(x.length).fill(null);
    if (!points.length) return values;
    const last = points.at(-1)![0];
    let position = 0, current: number | null = null;
    x.forEach((time, index) => {
      if (time < points[0]![0] || time > last) return;
      while (position < points.length && points[position]![0] <= time) {
        const value = points[position++]![1];
        current = value !== null && Number.isFinite(value) ? value : null;
      }
      values[index] = current;
    });
    return values;
  });
  return {x, y, labels: inputs.map(input => input.label)};
}

/** Overlay inputs of a `series.get` response, labelled by recording and key. */
export function seriesInputs(entries: readonly SeriesEntry[], keys: readonly string[], name: (attempt: string) => string): OverlayInput[] {
  return entries.flatMap(entry => keys.flatMap(key => {
    const series = entry.series[key];
    if (!series) return [];
    return [{label: `${name(entry.attempt)} · ${key}${series.truncated ? " (truncated)" : ""}`,
      points: series.points.map(([time, , value]) => [time, value] as const), truncated: series.truncated}];
  }));
}

export class OverlayChart {
  #plot: uPlot | null = null;
  #resize: ResizeObserver;
  constructor(private readonly host: HTMLElement) {
    this.#resize = new ResizeObserver(() => {if (this.#plot && host.clientWidth) this.#plot.setSize({width: host.clientWidth, height: 220});});
    this.#resize.observe(host);
  }
  /** Stepped paths follow the previous-value rule of recorded KPIs; derived curves pass `stepped: false`. */
  render(inputs: readonly OverlayInput[], title: string, {stepped = true}: {stepped?: boolean} = {}): void {
    const data = mergeSeries(inputs);
    this.clear();
    this.#plot = new uPlot({
      width: Math.max(280, this.host.clientWidth), height: 220, title,
      scales: {x: {time: false}},
      series: [{}, ...data.labels.map((label, index) => ({
        label, stroke: COLORS[index % COLORS.length]!, spanGaps: false,
        paths: stepped ? uPlot.paths.stepped!({align: 1}) : uPlot.paths.linear!(),
      }))],
      axes: [{}, {}],
    }, [data.x, ...data.y], this.host);
  }
  clear(): void {this.#plot?.destroy(); this.#plot = null; this.host.replaceChildren();}
  dispose(): void {this.clear(); this.#resize.disconnect();}
}
