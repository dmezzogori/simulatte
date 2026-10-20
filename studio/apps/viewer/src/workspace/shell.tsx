import { useEffect, useReducer, useRef, useState } from "react";
import { StudioClient, type ConnectionStatus } from "../studio-client";
import {
  parseRoute,
  routeCrumbs,
  routeHash,
  routeSection,
  WorkspaceDrafts,
  type WorkspaceRoute,
  type ProjectState,
} from "./state";
import { shellReducer, type Notice, type NoticeContext } from "./notices";
import { Overview } from "./overview";
import { Setup } from "./setup";
import { Models, ModelPage } from "./models";
import { RunPage } from "./run-page";
import { RunsPage } from "./runs";
import { ExperimentsPage } from "./experiments";
import { ComparisonPage } from "./comparison";
import { AnalysesPage } from "./analyses";
import { PresentationsPage } from "./presentations";
import { Diagnostic, EmptyState } from "./feedback";
const ICONS: Record<string, string> = {
  overview: "M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z",
  models: "m12 3 9 5-9 5-9-5 9-5Zm-9 9 9 5 9-5M3 16l9 5 9-5",
  runs: "m9 5 11 7-11 7V5Z",
  experiments: "M8 3h8M10 3v6l-6 10q-1 2 2 2h12q3 0 2-2L14 9V3M7 15h10",
  analyses: "M4 3v18h17M8 16v-5m5 5V7m5 9v-3",
  presentations: "M3 4h18v13H3zM12 17v4m-4 0h8m-6-14 6 4-6 3V7Z",
};
export function Sidebar({
  name,
  route,
  counts = {},
}: {
  name: string;
  route: WorkspaceRoute;
  counts?: Record<string, number>;
}) {
  const active = routeSection(route);
  return (
    <aside className="ws-sidebar">
      <a className="ws-brand" href="#/overview">
        <span className="ws-brand-mark" aria-hidden="true">
          s
        </span>
        <span>
          simulatte<em>STUDIO</em>
        </span>
      </a>
      <div className="ws-project-name">
        <strong>{name}</strong>
        <small>Project workspace</small>
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
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d={ICONS[page!]} />
            </svg>
            <span>{label}</span>
            {counts[page!] ? (
              <small aria-hidden="true">{counts[page!]}</small>
            ) : null}
          </a>
        ))}
      </nav>
      <div className="ws-sidebar-bottom">
        <a
          href="#/settings"
          aria-current={active === "settings" ? "page" : undefined}
        >
          <span>Settings</span>
        </a>
        <small>
          <i aria-hidden="true" />
          Local workspace · changes are saved in the project
        </small>
      </div>
    </aside>
  );
}
function Notices({
  notices,
  dismiss,
}: {
  notices: readonly Notice[];
  dismiss: (id: string) => void;
}) {
  if (!notices.length) return null;
  return (
    <div className="ws-notices" aria-label="Completed work">
      {notices.map((n) => (
        <div
          key={n.id}
          className={"ws-toast " + n.tone}
          role="status"
          onKeyDown={(e) => {
            if (e.key === "Escape") {
              e.preventDefault();
              dismiss(n.id);
            }
          }}
        >
          <span>
            <strong>{n.title}</strong> {n.text}
          </span>
          <a href={n.hash}>Open →</a>
          <button
            type="button"
            aria-label={"Dismiss: " + n.title}
            onClick={() => dismiss(n.id)}
          >
            Dismiss
          </button>
        </div>
      ))}
    </div>
  );
}
export function WorkspaceShell({
  client,
  state,
  status = "connected",
  notices: initialNotices = [],
}: {
  client: StudioClient;
  state: ProjectState;
  status?: ConnectionStatus;
  /** Initial notices, for rendering tests. */
  notices?: Notice[];
}) {
  const [shell, dispatch] = useReducer(shellReducer, initialNotices, (notices) => ({
    route: parseRoute(typeof location === "undefined" ? "" : location.hash),
    notices,
  }));
  const route = shell.route;
  const [drafts] = useState(() => new WorkspaceDrafts());
  // The notification subscription outlives renders; it reads the latest names through a ref.
  const context = useRef<NoticeContext>({ models: [], activeRun: null });
  useEffect(() => {
    context.current = {
      models: state.models,
      activeRun: state.active_run
        ? { attempt_id: state.active_run.key.attempt_id, model_id: state.active_run.model_id }
        : null,
    };
  }, [state]);
  useEffect(() => {
    const update = () => dispatch({ type: "navigate", hash: location.hash });
    window.addEventListener("hashchange", update);
    return () => window.removeEventListener("hashchange", update);
  }, []);
  useEffect(
    () =>
      client.subscribeNotifications((message) =>
        dispatch({ type: "notification", message, context: context.current }),
      ),
    [client],
  );
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
  const crumbs = routeCrumbs(route, state.models);
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
      <Sidebar
        name={state.name}
        route={route}
        counts={{ models: state.models.length, runs: state.history.length }}
      />
      <div className="ws-body">
        <header className="ws-topbar">
          <nav aria-label="Breadcrumb" className="ws-crumbs">
            <ol>
              <li>
                <a href="#/overview">{state.name}</a>
              </li>
              {crumbs.map((c, i) => (
                <li key={i} aria-current={i === crumbs.length - 1 ? "page" : undefined}>
                  {c.hash && i < crumbs.length - 1 ? (
                    <a href={c.hash}>{c.label}</a>
                  ) : (
                    <span>{c.label}</span>
                  )}
                </li>
              ))}
            </ol>
          </nav>
          <span className="ws-connection" role="status">
            <i className={connected ? "online" : ""} aria-hidden="true" />
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
        <Notices
          notices={shell.notices}
          dismiss={(id) => dispatch({ type: "dismiss", id })}
        />
        <main id="workspace-content" className="ws-content" tabIndex={-1}>
          {route.page === "overview" && (
            <Overview state={state}>
              {state.phase !== "ready" && (
                <Setup client={client} state={state} connected={connected} />
              )}
            </Overview>
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
              <EmptyState
                screen={{
                  title: "Model no longer registered",
                  text: "Its saved runs remain available in Runs.",
                  link: { label: "Back to models", hash: "#/models" },
                }}
              />
            ))}
          {route.page === "runs" && (
            <RunsPage
              key={route.model ?? ""}
              client={client}
              state={state}
              {...(route.model ? { modelId: route.model } : {})}
            />
          )}
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
                <div className="ws-eyebrow">PROJECT SETTINGS</div>
                <h1>Project settings</h1>
                <p>Environment ownership and project setup.</p>
              </div>
              {state.environment && (
                <section className="ws-card">
                  <h2>Selected environment</h2>
                  <p>
                    {state.environment.kind === "managed"
                      ? "Managed by Studio"
                      : "Existing interpreter"}{" "}
                    · <code>{state.environment.interpreter}</code>
                  </p>
                  <Diagnostic
                    lines={[
                      "Generation " + state.environment.id,
                      "State " + state.environment.state,
                    ]}
                  />
                </section>
              )}
              <Setup client={client} state={state} connected={connected} />
            </>
          )}
          {route.page === "experiments" && (
            <ExperimentsPage
              client={client}
              state={state}
              status={status}
              {...(route.run ? { experimentRunId: route.run } : {})}
            />
          )}
          {route.page === "comparison" && (
            <ComparisonPage client={client} state={state} selection={route.selection} />
          )}
          {route.page === "analyses" && (
            <AnalysesPage
              client={client}
              state={state}
              status={status}
              {...(route.execution ? { executionId: route.execution } : {})}
              {...(route.definition ? { registrationKey: route.definition } : {})}
            />
          )}
          {route.page === "presentations" && (
            <PresentationsPage
              client={client}
              state={state}
              status={status}
              {...(route.id ? { presentationId: route.id } : {})}
              {...(route.attempt ? { attemptId: route.attempt } : {})}
              present={route.present === true}
            />
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
