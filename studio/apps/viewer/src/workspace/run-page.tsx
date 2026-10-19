import { offlineHtml, download, HTML_SOURCE_LIMIT } from "@simulatte/export";
import type { RecordingView, LayoutBundle } from "@simulatte/trace";
import { ExportDialog } from "../export-dialog";
import playerStyle from "../../../player/src/player.css?raw";
import playerScript from "../../../player/dist/player.js?raw";
import { useEffect, useRef, useState } from "react";
import type { StudioClient, StudioState } from "../studio-client";
import { Controller } from "../controller";
import { StudioPlayback } from "../studio-playback";
import type { RunDetails, ProjectState } from "./state";
import { invoke } from "./setup";
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
          disabled={exporting || run.status !== "succeeded"}
          onClick={() => void saveHtml()}
        >
          Export HTML
        </button>
        {exporting && (
          <button onClick={() => abort.current?.abort()}>Cancel export</button>
        )}
        <button
          disabled={run.status !== "succeeded"}
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
          onClick={() => void controller.current?.playback?.step(-1)}
        >
          Previous
        </button>
        <button
          className="primary"
          ref={play}
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
      )}{" "}
      {error && <p role="alert">{error}</p>}
    </section>
  );
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
          if (!closed) setRun(r.run as unknown as RunDetails);
        } catch (e) {
          if (!closed) setError(String(e));
        }
      if (!closed) timer = setTimeout(() => void load(), 1000);
    };
    setRun(null);
    setError("");
    void load();
    return () => {
      closed = true;
      clearTimeout(timer);
    };
  }, [client, attempt, connected]);
  const active = state.active_run?.key.attempt_id === attempt;
  const download = async (path: string) => {
    try {
      const response = await fetch(
        "/api/artifacts/" +
          attempt +
          "/" +
          path.split("/").map(encodeURIComponent).join("/"),
        {
          headers: {
            "X-Simulatte-Session":
              sessionStorage.getItem("simulatte-session") ?? "",
          },
        },
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
  return (
    <>
      <div className="ws-page-heading">
        <div className="ws-eyebrow">SAVED EXECUTION</div>
        <h1>
          {run?.name ||
            "Run " + attempt.slice(0, 8)}
        </h1>
        <p>
          <span className="ws-badge">{run?.status ?? "Loading…"}</span>
          {active && (
            <button
              disabled={!connected || state.read_only}
              onClick={() =>
                void invoke(client, "run.cancel", {
                  owner: state.active_run!.key,
                }).catch((e) => setError(String(e)))
              }
            >
              Stop run
            </button>
          )}
        </p>
      </div>
      {run?.trace ? (
        <RecordingPane client={client} run={run} />
      ) : (
        <div className="ws-empty">
          <h2>{active ? "Simulation is running" : "No recording available"}</h2>
          <p>
            {active
              ? "Committed trace data will appear here as it becomes available."
              : "Inspect the captured inputs and diagnostic information below."}
          </p>
        </div>
      )}
      {run && (
        <section className="ws-card">
          <h2>Run details</h2>
          <details>
            <summary>Captured inputs and provenance</summary>
            <pre>{JSON.stringify(run.request, null, 2)}</pre>
          </details>
          {run.details != null && (
            <details open={run.status === "failed"}>
              <summary>Diagnostics</summary>
              <pre>{JSON.stringify(run.details, null, 2)}</pre>
            </details>
          )}
          {run.artifacts.length > 0 && (
            <>
              <h3>Saved artifacts</h3>
              {run.artifacts.map((a) => (
                <button key={a.path} onClick={() => void download(a.path)}>
                  {a.path} · {a.length} bytes ↓
                </button>
              ))}
            </>
          )}
        </section>
      )}
      <section className="ws-card">
        <h2>Keep context with this run</h2>
        {!loaded && <p role="status">Loading saved notes…</p>}
        <label>
          Run name
          <input value={annotation?.name ?? ""} disabled={!loaded} readOnly={state.read_only} onChange={(e) => edit({name: e.target.value})} />
        </label>
        <label>
          Notes
          <textarea aria-label="Notes" value={annotation?.text ?? ""} disabled={!loaded} readOnly={state.read_only} onChange={(e) => edit({text: e.target.value})} />
        </label>
        <label className="ws-check">
          <input type="checkbox" checked={annotation?.important ?? false} disabled={!loaded || state.read_only} onChange={(e) => edit({important: e.target.checked})} />
          Important · protect from pruning
        </label>
        {!state.read_only && <button disabled={!connected || !loaded || draft?.attempt !== attempt || saving} onClick={() => void save()}>Save notes</button>}
      </section>
      {error && <p role="alert">{error}</p>}
    </>
  );
}
