/** A typed, server-paged table over a saved dataset; sorting and filtering are server-side over the retained artifact. */
import { useEffect, useState } from "react";
import { formatValue, tagJson } from "../analysis/codec";
import { Field } from "../workspace/forms";
import { downloadBlob } from "./source";
import { readableValue } from "./format";
import { estimateNote, estimateText, kpiName, niceAxis, tickLabels } from "../findings";
import type { Estimate } from "../studio-client";
import {
  ArtifactError,
  type ArtifactSource,
  type DatasetSchema,
  type OutputDescriptor,
  type Row,
  type RowsPage,
  type TableQueryStrings,
} from "./types";
export const PAGE_LIMITS = [50, 100, 250, 500] as const;
export const OPS = ["eq", "ne", "lt", "le", "gt", "ge", "in"] as const;
export type Op = (typeof OPS)[number];
export interface Sort {
  field: string;
  direction: "asc" | "desc";
}
export interface Filter {
  field: string;
  op: Op;
  /** Tagged JSON text, as the server parses it. */
  value: string;
}
/** Column names in words; the code name stays in the CSV and the full table. */
const FIELD_WORDS: Record<string, string> = { nonfinite: "non-finite", n: "n" };
export const fieldLabel = (name: string) => FIELD_WORDS[name] ?? name.replace(/[_.-]+/g, " ");
export function clampLimit(limit: number): number {
  return Math.min(500, Math.max(1, Math.trunc(limit) || 1));
}
/** The ``sort`` and ``filter`` query strings of the rows route. */
export function tableQuery(sort: Sort | null, filters: readonly Filter[]): TableQueryStrings {
  return {
    sort: sort ? `${sort.field}:${sort.direction}` : "",
    filter: filters.map((f) => `${f.field}:${f.op}:${f.value}`).join(","),
  };
}
const numeric = (dtype: unknown) => dtype === "int64" || dtype === "float64";
const dtypeName = (dtype: unknown) => (typeof dtype === "string" ? dtype : "nested");
/** Static rows: exact int64 digits, explicit non-finite labels, ``—`` for nulls; floats read at four significant digits
 * unless ``full``. */
