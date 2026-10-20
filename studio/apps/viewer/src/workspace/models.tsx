import { useState } from "react";
import type { StudioClient, Revision, AttemptKey } from "../studio-client";
import {
  parseField,
  parseExecutionOptions,
  type ExecutionControls,
} from "../parameter-draft";
import { LayoutEditor } from "../layout-editor";
import { invoke } from "./setup";
import { FailureNotice } from "./feedback";
import { Actions, CheckField, Field, FieldGroup, NOT_CONNECTED, READ_ONLY, WORKING, disabledWhen } from "./forms";
import {
  routeHash,
  WorkspaceDrafts,
  type ProjectModel,
  type ProjectState,
} from "./state";
export function Models({
  client,
  state,
  connected,
}: {
  client: StudioClient;
  state: ProjectState;
  connected: boolean;
}) {
  const [key, setKey] = useState("model"),
    [name, setName] = useState("My model"),
    [entry, setEntry] = useState(
      state.legacy_reference ?? "models/model.py:build",
    );
  const [layout, setLayout] = useState(state.legacy_layout ?? "");
  const [preview, setPreview] = useState<{
      preview_id: string;
      files: Record<string, string>;
    } | null>(null),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const change = async (body: Record<string, unknown>) => {
    setBusy(true);
    setError("");
    try {
      const r = await invoke(client, "project.configure", body);
      setPreview(r.preview_id ? (r as unknown as typeof preview) : null);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <div className="ws-page-heading">
        <div className="ws-eyebrow">PROJECT MODELS</div>
        <h1>Models</h1>
        <p>Named entrypoints with independent inputs and saved history.</p>
      </div>
      {state.models.length > 0 && (
        <div className="ws-tiles">
          {state.models.map((m) => (
            <section className="ws-tile ws-model-tile" key={m.id}>
              <span className="ws-badge">{m.inputs ? "Prepared" : "Python model"}</span>
              <h2>
                <a href={routeHash({ page: "model", id: m.id })}>{m.name}</a>
              </h2>
              <code className="ws-source">{m.entrypoint}</code>
              <p>Layout: {m.layout}</p>
              <div className="ws-actions">
                <a className="ws-button primary" href={routeHash({ page: "model", id: m.id })}>
                  Configure run
                </a>
                <a href={routeHash({ page: "runs", model: m.id })}>Runs →</a>
                <button
                  type="button"
                  {...disabledWhen(state.read_only && READ_ONLY, busy && WORKING, !connected && NOT_CONNECTED)}
                  onClick={() =>
                    void change({ models: state.models.filter((v) => v.id !== m.id) })
                  }
                >
                  Review removal
                </button>
              </div>
            </section>
          ))}
        </div>
      )}
      {!state.read_only && (
        <section className="ws-card">
          <h2>Register a Python model</h2>
          <p>
            The file must be inside this project. Registration preserves its
            code and does not run it. Edit the code in your own editor.
          </p>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void change({
                models: [
                  ...state.models,
                  {
                    key,
                    name,
                    entrypoint: entry,
                    layout: layout || `layouts/${key}.json`,
                  },
                ],
                include: [entry.split(":")[0]],
              });
            }}
          >
            <div className="ws-form-grid">
              <Field label="Registration key" hint="Stable identifier used in file names" required>
                <input required value={key} onChange={(e) => setKey(e.target.value)} />
              </Field>
              <Field label="Display name" required>
                <input required value={name} onChange={(e) => setName(e.target.value)} />
              </Field>
              <Field label="Entrypoint" hint="Project-relative module path and factory" required wide>
                <input
                  required
                  value={entry}
                  onChange={(e) => setEntry(e.target.value)}
                  placeholder="models/plant.py:build"
                />
              </Field>
              <Field label="Layout path" hint="Created when empty" wide>
                <input
                  value={layout}
                  onChange={(e) => setLayout(e.target.value)}
                  placeholder={`layouts/${key}.json`}
                />
              </Field>
            </div>
            <Actions note="Registration changes are previewed before they are applied.">
              <button className="primary" {...disabledWhen(busy && WORKING, !connected && NOT_CONNECTED)}>
                Preview registration
              </button>
            </Actions>
          </form>
        </section>
      )}
      {preview && (
        <section className="ws-card">
          <h2>Review registration changes</h2>
          {Object.entries(preview.files).map(([path, text]) => (
            <details key={path} open>
              <summary>{path}</summary>
              <pre>{text}</pre>
            </details>
          ))}
          <Actions>
            <button
              className="primary"
              {...disabledWhen(busy && WORKING)}
              onClick={() => void change({ apply: preview.preview_id })}
            >
              Apply registration
            </button>
            <button onClick={() => setPreview(null)}>Discard</button>
          </Actions>
        </section>
      )}
      {error && <FailureNotice message={error} compact />}
    </>
  );
}
const STOPPING_UNITS: Record<string, string> = {
  horizon: "time units",
  warmup: "time units",
  warm_up: "time units",
  count: "entities",
};
export function ModelPage({
  client,
  state,
  model,
  drafts,
  connected,
}: {
  client: StudioClient;
  state: ProjectState;
  model: ProjectModel;
  drafts: WorkspaceDrafts;
  connected: boolean;
}) {
  const [, redraw] = useState(0),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [blocked, setBlocked] = useState<{
      owner: AttemptKey;
      revision: Revision;
    } | null>(null);
  if (model.inputs && model.revision)
    drafts.observe(model.id, model.revision, model.inputs.options);
  const draft = drafts.get(model.id),
    meta = model.metadata?.models.find(
      (m) => m.name === model.metadata?.selected,
    );
  const enabled = connected && !state.read_only && !busy;
  const blockedReason = disabledWhen(!connected && NOT_CONNECTED, state.read_only && READ_ONLY, busy && WORKING).title;
  const edit = (patch: Record<string, unknown>) => {
    if (draft) {
      drafts.edit(model.id, { ...draft.options, ...patch });
      setBlocked(null);
      redraw((n) => n + 1);
    }
  };
  const prepare = async () => {
    setBusy(true);
    setError("");
    try {
      await invoke(client, "model.prepare", { model_id: model.id });
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };
  const run = async (owner?: AttemptKey) => {
    if (!draft || !meta || draft.conflict) return;
    setBusy(true);
    setError("");
    try {
      let revision = draft.revision;
      if (!owner) {
        const parameters: Record<string, unknown> = {};
        const raw = (draft.options.parameters ?? {}) as Record<string, unknown>;
        for (const field of meta.parameters.fields) {
          if (raw[field.name] !== undefined)
            parameters[field.name] = parseField(field, String(raw[field.name]));
          else if (field.required) throw Error(`Enter ${field.name}`);
        }
        const controls: ExecutionControls = {
          seed: String(draft.options.seed ?? "0"),
          level: String(draft.options.level ?? "full"),
          kpis: (draft.options.kpis ?? []) as string[],
          stopping: Object.fromEntries(
            Object.entries(
              draft.options.stopping as Record<string, unknown>,
            ).map(([k, v]) => [k, v === null ? "" : String(v)]),
          ),
        };
        const options = { ...parseExecutionOptions(controls), parameters };
        const applied = await invoke(
          client,
          "params.set",
          { model_id: model.id, changes: options },
          revision,
        );
        revision = applied.revision as unknown as Revision;
        drafts.accept(model.id, revision);
      } else revision = blocked!.revision;
      const result = await invoke(
        client,
        owner ? "run.stop_and_run" : "run.start",
        {
          model_id: model.id,
          ...(owner ? { owner } : { auto_session: model.auto_session }),
        },
        revision,
      );
      if (result.status === "busy")
        setBlocked({ owner: result.owner as AttemptKey, revision });
      else if (result.status === "stale")
        throw Error(
          "Inputs changed. Reload the current values before running.",
        );
      else {
        setBlocked(null);
        location.hash = routeHash({
          page: "run",
          attempt: (result.key as AttemptKey).attempt_id,
        });
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };
  const stopping = (draft?.options.stopping ?? {}) as Record<string, unknown>;
  const level = String(draft?.options.level ?? "full");
  const issues = model.inputs ? Object.entries(model.inputs.issues) : [];
  return (
    <>
      <p className="ws-back">
        <a href="#/models">← Models</a>
      </p>
      <div className="ws-page-heading">
        <div>
          <div className="ws-eyebrow">MODEL · {model.key}</div>
          <h1>{model.name}</h1>
          <p>
            <code>{model.entrypoint}</code> · edit the code in your editor; Studio
            reads it when you prepare the model.
          </p>
        </div>
        <div className="ws-heading-links">
          {model.revision && (
            <span className="ws-tag">Source revision {model.revision.number}</span>
          )}
          <a href={routeHash({ page: "runs", model: model.id })}>Runs of this model →</a>
          <a href="#/experiments">Define experiment →</a>
        </div>
      </div>
      {!model.inputs ? (
        <section className="ws-card">
          <h2>Inspect model inputs</h2>
          <p>
            Preparation reads metadata in a separate process. It does not start
            a simulation.
          </p>
          <Actions
            note={
              state.environment
                ? "Prepared metadata is cached against the captured source."
                : "An environment is needed to inspect the model."
            }
          >
            <button
              className="primary"
              {...disabledWhen(blockedReason, !state.environment && "Prepare an environment in Settings first")}
              onClick={() => void prepare()}
            >
              Prepare model
            </button>
            {!state.environment && (
              <a href="#/settings">Prepare an environment first →</a>
            )}
          </Actions>
        </section>
      ) : (
        draft &&
        meta && (
          <>
            <div className="ws-cols">
              <section className="ws-card">
                <div className="ws-section-heading">
                  <h2>Configure this run</h2>
                  <span className="ws-muted">Draft for {model.name}</span>
                </div>
                {draft.conflict && (
                  <div className="ws-notice" role="alert">
                    Inputs changed while you were editing. Your draft is retained.
                    <button
                      type="button"
                      onClick={() => {
                        drafts.reload(model.id);
                        redraw((n) => n + 1);
                      }}
                    >
                      Reload current inputs
                    </button>
                  </div>
                )}
                <FieldGroup legend="Model parameters" disabled={!enabled}>
                  {meta.parameters.fields.length === 0 && (
                    <p className="ws-hint ws-wide">This model declares no parameters.</p>
                  )}
                  {meta.parameters.fields.map((f) => {
                    const value = (
                      draft.options.parameters as Record<string, unknown>
                    )[f.name];
                    const set = (next: string) =>
                      edit({
                        parameters: {
                          ...(draft.options.parameters as object),
                          [f.name]: next,
                        },
                      });
                    return (
                      <Field
                        key={f.name}
                        label={f.name}
                        hint={f.description || f.type}
                        required={f.required}
                      >
                        {f.choices.length || f.type === "bool" ? (
                          <select
                            value={value === undefined ? "" : String(value)}
                            onChange={(e) => set(e.target.value)}
                          >
                            <option value="" disabled>
                              Choose…
                            </option>
                            {(f.choices.length ? f.choices : [true, false]).map(
                              (c) => (
                                <option key={String(c)} value={String(c)}>
                                  {String(c)}
                                </option>
                              ),
                            )}
                          </select>
                        ) : (
                          <input
                            inputMode={
                              f.type === "int" || f.type === "float" ? "decimal" : undefined
                            }
                            value={value === undefined ? "" : String(value)}
                            onChange={(e) => set(e.target.value)}
                          />
                        )}
                      </Field>
                    );
                  })}
                  {meta.parameters.fields
                    .filter((f) => f.type === "str" && (draft.options.parameters as Record<string, unknown>)[f.name] === undefined)
                    .map((f) => (
                      <button
                        key={f.name}
                        type="button"
                        className="ws-wide ws-inline"
                        onClick={() =>
                          edit({
                            parameters: {
                              ...(draft.options.parameters as object),
                              [f.name]: "",
                            },
                          })
                        }
                      >
                        Use empty string for {f.name}
                      </button>
                    ))}
                </FieldGroup>
                <FieldGroup legend="Execution" disabled={!enabled}>
                  <Field label="Seed" hint="Saved with the run for reproducibility">
                    <input
                      inputMode="numeric"
                      value={String(draft.options.seed ?? "0")}
                      onChange={(e) => edit({ seed: e.target.value })}
                    />
                  </Field>
                  <Field
                    label="Recording"
                    hint="Full recordings support playback and inspection; KPIs only keep numerical outputs"
                  >
                    <select
                      value={level}
                      onChange={(e) => edit({ level: e.target.value })}
                    >
                      <option value="full">Full recording</option>
                      <option value="kpi">KPIs only</option>
                    </select>
                  </Field>
                  {Object.entries(stopping)
                    .filter(([k]) => k !== "type")
                    .map(([k, v]) => (
                      <Field
                        key={k}
                        label={k.replaceAll("_", " ")}
                        {...(STOPPING_UNITS[k] ? { unit: STOPPING_UNITS[k] } : {})}
                        hint={`Stopping policy: ${String(stopping.type ?? "horizon")}`}
                      >
                        <input
                          inputMode="decimal"
                          value={v === null ? "" : String(v)}
                          onChange={(e) =>
                            edit({
                              stopping: {
                                ...(draft.options.stopping as object),
                                [k]: e.target.value,
                              },
                            })
                          }
                        />
                      </Field>
                    ))}
                </FieldGroup>
                {issues.length > 0 && (
                  <FailureNotice
                    compact
                    announce={false}
                    message={issues.map(([k, v]) => `${k}: ${String(v)}`).join("\n")}
                  />
                )}
                <Actions note="Runs start only when you choose Run.">
                  <button
                    className="primary"
                    {...disabledWhen(blockedReason, draft.conflict && "Reload the current inputs before running")}
                    onClick={() => void run()}
                  >
                    Run
                  </button>
                </Actions>
              </section>
              <aside className="ws-stack">
                <section className="ws-card ws-details">
                  <h2>This run will save</h2>
                  <dl className="ws-pairs">
                    <dt>Recording</dt>
                    <dd>
                      {level === "full"
                        ? "Numerical outputs and a full recording"
                        : "Numerical outputs only"}
                    </dd>
                    <dt>Stopping</dt>
                    <dd>
                      {String(stopping.type ?? "horizon")}
                      {Object.entries(stopping)
                        .filter(([k, v]) => k !== "type" && v !== null && v !== "")
                        .map(([k, v]) => ` · ${k.replaceAll("_", " ")} ${String(v)}`)
                        .join("")}
                    </dd>
                    <dt>Reproducibility</dt>
                    <dd>Model and input snapshot, parameters and seed</dd>
                  </dl>
                </section>
                <section className="ws-card ws-details">
                  <h3>Planning a study?</h3>
                  <p>
                    Use an experiment for multiple configurations and
                    replications. Recording is off by default there.
                  </p>
                  <a href="#/experiments">Define experiment →</a>
                  <br />
                  <a href={routeHash({ page: "runs", model: model.id })}>Runs of this model →</a>
                </section>
              </aside>
            </div>
            <details className="ws-card">
              <summary>Automatic runs and model preparation</summary>
              <p>
                Automatic runs can replace only their own active attempt. Other
                runs remain protected.
              </p>
              <CheckField label="Automatically run after source or input changes">
                <input
                  type="checkbox"
                  checked={!!model.auto_session}
                  disabled={!enabled}
                  onChange={(e) =>
                    void invoke(client, "model.auto", {
                      model_id: model.id,
                      enabled: e.target.checked,
                    }).catch((e) => setError(String(e)))
                  }
                />
              </CheckField>
              <button type="button" {...disabledWhen(blockedReason)} onClick={() => void prepare()}>
                Refresh model metadata
              </button>
            </details>
            <LayoutEditor
              client={client}
              modelId={model.id}
              state={{
                ...client.state!,
                revision: model.revision,
                inputs: model.inputs,
                view: model.view ?? null,
                last_good: null,
              }}
              status={connected ? "connected" : "reconnecting"}
            />
          </>
        )
      )}
      {blocked && (
        <section role="alert" className="ws-notice">
          <h2>The interactive slot is busy</h2>
          <p>Your inputs are saved. Another run owns the slot.</p>
          <a href={routeHash({ page: "run", attempt: blocked.owner.attempt_id })}>
            Inspect active run
          </a>
          <Actions>
            <button
              className="primary"
              {...disabledWhen(blockedReason)}
              onClick={() => void run(blocked.owner)}
            >
              Stop active run and run {model.name}
            </button>
            <button onClick={() => setBlocked(null)}>Keep waiting</button>
          </Actions>
        </section>
      )}
      {busy && <p role="status">Working…</p>}
      {error && <FailureNotice message={error} compact />}
    </>
  );
}
