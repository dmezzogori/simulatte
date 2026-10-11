import { useEffect, useRef, useState } from "react";
import {
  destination,
  exportMedia,
  validateMedia,
  abandonedExports,
  removeAbandoned,
  type ExportFile,
  type MediaOptions,
} from "@simulatte/export";
import type { Recording } from "@simulatte/trace";
export function ExportDialog({
  recording,
  close,
}: {
  recording: Recording;
  close(): void;
}) {
  const [options, setOptions] = useState<MediaOptions>({
    start: recording.finiteRange?.[0] ?? 0,
    end: recording.finiteRange?.[1] ?? 0,
    fps: 30,
    rate: 1,
    width: 1920,
    height: 1080,
    format: "mp4",
    background: "#f4f5f0",
    overlays: true,
  });
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
  const run = async () => {
    if (busy) return;
    try {
      validateMedia(options, recording.finiteRange);
      setBusy(true);
      setMessage("");
      // Invoke the picker synchronously within the click gesture, before codec probing.
      const pending = destination(
        `simulatte.${options.format === "png" ? "zip" : options.format}`,
        options.format === "png",
      );
      abort.current = new AbortController();
      const sink = await pending;
      const file = await exportMedia(
        recording,
        options,
        sink,
        abort.current.signal,
        (frame, total) => {
          if (progress.current)
            progress.current.textContent = `${frame.toLocaleString()} / ${total.toLocaleString()} frames`;
        },
      );
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
    } catch (error) {
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
  const field = (
    key: "start" | "end" | "rate" | "width" | "height",
    label: string,
  ) => (
    <label>
      {label}
      <input
        aria-label={label}
        type="number"
        step="any"
        value={options[key]}
        disabled={busy}
        onChange={(e) =>
          setOptions({ ...options, [key]: Number(e.target.value) })
        }
      />
    </label>
  );
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
              dialog.current?.querySelectorAll<HTMLElement>(
                "button:not(:disabled),input:not(:disabled),select:not(:disabled),a[href]",
              ) ?? [],
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
        className="export-dialog"
        role="dialog"
        aria-modal="true"
        aria-label="Export recording"
      >
        <h2>Export recording</h2>
        <p>
          Exports use their own clock and a fitted scene. Your viewer position
          stays unchanged.
        </p>
        <div className="export-grid">
          <label>
            Format
            <select
              aria-label="Format"
              disabled={busy}
              value={options.format}
              onChange={(e) =>
                setOptions({
                  ...options,
                  format: e.target.value as MediaOptions["format"],
                })
              }
            >
              <option value="mp4">MP4 · H.264</option>
              <option value="webm">WebM · VP9 / VP8</option>
              <option value="png">PNG frames · ZIP64</option>
            </select>
          </label>
          <label>
            Frames per second
            <select
              aria-label="Frames per second"
              disabled={busy}
              value={options.fps}
              onChange={(e) =>
                setOptions({ ...options, fps: Number(e.target.value) })
              }
            >
              {[24, 30, 60].map((fps) => (
                <option key={fps}>{fps}</option>
              ))}
            </select>
          </label>
          {field("start", "Start time")}
          {field("end", "End time")}
          {field("rate", "Simulation units per second")}
          {field("width", "Width")}
          {field("height", "Height")}
          <label>
            Background
            <input
              type="color"
              disabled={busy}
              value={options.background}
              onChange={(e) =>
                setOptions({ ...options, background: e.target.value })
              }
            />
          </label>
          <label>
            <input
              type="checkbox"
              disabled={busy}
              checked={options.overlays}
              onChange={(e) =>
                setOptions({ ...options, overlays: e.target.checked })
              }
            />
            Time overlay
          </label>
        </div>
        <p>
          Maximum 100,000 video frames or 10,000 PNG frames; 3840 × 2160.
          Temporary files use this browser origin’s storage. A crash can leave a
          copy; a different server port cannot remove it.
        </p>
        <output ref={progress} aria-live="polite" />
        {message && <p role="status">{message}</p>}
        {url && result && (
          <a className="download" href={url} download={result.name}>
            Download {result.name}
          </a>
        )}
        {result && (
          <button onClick={() => void remove()}>
            {result.kind === "direct"
              ? "Export another file"
              : "Remove temporary copy"}
          </button>
        )}
        {busy ? (
          <button onClick={() => abort.current?.abort()}>Cancel export</button>
        ) : (
          <button disabled={!!result} onClick={() => void run()}>
            Start export
          </button>
        )}
        <button disabled={busy} onClick={close}>
          {result?.kind === "temporary"
            ? "Close and remove temporary copy"
            : "Close"}
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
