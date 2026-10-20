/** Run history by model. The filter lives in the route so a bookmark reopens the same view. */
import { useEffect, useState } from "react";
import type { StudioClient } from "../studio-client";
import { Feedback, type PageState } from "./feedback";
import { Field } from "./forms";
import { HistoryList } from "./overview";
import { routeHash, type HistoryItem, type ProjectState } from "./state";
export function RunsPage({
  client,
  state,
  modelId,
}: {
  client: StudioClient;
  state: ProjectState;
  modelId?: string;
}) {
  const [page, setPage] = useState<PageState<HistoryItem[]>>(() =>
    modelId === undefined
      ? state.history.length
        ? { status: "ready", value: state.history }
        : { status: "empty" }
      : { status: "loading" },
  );
  const [cursor, setCursor] = useState<string | null>(null),
    [busy, setBusy] = useState(false),
    [more, setMore] = useState("");
  const load = async (after: string | null) => {
    setBusy(true);
    setMore("");
    try {
      const r = await client.command("history.get", {
        model_id: modelId ?? null,
        after,
      });
      if (!r.ok) throw Error((r.error as { message: string }).message);
      const items = r.items as HistoryItem[];
      setPage((old) => {
        const value =
          after && old.status === "ready" ? [...old.value, ...items] : items;
        return value.length ? { status: "ready", value } : { status: "empty" };
      });
      setCursor(r.next_cursor as string | null);
    } catch (e) {
      if (after) setMore(String(e));
      else
        setPage({
          status: "error",
          message: String(e),
          retry: { label: "Try again", invoke: () => void load(null) },
        });
    } finally {
      setBusy(false);
    }
  };
  useEffect(() => {
    void load(null);
  }, [modelId, client]);
  const model = modelId ? state.models.find((m) => m.id === modelId) : null;
  return (
    <>
      <div className="ws-page-heading">
        <div className="ws-eyebrow">PROJECT HISTORY</div>
        <h1>{model ? `Runs of ${model.name}` : "Runs"}</h1>
        <p>Captured inputs and results stay with the project.</p>
      </div>
      <div className="ws-toolbar">
        <Field label="Filter by model">
          <select
            value={modelId ?? ""}
            onChange={(e) => {
              location.hash = routeHash(
                e.target.value
                  ? { page: "runs", model: e.target.value }
                  : { page: "runs" },
              );
            }}
          >
            <option value="">
              All models, including removed and unassigned
            </option>
            {state.models.map((m) => (
              <option value={m.id} key={m.id}>
                {m.name}
              </option>
            ))}
          </select>
        </Field>
        <button type="button" disabled={busy} onClick={() => void load(null)}>
          Refresh history
        </button>
      </div>
      <Feedback
        state={page}
        loading="Loading saved runs…"
        empty={{
          title: "No runs yet",
          text: model
            ? `${model.name} has no saved runs.`
            : "Choose a model and press Run when its inputs are ready.",
          link: model
            ? { label: `Configure ${model.name}`, hash: routeHash({ page: "model", id: model.id }) }
            : { label: "Choose a model to begin", hash: "#/models" },
        }}
      >
        {(items) => (
          <>
            <HistoryList state={{ ...state, history: items }} limit={items.length} />
            {cursor && (
              <div className="ws-actions">
                <button type="button" disabled={busy} onClick={() => void load(cursor)}>
                  Load more runs
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
