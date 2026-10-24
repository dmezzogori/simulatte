import { offlineHtml, download, HTML_SOURCE_LIMIT } from "@simulatte/export";
import type { RecordingView, LayoutBundle } from "@simulatte/trace";
import { ExportDialog } from "../export-dialog";
import playerStyle from "../../../player/src/player.css?raw";
import playerScript from "../../../player/dist/player.js?raw";
import { Fragment, useEffect, useRef, useState } from "react";
import type { StudioClient, StudioState } from "../studio-client";
import { Controller } from "../controller";
import { StudioPlayback } from "../studio-playback";
import type { RunDetails, ProjectState } from "./state";
import { invoke } from "./setup";
import { Diagnostic, FailureNotice, Feedback, classifyFailure, type PageState } from "./feedback";
import { Actions, CheckField, Field, NOT_CONNECTED, READ_ONLY, disabledWhen } from "./forms";
import { formatNumber } from "./tables";
import { PresentationOriginalAction } from "./presentations";
export function RecordingPane({
  client,
  run,
}: {
  client: StudioClient;
  run: RunDetails;
}) {
  const host = useRef<HTMLDivElement>(null),
    timeline = useRef<HTMLInputElement>(null),
    time = useRef<HTMLOutputElement>(null),
    status = useRef<HTMLSpanElement>(null),
    play = useRef<HTMLButtonElement>(null);
  const controller = useRef<Controller | null>(null),
    bridge = useRef<StudioPlayback | null>(null),
    [error, setError] = useState("");
  const [media, setMedia] = useState<{
      recording: RecordingView;
      presentation: LayoutBundle | null;
    } | null>(null),
    [present, setPresent] = useState(false),
    [exporting, setExporting] = useState(false);
  const abort = useRef<AbortController | null>(null);
  const saveHtml = async () => {
    const c = controller.current;
    if (!c?.source) return;
    setExporting(true);
    abort.current = new AbortController();
    try {
      download(
        await offlineHtml(
          await c.exportSource(HTML_SOURCE_LIMIT, abort.current.signal),
          playerScript,
          playerStyle,
          abort.current.signal,
          c.presentation,
        ),
        "recording.html",
      );
    } catch (e) {
      setError(String(e));
    } finally {
      setExporting(false);
      abort.current = null;
    }
  };
  useEffect(() => {
    const c = new Controller(
      host.current!,
      timeline.current!,
      time.current!,
      status.current!,
      play.current!,
    );
    controller.current = c;
    c.onError = setError;
    const b = new StudioPlayback(
      c,
      client,
      {
        "X-Simulatte-Session":
          sessionStorage.getItem("simulatte-session") ?? "",
      },
      setError,
    );
    bridge.current = b;
    const unsubscribe = client.subscribeNotifications(message => b.notification(message));
    return () => {
      unsubscribe();
      abort.current?.abort();
      b.dispose();
      c.dispose();
      bridge.current = null;
      controller.current = null;
    };
  }, [client]);
  useEffect(() => {
    if (!run.trace) return;
    const key = {
      request_id: run.request_id,
      attempt_id: run.id,
      generation: run.generation,
    };
    const revision = { number: run.revision, digest: "0".repeat(64) };
    const view = run.layout
      ? {
          revision,
          key,
          committed: run.status === "succeeded",
          layout: run.layout,
        }
      : null;
    const committed = run.status === "succeeded";
    const state = {
      revision,
      active: committed
        ? null
        : {
            key,
            revision: run.revision,
            status: run.status,
            details: run.details,
            prefix: run.trace,
          },
      results: committed
        ? [{ key, revision: run.revision, created: 0, trace: run.trace }]
        : [],
      view,
      last_good: committed ? view : null,
    } as StudioState;
    bridge.current?.update(state);
  }, [run]);
  return (
    <section
      className={"ws-recording" + (present ? " ws-present" : "")}
      aria-label="Recording inspector"
    >
      <div className="ws-recording-toolbar">
        <span ref={status} className="status">
          Opening recording…
        </span>
        <button
          onClick={() => {
            setPresent(!present);
            controller.current?.present(!present);
          }}
        >
          {present ? "Exit presentation" : "Present"}
        </button>
        <button
          {...disabledWhen(exporting && "An export is in progress", run.status !== "succeeded" && "Available once the run has succeeded")}
          onClick={() => void saveHtml()}
        >
          Export HTML
        </button>
        {exporting && (
          <button onClick={() => abort.current?.abort()}>Cancel export</button>
        )}
        <button
          {...disabledWhen(run.status !== "succeeded" && "Available once the run has succeeded")}
          onClick={() => {
            const c = controller.current;
            if (c?.recording?.finiteRange)
              setMedia({
                recording: c.recording,
                presentation: c.presentation,
              });
          }}
        >
          Export video / PNG
        </button>
        <button onClick={() => controller.current?.scene?.fit()}>
          Fit scene
        </button>
        <button onClick={() => controller.current?.resetLayout()}>
          Reset panels
        </button>
      </div>
      <div ref={host} className="ws-dock" />
      <footer>
        <button
          aria-label="Previous event"
          title="Step to the previous event; unavailable before the recording is open or at its start"
          onClick={() => void controller.current?.playback?.step(-1)}
        >
          Previous
        </button>
        <button
          className="primary"
          ref={play}
          title="Play or pause the recording; unavailable until the recording is open"
          onClick={() => {
            const p = controller.current?.playback;
            if (p) {
              if (p.status === "playing" || p.status === "buffering") p.pause();
              else p.play();
            }
          }}
        >
          Play
        </button>
        <button
          aria-label="Next event"
          title="Step to the next event; unavailable before the recording is open or at its end"
          onClick={() => void controller.current?.playback?.step(1)}
        >
          Next
        </button>
        <output ref={time} />
        <input
          ref={timeline}
          aria-label="Simulation time"
          type="range"
          min="0"
          max="1"
          disabled
          onChange={(e) =>
            void controller.current?.playback?.seekTime(Number(e.target.value))
          }
        />
      </footer>
      {media && (
        <ExportDialog
          recording={media.recording}
          presentation={media.presentation}
          close={() => setMedia(null)}
        />
      )}
      {error && <FailureNotice message={error} compact />}
    </section>
  );
}
const RUN_WORDING: Record<string, [string, string]> = {
  succeeded: ["Run completed", "Saved in this project with its captured inputs."],
  running: ["Running", "Capturing outputs; committed trace data appears as it arrives."],
  queued: ["Queued", "Waiting for the interactive slot."],
  ready: ["Starting", "The simulation process is being prepared."],
  failed: ["Run failed", "The diagnostics below describe what happened."],
  cancelled: ["Run stopped", "The run was stopped before completion."],
  lost: ["Run lost", "Studio stopped before this run could finish."],
  disk_full: ["Run stopped: disk full", "Free disk space before running again."],
  validation_failed: ["Run failed validation", "The captured inputs did not validate."],
};
const describeValue = (value: unknown): string =>
  typeof value === "number"
    ? formatNumber(value)
    : value === null || value === undefined
      ? "—"
      : typeof value === "object"
        ? JSON.stringify(value)
        : String(value);
