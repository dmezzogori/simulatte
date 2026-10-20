/** Experiments: the existing experiment and compare tools behind the focused route. ``experimentRunId`` names the run a bookmark selected. */
import { useEffect, useMemo, useState } from "react";
import { Experiments } from "../experiments";
import { ComparePanel, ReplayNotifications } from "../compare-panel";
import { ExperimentFeed } from "../experiment-feed";
import type {
  StudioClient,
  StudioState,
  ConnectionStatus,
  Command,
} from "../studio-client";
import type { ProjectState } from "./state";
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
    [error, setError] = useState("");
  const model = state.models.find((m) => m.id === id);
  const [feed] = useState(
    () => new ExperimentFeed((ref) => client.reference(ref)),
  );
  const [replays] = useState(() => new ReplayNotifications(ref => client.reference(ref)));
  useEffect(() => {
    const reconcile = (value: StudioState, arrival: number) => {feed.observe(arrival); replays.observe(value.replays ?? [], arrival);};
    const unnotify = client.subscribeNotifications(message => {feed.notification(message); replays.notification(message);});
    const unstate = client.subscribeState(reconcile);
    if(client.state) reconcile(client.state, client.stateArrival);
    return () => {unnotify(); unstate(); feed.dispose(); replays.dispose();};
  }, [client, feed, replays]);
  const scoped = useMemo(
    () =>
      new Proxy(client, {
        get(target, key) {
          if (key === "command")
            return (
              type: Command,
              body: Record<string, unknown> = {},
              options = {},
            ) => target.command(type, { ...body, model_id: id }, options);
          const value = Reflect.get(target, key, target);
          return typeof value === "function" ? value.bind(target) : value;
        },
      }),
    [client, id],
  );
  const legacy: StudioState = {
    ...client.state!,
    revision: model?.revision ?? null,
    metadata: model?.metadata_reference ?? null,
    model: model?.entrypoint.split(":")[0] ?? "",
    experiment_files: model?.experiment_files ?? [],
  };
  return (
    <>
      <div className="ws-page-heading">
        <div className="ws-eyebrow">NUMERICAL EXPERIMENTS</div>
        <h1>Experiments</h1>
        <p>
          Run replications, inspect summary tables and compare saved recordings.
        </p>
        {experimentRunId && (
          <p className="ws-identity">
            Selected experiment run: <code>{experimentRunId}</code>
          </p>
        )}
      </div>
      <section className="ws-card">
        <label>
          Experiment model
          <select value={id} onChange={(e) => setId(e.target.value)}>
            {state.models.map((m) => (
              <option key={m.id} value={m.id}>
                {m.name}
              </option>
            ))}
          </select>
        </label>
        {!model?.metadata && (
          <p>
            <a href={"#/model/" + id}>Prepare this model</a> to define or start
            experiments.
          </p>
        )}
        <Experiments
          client={scoped}
          state={legacy}
          status={state.read_only ? "authentication_required" : status}
          feed={feed}
          onError={setError}
          onOpen={(_report, item) => {
            if (item.attempt) location.hash = "#/run/" + item.attempt;
          }}
        />
        <ComparePanel
          client={scoped}
          state={legacy}
          status={state.read_only ? "authentication_required" : status}
          headers={{
            "X-Simulatte-Session":
              sessionStorage.getItem("simulatte-session") ?? "",
          }}
          replays={replays}
          onError={setError}
        />
      </section>
      {error && <p role="alert">{error}</p>}
    </>
  );
}
