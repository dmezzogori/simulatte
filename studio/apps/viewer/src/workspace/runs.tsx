/** Run history as a research page: bounded pages, text search over loaded rows, grouped
 * replications and legacy history, inline annotations and human-readable provenance. */
import { Fragment, useEffect, useState, useSyncExternalStore } from "react";
import type { ExperimentReport, ExperimentRun, StudioClient } from "../studio-client";
import { Diagnostic, FailureNotice, Feedback, type PageState } from "./feedback";
import { Actions, CheckField, Field, disabledWhen } from "./forms";
import { formatTime } from "./tables";
import { routeHash, type HistoryItem, type ProjectState, type RunDetails } from "./state";
import "./research.css";

/** ``history.get`` accepts up to 200; the viewer asks for 100 per page. */
export const PAGE_SIZE = 100;
/** A history item with the slot columns the server lists; ``interactive`` marks manual runs. */
export type HistoryRow = HistoryItem & { slot?: string | null; worker?: string | null };
export interface HistoryPageResult {
  items: HistoryRow[];
  next_cursor: string | null;
}
export type HistoryFetch = (after: string | null) => Promise<HistoryPageResult>;
const HASH = /\b[0-9a-f]{32,}\b/g;

/** Loaded history pages. Pages append in cursor order; runs the authoritative state lists
 * before the first page are prepended, so loaded pages never reorder while new runs arrive. */