export function TableRows({ schema, rows, offset, full = false }: { schema: DatasetSchema; rows: readonly Row[]; offset: number; full?: boolean }) {
  return (
    <div className="ws-table-wrap af-table-wrap">
      <table className="ws-table af-table">
        <thead>
          <tr>
            <th scope="col" className="ws-num af-row-number">
              #
            </th>
            {schema.fields.map((f) => (
              <th key={f.name} scope="col" className={numeric(f.dtype) ? "ws-num" : undefined}>
                {fieldLabel(f.name)}
                {f.unit && <small> · {f.unit}</small>}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i}>
              <td className="ws-num af-row-number">{offset + i + 1}</td>
              {schema.fields.map((f) => {
                const value = row[f.name];
                const text = readableValue(value, f.dtype, full);
                const state =
                  value === null || value === undefined
                    ? "null"
                    : typeof value === "number" && !Number.isFinite(value)
                      ? "non-finite"
                      : undefined;
                return (
                  <td key={f.name} className={numeric(f.dtype) ? "ws-num" : undefined} data-value={state}>
                    {text}
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
type Loaded = { status: "loading" } | { status: "ready"; page: RowsPage } | { status: "error"; message: string; retry: boolean };
export function ArtifactTable({ output, source }: { output: OutputDescriptor; source: ArtifactSource }) {
  const schema = output.metadata.schema ?? { name: output.name, fields: [] };
  const [limit, setLimit] = useState<number>(100);
  const [sort, setSort] = useState<Sort | null>(null);
  const [filters, setFilters] = useState<Filter[]>([]);
  const [sortDraft, setSortDraft] = useState<Sort>({ field: schema.fields[0]?.name ?? "", direction: "asc" });
  const [filterDraft, setFilterDraft] = useState<{ field: string; op: Op; text: string }>({
    field: schema.fields[0]?.name ?? "",
    op: "eq",
    text: "",
  });
  const [filterIssue, setFilterIssue] = useState("");
  const [cursor, setCursor] = useState<string | null>(null);
  const [history, setHistory] = useState<(string | null)[]>([]);
  const [offset, setOffset] = useState(0);
  const [loaded, setLoaded] = useState<Loaded>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);
  const [exporting, setExporting] = useState("");
  const query = tableQuery(sort, filters);
  useEffect(() => {
    let cancelled = false;
    setLoaded({ status: "loading" });
    source
      .page(output.id, cursor, limit, query)
      .then((page) => {
        if (!cancelled) setLoaded({ status: "ready", page });
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        const retry = error instanceof ArtifactError && error.retry;
        setLoaded({
          status: "error",
          message: retry
            ? "The server is still indexing this table for the requested order or filter. Retry in a moment."
            : String(error instanceof Error ? error.message : error),
          retry,
        });
      });
    return () => {
      cancelled = true;
    };
  }, [source, output.id, cursor, limit, query.sort, query.filter, attempt]);
  const restart = () => {
    setCursor(null);
    setHistory([]);
    setOffset(0);
  };
  const page = loaded.status === "ready" ? loaded.page : null;
  const field = (name: string) => schema.fields.find((f) => f.name === name);
  const addFilter = () => {
    const target = field(filterDraft.field);
    if (!target) return;
    try {
      const value = tagJson(filterDraft.text, dtypeName(target.dtype), filterDraft.op === "in");
      setFilters([...filters, { field: filterDraft.field, op: filterDraft.op, value }]);
      setFilterDraft({ ...filterDraft, text: "" });
      setFilterIssue("");
      restart();
    } catch (error) {
      setFilterIssue(String(error instanceof Error ? error.message : error));
    }
  };
  const exportCsv = async () => {
    setExporting("Preparing the CSV download…");
    try {
      downloadBlob(await source.csv(output.id), output.name + ".csv");
      setExporting("");
    } catch (error) {
      setExporting(String(error instanceof Error ? error.message : error));
    }
  };
  const total = page?.row_count ?? output.metadata.row_count ?? 0;
  const shown = page?.rows.length ?? 0;
  return (
    <div className="af-table-card">
      <form
        className="af-toolbar"
        aria-label={`Order and filter ${output.name}`}
        onSubmit={(e) => {
          e.preventDefault();
          setSort(sortDraft.field ? sortDraft : null);
          restart();
        }}
      >
        <Field label="Sort by">
          <select value={sortDraft.field} onChange={(e) => setSortDraft({ ...sortDraft, field: e.target.value })}>
            <option value="">Saved order</option>
            {schema.fields.map((f) => (
              <option key={f.name} value={f.name}>
                {f.name}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Direction">
          <select
            value={sortDraft.direction}
            onChange={(e) => setSortDraft({ ...sortDraft, direction: e.target.value as Sort["direction"] })}
          >
            <option value="asc">Ascending</option>
            <option value="desc">Descending</option>
          </select>
        </Field>
        <Field label="Rows per page">
          <select
            value={limit}
            onChange={(e) => {
              setLimit(clampLimit(Number(e.target.value)));
              restart();
            }}
          >
            {PAGE_LIMITS.map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        </Field>
        <button type="submit">Apply order</button>
      </form>
      <div className="af-toolbar" role="group" aria-label={`Filters of ${output.name}`}>
        <Field label="Filter field">
          <select value={filterDraft.field} onChange={(e) => setFilterDraft({ ...filterDraft, field: e.target.value })}>
            {schema.fields.map((f) => (
              <option key={f.name} value={f.name}>
                {f.name}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Operator">
          <select value={filterDraft.op} onChange={(e) => setFilterDraft({ ...filterDraft, op: e.target.value as Op })}>
            {OPS.map((op) => (
              <option key={op} value={op}>
                {op}
              </option>
            ))}
          </select>
        </Field>
        <Field
          label="Filter value"
          hint={
            filterDraft.op === "in"
              ? "Comma-separated values of the field type"
              : "A value of the field type; null, NaN, inf and -inf are accepted"
          }
        >
          <input
            value={filterDraft.text}
            onChange={(e) => setFilterDraft({ ...filterDraft, text: e.target.value })}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                addFilter();
              }
            }}
          />
        </Field>
        <button type="button" onClick={addFilter}>
          Add filter
        </button>
        {filterIssue && (
          <small role="alert" className="af-issue">
            {filterIssue}
          </small>
        )}
      </div>
      {filters.length > 0 && (
        <ul className="ws-identity-list af-filters" aria-label="Active filters">
          {filters.map((f, i) => (
            <li key={i}>
              <code>
                {f.field} {f.op} {f.value}
              </code>{" "}
              <button
                type="button"
                className="af-chip-remove"
                aria-label={`Remove filter ${f.field} ${f.op}`}
                onClick={() => {
                  setFilters(filters.filter((_, j) => j !== i));
                  restart();
                }}
              >
                ×
              </button>
            </li>
          ))}
        </ul>
      )}
      <p className="af-table-summary" role="status">
        {page
          ? shown
            ? `Rows ${offset + 1}–${offset + shown} of ${total.toLocaleString()}`
            : `No rows match of ${total.toLocaleString()}`
          : loaded.status === "loading"
            ? "Loading rows…"
            : `${total.toLocaleString()} rows`}
        {" · "}
        {page?.indexed
          ? "served from a derived index over the saved table"
          : sort || filters.length
            ? "ordered or filtered pages need a server index"
            : "saved order"}
        {" · "}paging, sorting and filtering run on the server; this page never holds the whole table.
      </p>
      {loaded.status === "error" ? (
        <div role="alert" className="ws-state ws-failure ws-compact" data-kind={loaded.retry ? "connection" : "missing-data"}>
          <strong>{loaded.retry ? "Index not ready" : "Table page unavailable"}</strong>
          <p>{loaded.message}</p>
          <button type="button" onClick={() => setAttempt((n) => n + 1)}>
            {loaded.retry ? "Retry" : "Try again"}
          </button>
        </div>
      ) : page ? (
        // A whole dataset is data, not a finding: its values print exactly as saved.
        <TableRows schema={schema} rows={page.rows} offset={offset} full />
      ) : (
        <div className="ws-state ws-loading-state" role="status" aria-busy="true">
          <span className="ws-spinner" aria-hidden="true" />
          <p>Loading rows…</p>
        </div>
      )}
      <div className="ws-actions af-pager">
        <button type="button" disabled={!history.length} onClick={restart}>
          First page
        </button>
        <button
          type="button"
          disabled={!history.length}
          onClick={() => {
            const previous = history[history.length - 1] ?? null;
            setHistory(history.slice(0, -1));
            setCursor(previous);
            setOffset(Math.max(0, offset - limit));
          }}
        >
          Previous page
        </button>
        <button
          type="button"
          disabled={!page?.next_cursor}
          onClick={() => {
            if (!page?.next_cursor) return;
            setHistory([...history, cursor]);
            setCursor(page.next_cursor);
            setOffset(offset + page.rows.length);
          }}
        >
          Next page
        </button>
        <button type="button" onClick={() => void exportCsv()} disabled={exporting === "Preparing the CSV download…"}>
          Download CSV
        </button>
        {exporting && <small role="status">{exporting}</small>}
      </div>
    </div>
  );
}

const number = (value: unknown): number | null =>
  typeof value === "number" ? value : typeof value === "bigint" ? Number(value) : null;
/** A saved table of interval estimates (mean, lower, upper) drawn as forest-plot rows, grouped by metric. */
export function isIntervalTable(schema: DatasetSchema): boolean {
  const names = new Set(schema.fields.map((f) => f.name));
  return names.has("mean") && names.has("lower") && names.has("upper");
}
function IntervalRows({ schema, rows, full }: { schema: DatasetSchema; rows: readonly Row[]; full: boolean }) {
  const labelField = ["label", "configuration_label", "name", "group"].find((n) => schema.fields.some((f) => f.name === n))
    ?? schema.fields.find((f) => f.dtype === "string")?.name;
  const groups = new Map<string, Row[]>();
  for (const row of rows) {
    const metric = typeof row.metric === "string" ? row.metric : "";
    groups.set(metric, [...(groups.get(metric) ?? []), row]);
  }
  const unit = schema.fields.find((f) => f.name === "mean")?.unit ?? null;
  return (
    <div className="af-interval-groups">
      {[...groups].map(([metric, items]) => {
        const estimates = items.map((row): Estimate => {
          const mean = number(row.mean), lower = number(row.lower), upper = number(row.upper);
          return {
            n: number(row.n) ?? 0,
            mean,
            sd: null,
            half_width: mean !== null && upper !== null ? upper - mean : null,
            lower,
            upper,
            state: typeof row.state === "string" ? row.state : lower !== null && upper !== null ? "ok" : "insufficient",
            level: number(row.level) ?? 0,
          };
        });
        const axis = niceAxis(estimates.flatMap((e) => [e.lower, e.upper, e.mean].filter((v): v is number => v !== null && Number.isFinite(v))));
        const labels = tickLabels(axis.ticks);
        return (
          <table key={metric} className="fp-table af-interval-table">
            {metric && (
              <caption>
                {kpiName(metric).title} <code>{metric}</code>
              </caption>
            )}
            <tbody>
              {items.map((row, i) => {
                const e = estimates[i]!;
                const text = estimateText(e, { full });
                const at = (v: number) => Math.min(100, Math.max(0, axis.at(v)));
                const note = estimateNote(e);
                return (
                  <tr key={i} className="fp-row">
                    <th scope="row">{labelField ? readableValue(row[labelField]) : `Row ${i + 1}`}</th>
                    <td className="fp-estimate">
                      <span className="fp-value">{text.value}</span>
                      {text.interval && <span className="fp-interval"> {text.interval}</span>}
                      <small>
                        n = {e.n}
                        {note ? ` · ${note}` : ""}
                      </small>
                    </td>
                    <td className="fp-plot" aria-hidden="true">
                      <span className="fp-track">
                        {e.state === "ok" && e.lower !== null && e.upper !== null && (
                          <span className="fp-ci" style={{ left: `${at(e.lower)}%`, width: `${at(e.upper) - at(e.lower)}%` }} />
                        )}
                        {e.mean !== null && <span className={"fp-point " + e.state} style={{ left: `${at(e.mean)}%` }} />}
                      </span>
                    </td>
                  </tr>
                );
              })}
              <tr className="fp-axis-row" aria-hidden="true">
                <td colSpan={2} className="fp-axis-name">
                  {unit ?? ""}
                </td>
                <td className="fp-plot">
                  <span className="fp-axis">
                    {axis.ticks.map((t, i) => (
                      <span key={i} className="fp-tick" style={{ left: `${axis.at(t)}%` }}>
                        {labels[i]}
                      </span>
                    ))}
                  </span>
                </td>
              </tr>
            </tbody>
          </table>
        );
      })}
    </div>
  );
}

/** A summary table read whole: one row as label and value pairs, a few rows as a plain table; no paging or filters. */
export function SummaryTable({ output, source, full = false }: { output: OutputDescriptor; source: ArtifactSource; full?: boolean }) {
  const schema = output.metadata.schema ?? { name: output.name, fields: [] };
  const [loaded, setLoaded] = useState<Loaded>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let cancelled = false;
    setLoaded({ status: "loading" });
    source
      .page(output.id, null, clampLimit(output.metadata.row_count ?? 50), { sort: "", filter: "" })
      .then((page) => {
        if (!cancelled) setLoaded({ status: "ready", page });
      })
      .catch((error: unknown) => {
        if (!cancelled) setLoaded({ status: "error", message: String(error instanceof Error ? error.message : error), retry: false });
      });
    return () => {
      cancelled = true;
    };
  }, [source, output.id, attempt]);
  if (loaded.status === "error")
    return (
      <div role="alert" className="ws-state ws-failure ws-compact" data-kind="missing-data">
        <strong>Table unavailable</strong>
        <p>{loaded.message}</p>
        <button type="button" onClick={() => setAttempt((n) => n + 1)}>
          Try again
        </button>
      </div>
    );
  if (loaded.status === "loading")
    return (
      <p className="af-loading" role="status" aria-busy="true">
        Loading…
      </p>
    );
  const rows = loaded.page.rows;
  const single = rows.length === 1 ? rows[0]! : null;
  const empty = single !== null && (single.n === 0 || single.n === 0n);
  return (
    <div className="af-summary">
      {isIntervalTable(schema) ? (
        <IntervalRows schema={schema} rows={rows} full={full} />
      ) : empty ? (
        <p className="ws-state ws-stale af-empty" role="status">
          <strong>Empty population.</strong> No observation was included, so there is no mean, minimum or maximum.
        </p>
      ) : single ? (
        <dl className="af-summary-pairs">
          {schema.fields.map((f) => (
            <div key={f.name} className={numeric(f.dtype) ? "is-number" : undefined}>
              <dt>{fieldLabel(f.name)}</dt>
              <dd>
                {readableValue(single[f.name], f.dtype, full)}
                {f.unit && <small> {f.unit}</small>}
              </dd>
            </div>
          ))}
        </dl>
      ) : (
        <TableRows schema={schema} rows={rows} offset={0} full={full} />
      )}
      {isIntervalTable(schema) && (
        <details className="af-raw">
          <summary>All columns</summary>
          <TableRows schema={schema} rows={rows} offset={0} full={full} />
        </details>
      )}
    </div>
  );
}
/** One export per summary output, in its header. */
export function CsvButton({ output, source }: { output: OutputDescriptor; source: ArtifactSource }) {
  const [exporting, setExporting] = useState("");
  const exportCsv = async () => {
    setExporting("Preparing the CSV download…");
    try {
      downloadBlob(await source.csv(output.id), output.name + ".csv");
      setExporting("");
    } catch (error) {
      setExporting(String(error instanceof Error ? error.message : error));
    }
  };
  return (
    <>
      <button type="button" className="af-csv" onClick={() => void exportCsv()} disabled={exporting === "Preparing the CSV download…"}>
        Download CSV
      </button>
      {exporting && <small role="status">{exporting}</small>}
    </>
  );
}
