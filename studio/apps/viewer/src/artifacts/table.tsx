/** A typed, server-paged table over a saved dataset; sorting and filtering are server-side over the retained artifact. */
import { useEffect, useState } from "react";
import { formatValue, tagJson } from "../analysis/codec";
import { Field } from "../workspace/forms";
import { downloadBlob } from "./source";
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
/** Static rows: exact int64 digits, explicit non-finite labels, ``—`` for nulls. */
export function TableRows({ schema, rows, offset }: { schema: DatasetSchema; rows: readonly Row[]; offset: number }) {
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
                {f.name}
                {f.unit && <small> · {f.unit}</small>}
                <small className="af-dtype"> {dtypeName(f.dtype)}</small>
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
                const text = formatValue(value, f.dtype);
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
        <TableRows schema={schema} rows={page.rows} offset={offset} />
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
