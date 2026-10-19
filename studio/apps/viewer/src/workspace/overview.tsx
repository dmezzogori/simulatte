import { useEffect, useState, type ReactNode } from "react";
import type { ProjectState } from "./state";
export function Overview({
  state,
  children,
}: {
  state: ProjectState;
  children?: ReactNode;
}) {
  return (
    <>
      <div className="ws-page-heading">
        <div className="ws-eyebrow">PROJECT OVERVIEW</div>
        <h1>{state.name}</h1>
        <p>Build in Python. Run experiments. Inspect what happened.</p>
      </div>
      {children}
      {state.models.length > 0 && (
        <div className="ws-stats">
          <div>
            <strong>{state.models.length}</strong>
            <span>Registered models</span>
          </div>
          <div>
            <strong>{state.history.filter((r) => r.has_result).length}</strong>
            <span>Recent saved runs</span>
          </div>
          <div>
            <strong>
              {state.environment
                ? "Ready"
                : state.read_only
                  ? "Read-only"
                  : "Setup"}
            </strong>
            <span>Project environment</span>
          </div>
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
                href={"#/model/" + encodeURIComponent(m.id)}
                key={m.id}
              >
                <span className="ws-eyebrow">PYTHON MODEL</span>
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
          <div className="ws-empty">
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
  return (
    <table className="ws-table">
      <thead>
        <tr>
          <th>Run</th>
          <th>Model</th>
          <th>Status</th>
          <th>Created</th>
        </tr>
      </thead>
      <tbody>
        {state.history.slice(0, limit).map((r) => (
          <tr key={r.id}>
            <td>
              <a href={"#/run/" + r.id}>
                {r.important ? "★ " : ""}
                {r.name || r.id.slice(0, 8)}
              </a>
            </td>
            <td>
              {state.models.find((m) => m.id === r.model_id)?.name ??
                (r.model_id ? "Removed model" : "Unassigned")}
            </td>
            <td>
              <span className={"ws-badge " + r.status}>{r.status}</span>
            </td>
            <td>{new Date(r.created * 1000).toLocaleString()}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function RunsPage({
  client,
  state,
}: {
  client: import("../studio-client").StudioClient;
  state: ProjectState;
}) {
  const [filter, setFilter] = useState(""),
    [items, setItems] = useState(state.history),
    [cursor, setCursor] = useState<string | null>(null),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const load = async (after: string | null) => {
    setBusy(true);
    try {
      const r = await client.command("history.get", {
        model_id: filter || null,
        after,
      });
      if (!r.ok) throw Error((r.error as { message: string }).message);
      setItems((old) =>
        after ? [...old, ...(r.items as typeof old)] : (r.items as typeof old),
      );
      setCursor(r.next_cursor as string | null);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };
  useEffect(() => {
    void load(null);
  }, [filter, client]);
  return (
    <>
      <div className="ws-page-heading">
        <div className="ws-eyebrow">PROJECT HISTORY</div>
        <h1>Runs</h1>
        <p>Captured inputs and results stay with the project.</p>
      </div>
      <div className="ws-actions">
        <label>
          Filter by model
          <select value={filter} onChange={(e) => setFilter(e.target.value)}>
            <option value="">
              All models, including removed and unassigned
            </option>
            {state.models.map((m) => (
              <option value={m.id} key={m.id}>
                {m.name}
              </option>
            ))}
          </select>
        </label>
        <button disabled={busy} onClick={() => void load(null)}>
          Refresh history
        </button>
      </div>
      {items.length ? (
        <HistoryList
          state={{ ...state, history: items }}
          limit={items.length}
        />
      ) : (
        <div className="ws-empty">
          <h2>No runs yet</h2>
          <a href="#/models">Choose a model to begin →</a>
        </div>
      )}
      {cursor && (
        <button disabled={busy} onClick={() => void load(cursor)}>
          Load more runs
        </button>
      )}
      {error && <p role="alert">{error}</p>}
    </>
  );
}
