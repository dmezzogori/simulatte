import { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { HttpSource, type RecordingView, type LayoutBundle } from "@simulatte/trace";
import { offlineHtml, download, HTML_SOURCE_LIMIT } from "@simulatte/export";
import notices from "../../../THIRD_PARTY_NOTICES.md?raw";
import playerStyle from "../../player/src/player.css?raw";
import playerScript from "../../player/dist/player.js?raw";
import { ExportDialog } from "./export-dialog";
import { Controller } from "./controller";
import {StudioClient, type StudioState, type ConnectionStatus} from "./studio-client";
import {StudioPlayback} from "./studio-playback";
import {StudioPanel} from "./studio-panel";
import {ExperimentFeed} from "./experiment-feed";
import {openReplication} from "./experiments";
import {ReplayNotifications} from "./compare-panel";
import "dockview/dist/styles/dockview.css";
import "uplot/dist/uPlot.min.css";
import "./style.css";
import "./workspace/tokens.css";
import "./workspace/world.css";
import {WorkspaceApplication} from "./workspace/shell";
function App() {
  const [studioMode] = useState(() => {
    try {return sessionStorage.getItem("simulatte-mode") === "studio";} catch {return false;}
  });
  const studio = useRef<StudioClient | null>(null);
  const studioPlayback = useRef<StudioPlayback | null>(null);
  const [studioState, setStudioState] = useState<StudioState | null>(null);
  // Experiment progress is kept outside React and re-rendered at most every 250 ms.
  const [experimentFeed] = useState(() => new ExperimentFeed(reference => studio.current
    ? studio.current.reference(reference) : Promise.reject(new Error("Studio is not connected"))));
  const [replayNotifications] = useState(() => new ReplayNotifications(reference => studio.current
    ? studio.current.reference(reference) : Promise.reject(new Error("Studio is not connected"))));
  const [studioHeaders] = useState<Readonly<Record<string, string>>>(() => {
    try {
      const secret = sessionStorage.getItem("simulatte-session");
      return secret ? {"X-Simulatte-Session": secret} : {};
    } catch {return {};}
  });
  const progress = useRef<HTMLOutputElement>(null);
  const diagnostics = useRef<HTMLPreElement>(null);
  const [connection, setConnection] = useState<ConnectionStatus>("connecting");
  const dock = useRef<HTMLDivElement>(null),
    timeline = useRef<HTMLInputElement>(null),
    time = useRef<HTMLOutputElement>(null),
    status = useRef<HTMLSpanElement>(null),
    play = useRef<HTMLButtonElement>(null),
    controller = useRef<Controller | null>(null),
    file = useRef<HTMLInputElement>(null),
    htmlAbort = useRef<AbortController | null>(null);
  const [name, setName] = useState("Open a recorded run"),
    [info, setInfo] = useState("Read-only simulation playback"),
    [error, setError] = useState(""),
    [presentation, setPresentation] = useState(false),
    [help, setHelp] = useState(false),
    [exporting, setExporting] = useState(false),
    [media, setMedia] = useState<{recording: RecordingView; presentation: LayoutBundle | null} | null>(null);
  useEffect(() => {
    const c = new Controller(
      dock.current!,
      timeline.current!,
      time.current!,
      status.current!,
      play.current!,
    );
    controller.current = c;
    c.onInfo = (n, i) => {
      setName(n);
      setInfo(i);
      setError("");
    };
    c.onError = setError;
    let secret: string | null = null;
    try {
      secret = sessionStorage.getItem("simulatte-session");
    } catch {
      setError(
        "Session storage is unavailable. Local trace files can still be opened.",
      );
    }
    if (secret && studioMode) {
      const headers = {"X-Simulatte-Session": secret};
      const client = new StudioClient(secret, {
        onStatus: setConnection,
        onError: error => setError(String(error)),
        onNotification: message => {
          experimentFeed.notification(message);
          replayNotifications.notification(message);
          studioPlayback.current?.notification(message);
          if (message.attempt_id && message.attempt_id !== client.state?.active?.key.attempt_id) return;
          if (message.type === 'progress' && progress.current) {
            const value = message.body as Record<string, unknown>;
            progress.current.textContent = `${value.phase} · t = ${value.simulation_time} · ${value.event_count} events · ${value.backpressure ? 'waiting for trace storage' : value.stalled ? 'stalled (worker alive)' : 'worker alive'} · spool ${value.spool_bytes} bytes`;
          }
          if (message.type === 'error' && diagnostics.current) {
            const text = `Revision ${message.revision?.number ?? 'unknown'} · attempt ${message.attempt_id?.slice(0, 8) ?? 'project'}\n${JSON.stringify(message.body, null, 2)}`;
            diagnostics.current.textContent = ((diagnostics.current.textContent ?? '') + '\n' + text).slice(-16384);
          }
        },
        onState: (state, arrival) => {
          if (client.state?.active?.key.attempt_id !== progress.current?.dataset['attempt'] && progress.current) {
            progress.current.dataset['attempt'] = state.active?.key.attempt_id ?? '';
            progress.current.textContent = state.progress ? JSON.stringify(state.progress.body) : '';
          }
          experimentFeed.observe(arrival); // Authoritative counts supersede earlier progress notifications.
          replayNotifications.observe(state.replays ?? [], arrival);
          setStudioState(state);
          studioPlayback.current?.update(state);
        },
      });
      studio.current = client;
      studioPlayback.current = new StudioPlayback(c, client, headers, setError);
      client.connect();
    } else if (secret)
      void HttpSource.open("/trace", { "X-Simulatte-Session": secret })
        .then((source) => c.open(source, "Recorded run"))
        .catch((e) => setError(String(e)));
    const keyboard = (event: KeyboardEvent) => {
      if (
        event.key !== " " ||
        (event.target instanceof HTMLElement &&
          (/^(INPUT|SELECT|TEXTAREA|BUTTON)$/.test(event.target.tagName) ||
            event.target.closest('[role="dialog"]')))
      )
        return;
      event.preventDefault();
      const p = c.playback;
      if (p?.status === "playing" || p?.status === "buffering") p.pause();
      else p?.play();
    };
    const rescan = () => {
      const client = studio.current;
      if (!document.hidden && client?.status === 'connected' && client.state?.revision)
        void client.command('project.rescan', {}, {revision: client.state.revision}).then(() => client.resync()).catch(() => {});
    };
    window.addEventListener('focus', rescan);
    document.addEventListener("keydown", keyboard);
    return () => {
      studioPlayback.current?.dispose(); studioPlayback.current = null;
      experimentFeed.dispose();
      replayNotifications.dispose();
      studio.current?.dispose(); studio.current = null;
      window.removeEventListener("focus", rescan);
      document.removeEventListener("keydown", keyboard);
      htmlAbort.current?.abort();
      c.dispose();
      controller.current = null;
    };
  }, []);
  const saveHtml = async () => {
    const c = controller.current;
    if (!c?.source) {
      setError("Open a trace to export an offline player.");
      return;
    }
    if (exporting) return;
    setExporting(true);
    const source = c.source,
      filename = c.name,
      layout = c.presentation;
    if (source.size > 25 * 1024 * 1024)
      setError(
        "This HTML export is larger than 25 MiB and may be slow to open or share.",
      );
    try {
      const abort = new AbortController();
      htmlAbort.current = abort;
      const html = await offlineHtml(
        await c.exportSource(HTML_SOURCE_LIMIT, abort.signal),
        playerScript,
        playerStyle,
        abort.signal,
        layout,
      );
      download(html, filename.replace(/\.simtrace$/i, "") + ".html");
    } catch (e) {
      setError(String(e));
    } finally {
      setExporting(false);
      htmlAbort.current = null;
    }
  };
  const toggle = () => {
    const p = controller.current?.playback;
    if (p?.status === "playing" || p?.status === "buffering") p.pause();
    else p?.play();
  };
  return (
    <main className={presentation ? "presentation" : ""}>
      <header>
        <div className="brand">
          <span className="mark">s</span>
          <div>
            simulatte <b>studio</b>
            <small>{studioMode ? "LOCAL MODEL STUDIO" : "RECORDED RUN VIEWER"}</small>
          </div>
        </div>
        <div className="run-title">
          <strong>{name}</strong>
          <small>{info}</small>
        </div>
        <nav>
          <input
            hidden
            ref={file}
            type="file"
            accept=".simtrace"
            onChange={(event) => {
              const f = event.currentTarget.files?.[0];
              if (f) {
                studioPlayback.current?.detach();
                htmlAbort.current?.abort();
                setMedia(null);
                void controller.current?.open(f, f.name);
              }
              event.currentTarget.value = "";
            }}
          />
          <button onClick={() => file.current?.click()}>Open trace</button>
          <button
            onClick={() => {
              setPresentation(!presentation);
              controller.current?.present(!presentation);
            }}
          >
            {presentation ? "Exit presentation" : "Present"}
          </button>
          <button disabled={exporting || (studioMode && !controller.current?.source)} onClick={() => void saveHtml()}>
            {exporting ? "Exporting…" : "Export HTML"}
          </button>
          {exporting && (
            <button onClick={() => htmlAbort.current?.abort()}>
              Cancel HTML export
            </button>
          )}
          <button
            disabled={studioMode && !controller.current?.source}
            onClick={() => {
              if (controller.current?.source && controller.current.recording?.finiteRange) setMedia({recording: controller.current.recording, presentation: controller.current.presentation});
              else setError("Open a recording with a finite time range first.");
            }}
          >
            Export video / PNG
          </button>
          <button
            onClick={() => setHelp(!help)}
            aria-label="Keyboard and viewer help"
          >
            ?
          </button>
        </nav>
      </header>
      {studioMode && <StudioPanel client={studio.current} state={studioState} status={connection} onError={setError} onSelect={(result, view) => {if (result.trace) studioPlayback.current?.select(result.trace, result.revision, view);}}
        feed={experimentFeed} headers={studioHeaders} replays={replayNotifications} onOpenReplication={(report, replication) => {openReplication(studioPlayback.current, report, replication);}} />}
      {studioMode && <aside className="studio-progress"><output ref={progress} aria-label="Execution progress" /><details><summary>Run diagnostics (latest 64 KiB)</summary><pre ref={diagnostics} /></details></aside>}
      {error && (
        <div role="alert" className="error">
          {error}
          <button onClick={() => setError("")}>Dismiss</button>
        </div>
      )}
      {help && (
        <aside className="help">
          Open a .simtrace file to inspect recorded state. Space: play/pause.
          Use the scene arrow keys to pan, +/− to zoom, Escape to clear
          selection. Event rows open exact post-event state; the preparation log
          is separate. Colored equipment shows recorded occupancy, not a
          calculated KPI.
          <button onClick={() => controller.current?.resetLayout()}>
            Reset panels
          </button>
          <details>
            <summary>Dependency notices</summary>
            <pre>{notices}</pre>
          </details>
        </aside>
      )}
      {media && (
        <ExportDialog
          recording={media.recording}
          presentation={media.presentation}
          close={() => setMedia(null)}
        />
      )}
      <div className="workspace" ref={dock} />
      <footer>
        <div className="transport">
          <button
            onClick={() => void controller.current?.playback?.step(-1)}
            aria-label="Previous event"
          >
            ◀│
          </button>
          <button className="primary" ref={play} onClick={toggle}>
            Play
          </button>
          <button
            onClick={() => void controller.current?.playback?.step(1)}
            aria-label="Next event"
          >
            │▶
          </button>
          <select
            aria-label="Playback speed"
            defaultValue="1"
            onChange={(e) => {
              if (controller.current?.playback)
                controller.current.playback.rate = Number(e.target.value);
            }}
          >
            {[0.1, 0.25, 0.5, 1, 2, 5, 10, 50, 100].map((n) => (
              <option key={n} value={n}>
                {n}×
              </option>
            ))}
          </select>
          {studioMode && <button onClick={() => {studioPlayback.current?.resume(); void studioPlayback.current?.live?.follow().catch(error => setError(String(error)));}}>Follow live</button>}
          <output ref={time}>t = —</output>
          <span className="status" ref={status}>
            No trace loaded
          </span>
          <div className="camera">
            <button onClick={() => controller.current?.scene?.fit()}>
              Fit
            </button>
            <button onClick={() => controller.current?.focus()}>Focus</button>
            <button
              aria-label="Zoom out"
              onClick={() => controller.current?.scene?.zoom(1 / 1.2)}
            >
              −
            </button>
            <button
              aria-label="Zoom in"
              onClick={() => controller.current?.scene?.zoom(1.2)}
            >
              +
            </button>
          </div>
        </div>
        <input
          ref={timeline}
          aria-label="Simulation time"
          type="range"
          disabled
          min="0"
          max="1"
          onChange={(e) => (
            controller.current?.scene?.resetTransfers(),
            void controller.current?.playback?.seekTime(Number(e.target.value))
          )}
        />
      </footer>
    </main>
  );
}
function Entry() {
  const [project,setProject]=useState<boolean|null>(null);
  useEffect(()=>{let current=true;
    if(sessionStorage.getItem('simulatte-mode')!=='studio'){setProject(false);return;}
    void fetch('/api/state',{headers:{'X-Simulatte-Session':sessionStorage.getItem('simulatte-session')??''}})
      .then(r=>r.json()).then(s=>{if(current)setProject(!!s.project);}).catch(()=>{if(current)setProject(false);});
    return()=>{current=false;};
  },[]);
  return project===null?<div className="ws-loading">Opening Simulatte…</div>:project?<WorkspaceApplication/>:<App/>;
}
createRoot(document.getElementById("root")!).render(<Entry />);
