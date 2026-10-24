/** The finding unit: how a replication-level estimate is read. Every number comes from the server; this module only
 * rounds it for display, names it and places it on an axis. */
import type { Estimate } from "./studio-client";

const MINUS = "−";
/** Decimals that keep two significant digits of the half-width: the precision the interval supports. */
export function supportedDecimals(halfWidth: number | null | undefined): number | null {
  if (halfWidth === null || halfWidth === undefined || !Number.isFinite(halfWidth) || halfWidth <= 0) return null;
  return Math.min(12, Math.max(0, 1 - Math.floor(Math.log10(halfWidth))));
}
/** One value as text: ``—`` when missing, a typographic minus, non-finite values named. */
export function formatFinding(
  value: number | null | undefined,
  { decimals = null, full = false, signed = false }: { decimals?: number | null; full?: boolean; signed?: boolean } = {},
): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  if (!Number.isFinite(value)) return value > 0 ? "+∞" : MINUS + "∞";
  let text: string;
  if (full) text = String(Math.abs(value));
  else if (decimals !== null) text = Math.abs(value).toFixed(decimals);
  else if (Number.isInteger(value)) text = String(Math.abs(value));
  else text = String(Number(Math.abs(value).toPrecision(4)));
  // A value that rounds to zero carries no sign.
  const zero = Number(text) === 0;
  const sign = value < 0 && !zero ? MINUS : signed && !zero ? "+" : "";
  return sign + text;
}
export interface FindingText {
  value: string;
  interval: string | null;
}
/** The estimate at the precision its interval supports, with ``[lower, upper]`` when there is an interval. */
export function estimateText(estimate: Estimate, { full = false, signed = false } = {}): FindingText {
  const interval = estimate.state !== "insufficient" && estimate.lower !== null && estimate.upper !== null;
  const decimals = interval ? supportedDecimals(estimate.half_width) : null;
  const options = { decimals, full, signed };
  return {
    value: formatFinding(estimate.mean, options),
    interval: interval && estimate.state !== "degenerate"
      ? `[${formatFinding(estimate.lower, { decimals, full })}, ${formatFinding(estimate.upper, { decimals, full })}]`
      : null,
  };
}
export type Evidence = "excludes" | "includes" | "degenerate" | "insufficient";
/** What a paired difference says about zero; the colour of a difference encodes this and nothing else. */
export function differenceEvidence(estimate: Estimate): Evidence {
  if (estimate.state === "degenerate") return "degenerate";
  if (estimate.state !== "ok" || estimate.lower === null || estimate.upper === null) return "insufficient";
  return estimate.lower > 0 || estimate.upper < 0 ? "excludes" : "includes";
}
/** A plain-language note for an estimate, or ``null`` when the numbers speak for themselves. */
export function estimateNote(estimate: Estimate): string | null {
  if (estimate.state === "insufficient")
    return estimate.n === 1 ? "One replication: no interval" : `${estimate.n} replications: no interval`;
  if (estimate.state === "degenerate") return `All ${estimate.n} replications equal: no interval`;
  const { mean, half_width: halfWidth } = estimate;
  if (mean !== null && halfWidth !== null && mean !== 0 && halfWidth > Math.abs(mean)) return "Interval wider than the estimate";
  return null;
}
export function differenceNote(estimate: Estimate): string {
  switch (differenceEvidence(estimate)) {
    case "excludes":
      return "Interval excludes 0";
    case "includes":
      return "Interval includes 0";
    case "degenerate":
      return estimate.mean === 0 ? "Every pair is equal" : "Every pair differs by the same amount";
    default:
      return estimate.n < 2 ? "Fewer than two pairs: no interval" : "No interval";
  }
}
export interface KpiName {
  key: string;
  title: string;
  scope: string | null;
}
/** ``shopfloor/jobs_in_system`` reads as "Jobs in system", scoped to ``shopfloor``; the key stays visible as the code name. */
export function kpiName(key: string): KpiName {
  const cut = key.lastIndexOf("/");
  const leaf = cut >= 0 ? key.slice(cut + 1) : key;
  const words = leaf.replace(/[_.-]+/g, " ").trim();
  return { key, title: words ? words.charAt(0).toUpperCase() + words.slice(1) : key, scope: cut > 0 ? key.slice(0, cut) : null };
}
export interface Axis {
  min: number;
  max: number;
  ticks: number[];
  at(value: number): number;
}
const step = (span: number, count: number) => {
  const raw = span / Math.max(1, count);
  const power = 10 ** Math.floor(Math.log10(raw));
  const unit = raw / power;
  return (unit >= 5 ? 10 : unit >= 2 ? 5 : unit >= 1 ? 2 : 1) * power;
};
/** A shared axis over the given values, widened to nice ticks; ``zero`` forces 0 into the domain. */
export function niceAxis(values: readonly number[], { zero = false, ticks = 4 }: { zero?: boolean; ticks?: number } = {}): Axis {
  const finite = values.filter(Number.isFinite);
  if (zero) finite.push(0);
  let lo = finite.length ? Math.min(...finite) : 0;
  let hi = finite.length ? Math.max(...finite) : 1;
  if (lo === hi) {
    const pad = lo === 0 ? 1 : Math.abs(lo) * 0.1;
    lo -= pad;
    hi += pad;
  }
  const size = step(hi - lo, ticks);
  const min = Math.floor(lo / size) * size;
  const max = Math.ceil(hi / size) * size;
  const marks: number[] = [];
  for (let v = min; v <= max + size / 2; v += size) marks.push(Number(v.toPrecision(12)));
  return { min, max, ticks: marks, at: (value) => ((value - min) / (max - min)) * 100 };
}
/** Tick labels with the fewest decimals that tell the ticks apart. */
export function tickLabels(ticks: readonly number[]): string[] {
  for (let decimals = 0; decimals <= 8; decimals++) {
    const labels = ticks.map((t) => formatFinding(t, { decimals }));
    if (new Set(labels).size === labels.length) return labels;
  }
  return ticks.map((t) => formatFinding(t));
}
/** Replication marks for a configuration's status counts, in a stable order. */
export const MARK_ORDER = ["succeeded", "reused", "running", "queued", "pending", "failed", "lost", "cancelled"] as const;
export type Mark = "done" | "running" | "waiting" | "failed";
export function replicationMarks(counts: Record<string, number>, cap = 40): { marks: Mark[]; more: number; summary: string } {
  const kind = (status: string): Mark =>
    status === "succeeded" || status === "reused" ? "done" : status === "running" ? "running" : status === "failed" || status === "lost" || status === "cancelled" ? "failed" : "waiting";
  const statuses = [...MARK_ORDER.filter((s) => s in counts), ...Object.keys(counts).filter((s) => !(MARK_ORDER as readonly string[]).includes(s))];
  const marks: Mark[] = [];
  for (const status of statuses) for (let i = 0; i < (counts[status] ?? 0); i++) marks.push(kind(status));
  const total = marks.length;
  const parts = statuses.filter((s) => (counts[s] ?? 0) > 0).map((s) => `${counts[s]} ${s}`);
  return { marks: marks.slice(0, cap), more: Math.max(0, total - cap), summary: `${total} ${total === 1 ? "replication" : "replications"}: ${parts.join(", ")}` };
}
/** Parameters whose values differ across configurations: the factors of a grid, in first-seen order. */
export function factorsOf(configurations: readonly { parameters: Record<string, unknown> }[]): { name: string; values: string[] }[] {
  const seen = new Map<string, string[]>();
  for (const c of configurations)
    for (const [name, value] of Object.entries(c.parameters)) {
      const text = typeof value === "string" ? value : JSON.stringify(value);
      const values = seen.get(name) ?? [];
      if (!values.includes(text)) values.push(text);
      seen.set(name, values);
    }
  return [...seen].filter(([name, values]) => values.length > 1 || configurations.some((c) => !(name in c.parameters))).map(([name, values]) => ({ name, values }));
}
export const parameterText = (value: unknown) => (typeof value === "string" ? value : JSON.stringify(value));
