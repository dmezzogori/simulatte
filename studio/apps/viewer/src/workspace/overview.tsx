import type { ReactNode } from "react";
import { DataTable, formatTime, type Column } from "./tables";
import { routeHash, type HistoryItem, type ProjectState } from "./state";
/** The project's next useful step, derived from its state. */
function nextAction(state: ProjectState): {
  title: string;
  text: string;
  link: { label: string; hash: string };
} {
  if (state.active_run)
    return {
      title: "A run is in progress",
      text: "Browse other pages freely; the run keeps going and its result stays saved.",
      link: {
        label: "Inspect active run",
        hash: routeHash({ page: "run", attempt: state.active_run.key.attempt_id }),
      },
    };
  if (!state.models.length)
    return {
      title: "Start with a model",
      text: "Register a Python model or create a starter project.",
      link: { label: "Open models", hash: "#/models" },
    };
  const latest = state.history.find((r) => r.has_result);
  if (latest)
    return {
      title: "Review your latest run",
      text: `${latest.name || "The latest run"} · ${formatTime(latest.created)}`,
      link: { label: "Open run", hash: routeHash({ page: "run", attempt: latest.id }) },
    };
  const first = state.models[0]!;
  return {
    title: "Run your first simulation",
    text: `Configure ${first.name}, then press Run. Opening this project starts nothing.`,
    link: { label: `Configure ${first.name}`, hash: routeHash({ page: "model", id: first.id }) },
  };
}
export function Overview({
  state,
  children,
}: {
  state: ProjectState;
  children?: ReactNode;
}) {
  const next = nextAction(state);
  return (
    <>
      <div className="ws-page-heading">
        <h1>{state.name}</h1>
        <p>Build in Python. Run experiments. Inspect what happened.</p>
      </div>
      {children}
      {state.models.length > 0 && (
        <div className="ws-tiles">
          <section className="ws-tile">
            <div className="ws-number">{state.models.length}</div>
            <h2>Simulation models</h2>
            <p>Named entrypoints with independent inputs and saved history.</p>
            <a href="#/models">Explore models →</a>
          </section>
          <section className="ws-tile">
            <div className="ws-number">
              {state.history.filter((r) => r.has_result).length}
            </div>
            <h2>Saved runs</h2>
            <p>Individual runs keep their configuration, source and outputs.</p>
            <a href="#/runs">All runs →</a>
          </section>
          <section className="ws-tile">
            <div className="ws-number">
              {state.environment ? "Ready" : state.read_only ? "Read-only" : "Setup"}
            </div>
            <h2>Project environment</h2>
            <p>
              {state.environment
                ? "Dependencies are prepared; experiments can run."
                : "Prepare an environment before running models."}
            </p>
            <a href={state.environment ? "#/experiments" : "#/settings"}>
              {state.environment ? "Open experiments →" : "Project settings →"}
            </a>
          </section>
        </div>
      )}
      {state.phase === "ready" && (
        <div className="ws-next">
          <div>
            <strong>{next.title}</strong>
            <p>{next.text}</p>
          </div>
          <a className="ws-button primary" href={next.link.hash}>
            {next.link.label} →
          </a>
        </div>
      )}
      {state.models.length > 0 && (
        <section className="ws-section">
          <div className="ws-section-heading">
            <h2>Your models</h2>
            <a href="#/models">Manage models →</a>
          </div>
          <div className="ws-grid">
            {state.models.map((m) => (
              <a
                className="ws-card ws-model-card"
                href={routeHash({ page: "model", id: m.id })}
                key={m.id}
              >
                <h3>{m.name}</h3>
                <code>{m.entrypoint}</code>
                <span className="ws-link">Configure and run →</span>
              </a>
            ))}
          </div>
        </section>
      )}
      <section className="ws-section">
        <div className="ws-section-heading">
          <h2>Recent runs</h2>
          <a href="#/runs">All runs →</a>
        </div>
        {state.history.length ? (
          <HistoryList state={state} limit={5} />
        ) : (
          <div className="ws-state ws-empty">
            <h3>No runs yet</h3>
            <p>
              Choose a model and press Run when its inputs are ready. Opening
              this project starts nothing.
            </p>
          </div>
        )}
      </section>
    </>
  );
}
export function HistoryList({
  state,
  limit = 50,
}: {
  state: ProjectState;
  limit?: number;
}) {
  const columns: Column<HistoryItem>[] = [
    {
      key: "run",
      header: "Run",
      render: (r) => (
        <a href={routeHash({ page: "run", attempt: r.id })}>
          {r.important ? "★ " : ""}
          {r.name || "Run " + r.id.slice(0, 8)}
        </a>
      ),
    },
    {
      key: "model",
      header: "Model",
      render: (r) =>
        state.models.find((m) => m.id === r.model_id)?.name ??
        (r.model_id ? "Removed model" : "Unassigned"),
    },
    {
      key: "status",
      header: "Status",
      render: (r) => <span className={"ws-badge " + r.status}>{r.status}</span>,
    },
    {
      key: "created",
      header: "Created",
      align: "right",
      render: (r) => formatTime(r.created),
    },
  ];
  return (
    <DataTable
      columns={columns}
      rows={state.history.slice(0, limit)}
      rowKey={(r) => r.id}
    />
  );
}
