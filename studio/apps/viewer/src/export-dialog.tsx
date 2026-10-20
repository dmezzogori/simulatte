import { useEffect, useRef, useState } from "react";
import {
  destination,
  exportMedia,
  validateMedia,
  abandonedExports,
  removeAbandoned,
  offlineHtml,
  download,
  prepareExport,
  staticIdentities,
  sha256Blob,
  ExportRefusal,
  HTML_SOURCE_LIMIT,
  type ExportFile,
  type MediaOptions,
  type ExportSnapshot,
  type ExportTarget,
  type EvidenceSource,
  type PresentationManifest,
  type TargetOptions,
} from "@simulatte/export";
import { pinPresentation } from "@simulatte/playback";
import type { RecordingView, LayoutBundle } from "@simulatte/trace";
import { ExportPreview, RefusalNotice } from "./presentation/export-preview";
/** The export-job protocol of a saved presentation (R4): admit with the static identities, start, finish. */
export interface ExportJobs {
  admit(body: { presentation_id: string; revision: number; target: ExportTarget; options: TargetOptions; statics: ReturnType<typeof staticIdentities> }): Promise<{ job_id: string; snapshot_hash: string }>;
  start(jobId: string): Promise<void>;
  finish(jobId: string, outcome: ExportOutcome): Promise<void>;
}
export type ExportOutcome =
  | { state: "succeeded"; output: { name: string; size: number; sha256: string; media_type: string } }
  | { state: "failed"; message: string }
  | { state: "cancelled"; message?: string };
export interface PresentationExport {
  manifest: PresentationManifest;
  presentationId: string;
  revision: number;
  evidence: EvidenceSource;
  jobs: ExportJobs;
  /** The recording bytes for the offline HTML, bounded by the caller. */
  traceSource(limit: number, signal: AbortSignal): Promise<Blob>;
  /** The recording's size in bytes, known without reading it; the preview needs only this. */
  traceSize: number;
  playerScript: string;
  playerStyle: string;
}
export type Format = MediaOptions["format"] | "html";
/** The outcome reported to ``export.finish``: the server needs the exact size and digest, so presentation exports
 * always finish in readable temporary storage (never a direct picker handle) and this reads the bytes back. */