export class HistoryPaging {
  #pages: HistoryRow[][] = [];
  #ids = new Set<string>();
  #fresh: HistoryRow[] = [];
  #latest: readonly HistoryRow[] = [];
  cursor: string | null = null;
  loaded = false;
  constructor(private readonly fetch: HistoryFetch) {}
  get hasMore(): boolean {
    return this.cursor !== null;
  }
  get pages(): number {
    return this.#pages.length;
  }
  async loadFirst(): Promise<void> {
    const page = await this.fetch(null);
    this.#pages = [page.items];
    this.#ids = new Set(page.items.map((item) => item.id));
    this.cursor = page.next_cursor;
    this.loaded = true;
    this.merge(this.#latest);
  }
  async loadMore(): Promise<void> {
    const after = this.cursor;
    if (after === null) return;
    const page = await this.fetch(after);
    if (this.cursor !== after) return; // Reloaded meanwhile; that load owns the cursor.
    const items = page.items.filter((item) => !this.#ids.has(item.id));
    for (const item of items) this.#ids.add(item.id);
    this.#pages.push(items);
    this.cursor = page.next_cursor;
  }
  /** Reconcile with the authoritative listing: unseen runs are newer and go on top; listed
   * runs that are already loaded take the listing's status and annotation in place. */
  merge(history: readonly HistoryRow[]): void {
    this.#latest = history;
    this.#fresh = history.filter((item) => !this.#ids.has(item.id));
    const known = new Map(history.map((item) => [item.id, item]));
    this.#pages = this.#pages.map((page) =>
      page.map((item) => {
        const listed = known.get(item.id);
        return listed && (listed.status !== item.status || listed.has_result !== item.has_result || listed.name !== item.name || listed.text !== item.text || listed.important !== item.important)
          ? { ...item, status: listed.status, has_result: listed.has_result, name: listed.name, text: listed.text, important: listed.important }
          : item;
      }),
    );
  }
  /** Replace annotation fields of one run (an optimistic edit or its confirmation). */
  update(id: string, patch: Partial<HistoryRow>): void {
    const apply = (item: HistoryRow) => (item.id === id ? { ...item, ...patch } : item);
    this.#fresh = this.#fresh.map(apply);
    this.#pages = this.#pages.map((page) => page.map(apply));
  }
  rows(): HistoryRow[] {
    return [...this.#fresh, ...this.#pages.flat()];
  }
}

/** Bounded ``run.get`` prefetch for the rendered rows: at most ``concurrency`` in flight, cached per attempt. */
export class DetailsLoader {
  #cache = new Map<string, RunDetails | Error>();
  #queued = new Set<string>();
  #queue: string[] = [];
  #active = 0;
  #listeners = new Set<() => void>();
  #waiters: (() => void)[] = [];
  #closed = false;
  #version = 0;
  constructor(
    private readonly fetch: (id: string) => Promise<RunDetails>,
    private readonly concurrency = 8,
  ) {}
  get version(): number {
    return this.#version;
  }
  get(id: string): RunDetails | Error | undefined {
    return this.#cache.get(id);
  }
  request(ids: readonly string[]): void {
    if (this.#closed) return;
    for (const id of ids)
      if (!this.#cache.has(id) && !this.#queued.has(id)) {
        this.#queued.add(id);
        this.#queue.push(id);
      }
    this.pump();
  }
  private pump(): void {
    while (!this.#closed && this.#active < this.concurrency && this.#queue.length) {
      const id = this.#queue.shift()!;
      this.#active++;
      this.fetch(id)
        .then(
          (value) => this.#cache.set(id, value),
          (error: unknown) => this.#cache.set(id, error instanceof Error ? error : new Error(String(error))),
        )
        .finally(() => {
          this.#queued.delete(id);
          this.#active--;
          this.#version++;
          for (const listener of [...this.#listeners]) listener();
          this.pump();
          if (this.#active === 0 && this.#queue.length === 0) {
            const waiters = this.#waiters;
            this.#waiters = [];
            for (const resolve of waiters) resolve();
          }
        });
    }
  }
  /** Resolves once nothing is queued or in flight. */
  idle(): Promise<void> {
    if (this.#active === 0 && this.#queue.length === 0) return Promise.resolve();
    return new Promise((resolve) => this.#waiters.push(resolve));
  }
  subscribe = (listener: () => void): (() => void) => {
    this.#listeners.add(listener);
    return () => {
      this.#listeners.delete(listener);
    };
  };
  snapshot = (): number => this.#version;
  dispose(): void {
    this.#closed = true;
    this.#queue = [];
    this.#listeners.clear();
  }
}

export interface Provenance {
  model: string;
  seed: string;
  level: string;
  stopping: string;
  parameters: [string, string][];
  runtime: string;
  diagnostic: string[];
}
const LEVELS: Record<string, string> = { full: "Full recording", kpi: "KPIs only" };
const scalar = (value: unknown): string =>
  value === null || value === undefined ? "—" : typeof value === "object" ? JSON.stringify(value) : String(value);
/** Human-readable provenance of a saved run; raw hashes and ids go to ``diagnostic`` only. */
export function summarizeProvenance(run: RunDetails): Provenance {
  const request = run.request ?? {};
  const stopping = (request.stopping ?? {}) as Record<string, unknown>;
  const runtime = (request.runtime ?? {}) as Record<string, unknown>;
  const words = (value: unknown) => scalar(value).replace(HASH, "[id in details]");
  return {
    model: scalar(request.model),
    seed: scalar(request.seed),
    level: LEVELS[String(request.level)] ?? scalar(request.level),
    stopping:
      scalar(stopping.type) +
      Object.entries(stopping)
        .filter(([k, v]) => k !== "type" && v !== null && v !== undefined)
        .map(([k, v]) => ` · ${k.replaceAll("_", " ")} ${scalar(v)}`)
        .join(""),
    parameters: Object.entries((request.parameters ?? {}) as Record<string, unknown>).map(([k, v]) => [k, scalar(v)]),
    runtime: Object.entries(runtime)
      .filter(([, v]) => typeof v !== "object" || v === null)
      .map(([k, v]) => `${k.replaceAll("_", " ")} ${words(v)}`)
      .filter((line) => !line.includes("[id in details]"))
      .join(" · ") || "—",
    diagnostic: [
      `attempt ${run.id}`,
      `request ${run.request_id}`,
      `generation ${run.generation} · revision ${run.revision}`,
      JSON.stringify(request, null, 2),
    ],
  };
}
/** Client-side text filter over the loaded rows only. */
export function filterRuns(
  rows: HistoryRow[],
  query: string,
  models: readonly { id: string; name: string }[],
): HistoryRow[] {
  const needle = query.trim().toLowerCase();
  if (!needle) return rows;
  const names = new Map(models.map((m) => [m.id, m.name.toLowerCase()]));
  return rows.filter((r) =>
    [r.name, r.id, r.status, r.text, r.model_id ? (names.get(r.model_id) ?? "") : "unassigned"]
      .join("\n")
      .toLowerCase()
      .includes(needle),
  );
}
export interface RunGroups {
  interactive: HistoryRow[];
  replications: HistoryRow[];
  unassigned: HistoryRow[];
}
/** Manual runs, experiment and replay replications (non-interactive slots) and legacy history without a model. */
export function groupRuns(rows: readonly HistoryRow[]): RunGroups {
  const groups: RunGroups = { interactive: [], replications: [], unassigned: [] };
  for (const r of rows)
    (r.model_id === null ? groups.unassigned : r.slot && r.slot !== "interactive" ? groups.replications : groups.interactive).push(r);
  return groups;
}
export interface ReplicationRef {
  run_id: string;
  name: string;
  configuration: string;
  index: number;
}
/** attempt id → experiment run, from the listed experiments' reports (bounded by the listing cap). */
export function indexReplications(reports: readonly ExperimentReport[]): Map<string, ReplicationRef> {
  const index = new Map<string, ReplicationRef>();
  for (const report of reports)
    for (const item of report.replications)
      if (item.attempt)
        index.set(item.attempt, {
          run_id: report.run_id,
          name: report.name,
          configuration: report.configurations[item.configuration]?.label ?? String(item.configuration),
          index: item.index,
        });
  return index;
}
export interface AnnotationDraft {
  id: string;
  name: string;
  text: string;
  important: boolean;
}
export function RunsTable({
  rows,
  models,
  details,
  replications,
  editing,
  onEdit,
  readOnly = false,
}: {
  rows: readonly HistoryRow[];
  models: readonly { id: string; name: string }[];
  details: ReadonlyMap<string, RunDetails | Error> | { get(id: string): RunDetails | Error | undefined };
  replications: ReadonlyMap<string, ReplicationRef>;
  editing: { draft: AnnotationDraft; busy: boolean; error: string; save: () => void; cancel: () => void; change: (patch: Partial<AnnotationDraft>) => void } | null;
  onEdit: (row: HistoryRow) => void;
  readOnly?: boolean;
}) {
  const name = (id: string | null) =>
    id === null ? "Unassigned" : (models.find((m) => m.id === id)?.name ?? "Removed model");
  return (
    <div className="ws-table-wrap">
      <table className="ws-table ws-runs">
        <thead>
          <tr>
            <th scope="col">Run</th>
            <th scope="col">Model · configuration</th>
            <th scope="col">Status</th>
            <th scope="col" className="ws-num">Seed</th>
            <th scope="col">Recording</th>
            <th scope="col" className="ws-num">Created</th>
            <th scope="col">Notes</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const detail = details.get(r.id);
            const run = detail instanceof Error ? undefined : detail;
            const provenance = run ? summarizeProvenance(run) : null;
            const replication = replications.get(r.id);
            const edit = editing?.draft.id === r.id ? editing : null;
            return (
              <Fragment key={r.id}>
                <tr aria-selected={edit ? true : undefined}>
                  <td>
                    <a href={routeHash({ page: "run", attempt: r.id })}>
                      {r.important ? "★ " : ""}
                      {r.name || "Run " + r.id.slice(0, 8)}
                    </a>
                    {provenance && (
                      <details className="ws-provenance">
                        <summary>Provenance</summary>
                        <dl className="ws-pairs">
                          <dt>Model</dt>
                          <dd>{provenance.model}</dd>
                          <dt>Stopping</dt>
                          <dd>{provenance.stopping}</dd>
                          {provenance.parameters.map(([k, v]) => (
                            <Fragment key={k}>
                              <dt>{k}</dt>
                              <dd className="ws-num">{v}</dd>
                            </Fragment>
                          ))}
                          <dt>Runtime</dt>
                          <dd>{provenance.runtime}</dd>
                        </dl>
                        <Diagnostic lines={provenance.diagnostic} />
                      </details>
                    )}
                  </td>
                  <td>
                    {replication ? (
                      <>
                        <a href={routeHash({ page: "experiments", run: replication.run_id })}>{replication.name}</a>
                        <small className="ws-sub">
                          {replication.configuration} · replication {replication.index}
                        </small>
                      </>
                    ) : (
                      <>
                        {name(r.model_id)}
                        {r.slot && r.slot !== "interactive" && (
                          <small className="ws-sub">Slot {r.slot}{r.worker ? ` · ${r.worker}` : ""}</small>
                        )}
                      </>
                    )}
                  </td>
                  <td>
                    <span className={"ws-badge " + r.status}>{r.status}</span>
                  </td>
                  <td className="ws-num">{provenance ? provenance.seed : detail instanceof Error ? "—" : "…"}</td>
                  <td>{provenance ? provenance.level : detail instanceof Error ? "—" : "…"}</td>
                  <td className="ws-num">{formatTime(r.created)}</td>
                  <td className="ws-notes">
                    {r.text && <span className="ws-note-text">{r.text}</span>}
                    {!readOnly && !edit && (
                      <button type="button" className="ws-inline" onClick={() => onEdit(r)} aria-label={`Edit notes of ${r.name || "run " + r.id.slice(0, 8)}`}>
                        Edit
                      </button>
                    )}
                  </td>
                </tr>
                {edit && (
                  <tr className="ws-edit-row">
                    <td colSpan={7}>
                      <form
                        className="ws-form-grid"
                        aria-label="Run notes"
                        onSubmit={(e) => {
                          e.preventDefault();
                          edit.save();
                        }}
                      >
                        <Field label="Run name">
                          <input value={edit.draft.name} disabled={edit.busy} onChange={(e) => edit.change({ name: e.target.value })} />
                        </Field>
                        <Field label="Notes" wide>
                          <textarea value={edit.draft.text} disabled={edit.busy} onChange={(e) => edit.change({ text: e.target.value })} />
                        </Field>
                        <CheckField label="Important · protect from pruning">
                          <input type="checkbox" checked={edit.draft.important} disabled={edit.busy} onChange={(e) => edit.change({ important: e.target.checked })} />
                        </CheckField>
                        <div className="ws-wide">
                          {edit.error && <FailureNotice message={edit.error} compact retry={{ label: "Try again", invoke: edit.save }} />}
                          <Actions note="Notes are saved with the run; the list updates immediately and reverts if saving fails.">
                            <button type="submit" className="primary" {...disabledWhen(edit.busy && "Saving…")}>Save notes</button>
                            <button type="button" {...disabledWhen(edit.busy && "Saving…")} onClick={edit.cancel}>Cancel</button>
                          </Actions>
                        </div>
                      </form>
                    </td>
                  </tr>
                )}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
const GROUPS: [keyof RunGroups, string, string][] = [
  ["interactive", "Interactive runs", "Started explicitly from a model page."],
  ["replications", "Experiment and replay replications", "Scheduled on experiment slots; each links to its experiment when it is listed."],
  ["unassigned", "Unassigned history", "Saved before this project assigned runs to models."],
];
export function RunsPage({
  client,
  state,
  modelId,
}: {
  client: StudioClient;
  state: ProjectState;
  modelId?: string;
}) {
  const [paging] = useState(
    () =>
      new HistoryPaging(async (after) => {
        const r = await client.command("history.get", { model_id: modelId ?? null, after, limit: PAGE_SIZE });
        if (!r.ok) throw Error((r.error as { message: string }).message);
        return { items: r.items as HistoryRow[], next_cursor: r.next_cursor as string | null };
      }),
  );
  const [loader] = useState(
    () =>
      new DetailsLoader(async (id) => {
        const r = await client.command("run.get", { attempt_id: id });
        if (!r.ok) throw Error((r.error as { message: string }).message);
        return r.run as unknown as RunDetails;
      }),
  );
  useSyncExternalStore(loader.subscribe, loader.snapshot, loader.snapshot);
  const [page, setPage] = useState<PageState<HistoryRow[]>>({ status: "loading" });
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const [more, setMore] = useState("");
  const [editing, setEditing] = useState<{ draft: AnnotationDraft; previous: HistoryRow; busy: boolean; error: string } | null>(null);
  const [reports, setReports] = useState<Map<string, { status: string; report: ExperimentReport }>>(new Map());
  const refresh = () => {
    const rows = paging.rows();
    setPage(rows.length ? { status: "ready", value: rows } : { status: "empty" });
  };
  const load = async (first: boolean) => {
    setBusy(true);
    setMore("");
    try {
      if (first) await paging.loadFirst();
      else await paging.loadMore();
      refresh();
    } catch (e) {
      if (first) setPage({ status: "error", message: String(e), retry: { label: "Try again", invoke: () => void load(true) } });
      else setMore(String(e));
    } finally {
      setBusy(false);
    }
  };
  useEffect(() => {
    void load(true);
    return () => loader.dispose();
  }, [client, modelId]);
  useEffect(() => {
    if (!paging.loaded) return;
    paging.merge(modelId ? (state.history as HistoryRow[]).filter((r) => r.model_id === modelId) : (state.history as HistoryRow[]));
    refresh();
  }, [state.history]);
  const rows = page.status === "ready" ? page.value : [];
  const visible = filterRuns(rows, query, state.models);
  const groups = groupRuns(visible);
  useEffect(() => {
    loader.request(visible.map((r) => r.id));
  }, [visible.length, page]);
  // Experiment replications link to their run through the listed experiments' reports.
  const experiments: ExperimentRun[] = (client.state?.experiments ?? []) as ExperimentRun[];
  const needsIndex = groups.replications.length > 0;
  useEffect(() => {
    if (!needsIndex) return;
    let closed = false;
    for (const run of experiments.slice(0, 20)) {
      if (reports.get(run.run_id)?.status === run.status) continue;
      void client
        .command("comparison.get", { run_id: run.run_id })
        .then((r) => {
          if (closed || !r.ok) return;
          setReports((old) => new Map(old).set(run.run_id, { status: run.status, report: r.report as ExperimentReport }));
        })
        .catch(() => {});
    }
    return () => {
      closed = true;
    };
  }, [needsIndex, experiments.map((r) => r.run_id + r.status).join()]);
  const replications = indexReplications([...reports.values()].map((r) => r.report));
  const model = modelId ? state.models.find((m) => m.id === modelId) : null;
  const startEdit = (row: HistoryRow) =>
    setEditing({ draft: { id: row.id, name: row.name, text: row.text, important: row.important }, previous: row, busy: false, error: "" });
  const save = async () => {
    if (!editing) return;
    const { draft, previous } = editing;
    setEditing({ ...editing, busy: true, error: "" });
    paging.update(draft.id, { name: draft.name, text: draft.text, important: draft.important });
    refresh();
    try {
      const r = await client.command("history.annotate", { attempt_id: draft.id, name: draft.name, text: draft.text, important: draft.important });
      if (!r.ok) throw Error((r.error as { message: string }).message);
      setEditing(null);
    } catch (e) {
      paging.update(draft.id, { name: previous.name, text: previous.text, important: previous.important });
      refresh();
      setEditing((old) => (old && old.draft.id === draft.id ? { ...old, busy: false, error: String(e) } : old));
    }
  };
  const editor = editing
    ? {
        draft: editing.draft,
        busy: editing.busy,
        error: editing.error,
        save: () => void save(),
        cancel: () => setEditing(null),
        change: (patch: Partial<AnnotationDraft>) => setEditing((old) => (old ? { ...old, draft: { ...old.draft, ...patch } } : old)),
      }
    : null;
  const loaded = rows.length;
  return (
    <>
      <div className="ws-page-heading">
        <div>
          <div className="ws-eyebrow">PROJECT HISTORY</div>
          <h1>{model ? `Runs of ${model.name}` : "Runs"}</h1>
          <p>Individual runs keep their configuration, source and recorded outputs. Nothing here is selected for you.</p>
        </div>
      </div>
      <div className="ws-toolbar">
        <Field label="Filter by model">
          <select
            value={modelId ?? ""}
            onChange={(e) => {
              location.hash = routeHash(e.target.value ? { page: "runs", model: e.target.value } : { page: "runs" });
            }}
          >
            <option value="">All models, including removed and unassigned</option>
            {state.models.map((m) => (
              <option value={m.id} key={m.id}>
                {m.name}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Search loaded runs" hint={loaded ? `${visible.length} of ${loaded} loaded run${loaded === 1 ? "" : "s"} shown${paging.hasMore ? "; older runs are not loaded yet" : ""}` : undefined}>
          <input type="search" value={query} placeholder="Name, status, model or note" onChange={(e) => setQuery(e.target.value)} />
        </Field>
        <button type="button" {...disabledWhen(busy && "Loading runs…")} onClick={() => void load(true)}>
          Refresh history
        </button>
      </div>
      <Feedback
        state={page}
        loading="Loading saved runs…"
        empty={{
          title: "No runs yet",
          text: model ? `${model.name} has no saved runs.` : "Choose a model and press Run when its inputs are ready.",
          link: model
            ? { label: `Configure ${model.name}`, hash: routeHash({ page: "model", id: model.id }) }
            : { label: "Choose a model to begin", hash: "#/models" },
        }}
      >
        {() => (
          <>
            {visible.length === 0 && (
              <div className="ws-state ws-empty">
                <h2>No loaded run matches</h2>
                <p>The search covers the {loaded} loaded run{loaded === 1 ? "" : "s"} only.{paging.hasMore ? " Load older runs to search further." : ""}</p>
              </div>
            )}
            {GROUPS.map(([key, title, hint]) =>
              groups[key].length ? (
                <section className="ws-section ws-run-group" key={key} aria-label={title}>
                  <div className="ws-section-heading">
                    <h2>
                      {title} <span className="ws-count">{groups[key].length}</span>
                    </h2>
                    <span className="ws-hint">{hint}</span>
                  </div>
                  <RunsTable rows={groups[key]} models={state.models} details={loader} replications={replications} editing={editor} onEdit={startEdit} readOnly={state.read_only} />
                </section>
              ) : null,
            )}
            <div className="ws-actions">
              {paging.hasMore ? (
                <button type="button" {...disabledWhen(busy && "Loading runs…")} onClick={() => void load(false)}>
                  Load older runs
                </button>
              ) : (
                <small className="ws-note">All {loaded} saved run{loaded === 1 ? "" : "s"} loaded.</small>
              )}
              {more && <small role="alert">{more}</small>}
            </div>
          </>
        )}
      </Feedback>
      <p className="ws-hint">
        Seeds and recording levels come from each run's captured request; "…" means it is still being read.
      </p>
    </>
  );
}
