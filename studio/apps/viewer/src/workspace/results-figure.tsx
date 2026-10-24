/** The study results as a forest plot: one block per KPI, configurations as rows on a shared axis, paired differences
 * against the baseline drawn around zero. The matrix view lists the same estimates for large grids. Every number
 * comes from ``comparison.get``; the page only rounds and places it. */
import { useId, useMemo, useState, type ReactNode } from "react";
import {
  differenceEvidence,
  differenceNote,
  estimateNote,
  estimateText,
  factorsOf,
  kpiName,
  niceAxis,
  parameterText,
  replicationMarks,
  tickLabels,
  type Axis,
} from "../findings";
import { intervalLabel, pairedLabel, type ResultsOptions } from "../results-table";
import type { ConfigurationSummary, Estimate, ExperimentReport } from "../studio-client";
import "./findings.css";

export interface FigureSource {
  report: ExperimentReport;
  /** Configuration indexes to show, or ``null`` for all of them. */
  configurations?: ReadonlySet<number> | null;
  /** A heading for this run's rows when several runs share the figure. */
  title?: string;
}
interface Entry {
  id: string;
  source: number;
  configuration: ConfigurationSummary;
  baseline: boolean;
}
interface Paired {
  n: number;
  estimate: Estimate;
  missing: number[];
}
/** Above this many configurations the figure shows one slice of the grid at a time. */
export const SLICE_ABOVE = 8;
const MIN_LEVEL = 0.8, MAX_LEVEL = 0.999;
const key = (source: number, configuration: number, kpi = "") => `${source}\u0000${configuration}\u0000${kpi}`;

