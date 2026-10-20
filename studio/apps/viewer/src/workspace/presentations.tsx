/** Presentations: saved views of full recordings with their supporting evidence (P4 Task 2). */
import { useEffect, useState } from "react";
import { validatePresentation, type PresentationManifest } from "@simulatte/playback";
import type { ConnectionStatus, Prefix, StudioClient } from "../studio-client";
import { httpArtifactSource } from "../artifacts/source";
import { ConfirmDialog, EmptyState, FailureNotice, Feedback, type PageState } from "./feedback";
import { Actions, Field, NOT_CONNECTED, READ_ONLY, disabledWhen } from "./forms";
import { formatTime } from "./tables";
import { routeHash, type ProjectState, type RunDetails } from "./state";
import { PresentationEditor, type RecordingIdentity } from "../presentation/editor";
import { PresentationView, type StageSource } from "../presentation/view";
import { PresentationExportButton } from "../presentation/export-entry";
import "../presentation/presentation.css";

interface ListedPresentation {
  id: string;
  name: string;
  revision: number;
  created: number;
  recording: { attempt_id: string };
  exports: { active: number; total: number };
}
export interface RecordingEntry {
  attempt_id: string;
  original_attempt: string;
  mode: "replay" | "new_seed" | "edited";
  seed: number;
  status: string;
  verified: boolean | null;
  created: number;
}
export interface DiffEntry {
  field: string;
  original: unknown;
  target: unknown;
}
/** The label of a recording's provenance; a new seed is never shown as a reproduction. */
export function recordingLabel(entry: Pick<RecordingEntry, "mode" | "verified" | "status">): string {
  if (entry.mode === "replay") return entry.verified === true ? "verified replay" : entry.verified === false ? "replay, not verified" : entry.status === "succeeded" ? "replay, verification pending" : `replay · ${entry.status}`;
  if (entry.mode === "new_seed") return "new recording, not a verified reproduction";
  return "edited run, distinct from the original";
}
/** The server's refusal as a sentence: a stale revision or an invalid manifest names what to do. */
const message = (r: Record<string, unknown>, fallback: string) => {
  const error = r.error as { code?: string; message?: string; details?: { path?: string } } | undefined;
  if (error?.code === "stale_revision") return "Someone saved a newer revision of this presentation; reload it and apply your changes again.";
  if (error?.code === "invalid_manifest") return `The presentation was refused${error.details?.path ? ` at ${error.details.path}` : ""}: ${error.message ?? "invalid manifest"}`;
  return error?.message ?? fallback;
};
const headers = () => ({ "X-Simulatte-Session": sessionStorage.getItem("simulatte-session") ?? "" });
async function loadRun(client: StudioClient, attempt: string): Promise<RunDetails> {
  const r = await client.command("run.get", { attempt_id: attempt });
  if (!r.ok) throw new Error(message(r, "Run unavailable"));
  return r.run as unknown as RunDetails;
}
function RecordFlow({ client, state, connected, recordings }: { client: StudioClient; state: ProjectState; connected: boolean; recordings: RecordingEntry[] }) {
  const completed = state.history.filter((r) => r.has_result);
  const [original, setOriginal] = useState(completed[0]?.id ?? "");
  const [seed, setSeed] = useState("");
  const [diff, setDiff] = useState<{ mode: string; diff: DiffEntry[] } | null>(null);
  const [job, setJob] = useState<{ attempt_id: string; mode: string } | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const preview = async () => {
    setBusy(true);
    setError("");
    try {
      const r = await client.command("presentation.diff", { attempt_id: original, seed: Number(seed) }, { revision: null });
      if (!r.ok) throw new Error(message(r, "Preview failed"));
      setDiff({ mode: String(r.mode), diff: r.diff as DiffEntry[] });
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };
  const record = async () => {
    setBusy(true);
    setError("");
    try {
      const r = await client.command("presentation.record", { attempt_id: original, seed: Number(seed) }, { revision: null });
      if (!r.ok) throw new Error(message(r, "Recording refused"));
      const started = r.job as { key: { attempt_id: string }; mode: string };
      setJob({ attempt_id: started.key.attempt_id, mode: started.mode });
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };
  const current = job ? recordings.find((r) => r.attempt_id === job.attempt_id) : undefined;
  return (
    <section className="ws-card" aria-labelledby="pv-record">
      <h2 id="pv-record">Record a run for presentation</h2>
      <p className="ws-hint">
        Clones the captured specification of a run (source, layout, parameters, stopping policy, measurements, runtime) at full recording level. Only the seed changes unless you edit settings explicitly.
      </p>
      <div className="ws-form-grid">
        <Field label="Original run">
          <select value={original} onChange={(e) => { setOriginal(e.target.value); setDiff(null); }}>
            {completed.map((r) => (
              <option key={r.id} value={r.id}>
                {r.name || "Run " + r.id.slice(0, 8)} · {formatTime(r.created)}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Seed" hint="The original seed yields a verified replay; another seed records a new run">
          <input inputMode="numeric" value={seed} onChange={(e) => { setSeed(e.target.value); setDiff(null); }} />
        </Field>
      </div>
      <Actions>
        <button type="button" {...disabledWhen(!connected && NOT_CONNECTED, !original && "Choose a completed run", !/^\d+$/.test(seed) && "Enter a seed", busy && "Working…")} onClick={() => void preview()}>
          Preview differences
        </button>
        <button type="button" className="primary" {...disabledWhen(!diff && "Preview the differences first", busy && "Working…")} onClick={() => void record()}>
          Record
        </button>
      </Actions>
      {diff && (
        <div data-diff-mode={diff.mode}>
          <p>
            <span className="ws-tag">{diff.mode === "replay" ? "replay of the original" : diff.mode === "new_seed" ? "new seed" : "edited"}</span>{" "}
            {diff.diff.length ? `${diff.diff.length} field${diff.diff.length === 1 ? "" : "s"} differ from the original` : "identical to the original apart from the recording level"}
          </p>
          {diff.diff.length > 0 && (
            <table className="pv-diff">
              <thead>
                <tr><th scope="col">Field</th><th scope="col">Original</th><th scope="col">Presentation run</th></tr>
              </thead>
              <tbody>
                {diff.diff.map((d) => (
                  <tr key={d.field}><td>{d.field}</td><td>{JSON.stringify(d.original)}</td><td>{JSON.stringify(d.target)}</td></tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
      {job && (
        <p role="status" data-recording={job.attempt_id}>
          Recording {job.attempt_id.slice(0, 8)} · {current ? `${current.status} · ${recordingLabel(current)}` : "started"}
          {current?.status === "succeeded" && (
            <>
              {" "}
              <a href={routeHash({ page: "presentations", attempt: job.attempt_id })}>Open editor →</a>
            </>
          )}
        </p>
      )}
      {error && <FailureNotice message={error} compact />}
    </section>
  );
}
export function PresentationsPage({
  client,
  state,
  status,
  presentationId,
  attemptId,
  present = false,
}: {
  client: StudioClient;
  state: ProjectState;
  status: ConnectionStatus;
  presentationId?: string;
  attemptId?: string;
  present?: boolean;
}) {
  const connected = status === "connected";
  const [list, setList] = useState<PageState<ListedPresentation[]>>({ status: "loading" });
  const [recordings, setRecordings] = useState<RecordingEntry[]>([]);
  const [loaded, setLoaded] = useState<{ id: string; manifest: PresentationManifest; run: RunDetails } | null>(null);
  const [draftRun, setDraftRun] = useState<RunDetails | null>(null);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [version, setVersion] = useState(0);
  const loadList = async () => {
    try {
      const r = await client.command("presentation.list", { limit: 50 }, { revision: null });
      if (!r.ok) throw new Error(message(r, "Listing failed"));
      const items = r.items as ListedPresentation[];
      setList(items.length ? { status: "ready", value: items } : { status: "empty" });
      const rec = await client.command("presentation.recordings", {}, { revision: null });
      if (rec.ok) setRecordings(rec.items as RecordingEntry[]);
    } catch (e) {
      setList({ status: "error", message: String(e), retry: { label: "Try again", invoke: () => void loadList() } });
    }
  };
  useEffect(() => {
    if (connected) void loadList();
  }, [client, connected, version]);
  useEffect(
    () =>
      client.subscribeNotifications((m) => {
        if (m.type === "presentation_changed" || m.type === "recording_state" || m.type === "export_state") setVersion((n) => n + 1);
      }),
    [client],
  );
  useEffect(() => {
    if (!presentationId || !connected) return;
    let closed = false;
    setLoaded(null);
    void (async () => {
      try {
        const r = await client.command("presentation.get", { id: presentationId }, { revision: null });
        if (!r.ok) throw new Error(message(r, "Presentation unavailable"));
        const manifest = validatePresentation(r.manifest);
        const run = await loadRun(client, manifest.recording.attempt_id);
        if (!closed) setLoaded({ id: presentationId, manifest, run });
      } catch (e) {
        if (!closed) setError(String(e));
      }
    })();
    return () => {
      closed = true;
    };
  }, [client, presentationId, connected, version]);
  useEffect(() => {
    if (!attemptId || !connected) return;
    let closed = false;
    setDraftRun(null);
    loadRun(client, attemptId)
      .then((run) => {
        if (!closed) setDraftRun(run);
      })
      .catch((e) => {
        if (!closed) setError(String(e));
      });
    return () => {
      closed = true;
    };
  }, [client, attemptId, connected]);
  const evidence = (id: string) => httpArtifactSource((path, init) => client.fetchApi(path, init), id);
  const stageSource = (prefix: Prefix): StageSource => ({ prefix, headers: headers() });
  const identity = (run: RunDetails): RecordingIdentity => ({ attempt_id: run.id, request_id: run.request_id, trace_sha256: run.trace?.sha256 ?? "" });
  const save = async (manifest: PresentationManifest) => {
    setSaving(true);
    try {
      if (loaded) {
        const r = await client.command("presentation.revise", { id: loaded.id, expected_revision: loaded.manifest.revision, manifest }, { revision: null });
        if (!r.ok) throw new Error(message(r, "Save failed"));
        setVersion((n) => n + 1);
      } else {
        const r = await client.command("presentation.create", { manifest }, { revision: null });
        if (!r.ok) throw new Error(message(r, "Save failed"));
        location.hash = routeHash({ page: "presentations", id: String(r.id) });
      }
    } finally {
      setSaving(false);
    }
  };
  // Presentation mode.
  if (presentationId && present) {
    if (!loaded || loaded.id !== presentationId || !loaded.run.trace)
      return (
        <div className="ws-state ws-loading-state" role="status" aria-busy="true">
          <span className="ws-spinner" aria-hidden="true" />
          <p>Opening the presentation…</p>
          {error && <FailureNotice message={error} compact />}
        </div>
      );
    return (
      <PresentationView
        manifest={loaded.manifest}
        source={stageSource(loaded.run.trace)}
        evidence={evidence}
        onExit={() => {
          location.hash = routeHash({ page: "presentations", id: presentationId });
          requestAnimationFrame(() => document.getElementById("pv-present-button")?.focus());
        }}
      />
    );
  }
  // Editor of a saved presentation.
  if (presentationId) {
    const recording = loaded ? recordings.find((r) => r.attempt_id === loaded.manifest.recording.attempt_id) : undefined;
    return (
      <>
        <p className="ws-back"><a href="#/presentations">← Presentations</a></p>
        <div className="ws-page-heading">
          <div>
            <div className="ws-eyebrow">PRESENTATION</div>
            <h1>{loaded?.manifest.name ?? "Presentation"}</h1>
            <p>
              {loaded && <span className="ws-tag">Revision {loaded.manifest.revision}</span>}
              {loaded && <span className="ws-tag">Recording {loaded.manifest.recording.attempt_id.slice(0, 8)}</span>}
              {recording && <span className="ws-tag" data-recording-label>{recordingLabel(recording)}</span>}
            </p>
          </div>
          <div className="ws-heading-links">
            {loaded && loaded.id === presentationId && (
              <PresentationExportButton client={client} presentationId={presentationId} manifest={loaded.manifest} prefix={loaded.run.trace} connected={connected} readOnly={state.read_only} />
            )}
            <button type="button" {...disabledWhen(!connected && NOT_CONNECTED, state.read_only && READ_ONLY)} onClick={() => setDeleting(true)}>
              Delete
            </button>
          </div>
        </div>
        {error && <FailureNotice message={error} compact retry={{ label: "Try again", invoke: () => { setError(""); setVersion((n) => n + 1); } }} />}
        {loaded && loaded.id === presentationId && loaded.run.trace ? (
          <PresentationEditor
            key={loaded.id + ":" + loaded.manifest.revision}
            client={client}
            connected={connected && !state.read_only}
            source={stageSource(loaded.run.trace)}
            recording={identity(loaded.run)}
            initial={loaded.manifest}
            saving={saving}
            onSave={save}
            presentHash={routeHash({ page: "presentations", id: presentationId, present: true })}
          />
        ) : (
          !error && (
            <div className="ws-state ws-loading-state" role="status" aria-busy="true">
              <span className="ws-spinner" aria-hidden="true" />
              <p>Loading the presentation…</p>
            </div>
          )
        )}
        <ConfirmDialog
          open={deleting}
          title="Delete this presentation?"
          text="Its revisions stay readable for finished exports until the store is pruned. The recording and analyses are not deleted."
          confirmLabel="Delete"
          onCancel={() => setDeleting(false)}
          onConfirm={() => {
            setDeleting(false);
            void client
              .command("presentation.delete", { id: presentationId }, { revision: null })
              .then((r) => {
                if (!r.ok) throw new Error(message(r, "Delete refused"));
                location.hash = "#/presentations";
              })
              .catch((e) => setError(String(e)));
          }}
        />
      </>
    );
  }
  // Editor of a new presentation over a recording.
  if (attemptId) {
    const recording = recordings.find((r) => r.attempt_id === attemptId);
    return (
      <>
        <p className="ws-back"><a href="#/presentations">← Presentations</a></p>
        <div className="ws-page-heading">
          <div>
            <div className="ws-eyebrow">NEW PRESENTATION</div>
            <h1>{draftRun?.name || "New presentation"}</h1>
            <p>
              <span className="ws-tag">Recording {attemptId.slice(0, 8)}</span>
              {recording && <span className="ws-tag" data-recording-label>{recordingLabel(recording)}</span>}
            </p>
          </div>
        </div>
        {error && <FailureNotice message={error} compact />}
        {draftRun?.trace ? (
          <PresentationEditor client={client} connected={connected && !state.read_only} source={stageSource(draftRun.trace)} recording={identity(draftRun)} initial={null} saving={saving} onSave={save} />
        ) : draftRun ? (
          <EmptyState screen={{ title: "This run has no recording", text: "A presentation needs a full recording. Record one from this run below.", link: { label: "Back to presentations", hash: "#/presentations" } }} />
        ) : (
          !error && (
            <div className="ws-state ws-loading-state" role="status" aria-busy="true">
              <span className="ws-spinner" aria-hidden="true" />
              <p>Loading the run…</p>
            </div>
          )
        )}
      </>
    );
  }
  // The list.
  const completed = state.history.filter((r) => r.has_result);
  return (
    <>
      <div className="ws-page-heading">
        <div>
          <div className="ws-eyebrow">PRESENTATIONS</div>
          <h1>Presentations</h1>
          <p>Named views of full recordings with their supporting evidence. Studio presentation is primary; exports reuse the same saved revision.</p>
        </div>
      </div>
      <Feedback state={list} loading="Loading presentations…" empty={{ title: "No presentations yet", text: "Choose a completed run with a full recording below, or record one for presentation." }}>
        {(items) => (
          <div className="ws-table-wrap">
            <table className="ws-table" aria-label="Saved presentations">
              <thead>
                <tr>
                  <th scope="col">Presentation</th>
                  <th scope="col">Recording</th>
                  <th scope="col" className="ws-num">Revision</th>
                  <th scope="col" className="ws-num">Exports</th>
                  <th scope="col" className="ws-num">Created</th>
                  <th scope="col">Present</th>
                </tr>
              </thead>
              <tbody>
                {items.map((p) => {
                  const rec = recordings.find((r) => r.attempt_id === p.recording.attempt_id);
                  return (
                    <tr key={p.id}>
                      <td><a href={routeHash({ page: "presentations", id: p.id })}>{p.name}</a></td>
                      <td>
                        <a href={routeHash({ page: "run", attempt: p.recording.attempt_id })}>Run {p.recording.attempt_id.slice(0, 8)}</a>
                        {rec && <small className="ws-sub">{recordingLabel(rec)}</small>}
                      </td>
                      <td className="ws-num">{p.revision}</td>
                      <td className="ws-num">{p.exports.active ? `${p.exports.active} active · ` : ""}{p.exports.total}</td>
                      <td className="ws-num">{formatTime(p.created)}</td>
                      <td><a className="ws-button" href={routeHash({ page: "presentations", id: p.id, present: true })}>Present →</a></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Feedback>
      <section className="ws-card" aria-labelledby="pv-new">
        <h2 id="pv-new">New presentation from a recording</h2>
        <p className="ws-hint">Interactive runs and presentation recordings with a full trace. Experiment replications are numerical; record one for presentation first.</p>
        {completed.length || recordings.length ? (
          <ul className="ws-list">
            {[...recordings.filter((r) => r.status === "succeeded").map((r) => ({ id: r.attempt_id, label: `Recording ${r.attempt_id.slice(0, 8)} · seed ${r.seed}`, sub: recordingLabel(r), created: r.created })), ...completed.map((r) => ({ id: r.id, label: r.name || "Run " + r.id.slice(0, 8), sub: "interactive run", created: r.created }))]
              .filter((x, i, all) => all.findIndex((y) => y.id === x.id) === i)
              .map((x) => (
                <li key={x.id}>
                  <span>
                    {x.label} <small className="ws-sub">{x.sub} · {formatTime(x.created)}</small>
                  </span>
                  <a className="ws-button" href={routeHash({ page: "presentations", attempt: x.id })}>Open editor →</a>
                </li>
              ))}
          </ul>
        ) : (
          <p className="ws-hint">No completed runs yet.</p>
        )}
      </section>
      {!state.read_only && <RecordFlow client={client} state={state} connected={connected} recordings={recordings} />}
      {error && <FailureNotice message={error} compact />}
    </>
  );
}
