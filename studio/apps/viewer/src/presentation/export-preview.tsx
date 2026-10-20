/** The target preview: the first frame of the export at the actual target size, drawn by the same renderer that
 * produces every video frame, with the limits the snapshot was checked against. */
import { useEffect, useRef, useState } from "react";
import { Session } from "@simulatte/playback";
import { Scene } from "@simulatte/scene";
import { animationArea, renderPresentationFrame, type ExportRefusal, type ExportSnapshot, type PresentationScene } from "@simulatte/export";
import type { RecordingView } from "@simulatte/trace";
export interface PreviewSize {
  width: number;
  height: number;
}
/** Video previews use the output size; HTML previews use a 1280 × 720 opening view through the same path. */
export function previewSize(snapshot: Pick<ExportSnapshot, "target" | "options">): PreviewSize {
  if (snapshot.target === "video" && "width" in snapshot.options) return { width: snapshot.options.width, height: snapshot.options.height };
  return { width: 1280, height: 720 };
}
const mib = (bytes: number) => `${(bytes / 1048576).toFixed(bytes < 10 * 1048576 ? 2 : 0)} MiB`;
/** Human-readable limits of a prepared snapshot. */
export function limitsSummary(snapshot: ExportSnapshot): string[] {
  const lines: string[] = [];
  const { limits } = snapshot;
  if (snapshot.target === "video" && "format" in snapshot.options) {
    const o = snapshot.options;
    lines.push(`${o.format.toUpperCase()} · ${o.width} × ${o.height} · ${o.fps} fps · ${limits.frames?.toLocaleString() ?? "?"} of ${o.format === "png" ? "10,000" : "100,000"} frames`);
    if (limits.animation) lines.push(`animation ${limits.animation.width} × ${limits.animation.height}${limits.panelWidth ? ` · evidence panel ${limits.panelWidth} px` : " · no evidence panel"}`);
  } else if (limits.htmlBytes !== null) lines.push(`offline HTML about ${mib(limits.htmlBytes)} of 150 MiB including ${mib(limits.staticBytes)} of static views`);
  lines.push(`${snapshot.statics.length} static view${snapshot.statics.length === 1 ? "" : "s"} · ${snapshot.evidence.length} evidence item${snapshot.evidence.length === 1 ? "" : "s"} · snapshot ${snapshot.hash.slice(0, 12)}`);
  return lines;
}
export function RefusalNotice({ refusal }: { refusal: ExportRefusal }) {
  return (
    <div role="alert" className="ws-state ws-failure ws-compact export-refusal" data-kind="missing-data" data-item={refusal.item}>
      <strong>Export refused: {refusal.item}</strong>
      <p>{refusal.reason}.</p>
      <p className="ws-hint">{refusal.remedy}. Nothing is omitted or truncated on your behalf.</p>
    </div>
  );
}
export function ExportPreview({ snapshot, recording }: { snapshot: ExportSnapshot; recording: RecordingView }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const [state, setState] = useState<"rendering" | "ready" | "error">("rendering");
  const [message, setMessage] = useState("");
  const size = previewSize(snapshot);
  useEffect(() => {
    let cancelled = false;
    const scene = new Scene() as PresentationScene;
    const session = new Session(recording, snapshot.manifest.layout);
    session.setSeries(snapshot.manifest.measurements);
    const host = document.createElement("div");
    const area = animationArea(size.width, size.height, snapshot.options.panel);
    setState("rendering");
    (async () => {
      await scene.init(host, area.width, area.height);
      if (cancelled) return;
      const context = canvas.current?.getContext("2d");
      if (!context) throw new Error("Canvas rendering is unavailable");
      await renderPresentationFrame(snapshot, snapshot.manifest.time.start, context, scene, session);
      if (!cancelled) setState("ready");
    })().catch((error: unknown) => {
      if (cancelled) return;
      setState("error");
      setMessage(error instanceof Error ? error.message : String(error));
    });
    return () => {
      cancelled = true;
      session.dispose();
      scene.dispose();
    };
  }, [snapshot, recording, size.width, size.height]);
  return (
    <figure className="export-preview" data-preview={state}>
      <canvas ref={canvas} width={size.width} height={size.height} aria-label={`Target preview at ${size.width} × ${size.height}`} />
      <figcaption>
        <span role="status">
          {state === "rendering" ? "Rendering the target preview…" : state === "ready" ? `Preview at the actual target size ${size.width} × ${size.height}, first frame at t = ${snapshot.manifest.time.start}` : `Preview failed: ${message}`}
        </span>
        <ul className="export-limits">
          {limitsSummary(snapshot).map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      </figcaption>
    </figure>
  );
}
