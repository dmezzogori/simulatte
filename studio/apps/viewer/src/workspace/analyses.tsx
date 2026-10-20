/** Saved analyses: the list, one saved execution with its inputs, outputs and annotations, and explicit admission of a new one. */
import { useEffect, useRef, useState, type ReactNode } from "react";
import { formatValue, untag, type Plain } from "../analysis/codec";
import { httpArtifactSource } from "../artifacts/source";
import type { OutputManifest } from "../artifacts/types";
import { ArtifactView } from "../artifacts/view";
import { parseField, type ParameterField, type ParameterSchema, type Scalar } from "../parameter-draft";
import type { ConnectionStatus, ExperimentRun, Notification, StudioClient } from "../studio-client";
import { Diagnostic, EmptyState, FailureNotice, Feedback, type Action, type PageState } from "./feedback";
import { Actions, CheckField, Field, FieldGroup, WORKING, disabledWhen } from "./forms";
import { routeHash, type HistoryItem, type ProjectState } from "./state";
import { formatTime } from "./tables";
interface Registration {
  key: string;
  id: string;
  name: string;
  entrypoint: string;
}
interface ListItem {
  id: string;
  definition_id: string;
  name: string;
  registration_key?: string;
  state: string;
  created: number;
  snapshot_hash: string;
  available: number;
  planned: number;
  partial: boolean;
  annotation?: string;
}
interface Execution {
  id: string;
  definition_id: string;
  snapshot_hash: string;
  generation_id: string;
  state: string;
  created: number;
  name: string;
  parameters: unknown;
}
interface Definition {
  id: string;
  name: string;
  entrypoint: string;
  registration_key?: string;
  parameters: unknown;
  requirements: unknown;
}
interface SelectionBody {
  run_ids: string[];
  experiment_run_ids: string[];
  configurations: number[] | null;
  require_complete: boolean;
  min_replications: number;
}
interface Snapshot {
  hash: string;
  available: number;
  planned: number;
  missing: number;
  partial: boolean;
  selection: SelectionBody;
}
interface Detail {
  execution: Execution;
  definition: Definition;
  snapshot: Snapshot;
  details: unknown;
  output: unknown;
  annotations: { revision: string; text: string; ts: number }[];
}
interface Preview {
  preview_id: string;
  snapshot_hash: string;
  available: number;
  planned: number;
  missing: number;
  partial: boolean;
  definition_id: string;
  generation_id: string;
  definition: Definition;
}
interface Refusal {
  code: string;
  message: string;
  details?: unknown;
}
type CommandError = Error & { refusal?: Refusal };
/** ``service.state()`` carries the newest history page of this size. */
export const HISTORY_PAGE = 50;
export interface RunPaging {
  items: HistoryItem[];
  /** ``history.get`` cursor of the next older page; ``null`` asks for the first server page. */
  cursor: string | null;
  hasMore: boolean;
}
const generationOf = (item: HistoryItem): number | null => {
  const value = (item as HistoryItem & { generation?: unknown }).generation;
  return typeof value === "number" && Number.isSafeInteger(value) ? value : null;
};
const dedupe = (items: readonly HistoryItem[]): HistoryItem[] => {
  const seen = new Set<string>();
  return items.filter((item) => !seen.has(item.id) && (seen.add(item.id), true));
};
/** Start from the state's newest page: continue after its last generation when known, or page from the server's first page with dedup. */
export function initialRunPaging(history: readonly HistoryItem[]): RunPaging {
  const items = dedupe(history);
  const first = items[0], last = items[items.length - 1];
  const high = first ? generationOf(first) : null, low = last ? generationOf(last) : null;
  const hasMore = history.length >= HISTORY_PAGE;
  const cursor = hasMore && high !== null && low !== null && low >= 1 && low <= high + 1 ? `${high}:${low}` : null;
  return { items, cursor, hasMore };
}
/** Append one ``history.get`` page without repeating runs already listed. */
export function appendRuns(paging: RunPaging, page: { items: HistoryItem[]; next_cursor: string | null }): RunPaging {
  return { items: dedupe([...paging.items, ...page.items]), cursor: page.next_cursor, hasMore: page.next_cursor !== null };
}
/** ``service.state()`` carries the newest experiment runs of this size; ``experiment.list`` pages the rest. */
export const EXPERIMENT_PAGE = 20;
export interface ExperimentPaging {
  items: ExperimentRun[];
  /** Opaque ``experiment.list`` cursor of the next older page; ``null`` asks for the first server page. */
  cursor: string | null;
  hasMore: boolean;
}
const dedupeRuns = (items: readonly ExperimentRun[]): ExperimentRun[] => {
  const seen = new Set<string>();
  return items.filter((item) => !seen.has(item.run_id) && (seen.add(item.run_id), true));
};
export function initialExperimentPaging(listed: readonly ExperimentRun[]): ExperimentPaging {
  return { items: dedupeRuns(listed), cursor: null, hasMore: listed.length >= EXPERIMENT_PAGE };
}
/** Append one ``experiment.list`` page without repeating runs already listed. */
export function appendExperiments(
  paging: ExperimentPaging,
  page: { items: ExperimentRun[]; next_cursor: string | null },
): ExperimentPaging {
  return { items: dedupeRuns([...paging.items, ...page.items]), cursor: page.next_cursor, hasMore: page.next_cursor !== null };
}
/** Fresh status and counts from the state's newest page; runs it no longer lists stay where they were loaded. */
export function mergeExperiments(paging: ExperimentPaging, latest: readonly ExperimentRun[]): ExperimentPaging {
  const fresh = new Map(latest.map((run) => [run.run_id, run]));
  const known = new Set(paging.items.map((run) => run.run_id));
  const newer = latest.filter((run) => !known.has(run.run_id));
  return { ...paging, items: [...newer, ...paging.items.map((run) => fresh.get(run.run_id) ?? run)] };
}
/** Why the "Add annotation" button is disabled, so it is never disabled without a reason. */
export function annotationDisabled(busy: string, blocked: string, note: string) {
  return disabledWhen(busy && WORKING, blocked, !note.trim() && "Type an annotation first");
}
const TERMINAL = new Set(["succeeded", "failed", "cancelled", "lost"]);
const ACTIVE = new Set(["waiting", "running", "finalizing"]);
const registrations = (state: ProjectState): Registration[] =>
  (state as ProjectState & { analyses?: Registration[] }).analyses ?? [];