function useModel(sources: readonly FigureSource[]) {
  return useMemo(() => {
    const entries: Entry[] = [];
    const estimates = new Map<string, Estimate>();
    const paired = new Map<string, Paired>();
    const kpis: string[] = [];
    sources.forEach(({ report, configurations = null }, source) => {
      for (const c of report.configurations)
        if (configurations === null || configurations.has(c.index))
          entries.push({ id: key(source, c.index), source, configuration: c, baseline: report.baseline === c.label });
      for (const row of report.rows) {
        if (!kpis.includes(row.kpi)) kpis.push(row.kpi);
        estimates.set(key(source, row.configuration, row.kpi), row.estimate);
      }
      for (const p of report.paired ?? []) paired.set(key(source, p.configuration, p.kpi), p);
    });
    return { entries, estimates, paired, kpis };
  }, [sources]);
}
function Marks({ counts }: { counts: Record<string, number> }) {
  const { marks, more, summary } = replicationMarks(counts);
  return (
    <span className="fp-marks" role="img" aria-label={summary} title={summary}>
      {marks.map((m, i) => (
        <i key={i} className={"fp-mark " + m} />
      ))}
      {more > 0 && <small>+{more}</small>}
    </span>
  );
}
function EntryLabel({ entry, sources, marks = true }: { entry: Entry; sources: readonly FigureSource[]; marks?: boolean }) {
  const title = sources.length > 1 ? sources[entry.source]?.title : undefined;
  return (
    <>
      <span className="fp-label">
        {title && <small className="fp-run">{title}</small>}
        <span>{entry.configuration.label}</span>
        {entry.baseline && <span className="fp-own">Baseline</span>}
        {entry.configuration.mixed_runtime && (
          <span className="ws-badge amber" title="Replications resolved different dependency sets">
            mixed runtime
          </span>
        )}
      </span>
      {marks && <Marks counts={entry.configuration.counts} />}
    </>
  );
}
function Interval({ axis, estimate, difference = false }: { axis: Axis; estimate: Estimate; difference?: boolean }) {
  if (estimate.mean === null || !Number.isFinite(estimate.mean)) return null;
  const at = (v: number) => Math.min(100, Math.max(0, axis.at(v)));
  const evidence = difference ? differenceEvidence(estimate) : null;
  const interval = estimate.state === "ok" && estimate.lower !== null && estimate.upper !== null;
  return (
    <>
      {interval && <span className="fp-ci" style={{ left: `${at(estimate.lower!)}%`, width: `${at(estimate.upper!) - at(estimate.lower!)}%` }} />}
      <span className={difference ? "fp-diamond " + evidence : "fp-point " + estimate.state} style={{ left: `${at(estimate.mean)}%` }} />
    </>
  );
}
function AxisRow({ axis, label, zero = false }: { axis: Axis; label: string; zero?: boolean }) {
  const labels = tickLabels(axis.ticks);
  return (
    <tr className="fp-axis-row" aria-hidden="true">
      <td colSpan={2} className="fp-axis-name">
        {label}
      </td>
      <td className="fp-plot">
        <span className="fp-axis">
          {axis.ticks.map((t, i) => (
            <span key={i} className={"fp-tick" + (zero && t === 0 ? " zero" : "")} style={{ left: `${axis.at(t)}%` }}>
              {labels[i]}
            </span>
          ))}
        </span>
      </td>
    </tr>
  );
}
function EstimateCell({ estimate, full, note, signed = false }: { estimate: Estimate | undefined; full: boolean; note: string | null; signed?: boolean }) {
  if (!estimate) return <td className="fp-estimate">—<small>No result reported</small></td>;
  const text = estimateText(estimate, { full, signed });
  return (
    <td className="fp-estimate">
      <span className="fp-value">{text.value}</span>
      {text.interval && <span className="fp-interval"> {text.interval}</span>}
      <small>
        n = {estimate.n}
        {note ? ` · ${note}` : ""}
      </small>
    </td>
  );
}
function KpiBlock({
  kpi,
  entries,
  context,
  sources,
  estimates,
  paired,
  full,
  unit,
}: {
  kpi: string;
  entries: readonly Entry[];
  context: readonly Entry[];
  sources: readonly FigureSource[];
  estimates: Map<string, Estimate>;
  paired: Map<string, Paired>;
  full: boolean;
  unit: string | null;
}) {
  const id = useId();
  const name = kpiName(kpi);
  const of = (e: Entry) => estimates.get(key(e.source, e.configuration.index, kpi));
  const bounds = (list: readonly (Estimate | undefined)[]) =>
    list.flatMap((e) => (e ? [e.lower, e.upper, e.mean].filter((v): v is number => v !== null && Number.isFinite(v)) : []));
  const axis = niceAxis([...bounds(entries.map(of)), ...context.flatMap((e) => of(e)?.mean ?? [])]);
  const differences = entries
    .filter((e) => !e.baseline)
    .map((e) => ({ entry: e, pair: paired.get(key(e.source, e.configuration.index, kpi)) }))
    .filter((d): d is { entry: Entry; pair: Paired } => d.pair !== undefined);
  const diffAxis = differences.length ? niceAxis(bounds(differences.map((d) => d.pair.estimate)), { zero: true }) : null;
  const baseline = entries.find((e) => e.baseline) ?? context.find((e) => e.baseline);
  return (
    <section className="fp-block" aria-labelledby={id} data-kpi={kpi}>
      <header className="fp-head">
        <h3 id={id}>{name.title}</h3>
        <span className="fp-key">
          {unit && <span className="fp-unit">{unit} · </span>}
          <code>{kpi}</code>
        </span>
      </header>
      <table className="fp-table">
        <caption className="ws-visually-hidden">
          {name.title}: estimates per configuration{differences.length ? " and paired differences against the baseline" : ""}
        </caption>
        <thead className="ws-visually-hidden">
          <tr>
            <th scope="col">Configuration</th>
            <th scope="col">Estimate and interval</th>
            <th scope="col">Plot</th>
          </tr>
        </thead>
        <tbody>
          {entries.map((e) => {
            const estimate = of(e);
            return (
              <tr key={e.id} className={e.baseline ? "fp-row is-baseline" : "fp-row"}>
                <th scope="row">
                  <EntryLabel entry={e} sources={sources} />
                </th>
                <EstimateCell estimate={estimate} full={full} note={estimate ? estimateNote(estimate) : null} />
                <td className="fp-plot" aria-hidden="true">
                  <span className="fp-track">{estimate && <Interval axis={axis} estimate={estimate} />}</span>
                </td>
              </tr>
            );
          })}
          {context.length > 0 && (
            <tr className="fp-row fp-context">
              <th scope="row">
                <span className="fp-label">
                  <span>Other configurations</span>
                </span>
                <small>{context.length} outside this slice</small>
              </th>
              <td className="fp-estimate">
                <small>Point estimates only</small>
              </td>
              <td className="fp-plot" aria-hidden="true">
                <span className="fp-track">
                  {context.map((e) => {
                    const m = of(e)?.mean;
                    return m === null || m === undefined || !Number.isFinite(m) ? null : (
                      <span key={e.id} className="fp-context-tick" style={{ left: `${Math.min(100, Math.max(0, axis.at(m)))}%` }} />
                    );
                  })}
                </span>
              </td>
            </tr>
          )}
          <AxisRow axis={axis} label={unit ?? ""} />
        </tbody>
        {diffAxis && (
          <tbody className="fp-differences">
            <tr className="fp-subhead">
              <th scope="rowgroup" colSpan={3}>
                Difference from {baseline?.configuration.label ?? "the baseline"}, paired by replication
              </th>
            </tr>
            {differences.map(({ entry, pair }) => {
              const evidence = differenceEvidence(pair.estimate);
              return (
                <tr key={entry.id} className={"fp-row fp-difference " + evidence}>
                  <th scope="row">
                    <span className="fp-label fp-minus">
                      <span>{entry.configuration.label}</span>
                      <span className="fp-subtrahend">− {baseline?.configuration.label ?? "baseline"}</span>
                    </span>
                    {pair.missing.length > 0 && (
                      <small>
                        {pair.missing.length} unpaired: replication {pair.missing.join(", ")}
                      </small>
                    )}
                  </th>
                  <EstimateCell estimate={pair.estimate} full={full} note={differenceNote(pair.estimate)} signed />
                  <td className="fp-plot" aria-hidden="true">
                    <span className="fp-track">
                      <span className="fp-zero" style={{ left: `${diffAxis.at(0)}%` }} />
                      <Interval axis={diffAxis} estimate={pair.estimate} difference />
                    </span>
                  </td>
                </tr>
              );
            })}
            <AxisRow axis={diffAxis} label="difference" zero />
          </tbody>
        )}
      </table>
      {differences.length > 0 && (
        <p className="fp-sentence">
          {differences
            .map(({ entry, pair }) => differenceSentence(entry.configuration.label, baseline?.configuration.label ?? "the baseline", name.title.toLowerCase(), unit, pair))
            .join(" ")}
        </p>
      )}
    </section>
  );
}
/** The block's difference in one neutral sentence: the estimate, its interval and what the interval says about zero. */
export function differenceSentence(label: string, baseline: string, kpi: string, unit: string | null, pair: Paired): string {
  const text = estimateText(pair.estimate, { signed: true });
  const evidence = differenceEvidence(pair.estimate);
  const lead = unit ? `${label} minus ${baseline}: ${text.value} ${unit} of ${kpi}` : `${label} minus ${baseline}: ${kpi} differs by ${text.value}`;
  const pairs = `${pair.n} paired ${pair.n === 1 ? "replication" : "replications"}${pair.missing.length ? ` (replication ${pair.missing.join(", ")} unpaired)` : ""}`;
  const said =
    evidence === "excludes" || evidence === "includes"
      ? `, interval ${text.interval!.slice(1, -1).replace(", ", " to ")} ${evidence} 0`
      : evidence === "degenerate"
        ? ", the same in every pair"
        : ", no interval";
  return `${lead}${said}, ${pairs}.`;
}
/** KPIs where no configuration has an interval (all degenerate or single replications), read as one sentence each. */
function WithoutIntervals({ kpis, entries, estimates, full }: { kpis: readonly string[]; entries: readonly Entry[]; estimates: Map<string, Estimate>; full: boolean }) {
  if (!kpis.length) return null;
  return (
    <section className="fp-quiet" aria-label="KPIs without intervals">
      <h3>Without intervals</h3>
      <ul>
        {kpis.map((kpi) => {
          const values = entries.map((e) => ({ e, estimate: estimates.get(key(e.source, e.configuration.index, kpi)) }));
          const same = values.every((v) => v.estimate?.state === "degenerate" && v.estimate.mean === values[0]?.estimate?.mean);
          return (
            <li key={kpi} data-kpi={kpi}>
              <strong>{kpiName(kpi).title}</strong> <code>{kpi}</code> <span className="ws-badge amber">No interval</span>
              <span className="fp-quiet-text">
                {same && values[0]?.estimate
                  ? `Every replication of every configuration is ${estimateText(values[0].estimate, { full }).value}; no interval can be formed.`
                  : values
                      .map(({ e, estimate }) => `${e.configuration.label}: ${estimate ? estimateText(estimate, { full }).value + " (" + (estimateNote(estimate) ?? "") + ")" : "—"}`)
                      .join(" · ")}
              </span>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
type SortState = { kpi: string; direction: "ascending" | "descending" } | null;
function Matrix({
  kpis,
  entries,
  sources,
  estimates,
  paired,
  full,
}: {
  kpis: readonly string[];
  entries: readonly Entry[];
  sources: readonly FigureSource[];
  estimates: Map<string, Estimate>;
  paired: Map<string, Paired>;
  full: boolean;
}) {
  const [sort, setSort] = useState<SortState>(null);
  const rows = useMemo(() => {
    if (!sort) return entries;
    const value = (e: Entry) => estimates.get(key(e.source, e.configuration.index, sort.kpi))?.mean ?? null;
    return [...entries].sort((a, b) => {
      const x = value(a), y = value(b);
      if (x === null || y === null) return x === null ? (y === null ? 0 : 1) : -1;
      return sort.direction === "ascending" ? x - y : y - x;
    });
  }, [entries, sort, estimates]);
  return (
    <div className="ws-table-wrap fp-matrix-wrap">
      <table className="ws-table fp-matrix">
        <caption className="ws-visually-hidden">Estimates per configuration and KPI</caption>
        <thead>
          <tr>
            <th scope="col">Configuration</th>
            {kpis.map((kpi) => {
              const active = sort?.kpi === kpi ? sort.direction : undefined;
              return (
                <th key={kpi} scope="col" aria-sort={active ?? "none"}>
                  <button
                    type="button"
                    className="fp-sort"
                    onClick={() => setSort(active === "ascending" ? { kpi, direction: "descending" } : active === "descending" ? null : { kpi, direction: "ascending" })}
                  >
                    {kpiName(kpi).title}
                    <span aria-hidden="true">{active === "ascending" ? " ↑" : active === "descending" ? " ↓" : ""}</span>
                  </button>
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {rows.map((e) => (
            <tr key={e.id} className={e.baseline ? "is-baseline" : undefined}>
              <th scope="row">
                <EntryLabel entry={e} sources={sources} />
              </th>
              {kpis.map((kpi) => {
                const estimate = estimates.get(key(e.source, e.configuration.index, kpi));
                const pair = paired.get(key(e.source, e.configuration.index, kpi));
                const text = estimate ? estimateText(estimate, { full }) : null;
                const diff = pair ? estimateText(pair.estimate, { full, signed: true }) : null;
                return (
                  <td key={kpi} className="ws-num">
                    {text ? (
                      <>
                        <span className="fp-value">{text.value}</span>
                        {text.interval && <span className="fp-interval"> {text.interval}</span>}
                      </>
                    ) : (
                      "—"
                    )}
                    {diff && pair && (
                      <small className={"fp-matrix-diff " + differenceEvidence(pair.estimate)}>
                        <span className="fp-diamond-inline" aria-hidden="true" />Δ {diff.value}
                        {diff.interval ? " " + diff.interval : ""}
                        <span className="ws-visually-hidden"> {differenceNote(pair.estimate)}</span>
                      </small>
                    )}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
/** The controls the server answers: level, Bonferroni and baseline; plus the reader's own view settings. */
export function ResultsControls({
  report,
  options,
  onOptions,
  busy,
  view,
  onView,
  full,
  onFull,
  labels,
}: {
  report: ExperimentReport;
  options: ResultsOptions;
  onOptions(options: ResultsOptions): void;
  busy: boolean;
  view: "figure" | "matrix";
  onView(view: "figure" | "matrix"): void;
  full: boolean;
  onFull(full: boolean): void;
  labels: readonly string[];
}) {
  const [level, setLevel] = useState(String(options.level));
  const parsed = Number(level);
  const levelValid = /^0\.[0-9]+$/.test(level) && parsed >= MIN_LEVEL && parsed <= MAX_LEVEL;
  return (
    <div className="fp-controls">
      <div className="fp-control-row" role="group" aria-label="Interval settings">
        <label className="fp-inline-field">
          <span>Level</span>
          <input
            value={level}
            inputMode="decimal"
            aria-invalid={!levelValid}
            aria-describedby="fp-level-hint"
            onChange={(event) => setLevel(event.target.value)}
            onBlur={() => {
              if (levelValid && parsed !== options.level) onOptions({ ...options, level: parsed });
            }}
            onKeyDown={(event) => {
              if (event.key === "Enter" && levelValid && parsed !== options.level) onOptions({ ...options, level: parsed });
            }}
          />
        </label>
        <label className="fp-check">
          <input type="checkbox" checked={options.bonferroni} disabled={busy} onChange={(event) => onOptions({ ...options, bonferroni: event.target.checked })} />
          Bonferroni correction
        </label>
        <label className="fp-inline-field">
          <span>Baseline</span>
          <select value={options.baseline ?? ""} disabled={busy} onChange={(event) => onOptions({ ...options, baseline: event.target.value || null })}>
            <option value="">None</option>
            {labels.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
        </label>
        <span className="fp-spacer" />
        <fieldset className="fp-segmented" aria-label="View">
          {(["figure", "matrix"] as const).map((v) => (
            <label key={v} className={view === v ? "is-on" : undefined}>
              <input type="radio" name="fp-view" value={v} checked={view === v} onChange={() => onView(v)} />
              {v === "figure" ? "Figure" : "Matrix"}
            </label>
          ))}
        </fieldset>
        <label className="fp-check">
          <input type="checkbox" checked={full} onChange={(event) => onFull(event.target.checked)} />
          Full digits
        </label>
      </div>
      {!levelValid && (
        <small role="alert" id="fp-level-hint">
          Level must be between {MIN_LEVEL} and {MAX_LEVEL}
        </small>
      )}
      <p className="fp-convention">
        {report.baseline !== undefined && pairedLabel(report) === intervalLabel(report) ? (
          <>
            <strong>{intervalLabel(report)}</strong>, for estimates and for differences against {report.baseline}
          </>
        ) : (
          <>
            <strong>{intervalLabel(report)}</strong>
            {report.baseline !== undefined && <> · differences against {report.baseline}: {pairedLabel(report)}</>}
          </>
        )}
        {!full && " · values rounded to the precision their interval supports"}
      </p>
    </div>
  );
}
/** Factor selects for a grid: each factor shows every value or one. */
function SliceControls({ factors, slice, onSlice, shown, total }: { factors: { name: string; values: string[] }[]; slice: Record<string, string | null>; onSlice(slice: Record<string, string | null>): void; shown: number; total: number }) {
  return (
    <div className="fp-slice" role="group" aria-label="Grid slice">
      {factors.map((f) => (
        <label key={f.name} className="fp-inline-field">
          <span>{f.name}</span>
          <select value={slice[f.name] ?? ""} onChange={(event) => onSlice({ ...slice, [f.name]: event.target.value || null })}>
            <option value="">All values</option>
            {f.values.map((v) => (
              <option key={v} value={v}>
                {v}
              </option>
            ))}
          </select>
        </label>
      ))}
      <p className="ws-hint" role="status">
        Showing {shown} of {total} configurations; the others stay on each axis as grey ticks.
      </p>
    </div>
  );
}
export function ResultsPanel({
  sources,
  options,
  onOptions,
  busy = false,
  units = {},
  children,
}: {
  sources: readonly FigureSource[];
  options: ResultsOptions;
  onOptions(options: ResultsOptions): void;
  busy?: boolean;
  /** KPI units when the data carries them. */
  units?: Record<string, string>;
  children?: ReactNode;
}) {
  const { entries, estimates, paired, kpis } = useModel(sources);
  const [view, setView] = useState<"figure" | "matrix">("figure");
  const [full, setFull] = useState(false);
  const factors = useMemo(() => factorsOf(entries.map((e) => e.configuration)), [entries]);
  const sliced = entries.length > SLICE_ABOVE && factors.length > 0;
  const [slice, setSlice] = useState<Record<string, string | null>>(() =>
    Object.fromEntries(factors.map((f, i) => [f.name, i === 0 ? null : (f.values[0] ?? null)])),
  );
  const inSlice = (e: Entry) =>
    !sliced || factors.every((f) => slice[f.name] === null || slice[f.name] === undefined || parameterText(e.configuration.parameters[f.name]) === slice[f.name]);
  const shown = entries.filter(inSlice);
  const context = sliced ? entries.filter((e) => !inSlice(e)) : [];
  const first = sources[0]?.report;
  if (!first) return null;
  const labels = [...new Set(sources.flatMap((s) => s.report.configurations.map((c) => c.label)))];
  const withInterval = (kpi: string) => entries.some((e) => {
    const estimate = estimates.get(key(e.source, e.configuration.index, kpi));
    return estimate?.state === "ok";
  }) || entries.some((e) => {
    const pair = paired.get(key(e.source, e.configuration.index, kpi));
    return pair !== undefined && pair.estimate.state === "ok";
  });
  const figureKpis = kpis.filter(withInterval);
  const quietKpis = kpis.filter((k) => !withInterval(k));
  return (
    <div className="fp-results" aria-busy={busy}>
      <ResultsControls
        report={first}
        options={options}
        onOptions={onOptions}
        busy={busy}
        view={view}
        onView={setView}
        full={full}
        onFull={setFull}
        labels={labels}
      />
      {kpis.length === 0 ? (
        <div className="ws-state ws-empty">
          <h3>No KPI reported yet</h3>
          <p>Estimates appear here as replications finish; nothing is shown until a replication reports a KPI.</p>
        </div>
      ) : view === "figure" ? (
        <>
          {sliced && <SliceControls factors={factors} slice={slice} onSlice={setSlice} shown={shown.length} total={entries.length} />}
          <div className="fp-blocks">
            {figureKpis.map((kpi) => (
              <KpiBlock
                key={kpi}
                kpi={kpi}
                entries={shown}
                context={context}
                sources={sources}
                estimates={estimates}
                paired={paired}
                full={full}
                unit={units[kpi] ?? null}
              />
            ))}
          </div>
          <WithoutIntervals kpis={quietKpis} entries={entries} estimates={estimates} full={full} />
        </>
      ) : (
        <Matrix kpis={kpis} entries={entries} sources={sources} estimates={estimates} paired={paired} full={full} />
      )}
      {children}
    </div>
  );
}
