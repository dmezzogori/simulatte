import { useState } from "react";
import type { StudioClient, Revision, AttemptKey } from "../studio-client";
import {
  parseField,
  parseExecutionOptions,
  type ExecutionControls,
} from "../parameter-draft";
import { LayoutEditor } from "../layout-editor";
import { invoke } from "./setup";
import { WorkspaceDrafts, type ProjectModel, type ProjectState } from "./state";
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
      {state.models.map((m) => (
        <div className="ws-card ws-model-row" key={m.id}>
          <div>
            <h2>
              <a href={"#/model/" + m.id}>{m.name}</a>
            </h2>
            <code>{m.entrypoint}</code>
            <p>Layout: {m.layout}</p>
          </div>
          <button
            disabled={state.read_only || busy || !connected}
            onClick={() =>
              void change({ models: state.models.filter((v) => v.id !== m.id) })
            }
          >
            Review removal
          </button>
        </div>
      ))}
      {!state.read_only && (
        <section className="ws-card">
          <h2>Register a Python model</h2>
          <p>
            The file must be inside this project. Registration preserves its
            code and does not run it.
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
                    layout: `layouts/${key}.json`,
                  },
                ],
                include: [entry.split(":")[0]],
              });
            }}
          >
            <div className="ws-form-grid">
              <label>
                Registration key
                <input
                  required
                  value={key}
                  onChange={(e) => setKey(e.target.value)}
                />
              </label>
              <label>
                Display name
                <input
                  required
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                />
              </label>
              <label className="ws-wide">
                Entrypoint
                <input
                  required
                  value={entry}
                  onChange={(e) => setEntry(e.target.value)}
                  placeholder="models/plant.py:build"
                />
              </label>
            </div>
            <button className="primary" disabled={busy || !connected}>
              Preview registration
            </button>
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
          <button
            className="primary"
            disabled={busy}
            onClick={() => void change({ apply: preview.preview_id })}
          >
            Apply registration
          </button>
          <button onClick={() => setPreview(null)}>Discard</button>
        </section>
      )}
      {error && <p role="alert">{error}</p>}
    </>
  );
}
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
        location.hash = "#/run/" + (result.key as AttemptKey).attempt_id;
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <div className="ws-page-heading">
        <div className="ws-eyebrow">MODEL · {model.key}</div>
        <h1>{model.name}</h1>
        <code>{model.entrypoint}</code>
      </div>
      {!model.inputs ? (
        <section className="ws-card">
          <h2>Inspect model inputs</h2>
          <p>
            Preparation reads metadata in a separate process. It does not start
            a simulation.
          </p>
          <button
            className="primary"
            disabled={!enabled || !state.environment}
            onClick={() => void prepare()}
          >
            Prepare model
          </button>
          {!state.environment && (
            <p>
              <a href="#/settings">Prepare an environment first →</a>
            </p>
          )}
        </section>
      ) : (
        draft &&
        meta && (
          <>
            <section className="ws-card">
              <div className="ws-section-heading">
                <h2>Configure this run</h2>
                <span className="ws-muted">Draft for {model.name}</span>
              </div>
              {draft.conflict && (
                <div className="ws-notice" role="alert">
                  Inputs changed while you were editing. Your draft is retained.
                  <button
                    onClick={() => {
                      drafts.reload(model.id);
                      redraw((n) => n + 1);
                    }}
                  >
                    Reload current inputs
                  </button>
                </div>
              )}
              <fieldset disabled={!enabled}>
                <legend>Parameters</legend>
                <div className="ws-form-grid">
                  {meta.parameters.fields.map((f) => {
                    const value = (
                      draft.options.parameters as Record<string, unknown>
                    )[f.name];
                    return (
                      <label key={f.name}>
                        {f.name}
                        {f.required ? " *" : ""}
                        {f.choices.length || f.type === "bool" ? (
                          <select
                            value={value === undefined ? "" : String(value)}
                            onChange={(e) =>
                              edit({
                                parameters: {
                                  ...(draft.options.parameters as object),
                                  [f.name]: e.target.value,
                                },
                              })
                            }
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
                            value={value === undefined ? "" : String(value)}
                            onChange={(e) =>
                              edit({
                                parameters: {
                                  ...(draft.options.parameters as object),
                                  [f.name]: e.target.value,
                                },
                              })
                            }
                          />
                        )}
                        {value === undefined && f.type === "str" && (
                          <button
                            type="button"
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
                        )}
                        <small>
                          {f.description ||
                            `${f.type}${f.required ? " · required" : ""}`}
                        </small>
                      </label>
                    );
                  })}
                </div>
              </fieldset>
              <fieldset disabled={!enabled}>
                <legend>Execution</legend>
                <div className="ws-form-grid">
                  <label>
                    Seed
                    <input
                      inputMode="numeric"
                      value={String(draft.options.seed ?? "0")}
                      onChange={(e) => edit({ seed: e.target.value })}
                    />
                  </label>
                  <label>
                    Recording
                    <select
                      value={String(draft.options.level ?? "full")}
                      onChange={(e) => edit({ level: e.target.value })}
                    >
                      <option value="full">Full recording</option>
                      <option value="kpi">KPIs only</option>
                    </select>
                  </label>
                  {Object.entries(
                    draft.options.stopping as Record<string, unknown>,
                  )
                    .filter(([k]) => k !== "type")
                    .map(([k, v]) => (
                      <label key={k}>
                        {k.replaceAll("_", " ")}
                        <input
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
                      </label>
                    ))}
                </div>
              </fieldset>
              {Object.keys(model.inputs.issues).length > 0 && (
                <p className="ws-notice">
                  {Object.entries(model.inputs.issues)
                    .map(([k, v]) => `${k}: ${String(v)}`)
                    .join("; ")}
                </p>
              )}
              <div className="ws-actions">
                <button
                  className="primary"
                  disabled={!enabled || draft.conflict}
                  onClick={() => void run()}
                >
                  Run
                </button>
                <span className="ws-muted">Runs only when you choose.</span>
              </div>
            </section>
            <details className="ws-card">
              <summary>Automatic runs and model preparation</summary>
              <p>
                Automatic runs can replace only their own active attempt. Other
                runs remain protected.
              </p>
              <label className="ws-check">
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
                Automatically run after source or input changes
              </label>
              <button disabled={!enabled} onClick={() => void prepare()}>
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
          <a href={"#/run/" + blocked.owner.attempt_id}>Inspect active run</a>
          <div className="ws-actions">
            <button
              className="primary"
              disabled={!enabled}
              onClick={() => void run(blocked.owner)}
            >
              Stop active run and run {model.name}
            </button>
            <button onClick={() => setBlocked(null)}>Keep waiting</button>
          </div>
        </section>
      )}
      {busy && <p role="status">Working…</p>}
      {error && <p role="alert">{error}</p>}
    </>
  );
}