const shortHash = (hash: string) => hash.slice(0, 12);
const snapshotLabel = (available: number, planned: number, partial: boolean) =>
  `${available} of ${planned} replications` + (partial ? " · partial" : " · complete");
const message = (error: unknown) => (error instanceof Error ? error.message : String(error));
/** A project command whose refusal keeps its code and details. */
async function invoke(client: StudioClient, type: Parameters<StudioClient["command"]>[0], body: Record<string, unknown>) {
  const reply = await client.command(type, body, { revision: null });
  if (!reply.ok) {
    const refusal = (reply.error ?? {}) as Partial<Refusal>;
    const error = new Error(refusal.message ?? "Command refused") as CommandError;
    error.refusal = { code: refusal.code ?? "failed", message: refusal.message ?? "Command refused", details: refusal.details };
    throw error;
  }
  return reply;
}
const asRecord = (value: Plain): { [key: string]: Plain } | null =>
  value !== null && typeof value === "object" && !Array.isArray(value) ? value : null;
function SnapshotBadge({ available, planned, partial }: { available: number; planned: number; partial: boolean }) {
  return (
    <span className={"ws-badge " + (partial ? "running" : "")} data-partial={partial}>
      {snapshotLabel(available, planned, partial)}
    </span>
  );
}
function StateBadge({ state }: { state: string }) {
  return <span className={"ws-badge " + state}>{state}</span>;
}
function useAnalysisStates(client: StudioClient, listener: (body: { execution_id: string; state: string }) => void) {
  // One subscription per client: the latest listener is read through a ref, so renders never resubscribe.
  const current = useRef(listener);
  useEffect(() => {
    current.current = listener;
  }, [listener]);
  useEffect(
    () =>
      client.subscribeNotifications((notification: Notification) => {
        if (notification.type !== "analysis_state") return;
        const body = notification.body as { execution_id?: unknown; state?: unknown };
        if (typeof body?.execution_id === "string" && typeof body.state === "string")
          current.current({ execution_id: body.execution_id, state: body.state });
      }),
    [client],
  );
}
function Pairs({ items }: { items: [string, ReactNode][] }) {
  return (
    <dl className="ws-pairs">
      {items.map(([key, value]) => (
        <div key={key} style={{ display: "contents" }}>
          <dt>{key}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}
function parameterPairs(parameters: unknown): [string, ReactNode][] {
  const record = asRecord(untag(parameters));
  if (!record || !Object.keys(record).length) return [["Parameters", "none"]];
  return Object.entries(record).map(([key, value]) => [key, <code key={key}>{formatValue(value)}</code>]);
}
function parameterSchema(parameters: unknown): ParameterSchema {
  const record = asRecord(untag(parameters));
  const fields = Array.isArray(record?.fields) ? (record.fields as unknown as ParameterField[]) : [];
  return { version: 1, fields };
}
/** Parameter inputs from a definition's schema; values stay text until admission. */
function ParameterInputs({
  schema,
  values,
  issues,
  onChange,
}: {
  schema: ParameterSchema;
  values: Record<string, string>;
  issues: Record<string, string>;
  onChange: (name: string, value: string) => void;
}) {
  if (!schema.fields.length) return <p className="ws-hint">This definition takes no parameters.</p>;
  return (
    <div className="ws-form-grid">
      {schema.fields.map((field) => {
        const choices = field.choices.length ? field.choices : field.type === "bool" ? [true, false] : [];
        const hint = [
          field.description,
          field.default !== null && !field.required ? `default ${String(field.default)}` : "",
          issues[field.name] ?? "",
        ]
          .filter(Boolean)
          .join(" · ");
        return (
          <Field key={field.name} label={`${field.name} (${field.type})`} hint={hint} required={field.required}>
            {choices.length ? (
              <select
                value={values[field.name] ?? ""}
                aria-invalid={!!issues[field.name]}
                onChange={(e) => onChange(field.name, e.target.value)}
              >
                <option value="">{field.required ? "Choose a value" : "Use the default"}</option>
                {choices.map((choice) => (
                  <option key={String(choice)} value={String(choice)}>
                    {String(choice)}
                  </option>
                ))}
              </select>
            ) : (
              <input
                value={values[field.name] ?? ""}
                aria-invalid={!!issues[field.name]}
                inputMode={field.type === "int" ? "numeric" : field.type === "float" ? "decimal" : "text"}
                onChange={(e) => onChange(field.name, e.target.value)}
              />
            )}
          </Field>
        );
      })}
    </div>
  );
}
/** Text values parsed per field; untouched optional fields are omitted so the server applies the defaults. */
function parseParameters(schema: ParameterSchema, values: Record<string, string>) {
  const parsed: Record<string, Scalar> = {};
  const issues: Record<string, string> = {};
  for (const field of schema.fields) {
    const raw = values[field.name];
    if (raw === undefined || raw === "") {
      if (field.required) issues[field.name] = "Required parameter missing";
      continue;
    }
    try {
      parsed[field.name] = parseField(field, raw);
    } catch (error) {
      issues[field.name] = message(error);
    }
  }
  return { parsed, issues };
}
/** The actionable missing-data screen for an ``insufficient_input`` refusal: evidence, never substitutes. */
export function InsufficientInputScreen({
  refusal,
  onRevise,
}: {
  refusal: Refusal;
  onRevise?: Action;
}) {
  const details = asRecord(untag(refusal.details ?? null)) ?? {};
  const pairs: [string, ReactNode][] = [];
  const push = (label: string, key: string) => {
    if (details[key] !== undefined && details[key] !== null) pairs.push([label, formatValue(details[key])]);
  };
  push("Dataset", "dataset");
  push("Experiment run", "run");
  push("Configuration", "configuration");
  push("Valid replications", "valid");
  push("Replications in the snapshot", "replications");
  push("Required", "required");
  push("Available", "available");
  push("Planned", "planned");
  push("Partial snapshot", "partial");
  push("Requires complete inputs", "require_complete");
  push("Minimum replications", "min_replications");
  const unavailable = Array.isArray(details.unavailable) ? details.unavailable : [];
  return (
    <div role="alert" className="ws-state ws-failure" data-kind="missing-data" data-refusal="insufficient_input">
      <strong>The selected inputs do not satisfy this analysis</strong>
      <p>{refusal.message}</p>
      <p className="ws-hint">
        Studio never substitutes synthetic values. Produce the missing data with a revised experiment that retains the
        required fields, or change the selection and completeness policy.
      </p>
      {pairs.length > 0 && <Pairs items={pairs} />}
      {unavailable.length > 0 && (
        <details className="ws-diagnostic">
          <summary>{unavailable.length} unavailable replications</summary>
          <ul>
            {unavailable.map((item, i) => {
              const record = asRecord(item) ?? {};
              return (
                <li key={i}>
                  replication {formatValue(record.index)}
                  {record.status ? ` (${formatValue(record.status)})` : ""}
                  {Array.isArray(record.missing_fields) && record.missing_fields.length
                    ? ` lacks ${record.missing_fields.map((f) => formatValue(f)).join(", ")}`
                    : ""}
                  {record.reason ? `: ${formatValue(record.reason)}` : ""}
                </li>
              );
            })}
          </ul>
        </details>
      )}
      <div className="ws-actions">
        <a className="ws-button primary" href={routeHash({ page: "experiments" })}>
          Define a revised experiment →
        </a>
        {onRevise && (
          <button type="button" onClick={onRevise.invoke}>
            {onRevise.label}
          </button>
        )}
      </div>
    </div>
  );
}
function AnalysesList({ client, state, connected }: { client: StudioClient; state: ProjectState; connected: boolean }) {
  const [page, setPage] = useState<PageState<ListItem[]>>({ status: "loading" });
  const [cursor, setCursor] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [more, setMore] = useState("");
  const load = async (after: string | null) => {
    setBusy(true);
    setMore("");
    try {
      const reply = await invoke(client, "analysis.list", { limit: 50, ...(after ? { cursor: after } : {}) });
      const items = reply.items as ListItem[];
      setPage((old) => {
        const value = after && (old.status === "ready" || old.status === "stale") ? [...old.value, ...items] : items;
        return value.length ? { status: "ready", value } : { status: "empty" };
      });
      setCursor((reply.next_cursor as string | null) ?? null);
    } catch (error) {
      if (after) setMore(message(error));
      else setPage({ status: "error", message: message(error), retry: { label: "Try again", invoke: () => void load(null) } });
    } finally {
      setBusy(false);
    }
  };
  useEffect(() => {
    if (connected) void load(null);
  }, [client, connected]);
  useAnalysisStates(client, ({ execution_id, state: next }) =>
    setPage((old) =>
      old.status === "ready"
        ? { status: "ready", value: old.value.map((item) => (item.id === execution_id ? { ...item, state: next } : item)) }
        : old,
    ),
  );
  const definitions = registrations(state);
  return (
    <>
      <div className="ws-page-heading">
        <div className="ws-eyebrow">REPRODUCIBLE ANALYSIS</div>
        <h1>Analyses</h1>
        <p>Named definitions, saved executions, their frozen inputs and outputs.</p>
      </div>
      <section className="ws-card" aria-labelledby="analysis-definitions">
        <div className="ws-section-heading">
          <h2 id="analysis-definitions">Definitions</h2>
          <small className="ws-identity">Registered in pyproject.toml under tool.simulatte.analyses</small>
        </div>
        {definitions.length ? (
          <ul className="ws-list" aria-label="Analysis definitions">
            {definitions.map((item) => (
              <li key={item.key}>
                <span>
                  <strong>{item.name}</strong> <code className="ws-source">{item.entrypoint}</code>
                </span>
                <a className="ws-button primary" href={routeHash({ page: "analyses", definition: item.key })}>
                  New analysis
                </a>
              </li>
            ))}
          </ul>
        ) : (
          <p className="ws-hint">
            No analysis definitions are registered. Add a <code>[tool.simulatte.analyses.&lt;key&gt;]</code> table to the
            project's pyproject.toml; saved executions below stay readable.
          </p>
        )}
      </section>
      <Feedback
        state={page}
        loading="Loading saved analyses…"
        empty={{
          title: "No saved analyses yet",
          text: "Preview a selection of runs or experiment results for a registered definition, then admit it.",
          ...(definitions[0]
            ? { link: { label: `New ${definitions[0].name}`, hash: routeHash({ page: "analyses", definition: definitions[0].key }) } }
            : {}),
        }}
      >
        {(items) => (
          <>
            <div className="ws-table-wrap">
              <table className="ws-table" aria-label="Saved analyses">
                <caption>Saved executions, newest first</caption>
                <thead>
                  <tr>
                    <th scope="col">Name</th>
                    <th scope="col">Definition</th>
                    <th scope="col">State</th>
                    <th scope="col">Inputs</th>
                    <th scope="col">Created</th>
                    <th scope="col">Last note</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((item) => (
                    <tr key={item.id} data-execution={item.id}>
                      <td>
                        <a href={routeHash({ page: "analyses", execution: item.id })}>{item.name}</a>
                      </td>
                      <td>{item.registration_key ?? <span className="ws-identity">registration removed</span>}</td>
                      <td>
                        <StateBadge state={item.state} />
                      </td>
                      <td>
                        <SnapshotBadge available={item.available} planned={item.planned} partial={item.partial} />
                      </td>
                      <td>{formatTime(item.created)}</td>
                      <td className="ws-identity">{item.annotation ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {cursor && (
              <div className="ws-actions">
                <button type="button" disabled={busy} onClick={() => void load(cursor)}>
                  Load more analyses
                </button>
                {more && <small role="alert">{more}</small>}
              </div>
            )}
          </>
        )}
      </Feedback>
    </>
  );
}
function NewAnalysis({
  client,
  state,
  registrationKey,
  connected,
}: {
  client: StudioClient;
  state: ProjectState;
  registrationKey: string;
  connected: boolean;
}) {
  const registration = registrations(state).find((item) => item.key === registrationKey);
  const [experimentPaging, setExperimentPaging] = useState<ExperimentPaging>(() =>
    initialExperimentPaging(client.state?.experiments ?? []),
  );
  const experiments = experimentPaging.items;
  useEffect(
    () => client.subscribeState((value) => setExperimentPaging((old) => mergeExperiments(old, value.experiments ?? []))),
    [client],
  );
  const [loadingExperiments, setLoadingExperiments] = useState(false);
  const loadExperiments = async () => {
    setLoadingExperiments(true);
    try {
      // The cursor is opaque, so the first server page overlaps the state's newest runs; ask for two
      // pages then, so one click always reaches runs the state does not list.
      const reply = await invoke(client, "experiment.list" as Parameters<StudioClient["command"]>[0], {
        limit: experimentPaging.cursor ? EXPERIMENT_PAGE : 2 * EXPERIMENT_PAGE,
        ...(experimentPaging.cursor ? { cursor: experimentPaging.cursor } : {}),
      });
      setExperimentPaging((old) =>
        appendExperiments(old, { items: reply.items as ExperimentRun[], next_cursor: (reply.next_cursor as string | null) ?? null }),
      );
    } catch (e) {
      setError(message(e));
    } finally {
      setLoadingExperiments(false);
    }
  };
  const [paging, setPaging] = useState<RunPaging>(() => initialRunPaging(state.history));
  const runs = paging.items;
  const [selectedExperiments, setSelectedExperiments] = useState<string[]>([]);
  const [selectedRuns, setSelectedRuns] = useState<string[]>([]);
  const [configurations, setConfigurations] = useState<{ run: string; index: number; label: string }[]>([]);
  const [selectedConfigurations, setSelectedConfigurations] = useState<number[]>([]);
  const [requireComplete, setRequireComplete] = useState(false);
  const [minReplications, setMinReplications] = useState("0");
  const [parameters, setParameters] = useState<Record<string, string>>({});
  const [name, setName] = useState("");
  const [busy, setBusy] = useState("");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [refusal, setRefusal] = useState<Refusal | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let cancelled = false;
    setConfigurations([]);
    for (const run of selectedExperiments)
      void client
        .command("comparison.get", { run_id: run }, { revision: null })
        .then((reply) => {
          if (cancelled || !reply.ok) return;
          const report = reply.report as { configurations?: { index: number; label: string }[] };
          setConfigurations((old) => [
            ...old.filter((c) => c.run !== run),
            ...(report.configurations ?? []).map((c) => ({ run, index: c.index, label: c.label })),
          ]);
        })
        .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [client, selectedExperiments.join(",")]);
  const toggle = (list: string[], set: (next: string[]) => void, id: string) =>
    set(list.includes(id) ? list.filter((x) => x !== id) : [...list, id]);
  const completed = runs.filter((item) => item.status === "succeeded" && item.has_result);
  const loadRuns = async () => {
    try {
      const reply = await invoke(client, "history.get", { model_id: null, after: paging.cursor });
      setPaging((old) =>
        appendRuns(old, { items: reply.items as HistoryItem[], next_cursor: (reply.next_cursor as string | null) ?? null }),
      );
    } catch (e) {
      setError(message(e));
    }
  };
  const schema = preview ? parameterSchema(preview.definition.parameters) : { version: 1 as const, fields: [] };
  const { parsed, issues } = parseParameters(schema, parameters);
  const selection = (): SelectionBody => {
    const min = Number(minReplications);
    if (!/^\d+$/.test(minReplications.trim()) || !Number.isSafeInteger(min)) throw new Error("Minimum replications must be a whole number");
    return {
      run_ids: selectedRuns,
      experiment_run_ids: selectedExperiments,
      configurations: selectedConfigurations.length ? [...selectedConfigurations].sort((a, b) => a - b) : null,
      require_complete: requireComplete,
      min_replications: min,
    };
  };
  const runPreview = async () => {
    setBusy("Previewing…");
    setError("");
    setRefusal(null);
    try {
      if (!selectedExperiments.length && !selectedRuns.length)
        throw new Error("Select at least one experiment run or completed run; nothing is selected by default");
      const reply = await invoke(client, "analysis.preview", {
        registration_key: registrationKey,
        selection: selection(),
        parameters: preview ? parsed : {},
      });
      setPreview(reply as unknown as Preview);
    } catch (e) {
      const refused = (e as CommandError).refusal;
      if (refused?.code === "insufficient_input") setRefusal(refused);
      else setError(message(e));
    } finally {
      setBusy("");
    }
  };
  const admit = async () => {
    if (!preview) return;
    setBusy("Admitting…");
    setError("");
    try {
      const reply = await invoke(client, "analysis.admit", {
        preview_id: preview.preview_id,
        definition_id: preview.definition_id,
        generation_id: preview.generation_id,
        parameters: parsed,
        ...(name.trim() ? { name: name.trim() } : {}),
      });
      location.hash = routeHash({ page: "analyses", execution: (reply.execution as Execution).id });
    } catch (e) {
      const refused = (e as CommandError).refusal;
      if (refused?.code === "insufficient_input") {
        setRefusal(refused);
        setPreview(null);
      } else setError(message(e));
    } finally {
      setBusy("");
    }
  };
  const blocked = !connected ? "Studio is not connected" : state.read_only ? "This project is open read-only" : "";
  return (
    <>
      <div className="ws-page-heading">
        <div className="ws-eyebrow">REPRODUCIBLE ANALYSIS</div>
        <h1>New analysis{registration ? `: ${registration.name}` : ""}</h1>
        <p>
          Definition: <code>{registrationKey}</code>
          {registration && (
            <>
              {" · "}
              <code>{registration.entrypoint}</code>
            </>
          )}
        </p>
      </div>
      {!registration && (
        <EmptyState
          screen={{
            title: "This definition is not registered in the project",
            text: "Register it under tool.simulatte.analyses in pyproject.toml, or open a saved analysis instead.",
            link: { label: "Back to analyses", hash: routeHash({ page: "analyses" }) },
          }}
        />
      )}
      <form
        className="ws-card"
        aria-label="Analysis inputs"
        onSubmit={(e) => {
          e.preventDefault();
          void runPreview();
        }}
      >
        <h2>Inputs</h2>
        <p className="ws-hint">
          Choose explicit results. Studio never defaults to the latest run; the preview freezes exactly these identities.
        </p>
        <FieldGroup
          legend="Experiment runs"
          hint="Committed replications of running, paused or finished experiments, newest first."
          disabled={!!busy}
        >
          {experiments.length ? (
            experiments.map((run) => (
              <CheckField
                key={run.run_id}
                label={run.name}
                hint={`${run.status} · ${Object.entries(run.counts)
                  .map(([k, v]) => `${v} ${k}`)
                  .join(", ")}`}
              >
                <input
                  type="checkbox"
                  checked={selectedExperiments.includes(run.run_id)}
                  onChange={() => {
                    toggle(selectedExperiments, setSelectedExperiments, run.run_id);
                    setPreview(null);
                  }}
                />
              </CheckField>
            ))
          ) : (
            <p className="ws-hint">
              No experiment runs yet. <a href={routeHash({ page: "experiments" })}>Define an experiment</a> or select completed
              runs below.
            </p>
          )}
          {experimentPaging.hasMore && (
            <button type="button" {...disabledWhen(loadingExperiments && WORKING)} onClick={() => void loadExperiments()}>
              Load older experiment runs
            </button>
          )}
        </FieldGroup>
        {selectedExperiments.length > 0 && (
          <FieldGroup legend="Configurations" hint="Leave all unchecked to include every configuration." disabled={!!busy}>
            {configurations.length ? (
              configurations.map((c) => (
                <CheckField key={`${c.run}:${c.index}`} label={`${c.index} · ${c.label}`}>
                  <input
                    type="checkbox"
                    checked={selectedConfigurations.includes(c.index)}
                    onChange={() => {
                      setSelectedConfigurations(
                        selectedConfigurations.includes(c.index)
                          ? selectedConfigurations.filter((x) => x !== c.index)
                          : [...selectedConfigurations, c.index],
                      );
                      setPreview(null);
                    }}
                  />
                </CheckField>
              ))
            ) : (
              <p className="ws-hint">Loading configurations…</p>
            )}
          </FieldGroup>
        )}
        <FieldGroup legend="Completed runs" hint="Individual saved runs with results." disabled={!!busy}>
          {completed.length ? (
            completed.map((item) => (
              <CheckField
                key={item.id}
                label={item.name || item.id.slice(0, 12)}
                hint={`${state.models.find((m) => m.id === item.model_id)?.name ?? "unassigned"} · ${formatTime(item.created)}`}
              >
                <input
                  type="checkbox"
                  checked={selectedRuns.includes(item.id)}
                  onChange={() => {
                    toggle(selectedRuns, setSelectedRuns, item.id);
                    setPreview(null);
                  }}
                />
              </CheckField>
            ))
          ) : (
            <p className="ws-hint">No completed runs with results.</p>
          )}
          {paging.hasMore && (
            <button type="button" onClick={() => void loadRuns()}>
              Load older runs
            </button>
          )}
        </FieldGroup>
        <FieldGroup legend="Completeness policy" hint="Combined with the definition's own requirements by the stricter rule." disabled={!!busy}>
          <CheckField label="Require complete inputs" hint="Refuse admission while any planned replication is missing.">
            <input
              type="checkbox"
              checked={requireComplete}
              onChange={(e) => {
                setRequireComplete(e.target.checked);
                setPreview(null);
              }}
            />
          </CheckField>
          <Field label="Minimum replications" hint="Per configuration; 0 disables the rule.">
            <input
              inputMode="numeric"
              value={minReplications}
              onChange={(e) => {
                setMinReplications(e.target.value);
                setPreview(null);
              }}
            />
          </Field>
        </FieldGroup>
        <Actions note="Preview takes a dry-run snapshot: counts, missing inputs and the parameter form. Nothing runs yet.">
          <button type="submit" className="primary" disabled={!!busy || !registration || !!blocked}>
            Preview inputs
          </button>
          {blocked && <small>{blocked}</small>}
          {busy && <small role="status">{busy}</small>}
        </Actions>
      </form>
      {error && <FailureNotice message={error} compact />}
      {refusal && <InsufficientInputScreen refusal={refusal} onRevise={{ label: "Change the selection", invoke: () => setRefusal(null) }} />}
      {preview && (
        <section className="ws-card" aria-labelledby="analysis-preview" data-preview={preview.preview_id}>
          <div className="ws-section-heading">
            <h2 id="analysis-preview">Preview</h2>
            <SnapshotBadge available={preview.available} planned={preview.planned} partial={preview.partial} />
          </div>
          <Pairs
            items={[
              ["Available replications", String(preview.available)],
              ["Planned replications", String(preview.planned)],
              ["Missing", String(preview.missing)],
              ["Snapshot", <code key="h">{shortHash(preview.snapshot_hash)}</code>],
              ["Definition", `${preview.definition.name} · ${preview.definition.entrypoint}`],
            ]}
          />
          {preview.partial && (
            <p className="ws-hint">
              Partial snapshot: outputs will carry this label and the counts. Later replications never join this
              analysis; a refresh creates a new preview and execution.
            </p>
          )}
          <form
            aria-label="Admission"
            onSubmit={(e) => {
              e.preventDefault();
              void admit();
            }}
          >
            <FieldGroup legend="Parameters" disabled={!!busy}>
              <ParameterInputs
                schema={schema}
                values={parameters}
                issues={issues}
                onChange={(field, value) => setParameters({ ...parameters, [field]: value })}
              />
            </FieldGroup>
            <FieldGroup legend="Saved analysis" disabled={!!busy}>
              <Field label="Name" hint={`Optional; defaults to ${preview.definition.name}`}>
                <input value={name} maxLength={200} onChange={(e) => setName(e.target.value)} />
              </Field>
            </FieldGroup>
            <Actions note="Admission freezes exactly the previewed inputs and submits the execution.">
              <button type="submit" className="primary" disabled={!!busy || !!blocked || Object.keys(issues).length > 0}>
                Admit and run
              </button>
              <button type="button" disabled={!!busy} onClick={() => void runPreview()}>
                Preview again
              </button>
            </Actions>
          </form>
        </section>
      )}
    </>
  );
}
function AnalysisDetail({
  client,
  state,
  executionId,
  connected,
}: {
  client: StudioClient;
  state: ProjectState;
  executionId: string;
  connected: boolean;
}) {
  const [page, setPage] = useState<PageState<Detail>>({ status: "loading" });
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const [refreshed, setRefreshed] = useState<Preview | null>(null);
  const [refusal, setRefusal] = useState<Refusal | null>(null);
  const [name, setName] = useState("");
  const load = async () => {
    try {
      const reply = await invoke(client, "analysis.get", { execution_id: executionId });
      setPage({ status: "ready", value: reply as unknown as Detail });
    } catch (e) {
      setPage({ status: "error", message: message(e), retry: { label: "Try again", invoke: () => void load() } });
    }
  };
  useEffect(() => {
    if (connected) void load();
  }, [client, executionId, connected]);
  useAnalysisStates(client, ({ execution_id }) => {
    if (execution_id === executionId) void load();
  });
  const act = async (label: string, work: () => Promise<void>) => {
    setBusy(label);
    setError("");
    try {
      await work();
    } catch (e) {
      const refused = (e as CommandError).refusal;
      if (refused?.code === "insufficient_input") setRefusal(refused);
      else setError(message(e));
    } finally {
      setBusy("");
    }
  };
  const blocked = !connected ? "Studio is not connected" : state.read_only ? "This project is open read-only" : "";
  const [source] = useState(() => httpArtifactSource((path, init) => client.fetchApi(path, init), executionId));
  const loaded = page.status === "ready" || page.status === "stale" ? page.value : null;
  return (
    <>
      <div className="ws-page-heading">
        <div className="ws-eyebrow">SAVED ANALYSIS</div>
        <h1>{loaded ? loaded.execution.name : "Saved analysis"}</h1>
        <p>
          {loaded && (
            <>
              <StateBadge state={loaded.execution.state} />
              <SnapshotBadge
                available={loaded.snapshot.available}
                planned={loaded.snapshot.planned}
                partial={loaded.snapshot.partial}
              />
              <span>
                {loaded.definition.name} · {formatTime(loaded.execution.created)}
              </span>
            </>
          )}
          <span className="ws-identity">
            Execution: <code>{executionId}</code>
          </span>
        </p>
      </div>
      <Feedback
        state={page}
        loading="Loading the saved analysis…"
        empty={{ title: "Analysis not found", link: { label: "Back to analyses", hash: routeHash({ page: "analyses" }) } }}
      >
      {(detail) => {
        const { execution, definition, snapshot, annotations } = detail;
        const manifest = execution.state === "succeeded" && detail.output ? (untag(detail.output) as unknown as OutputManifest) : null;
        const details = asRecord(untag(detail.details ?? null)) ?? {};
        const diagnostics = typeof details.diagnostics === "string" ? details.diagnostics : "";
        const provenance = manifest ? asRecord(manifest.provenance as unknown as Plain) : null;
        const experimentName = (run: string) => client.state?.experiments?.find((e) => e.run_id === run)?.name ?? shortHash(run);
        const paramsForRefresh = asRecord(untag(execution.parameters)) ?? {};
        return (
          <>
            {error && <FailureNotice message={error} compact />}
            <div className="ws-cols">
              <div className="ws-stack">
                {execution.state !== "succeeded" ? (
                  <section className="ws-card" aria-labelledby="analysis-state" data-outputs="withheld">
                    <h2 id="analysis-state">{ACTIVE.has(execution.state) ? "Execution in progress" : "No published outputs"}</h2>
                    <p>
                      {execution.state === "failed"
                        ? "The analysis failed. Its inputs stay frozen; the diagnostics below come from the contained process."
                        : execution.state === "cancelled"
                          ? "The analysis was cancelled before publishing outputs."
                          : execution.state === "lost"
                            ? "The execution was lost (the process or Studio stopped). Rerun it on the same snapshot."
                            : "Outputs appear here when the execution succeeds; partial files are never shown as results."}
                    </p>
                    {diagnostics ? <pre className="ws-log">{diagnostics}</pre> : null}
                    <Diagnostic
                      lines={Object.entries(details)
                        .filter(([key]) => key !== "diagnostics")
                        .map(([key, value]) => `${key}: ${formatValue(value)}`)}
                    />
                  </section>
                ) : manifest ? (
                  <section aria-labelledby="analysis-outputs">
                    <div className="ws-section-heading">
                      <h2 id="analysis-outputs">Outputs</h2>
                      <small className="ws-identity">Derived from this execution · numeric results first</small>
                    </div>
                    <ArtifactView manifest={manifest} source={source} />
                  </section>
                ) : (
                  <EmptyState screen={{ title: "No output manifest", text: "The execution succeeded without a readable manifest." }} />
                )}
                <section className="ws-card" aria-labelledby="analysis-notes">
                  <h2 id="analysis-notes">Annotations</h2>
                  {annotations.length ? (
                    <ol className="ws-list" aria-label="Annotation revisions">
                      {annotations.map((a, i) => (
                        <li key={a.revision}>
                          <span>
                            <strong>Revision {i + 1}</strong> · {formatTime(a.ts)}
                            <br />
                            {a.text}
                          </span>
                        </li>
                      ))}
                    </ol>
                  ) : (
                    <p className="ws-hint">No annotations yet. Every note is an append-only revision.</p>
                  )}
                  <form
                    aria-label="Add annotation"
                    onSubmit={(e) => {
                      e.preventDefault();
                      void act("Saving note…", async () => {
                        await invoke(client, "analysis.annotate", { execution_id: executionId, text: note });
                        setNote("");
                        await load();
                      });
                    }}
                  >
                    <Field label="New annotation" wide>
                      <textarea value={note} maxLength={65536} onChange={(e) => setNote(e.target.value)} />
                    </Field>
                    <Actions>
                      <button type="submit" {...annotationDisabled(busy, blocked, note)}>
                        Add annotation
                      </button>
                      {blocked && <small>{blocked}</small>}
                    </Actions>
                  </form>
                </section>
              </div>
              <div className="ws-stack">
                <section className="ws-card" aria-labelledby="analysis-inputs">
                  <h2 id="analysis-inputs">Input snapshot</h2>
                  <p className="ws-hint">Original simulation results, frozen at admission. Later results never join this snapshot.</p>
                  <Pairs
                    items={[
                      ["Available", String(snapshot.available)],
                      ["Planned", String(snapshot.planned)],
                      ["Missing", String(snapshot.missing)],
                      ["Snapshot", <code key="h">{shortHash(snapshot.hash)}</code>],
                      [
                        "Experiment runs",
                        snapshot.selection.experiment_run_ids.length
                          ? snapshot.selection.experiment_run_ids.map((run) => (
                              <a key={run} href={routeHash({ page: "experiments", run })}>
                                {experimentName(run)}{" "}
                              </a>
                            ))
                          : "none",
                      ],
                      [
                        "Configurations",
                        snapshot.selection.configurations ? snapshot.selection.configurations.join(", ") : "all",
                      ],
                      [
                        "Runs",
                        snapshot.selection.run_ids.length
                          ? snapshot.selection.run_ids.map((attempt) => (
                              <a key={attempt} href={routeHash({ page: "run", attempt })}>
                                {shortHash(attempt)}{" "}
                              </a>
                            ))
                          : "none",
                      ],
                      ["Require complete", snapshot.selection.require_complete ? "yes" : "no"],
                      ["Minimum replications", String(snapshot.selection.min_replications)],
                    ]}
                  />
                </section>
                <section className="ws-card" aria-labelledby="analysis-definition">
                  <h2 id="analysis-definition">Definition</h2>
                  <Pairs
                    items={[
                      ["Name", definition.name],
                      ["Entrypoint", <code key="e">{definition.entrypoint}</code>],
                      ["Registration", definition.registration_key ?? "no longer registered"],
                      ["Definition revision", <code key="d">{shortHash(definition.id)}</code>],
                      ...parameterPairs(execution.parameters),
                    ]}
                  />
                </section>
                {provenance && (
                  <section className="ws-card" aria-labelledby="analysis-provenance">
                    <h2 id="analysis-provenance">Derived provenance</h2>
                    <p className="ws-hint">
                      Outputs are derived results with their own provenance; they never replace the original results used
                      for replay verification.
                    </p>
                    <Pairs
                      items={Object.entries(provenance)
                        .filter(([key]) => key !== "environment" && key !== "parameters")
                        .map(([key, value]) => [key.replaceAll("_", " "), <code key={key}>{formatValue(value)}</code>])}
                    />
                  </section>
                )}
                <section className="ws-card" aria-labelledby="analysis-actions">
                  <h2 id="analysis-actions">Actions</h2>
                  <div className="ws-actions">
                    {ACTIVE.has(execution.state) && (
                      <button
                        type="button"
                        disabled={!!busy || !!blocked}
                        onClick={() => void act("Cancelling…", async () => void (await invoke(client, "analysis.cancel", { execution_id: executionId })))}
                      >
                        Cancel execution
                      </button>
                    )}
                    {TERMINAL.has(execution.state) && (
                      <>
                        <button
                          type="button"
                          disabled={!!busy || !!blocked}
                          onClick={() =>
                            void act("Rerunning…", async () => {
                              const reply = await invoke(client, "analysis.rerun", { execution_id: executionId });
                              location.hash = routeHash({ page: "analyses", execution: (reply.execution as Execution).id });
                            })
                          }
                        >
                          Rerun on the same snapshot
                        </button>
                        <button
                          type="button"
                          disabled={!!busy || !!blocked}
                          onClick={() =>
                            void act("Refreshing…", async () => {
                              const reply = await invoke(client, "analysis.refresh", { execution_id: executionId });
                              setRefreshed(reply as unknown as Preview);
                              setName(execution.name);
                            })
                          }
                        >
                          Refresh inputs
                        </button>
                      </>
                    )}
                  </div>
                  <small className="ws-note">
                    Rerun recomputes on the frozen snapshot. Refresh previews the current results of the same selection; a
                    distinct execution is created only after you admit it.
                  </small>
                  {blocked && <small className="ws-identity">{blocked}</small>}
                  {busy && <small role="status">{busy}</small>}
                </section>
                {refusal && <InsufficientInputScreen refusal={refusal} onRevise={{ label: "Dismiss", invoke: () => setRefusal(null) }} />}
                {refreshed && (
                  <section className="ws-card" aria-labelledby="analysis-refresh" data-preview={refreshed.preview_id}>
                    <h2 id="analysis-refresh">Refreshed preview</h2>
                    <SnapshotBadge available={refreshed.available} planned={refreshed.planned} partial={refreshed.partial} />
                    <Pairs
                      items={[
                        ["Available", String(refreshed.available)],
                        ["Planned", String(refreshed.planned)],
                        ["Missing", String(refreshed.missing)],
                        [
                          "Snapshot",
                          <span key="s">
                            <code>{shortHash(refreshed.snapshot_hash)}</code>{" "}
                            {refreshed.snapshot_hash === snapshot.hash ? "(unchanged)" : "(new inputs)"}
                          </span>,
                        ],
                      ]}
                    />
                    <form
                      aria-label="Admit refreshed analysis"
                      onSubmit={(e) => {
                        e.preventDefault();
                        void act("Admitting…", async () => {
                          const reply = await invoke(client, "analysis.admit", {
                            preview_id: refreshed.preview_id,
                            definition_id: refreshed.definition_id,
                            generation_id: refreshed.generation_id,
                            parameters: paramsForRefresh,
                            ...(name.trim() ? { name: name.trim() } : {}),
                          });
                          location.hash = routeHash({ page: "analyses", execution: (reply.execution as Execution).id });
                        });
                      }}
                    >
                      <Field label="Name of the new execution">
                        <input value={name} maxLength={200} onChange={(e) => setName(e.target.value)} />
                      </Field>
                      <Actions note="The current execution and its evidence stay unchanged.">
                        <button type="submit" className="primary" disabled={!!busy || !!blocked}>
                          Admit refreshed analysis
                        </button>
                        <button type="button" disabled={!!busy} onClick={() => setRefreshed(null)}>
                          Discard preview
                        </button>
                      </Actions>
                    </form>
                  </section>
                )}
              </div>
            </div>
          </>
        );
      }}
      </Feedback>
    </>
  );
}
export function AnalysesPage({
  client,
  state,
  status,
  executionId,
  registrationKey,
}: {
  client: StudioClient;
  state: ProjectState;
  status: ConnectionStatus;
  executionId?: string;
  registrationKey?: string;
}) {
  const connected = status === "connected";
  if (registrationKey)
    return <NewAnalysis key={registrationKey} client={client} state={state} registrationKey={registrationKey} connected={connected} />;
  if (executionId) return <AnalysisDetail key={executionId} client={client} state={state} executionId={executionId} connected={connected} />;
  return <AnalysesList client={client} state={state} connected={connected} />;
}
