import { useState } from "react";
import type { StudioClient, Command } from "../studio-client";
import type { ProjectState } from "./state";
import { Diagnostic, FailureNotice } from "./feedback";
import { Field, NOT_CONNECTED, WORKING, disabledWhen } from "./forms";
export async function invoke(
  client: StudioClient,
  type: Command,
  body: Record<string, unknown> = {},
  revision?: import("../studio-client").Revision | null,
) {
  const result = await client.command(
    type,
    body,
    revision === undefined ? {} : { revision },
  );
  if (!result.ok)
    throw new Error(
      (result.error as { message?: string })?.message ?? "Command failed",
    );
  await client.resync();
  return result;
}
export function Setup({
  client,
  state,
  connected,
}: {
  client: StudioClient;
  state: ProjectState;
  connected: boolean;
}) {
  const [preview, setPreview] = useState<{
    preview_id: string;
    files: Record<string, string>;
  } | null>(null);
  const [operation, setOperation] = useState<string | null>(null);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [python, setPython] = useState("");
  const act = async (type: Command, body: Record<string, unknown>) => {
    setBusy(true);
    setError("");
    const operationId = type === "environment.prepare" ? crypto.randomUUID() : null;
    setOperation(operationId);
    try {
      const result = await invoke(client, type, operationId ? {...body, operation_id: operationId} : body);
      if (result.status === "busy") throw new Error("The current environment still has active work. Wait for it to finish or explicitly stop it, then select this generation again.");
      if (result.preview_id) setPreview(result as unknown as typeof preview);
      else setPreview(null);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
      setOperation(null);
    }
  };
  if (state.read_only)
    return (
      <section className="ws-card recovery">
        <h2>Saved work is available</h2>
        {state.error && <FailureNotice message={state.error} compact readOnly />}
        <p>
          This session is read-only. Repair the selected environment or close
          the other Studio process, then reopen this folder. Your saved runs
          remain in Runs.
        </p>
        <a href="#/runs">Browse saved runs →</a>
      </section>
    );
  return (
    <section className="ws-card">
      <div className="ws-eyebrow">PROJECT SETUP</div>
      <h2>{state.id ? "Prepare your environment" : "Start with a model"}</h2>
      <p>
        {state.id
          ? "Install dependencies into a separate environment. Preparation never starts a simulation."
          : "Create a small Python project you can also run from a terminal, or register your own model."}
      </p>
      {!state.id && (
        <div className="ws-grid">
          <button
            className="ws-choice"
            {...disabledWhen(busy && WORKING, !connected && NOT_CONNECTED)}
            onClick={() =>
              void act("project.configure", { starter: "production" })
            }
          >
            <strong>Production</strong>
            <span>Jobs, machines and a production flow</span>
          </button>
          <button
            className="ws-choice"
            {...disabledWhen(busy && WORKING, !connected && NOT_CONNECTED)}
            onClick={() =>
              void act("project.configure", { starter: "logistics" })
            }
          >
            <strong>Intralogistics</strong>
            <span>Transport orders and an AGV fleet</span>
          </button>
        </div>
      )}
      {!state.id && (
        <p>
          <a href="#/models">Register an existing Python model →</a>
        </p>
      )}
      {state.id && (
        <div className="ws-actions">
          <button
            className="primary"
            {...disabledWhen(busy && WORKING, !connected && NOT_CONNECTED)}
            onClick={() => void act("environment.prepare", {})}
          >
            Prepare environment
          </button>
          <button {...disabledWhen(busy && WORKING, !connected && NOT_CONNECTED)} onClick={()=>void act("environment.prepare",{lock_update:true})}>Preview dependency update</button>
          <button
            {...disabledWhen(!operation && "No preparation is running")}
            onClick={() => {
              if (operation) void client.cancelPreparation(operation).catch((e) => setError(String(e)));
            }}
          >
            Cancel preparation
          </button>
        </div>
      )}
      {preview && (
        <div className="ws-preview">
          <h3>Review proposed files</h3>
          <p>These changes are applied only when you choose Apply changes.</p>
          {Object.entries(preview.files).map(([name, text]) => (
            <details key={name} open>
              <summary>{name}</summary>
              <pre>{text}</pre>
            </details>
          ))}
          <div className="ws-actions">
            <button
              className="primary"
              {...disabledWhen(busy && WORKING)}
              onClick={() =>
                void act("project.configure", { apply: preview.preview_id })
              }
            >
              Apply changes
            </button>
            <button onClick={() => setPreview(null)}>Discard preview</button>
          </div>
        </div>
      )}
      {state.generations
        .filter((g) => g.state !== "selected")
        .map((g) => (
          <div className="ws-generation" key={g.id}>
            <span>
              Environment ready · {g.kind === "managed" ? "managed by Studio" : "existing interpreter"}
              <Diagnostic lines={["Generation " + g.id, g.interpreter]} />
            </span>
            <button
              {...disabledWhen(busy && WORKING, !connected && NOT_CONNECTED)}
              onClick={() =>
                void act("environment.select", { id: g.id, restart: true })
              }
            >
              Use environment and restart
            </button>
          </div>
        ))}
      {state.id && (
        <details>
          <summary>Use an existing interpreter (advanced)</summary>
          <p>
            Studio verifies its dependencies; it does not install into this
            environment.
          </p>
          <Field label="Python interpreter path" hint="Absolute path to a Python executable">
            <input
              value={python}
              onChange={(e) => setPython(e.target.value)}
              placeholder="/path/to/environment/bin/python"
            />
          </Field>
          <button
            {...disabledWhen(busy && WORKING, !connected && NOT_CONNECTED, !python && "Enter an interpreter path first")}
            onClick={() =>
              void act("environment.prepare", { existing: python })
            }
          >
            Check interpreter
          </button>
        </details>
      )}
      {busy && <p role="status">Preparing… This may take a few minutes.</p>}
      {state.preparation_progress && (
        <details>
          <summary>Preparation log</summary>
          <pre>{state.preparation_progress}</pre>
        </details>
      )}
      {error && <FailureNotice message={error} compact />}
    </section>
  );
}
