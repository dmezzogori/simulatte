/** Experiments as a research page: definitions per model, runs with their exact control
 * commands, a run detail route and the numerical results table. The playback compare of
 * selected recordings stays here as a secondary tool. */
import { useEffect, useMemo, useState, useSyncExternalStore } from "react";
import { Counts, DefinitionForm, RunDetails } from "../experiments";
import { ComparePanel, ReplayNotifications } from "../compare-panel";
import { ExperimentFeed } from "../experiment-feed";
import type { ParameterSchema } from "../parameter-draft";
import type {
  StudioClient,
  StudioState,
  ConnectionStatus,
  Command,
  ExperimentRun,
} from "../studio-client";
import { ConfirmDialog, EmptyState, FailureNotice } from "./feedback";
import { Actions, CheckField, Field, NOT_CONNECTED, READ_ONLY, WORKING, disabledWhen } from "./forms";
import { formatTime } from "./tables";
import { routeHash, type ProjectState } from "./state";
import { plansOf } from "./models";
import "./research.css";

export interface ExperimentAction {
  label: string;
  command: "experiment.start" | "experiment.cancel" | "experiment.resume" | "experiment.delete";
  body: Record<string, unknown>;
  /** Shown before sending; the action is sent only after confirmation. */
  confirm?: string;
  primary?: boolean;
}
/** The controls a run offers and exactly what each sends. There is no pause command: ``experiment.cancel``
 * stops a running or paused run for good; ``paused`` is the state a Studio shutdown leaves, which
 * ``experiment.resume`` continues; ``experiment.start {run_id}`` restarts a finished run's definition
 * (completed replications are reused). */