/** Whether the run page shows a recording: driven by the recording level, since KPI-level runs also keep a (KPI) trace. */
export function recordingOf(run: Pick<RunDetails, "trace" | "status" | "request">): "full" | "live" | null {
  if (!run.trace || run.request.level === "kpi") return null;
  return run.status === "succeeded" ? "full" : "live";
}
export function RunPage({
  client,
  state,
  attempt,
  connected,
}: {
  client: StudioClient;
  state: ProjectState;
  attempt: string;
  connected: boolean;
}) {
  const [run, setRun] = useState<RunDetails | null>(null),
    [error, setError] = useState(""),
    [saving, setSaving] = useState(false);
  const [draft, setDraft] = useState<{attempt: string; name: string; text: string; important: boolean} | null>(null);
  const loaded = run?.id === attempt;
  const annotation = draft?.attempt === attempt ? draft : loaded ? {
    attempt, name: run.name, text: run.text, important: run.important,
  } : null;
  const edit = (change: Partial<{name: string; text: string; important: boolean}>) => {
    if(annotation) setDraft({...annotation, ...change});
  };
  const save = async () => {
    if(!annotation || !loaded) return;
    const submitted = annotation;
    setSaving(true);
    try {
      await invoke(client, "history.annotate", {attempt_id: attempt, name: submitted.name, text: submitted.text, important: submitted.important});
      setRun(current => current?.id === attempt ? {...current, name: submitted.name, text: submitted.text, important: submitted.important} : current);
      setDraft(current => current === submitted ? null : current);
    } catch(e) {setError(String(e));}
    finally {setSaving(false);}
  };
  useEffect(() => {
    let closed = false;
    let timer: ReturnType<typeof setTimeout>;
    const load = async () => {
      if (connected)
        try {
          const r = await client.command("run.get", { attempt_id: attempt });
          if (!r.ok) throw Error((r.error as { message: string }).message);
          if (!closed) {setRun(r.run as unknown as RunDetails); setError("");}
        } catch (e) {
          if (!closed) setError(String(e));
        }
      if (!closed) timer = setTimeout(() => void load(), 1000);
    };
    // The shell keys this page by attempt, so a reconnect must not clear the run being read.
    void load();
    return () => {
      closed = true;
      clearTimeout(timer);
    };
  }, [client, attempt, connected]);
  const active = state.active_run?.key.attempt_id === attempt;
  const download = async (path: string) => {
    try {
      const response = await client.fetchApi(
        "/api/artifacts/" +
          encodeURIComponent(attempt) +
          "/" +
          path.split("/").map(encodeURIComponent).join("/"),
      );
      if (!response.ok) throw Error("Artifact unavailable");
      const url = URL.createObjectURL(await response.blob());
      const a = document.createElement("a");
      a.href = url;
      a.download = path.split("/").pop()!;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) {
      setError(String(e));
    }
  };
  const page: PageState<RunDetails> = loaded
    ? { status: "ready", value: run }
    : error
      ? { status: "error", message: error }
      : { status: "loading" };
  const status = run?.status ?? (active ? state.active_run!.status : "");
  const [activityTitle, activityText] = RUN_WORDING[status] ?? [status || "Loading", "Reading the saved run."];
  const model = run ? state.models.find((m) => m.entrypoint === run.request.model) : null;
  const request = run?.request ?? {};
  const stopping = (request.stopping ?? {}) as Record<string, unknown>;
  const parameters = (request.parameters ?? {}) as Record<string, unknown>;
  const failed = run != null && !["succeeded", "running", "queued", "ready"].includes(run.status);
  const recording = run ? recordingOf(run) : null;
  const plan = (run as (RunDetails & { measurement_plan?: string }) | null)?.measurement_plan;
  return (
    <>
      <p className="ws-back">
        <a href="#/runs">← Runs</a>
      </p>
      <div className="ws-page-heading">
        <div>
          <h1>{run?.name || "Run " + attempt.slice(0, 8)}</h1>
          <p>
            <span className={"ws-badge " + (run?.status ?? "")}>{run?.status ?? "Loading…"}</span>
            {recording && <span className="ws-tag">{recording === "full" ? "Full recording" : "Live recording"}</span>}
            {run && !recording && request.level === "kpi" && <span className="ws-tag">No recording</span>}
            {run && <span className="ws-tag">{plan ? `Measurements · ${plan}` : "KPIs only"}</span>}
          </p>
        </div>
        {loaded && run.status === "succeeded" && !state.read_only && <PresentationOriginalAction attempt={attempt} />}
        {active && (
          <button
            type="button"
            {...disabledWhen(!connected && NOT_CONNECTED, state.read_only && READ_ONLY)}
            onClick={() =>
              void invoke(client, "run.cancel", {
                owner: state.active_run!.key,
              }).catch((e) => setError(String(e)))
            }
          >
            Stop run
          </button>
        )}
      </div>
      {(run || active) && (
        <div className={"ws-activity" + (failed ? " failed" : "")} role="status">
          <strong>{activityTitle}</strong>
          <span>{activityText}</span>
        </div>
      )}
      <Feedback state={page} loading="Loading the saved run…" empty={{ title: "No run" }}>
        {(run) => (
          <>
            {recordingOf(run) ? (
              <RecordingPane client={client} run={run} />
            ) : (
              <div className="ws-state ws-empty">
                <h2>{active ? "Simulation is running" : "No recording available"}</h2>
                <p>
                  {active
                    ? "Committed trace data will appear here as it becomes available."
                    : run.status === "succeeded"
                      ? "Numerical outputs were kept without a recording. Analysis remains available."
                      : "Inspect the captured inputs and diagnostic information below."}
                </p>
              </div>
            )}
            {failed && run.details != null && (
              <FailureNotice
                message={describeDetails(run.details)}
                details={run.details}
              />
            )}
            <div className="ws-cols">
              <section className="ws-card ws-details">
                <h2>Run details</h2>
                <dl className="ws-pairs">
                  <dt>Model</dt>
                  <dd>{model?.name ?? String(request.model ?? "—")}</dd>
                  <dt>Seed</dt>
                  <dd className="ws-num">{describeValue(request.seed)}</dd>
                  <dt>Recording level</dt>
                  <dd>{String(request.level ?? "—")}</dd>
                  <dt>Measurements</dt>
                  <dd>{plan ? `Plan ${plan}` : "KPIs only"}</dd>
                  <dt>Stopping</dt>
                  <dd>
                    {String(stopping.type ?? "—")}
                    {Object.entries(stopping)
                      .filter(([k, v]) => k !== "type" && v !== null)
                      .map(([k, v]) => ` · ${k.replaceAll("_", " ")} ${describeValue(v)}`)
                      .join("")}
                  </dd>
                  {Object.entries(parameters).map(([k, v]) => (
                    <Fragment key={k}>
                      <dt>{k}</dt>
                      <dd className="ws-num">{describeValue(v)}</dd>
                    </Fragment>
                  ))}
                </dl>
                <details>
                  <summary>Captured inputs and provenance</summary>
                  <pre>{JSON.stringify(run.request, null, 2)}</pre>
                </details>
                {run.details != null && !failed && (
                  <Diagnostic lines={[JSON.stringify(run.details, null, 2)]} />
                )}
                {run.artifacts.length > 0 && (
                  <>
                    <h3>Saved artifacts</h3>
                    <ul className="ws-list">
                      {run.artifacts.map((a) => (
                        <li key={a.path}>
                          <span>{a.path}</span>
                          <button type="button" onClick={() => void download(a.path)}>
                            Download · {formatNumber(a.length)} bytes
                          </button>
                        </li>
                      ))}
                    </ul>
                  </>
                )}
              </section>
              <section className="ws-card ws-details">
                <h2>Keep context with this run</h2>
                <Field label="Run name">
                  <input value={annotation?.name ?? ""} disabled={!loaded} readOnly={state.read_only} onChange={(e) => edit({name: e.target.value})} />
                </Field>
                <Field label="Notes">
                  <textarea value={annotation?.text ?? ""} disabled={!loaded} readOnly={state.read_only} onChange={(e) => edit({text: e.target.value})} />
                </Field>
                <CheckField label="Important · protect from pruning">
                  <input type="checkbox" checked={annotation?.important ?? false} disabled={!loaded || state.read_only} onChange={(e) => edit({important: e.target.checked})} />
                </CheckField>
                {!state.read_only && (
                  <Actions note={state.read_only ? undefined : "Notes stay with the run in this project."}>
                    <button type="button" {...disabledWhen(!connected && NOT_CONNECTED, !loaded && "Loading the saved notes", saving && "Saving…", draft?.attempt !== attempt && "No changes to save")} onClick={() => void save()}>Save notes</button>
                  </Actions>
                )}
              </section>
            </div>
          </>
        )}
      </Feedback>
      {error && loaded && <FailureNotice message={error} compact />}
    </>
  );
}
/** The most useful sentence of a run's diagnostics: its message, or the first string field. */
function describeDetails(details: unknown): string {
  if (typeof details === "string") return details;
  if (details && typeof details === "object") {
    const record = details as Record<string, unknown>;
    for (const key of ["message", "error", "reason", "stderr"])
      if (typeof record[key] === "string" && record[key]) return record[key] as string;
  }
  return classifyFailure(JSON.stringify(details)).summary;
}