export async function exportOutcome(file: Pick<ExportFile, "name" | "blob">, mediaType: string): Promise<ExportOutcome> {
  if (!file.blob) throw new Error("The export finished where its bytes cannot be read back; the job cannot record its output");
  return { state: "succeeded", output: { name: file.name, size: file.blob.size, sha256: await sha256Blob(file.blob), media_type: mediaType } };
}
/** The export target's options; the HTML preview takes the trace size from the source, so the trace is read once, at export. */
export function targetOptionsOf(target: ExportTarget, options: MediaOptions, format: Format, panel: boolean, presentationExport: Pick<PresentationExport, "traceSize">): TargetOptions {
  return target === "video" ? { ...options, format: format as MediaOptions["format"], panel } : { panel, traceSize: presentationExport.traceSize };
}
export const MEDIA_TYPES: Record<Format, string> = { mp4: "video/mp4", webm: "video/webm", png: "application/zip", html: "text/html" };
export function ExportDialog({
  recording: initialRecording,
  presentation: initialPresentation = null,
  close,
  presentationExport = null,
}: {
  recording: RecordingView;
  presentation?: LayoutBundle | null;
  close(): void;
  /** When given, the dialog exports the saved presentation: preview, limits, refusals and export jobs. */
  presentationExport?: PresentationExport | null;
}) {
  const manifest = presentationExport?.manifest ?? null;
  const [{ recording, presentation }] = useState(() => ({
    recording: initialRecording,
    presentation: pinPresentation(initialRecording, manifest ? manifest.layout : initialPresentation),
  }));
  const [options, setOptions] = useState<MediaOptions>({
    start: manifest?.time.start ?? recording.finiteRange?.[0] ?? 0,
    end: manifest?.time.end ?? recording.finiteRange?.[1] ?? 0,
    fps: 30,
    rate: manifest?.rate ?? 1,
    width: 1920,
    height: 1080,
    format: "mp4",
    background: "#f4f5f0",
    overlays: true,
  });
  const [format, setFormat] = useState<Format>("mp4");
  const [panel, setPanel] = useState(true);
  const [snapshot, setSnapshot] = useState<ExportSnapshot | null>(null);
  const [refusal, setRefusal] = useState<ExportRefusal | null>(null);
  const [preparing, setPreparing] = useState(false);
  const [busy, setBusy] = useState(false),
    [message, setMessage] = useState(""),
    [result, setResult] = useState<ExportFile | null>(null),
    [url, setUrl] = useState(""),
    [abandoned, setAbandoned] = useState<string[]>([]);
  const mounted = useRef(true),
    dialog = useRef<HTMLElement>(null);
  const abort = useRef<AbortController | null>(null),
    progress = useRef<HTMLOutputElement>(null),
    owned = useRef<ExportFile | null>(null),
    ownedUrl = useRef("");
  useEffect(() => {
    void abandonedExports()
      .then(setAbandoned)
      .catch(() => {});
    mounted.current = true;
    const prior = document.activeElement;
    dialog.current?.querySelector("select")?.focus();
    const shutdown = () => {
      abort.current?.abort();
      void owned.current?.remove().catch(() => {});
    };
    window.addEventListener("pagehide", shutdown);
    return () => {
      mounted.current = false;
      window.removeEventListener("pagehide", shutdown);
      shutdown();
      if (ownedUrl.current) URL.revokeObjectURL(ownedUrl.current);
      if (prior instanceof HTMLElement) prior.focus();
    };
  }, []);
  const remove = async () => {
    try {
      await owned.current?.remove();
      if (ownedUrl.current) URL.revokeObjectURL(ownedUrl.current);
      owned.current = null;
      ownedUrl.current = "";
      setUrl("");
      setResult(null);
    } catch (error) {
      setMessage(String(error));
    }
  };
  const change = (next: Partial<MediaOptions>) => {
    setOptions({ ...options, ...next });
    setSnapshot(null);
    setRefusal(null);
  };
  const target: ExportTarget = format === "html" ? "html" : "video";
  const prepare = async () => {
    if (!presentationExport || preparing) return null;
    setPreparing(true);
    setRefusal(null);
    setMessage("");
    const controller = new AbortController();
    abort.current = controller;
    try {
      const prepared = await prepareExport(manifest!, target, targetOptionsOf(target, options, format, panel, presentationExport), presentationExport.evidence, controller.signal);
      if (mounted.current) setSnapshot(prepared);
      return prepared;
    } catch (error) {
      if (mounted.current) {
        setSnapshot(null);
        if (error instanceof ExportRefusal) setRefusal(error);
        else setMessage(String(error));
      }
      return null;
    } finally {
      if (mounted.current) setPreparing(false);
      if (abort.current === controller) abort.current = null;
    }
  };
  const keep = async (file: ExportFile) => {
    if (!mounted.current) {
      await file.remove();
      return;
    }
    owned.current = file;
    setResult(file);
    if (file.blob) {
      const next = URL.createObjectURL(file.blob);
      ownedUrl.current = next;
      setUrl(next);
    }
    setMessage(
      file.kind === "direct"
        ? "Export saved."
        : "Export ready. Keep this dialog open until your download finishes, then remove the temporary copy.",
    );
  };
  const runRecording = async () => {
    validateMedia(options, recording.finiteRange);
    setBusy(true);
    setMessage("");
    // Invoke the picker synchronously within the click gesture, before codec probing.
    const pending = destination(`simulatte.${options.format === "png" ? "zip" : options.format}`, options.format === "png");
    abort.current = new AbortController();
    const sink = await pending;
    const file = await exportMedia(recording, options, sink, abort.current.signal, (frame, total) => {
      if (progress.current) progress.current.textContent = `${frame.toLocaleString()} / ${total.toLocaleString()} frames`;
    }, presentation);
    await keep(file);
  };
  const runPresentation = async () => {
    if (!presentationExport || !snapshot) return;
    setBusy(true);
    setMessage("");
    const name = `${manifest!.name.replace(/[^\w.-]+/g, "-").replace(/^-+|-+$/g, "") || "presentation"}.${format === "png" ? "zip" : format}`;
    // Temporary storage (never the direct picker): export.finish needs the file's size and digest read back.
    const pending = target === "video" ? destination(name, format === "png", false) : null;
    const controller = new AbortController();
    abort.current = controller;
    const sink = pending ? await pending : null;
    let jobId: string | null = null;
    try {
      const admitted = await presentationExport.jobs.admit({
        presentation_id: presentationExport.presentationId,
        revision: presentationExport.revision,
        target,
        options: snapshot.options,
        statics: staticIdentities(snapshot.statics),
      });
      if (admitted.snapshot_hash !== snapshot.hash)
        throw new ExportRefusal("export job", `the server froze snapshot ${admitted.snapshot_hash.slice(0, 12)} but the preview is ${snapshot.hash.slice(0, 12)}`, "prepare the preview again");
      jobId = admitted.job_id;
      await presentationExport.jobs.start(jobId);
      controller.signal.throwIfAborted();
      let outcome: ExportOutcome;
      if (sink) {
        const file = await exportMedia(recording, snapshot.options as MediaOptions, sink, controller.signal, (frame, total) => {
          if (progress.current) progress.current.textContent = `${frame.toLocaleString()} / ${total.toLocaleString()} frames`;
        }, null, snapshot);
        outcome = await exportOutcome(file, MEDIA_TYPES[format]);
        await keep(file);
      } else {
        const trace = await presentationExport.traceSource(HTML_SOURCE_LIMIT, controller.signal);
        const html = await offlineHtml(trace, presentationExport.playerScript, presentationExport.playerStyle, controller.signal, null, snapshot);
        download(html, name);
        outcome = await exportOutcome({ name, blob: html }, "text/html");
        setMessage(`Offline HTML saved (${(html.size / 1048576).toFixed(1)} MiB).`);
      }
      await presentationExport.jobs.finish(jobId, outcome);
      jobId = null;
    } catch (error) {
      if (!pending) {
        /* nothing staged for HTML */
      } else if (sink && !(error instanceof DOMException)) await sink.abort().catch(() => {});
      if (jobId) {
        const cancelled = error instanceof DOMException && error.name === "AbortError";
        await presentationExport.jobs
          .finish(jobId, cancelled ? { state: "cancelled" } : { state: "failed", message: String(error) })
          .catch(() => setMessage("The export job could not be closed; Studio marks it lost on recovery."));
      }
      throw error;
    }
  };
  const run = async () => {
    if (busy) return;
    try {
      if (presentationExport) await runPresentation();
      else await runRecording();
    } catch (error) {
      if (error instanceof ExportRefusal) setRefusal(error);
      else
        setMessage(
          error instanceof DOMException && error.name === "AbortError"
            ? "Export cancelled. Any completed user-chosen file remains at its destination."
            : `${String(error)} A chosen destination may contain an incomplete file.`,
        );
    } finally {
      if (mounted.current) setBusy(false);
      abort.current = null;
    }
  };
  const field = (key: "start" | "end" | "rate" | "width" | "height", label: string) => (
    <label>
      {label}
      <input aria-label={label} type="number" step="any" value={options[key]} disabled={busy} onChange={(e) => change({ [key]: Number(e.target.value) })} />
    </label>
  );
  const videoTarget = format !== "html";
  return (
    <div className="modal-backdrop">
      <section
        ref={dialog}
        onKeyDown={(event) => {
          if (event.key === "Escape" && !busy) {
            event.stopPropagation();
            close();
          }
          if (event.key === "Tab") {
            const fields = Array.from(
              dialog.current?.querySelectorAll<HTMLElement>("button:not(:disabled),input:not(:disabled),select:not(:disabled),a[href]") ?? [],
            );
            const first = fields[0],
              last = fields.at(-1);
            if (event.shiftKey && document.activeElement === first) {
              event.preventDefault();
              last?.focus();
            } else if (!event.shiftKey && document.activeElement === last) {
              event.preventDefault();
              first?.focus();
            }
          }
        }}
        className={"export-dialog" + (presentationExport ? " export-presentation" : "")}
        role="dialog"
        aria-modal="true"
        aria-label={presentationExport ? "Export presentation" : "Export recording"}
      >
        <h2>{presentationExport ? `Export presentation: ${manifest!.name}` : "Export recording"}</h2>
        <p>
          {presentationExport
            ? `Revision ${presentationExport.revision} is frozen at admission: later edits never change this export.`
            : "Exports use their own clock and a fitted scene. Your viewer position stays unchanged."}
        </p>
        <div className="export-grid">
          <label>
            Format
            <select
              aria-label="Format"
              disabled={busy}
              value={format}
              onChange={(e) => {
                const next = e.target.value as Format;
                setFormat(next);
                if (next !== "html") change({ format: next });
                else {
                  setSnapshot(null);
                  setRefusal(null);
                }
              }}
            >
              <option value="mp4">MP4 · H.264</option>
              <option value="webm">WebM · VP9 / VP8</option>
              <option value="png">PNG frames · ZIP64</option>
              {presentationExport && <option value="html">Standalone HTML · offline player</option>}
            </select>
          </label>
          {videoTarget && (
            <label>
              Frames per second
              <select aria-label="Frames per second" disabled={busy} value={options.fps} onChange={(e) => change({ fps: Number(e.target.value) })}>
                {[24, 30, 60].map((fps) => (
                  <option key={fps}>{fps}</option>
                ))}
              </select>
            </label>
          )}
          {videoTarget && field("start", "Start time")}
          {videoTarget && field("end", "End time")}
          {videoTarget && field("rate", "Simulation units per second")}
          {videoTarget && field("width", "Width")}
          {videoTarget && field("height", "Height")}
          {videoTarget && (
            <label>
              Background
              <input type="color" disabled={busy} value={options.background} onChange={(e) => change({ background: e.target.value })} />
            </label>
          )}
          {videoTarget && (
            <label>
              <input type="checkbox" disabled={busy} checked={options.overlays} onChange={(e) => change({ overlays: e.target.checked })} />
              Time overlay
            </label>
          )}
          {presentationExport && (
            <label>
              <input
                type="checkbox"
                disabled={busy}
                checked={panel}
                onChange={(e) => {
                  setPanel(e.target.checked);
                  setSnapshot(null);
                  setRefusal(null);
                }}
              />
              Evidence panel
            </label>
          )}
        </div>
        <p>
          Maximum 100,000 video frames or 10,000 PNG frames; 3840 × 2160.
          {presentationExport ? " Offline HTML embeds the recording (≤ 100 MiB) with its evidence (≤ 150 MiB in total)." : ""} Temporary files use this browser origin’s storage. A crash can leave a copy; a different server port cannot remove it.
        </p>
        {presentationExport && (
          <div className="export-prepare">
            <button disabled={busy || preparing} onClick={() => void prepare()}>
              {preparing ? "Preparing preview…" : snapshot ? "Prepare preview again" : "Prepare preview"}
            </button>
            {!snapshot && !refusal && !preparing && <small>The preview validates every selected item and shows the actual target framing before anything is exported.</small>}
          </div>
        )}
        {refusal && <RefusalNotice refusal={refusal} />}
        {snapshot && <ExportPreview snapshot={snapshot} recording={recording} />}
        <output ref={progress} aria-live="polite" />
        {message && <p role="status">{message}</p>}
        {url && result && (
          <a className="download" href={url} download={result.name}>
            Download {result.name}
          </a>
        )}
        {result && <button onClick={() => void remove()}>{result.kind === "direct" ? "Export another file" : "Remove temporary copy"}</button>}
        {busy ? (
          <button onClick={() => abort.current?.abort()}>Cancel export</button>
        ) : (
          <button disabled={!!result || (!!presentationExport && !snapshot)} title={presentationExport && !snapshot ? "Prepare the preview first" : undefined} onClick={() => void run()}>
            Start export
          </button>
        )}
        <button disabled={busy} onClick={close}>
          {result?.kind === "temporary" ? "Close and remove temporary copy" : "Close"}
        </button>
        {abandoned.length > 0 && (
          <p>
            {abandoned.length} previous temporary copies on this origin.{" "}
            <button
              disabled={busy}
              onClick={() =>
                void Promise.all(abandoned.map(removeAbandoned))
                  .then(() => setAbandoned([]))
                  .catch((error) => setMessage(String(error)))
              }
            >
              Remove previous copies
            </button>
          </p>
        )}
      </section>
    </div>
  );
}