export function experimentActions(run: Pick<ExperimentRun, "run_id" | "status">): ExperimentAction[] {
  const body = { run_id: run.run_id };
  if (run.status === "running") return [{ label: "Cancel", command: "experiment.cancel", body }];
  if (run.status === "paused")
    return [
      { label: "Resume", command: "experiment.resume", body, primary: true },
      { label: "Cancel", command: "experiment.cancel", body },
      { label: "Delete", command: "experiment.delete", body, confirm: "Delete this experiment run and its replication results? This cannot be undone." },
    ];
  return [
    { label: "Start again", command: "experiment.start", body },
    { label: "Delete", command: "experiment.delete", body, confirm: "Delete this experiment run and its replication results? This cannot be undone." },
  ];
}
/** Models whose source revision moved past the baseline: their definitions are stale; saved runs keep their captured inputs. */
export function staleModels(
  baseline: ReadonlyMap<string, number>,
  models: readonly { id: string; revision: { number: number } | null }[],
): Set<string> {
  const stale = new Set<string>();
  for (const m of models) {
    const seen = baseline.get(m.id);
    if (seen !== undefined && m.revision && m.revision.number !== seen) stale.add(m.id);
  }
  return stale;
}
interface Metadata {
  selected: string;
  models: { name: string; parameters: ParameterSchema; measurement_plans?: { name: string; datasets?: { name: string; fields?: { name: string }[] }[] }[] }[];
}
const planOf = (run: ExperimentRun) => (run as ExperimentRun & { measurement_plan?: string }).measurement_plan;
export function ExperimentsPage({
  client,
  state,
  status,
  experimentRunId,
}: {
  client: StudioClient;
  state: ProjectState;
  status: ConnectionStatus;
  experimentRunId?: string;
}) {
  const [id, setId] = useState(state.models[0]?.id ?? ""),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [creating, setCreating] = useState(false),
    [saved, setSaved] = useState<string[]>([]),
    [workers, setWorkers] = useState<string[]>([]),
    [level, setLevel] = useState(""),
    [metadata, setMetadata] = useState<{ id: string; value: Metadata } | null>(null),
    [pending, setPending] = useState<ExperimentAction | null>(null),
    [baseline, setBaseline] = useState(() => new Map(state.models.map((m) => [m.id, m.revision?.number ?? 0])));
  const model = state.models.find((m) => m.id === id) ?? state.models[0];
  const [feed] = useState(() => new ExperimentFeed((ref) => client.reference(ref)));
  const [replays] = useState(() => new ReplayNotifications((ref) => client.reference(ref)));
  const live = useSyncExternalStore(feed.subscribe, feed.snapshot, feed.snapshot);
  useEffect(() => {
    const reconcile = (value: StudioState, arrival: number) => {
      feed.observe(arrival);
      replays.observe(value.replays ?? [], arrival);
    };
    const unnotify = client.subscribeNotifications((message) => {
      feed.notification(message);
      replays.notification(message);
    });
    const unstate = client.subscribeState(reconcile);
    if (client.state) reconcile(client.state, client.stateArrival);
    return () => {
      unnotify();
      unstate();
      feed.dispose();
      replays.dispose();
    };
  }, [client, feed, replays]);
  const reference = model?.metadata_reference ?? null;
  useEffect(() => {
    if (!reference) {
      setMetadata(null);
      return;
    }
    const abort = new AbortController();
    void client
      .reference(reference, abort.signal)
      .then((value) => {
        if (!abort.signal.aborted) setMetadata({ id: reference.id, value: value as Metadata });
      })
      .catch((e) => {
        if (!abort.signal.aborted) setError(String(e));
      });
    return () => abort.abort();
  }, [client, reference?.id]);
  const scoped = useMemo(
    () =>
      new Proxy(client, {
        get(target, key) {
          if (key === "command")
            return (type: Command, body: Record<string, unknown> = {}, options = {}) =>
              target.command(type, { ...body, model_id: model?.id ?? id }, options);
          const value = Reflect.get(target, key, target);
          return typeof value === "function" ? value.bind(target) : value;
        },
      }),
    [client, model?.id ?? id],
  );
  const connected = status === "connected" && !state.read_only;
  const blocked = disabledWhen(status !== "connected" && NOT_CONNECTED, state.read_only && READ_ONLY, busy && WORKING).title;
  const runs: ExperimentRun[] = (client.state?.experiments ?? []) as ExperimentRun[];
  const files = [...new Set([...(model?.experiment_files ?? []), ...saved])].sort();
  const pool = live.workers ?? client.state?.workers ?? null;
  const prepared = metadata?.id === reference?.id ? metadata?.value.models.find((m) => m.name === metadata.value.selected) : undefined;
  const stale = staleModels(baseline, state.models);
  const legacy: StudioState = {
    ...client.state!,
    revision: model?.revision ?? null,
    metadata: reference,
    model: model?.entrypoint.split(":")[0] ?? "",
    experiment_files: model?.experiment_files ?? [],
  };
  const send = async (action: ExperimentAction, confirmed = false) => {
    if (busy || !connected) return;
    if (action.confirm && !confirmed) {
      setPending(action);
      return;
    }
    setBusy(true);
    setError("");
    try {
      const result = await scoped.command(action.command, action.body);
      if (!result.ok) throw new Error((result.error as { message?: string })?.message ?? JSON.stringify(result.error));
      if (action.command === "experiment.start") {
        if (model) setBaseline((old) => new Map(old).set(model.id, model.revision?.number ?? 0));
        location.hash = routeHash({ page: "experiments", run: String(result.run_id) });
      }
      if (action.command === "experiment.delete" && experimentRunId === action.body.run_id) location.hash = routeHash({ page: "experiments" });
      await client.resync();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };
  const start = (path: string) =>
    void send({
      label: "Start",
      command: "experiment.start",
      body: { path, ...(workers.length ? { workers } : {}), ...(level ? { level } : {}) },
    });
  const selected = experimentRunId ? runs.find((r) => r.run_id === experimentRunId) : undefined;
  const heading = selected ? (
    <div className="ws-page-heading">
      <div>
        <p className="ws-back">
          <a href="#/experiments">← Experiments</a>
        </p>
        <div className="ws-eyebrow">EXPERIMENT RUN</div>
        <h1>{selected.name}</h1>
        <p>
          <span className={"ws-badge " + selected.status}>{selected.status}</span>
          <Counts counts={live.counts(selected)} />
          <span className="ws-muted">started {formatTime(selected.created)}</span>
          <span className="ws-tag">{planOf(selected) ? `Measurements · ${planOf(selected)}` : "KPIs only"}</span>
        </p>
      </div>
      <div className="ws-heading-links">
        <a className="ws-button" href={routeHash({ page: "comparison", selection: [selected.run_id] })}>
          Compare numerically →
        </a>
        {experimentActions(selected).map((action) => (
          <button key={action.label} type="button" className={action.primary ? "primary" : undefined} {...disabledWhen(blocked)} onClick={() => void send(action)}>
            {action.label}
          </button>
        ))}
      </div>
    </div>
  ) : (
    <div className="ws-page-heading">
      <div>
        <div className="ws-eyebrow">NUMERICAL EXPERIMENTS</div>
        <h1>Experiments</h1>
        <p>Definitions, configurations, replication progress and numerical comparisons.</p>
      </div>
    </div>
  );
  return (
    <>
      {heading}
      {experimentRunId && !selected && (
        <EmptyState
          screen={{
            title: "Experiment run not listed",
            text: "It is not among the newest experiment runs, or it was deleted. Its address stays valid for bookmarks.",
            link: { label: "All experiments", hash: "#/experiments" },
          }}
        />
      )}
      {selected && (
        <RunDetails
          key={selected.run_id}
          client={scoped}
          run={selected}
          counts={live.counts(selected)}
          live={live}
          filter={null}
          onOpen={(_report, item) => {
            if (item.attempt) location.hash = routeHash({ page: "run", attempt: item.attempt });
          }}
          onError={setError}
        />
      )}
      {!selected && (
        <div className="ws-cols ws-experiments">
          <section className="ws-card">
            <div className="ws-section-heading">
              <h2>Experiment runs</h2>
              <span className="ws-hint">Newest 20 · results stay in the project</span>
            </div>
            {runs.length ? (
              <div className="ws-table-wrap">
                <table className="ws-table">
                  <thead>
                    <tr>
                      <th scope="col">Experiment</th>
                      <th scope="col">Status</th>
                      <th scope="col">Replications</th>
                      <th scope="col" className="ws-num">Started</th>
                      <th scope="col">Controls</th>
                    </tr>
                  </thead>
                  <tbody>
                    {runs.map((run) => (
                      <tr key={run.run_id}>
                        <td>
                          <button type="button" className="ws-link-button" onClick={() => { location.hash = routeHash({ page: "experiments", run: run.run_id }); }}>
                            {run.name}
                          </button>
                        </td>
                        <td>
                          <span className={"ws-badge " + run.status}>{run.status}</span>
                        </td>
                        <td>
                          <Counts counts={live.counts(run)} />
                          {planOf(run) && <small className="ws-sub">Measurements: {planOf(run)}</small>}
                        </td>
                        <td className="ws-num">{formatTime(run.created)}</td>
                        <td className="ws-controls">
                          {experimentActions(run).map((action) => (
                            <button key={action.label} type="button" className="ws-inline" {...disabledWhen(blocked)} onClick={() => void send(action)}>
                              {action.label}
                            </button>
                          ))}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <details className="ws-footnote">
                  <summary>Experiments ({runs.length}) · what the controls send</summary>
                  <p>
                    Cancel sends <code>experiment.cancel</code> and stops the run for good; there is no pause. A run is
                    <em> paused</em> only when Studio stopped while it ran, and Resume sends <code>experiment.resume</code>.
                    Start again sends <code>experiment.start</code> with the saved definition and reuses completed replications.
                    Delete sends <code>experiment.delete</code> after confirmation.
                  </p>
                </details>
              </div>
            ) : (
              <EmptyState screen={{ title: "No experiment runs yet", text: "Start a saved definition or define a new one." }} />
            )}
          </section>
          <aside className="ws-stack">
            <section className="ws-card ws-details">
              <h2>Definitions</h2>
              <Field label="Experiment model">
                <select value={model?.id ?? ""} onChange={(e) => setId(e.target.value)}>
                  {state.models.map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.name}
                    </option>
                  ))}
                </select>
              </Field>
              {model && stale.has(model.id) && (
                <p className="ws-stale-note" role="status">
                  <span className="ws-badge warm">stale</span> The source of {model.name} changed since revision {baseline.get(model.id)}. New
                  runs capture the current source; saved runs keep theirs.{" "}
                  <button type="button" className="ws-inline" onClick={() => setBaseline((old) => new Map(old).set(model.id, model.revision?.number ?? 0))}>
                    Acknowledge
                  </button>
                </p>
              )}
              {model && !model.metadata && (
                <p className="ws-hint">
                  <a href={routeHash({ page: "model", id: model.id })}>Prepare this model</a> to define or start experiments.
                </p>
              )}
              {files.length ? (
                <ul className="ws-list">
                  {files.map((path) => (
                    <li key={path}>
                      <span>
                        <code>{path}</code>
                        {model && stale.has(model.id) && <span className="ws-badge warm">stale</span>}
                      </span>
                      <button type="button" className="ws-inline primary" {...disabledWhen(blocked, !model?.metadata && "Prepare the model first")} onClick={() => start(path)}>
                        Start
                      </button>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="ws-hint">No experiment files for this model yet.</p>
              )}
              <details>
                <summary>Execution settings</summary>
                <p className="ws-hint">
                  Default: local slots only ({pool?.local ?? 1}). Remote workers come from the user-level workers.toml.
                </p>
                {(pool?.workers ?? []).length > 0 && (
                  <div className="ws-worker-list">
                    {["local", ...(pool?.workers.map((w) => w.name) ?? [])].map((name) => (
                      <CheckField key={name} label={name}>
                        <input
                          type="checkbox"
                          checked={workers.includes(name)}
                          onChange={(e) => setWorkers(e.target.checked ? [...workers, name] : workers.filter((w) => w !== name))}
                        />
                      </CheckField>
                    ))}
                  </div>
                )}
                <Field label="Recording level override" hint="Empty keeps the definition's level">
                  <select value={level} onChange={(e) => setLevel(e.target.value)}>
                    <option value="">As defined</option>
                    <option value="kpi">KPIs only</option>
                    <option value="full">Full recording</option>
                  </select>
                </Field>
                {pool && pool.slots.length > 0 && (
                  <ul className="ws-list">
                    {pool.slots.map((slot) => (
                      <li key={slot.name}>
                        <span>{slot.name}</span>
                        <span className={"ws-badge gray slot-" + slot.state}>{slot.state}</span>
                      </li>
                    ))}
                  </ul>
                )}
                {pool?.error && <FailureNotice compact message={pool.error} />}
              </details>
              <Actions>
                <button type="button" {...disabledWhen(!model && "Register a model first", !prepared && "Prepare the model to read its parameters")} onClick={() => setCreating(!creating)}>
                  {creating ? "Close form" : "Define a new experiment"}
                </button>
              </Actions>
            </section>
          </aside>
        </div>
      )}
      {creating && !selected && prepared && model && (
        <section className="ws-card">
          <h2>New experiment</h2>
          <DefinitionForm
            client={scoped}
            fields={prepared.parameters.fields}
            model={`${model.entrypoint.split(":")[0]}:${metadata!.value.selected}`}
            workers={pool?.workers.map((w) => w.name) ?? []}
            plans={plansOf(prepared)}
            onError={setError}
            onSaved={(path) => {
              setSaved([...saved, path]);
              setCreating(false);
              setBaseline((old) => new Map(old).set(model.id, model.revision?.number ?? 0));
            }}
          />
        </section>
      )}
      <section className="ws-card ws-compare-tool">
        <h2>Selected recordings</h2>
        <p className="ws-hint">Play two committed full recordings side by side on one clock.</p>
        <ComparePanel
          client={scoped}
          state={legacy}
          status={state.read_only ? "authentication_required" : status}
          headers={{ "X-Simulatte-Session": sessionStorage.getItem("simulatte-session") ?? "" }}
          replays={replays}
          onError={setError}
        />
      </section>
      <ConfirmDialog
        open={pending !== null}
        title={pending ? `${pending.label} this experiment run?` : "Confirm"}
        text={pending?.confirm ?? ""}
        confirmLabel={pending?.label ?? "Confirm"}
        onCancel={() => setPending(null)}
        onConfirm={() => {
          const action = pending;
          setPending(null);
          if (action) void send(action, true);
        }}
      />
      {busy && <p role="status">Working…</p>}
      {error && <FailureNotice message={error} compact />}
    </>
  );
}
