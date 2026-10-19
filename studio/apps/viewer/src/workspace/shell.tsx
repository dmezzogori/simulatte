import { ExperimentsPage } from "./experiments-page";
import { useEffect, useState } from "react";
import { StudioClient, type ConnectionStatus } from "../studio-client";
import {
  parseRoute,
  WorkspaceDrafts,
  type WorkspaceRoute,
  type ProjectState,
} from "./state";
import { Overview, RunsPage } from "./overview";
import { Setup } from "./setup";
import { Models, ModelPage } from "./models";
import { RunPage } from "./run-page";
export function Sidebar({
  name,
  route,
}: {
  name: string;
  route: WorkspaceRoute;
}) {
  const active =
    route.page === "model"
      ? "models"
      : route.page === "run"
        ? "runs"
        : route.page;
  return (
    <aside className="ws-sidebar">
      <a className="ws-brand" href="#/overview">
        <span className="ws-brand-mark">S</span>Simulatte<span>STUDIO</span>
      </a>
      <div className="ws-project-name">
        <small>PROJECT</small>
        <strong>{name}</strong>
      </div>
      <nav aria-label="Project navigation">
        {[
          ["overview", "Overview"],
          ["models", "Models"],
          ["runs", "Runs"],
          ["experiments", "Experiments"],
          ["analyses", "Analyses"],
          ["presentations", "Presentations"],
        ].map(([page, label]) => (
          <a
            key={page}
            href={"#/" + page}
            aria-current={active === page ? "page" : undefined}
          >
            {label}
          </a>
        ))}
      </nav>
      <div className="ws-sidebar-bottom">
        <a
          href="#/settings"
          aria-current={active === "settings" ? "page" : undefined}
        >
          Settings
        </a>
        <small>Local project workspace</small>
      </div>
    </aside>
  );
}
export function WorkspaceShell({
  client,
  state,
  status = "connected",
}: {
  client: StudioClient;
  state: ProjectState;
  status?: ConnectionStatus;
}) {
  const [route, setRoute] = useState(() =>
    parseRoute(typeof location === "undefined" ? "" : location.hash),
  );
  const [drafts] = useState(() => new WorkspaceDrafts());
  useEffect(() => {
    const update = () => setRoute(parseRoute(location.hash));
    window.addEventListener("hashchange", update);
    return () => window.removeEventListener("hashchange", update);
  }, []);
  useEffect(() => {
    if (route.page !== "model" || status !== "connected") return;
    for (const m of state.models)
      if (m.id !== route.id && m.auto_session)
        void client
          .command("model.auto", { model_id: m.id, enabled: false })
          .catch(() => {});
  }, [route, status]);
  const connected = status === "connected";
  const model =
    route.page === "model" ? state.models.find((m) => m.id === route.id) : null;
  return (
    <div className="project-workspace">
      <a
        className="ws-skip"
        href="#workspace-content"
        onClick={(e) => {
          e.preventDefault();
          document.getElementById("workspace-content")?.focus();
        }}
      >
        Skip to content
      </a>
      <Sidebar name={state.name} route={route} />
      <div className="ws-body">
        <header className="ws-topbar">
          <span>
            {state.name}{" "}
            <span className="ws-muted">
              /{" "}
              {route.page === "model"
                ? model?.name
                : route.page === "run"
                  ? "Run"
                  : route.page}
            </span>
          </span>
          <span className="ws-connection" role="status">
            <i className={connected ? "online" : ""} />
            {status}
          </span>
        </header>
        {state.active_run && (
          <div className="ws-active">
            <span>
              <strong>
                {state.models.find((m) => m.id === state.active_run?.model_id)
                  ?.name ?? "Simulation"}
              </strong>{" "}
              · {state.active_run.status}
            </span>
            <a href={"#/run/" + state.active_run.key.attempt_id}>
              Inspect active run →
            </a>
          </div>
        )}
        {!connected && (
          <div className="ws-notice">
            {status === "authentication_required"
              ? "Open a fresh link from the serving terminal. A restarted Studio uses a new session."
              : "Reconnecting. Drafts are retained; commands wait for authoritative state."}
          </div>
        )}
        <main id="workspace-content" className="ws-content" tabIndex={-1}>
          {route.page === "overview" && (
            <>
              <Overview state={state}>
                {state.phase !== "ready" && (
                  <Setup client={client} state={state} connected={connected} />
                )}
              </Overview>
            </>
          )}
          {route.page === "models" && (
            <Models client={client} state={state} connected={connected} />
          )}
          {route.page === "model" &&
            (model ? (
              <ModelPage
                key={model.id}
                client={client}
                state={state}
                model={model}
                drafts={drafts}
                connected={connected}
              />
            ) : (
              <h1>Model no longer registered</h1>
            ))}
          {route.page === "runs" && <RunsPage client={client} state={state} />}
          {route.page === "run" && (
            <RunPage
              key={route.attempt}
              client={client}
              state={state}
              attempt={route.attempt}
              connected={connected}
            />
          )}
          {route.page === "settings" && (
            <>
              <div className="ws-page-heading">
                <h1>Project settings</h1>
                <p>Environment ownership and project setup.</p>
              </div>
              {state.environment && (
                <section className="ws-card">
                  <h2>Selected environment</h2>
                  <p>
                    {state.environment.kind} ·{" "}
                    {state.environment.id.slice(0, 12)}
                  </p>
                  <code>{state.environment.interpreter}</code>
                </section>
              )}
              <Setup client={client} state={state} connected={connected} />
            </>
          )}
          {route.page === "experiments" && (
            <ExperimentsPage client={client} state={state} status={status} />
          )}
          {["analyses", "presentations"].includes(route.page) && (
            <div className="ws-page-heading">
              <div className="ws-eyebrow">{route.page.toUpperCase()}</div>
              <h1>{route.page[0]!.toUpperCase() + route.page.slice(1)}</h1>
              <div className="ws-empty">
                <h2>{"This workspace is not available yet"}</h2>
                <p>
                  {"Saved model runs and recordings remain available in Runs."}
                </p>
                <a href="#/models">Back to models →</a>
              </div>
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
export function WorkspaceApplication() {
  const [state, setState] = useState<ProjectState | null>(null),
    [status, setStatus] = useState<ConnectionStatus>("connecting"),
    [error, setError] = useState("");
  const [client] = useState(
    () =>
      new StudioClient(sessionStorage.getItem("simulatte-session") ?? "", {
        onState: (s) => {
          if (s.project) setState(s.project);
        },
        onStatus: setStatus,
        onError: (e) => setError(String(e)),
      }),
  );
  useEffect(() => {
    client.connect();
    return () => client.dispose();
  }, [client]);
  return state ? (
    <WorkspaceShell client={client} state={state} status={status} />
  ) : (
    <main className="ws-loading">
      <h1>Opening project…</h1>
      <p role="status">{status}</p>
      {error && <p role="alert">{error}</p>}
    </main>
  );
}
