import {
  CanvasSource,
  Output,
  Mp4OutputFormat,
  WebMOutputFormat,
  StreamTarget,
} from "mediabunny";
import { ZipWriter, BlobReader, TextReader } from "@zip.js/zip.js";
import { Session } from "@simulatte/playback";
import { Scene } from "@simulatte/scene";
import type { RecordingView, LayoutBundle } from "@simulatte/trace";
import { frameSchedule } from "./index";
import type { Destination, ExportFile } from "./sink";
import { animationArea, renderPresentationFrame, type ExportSnapshot, type PresentationScene } from "./presentation";
export const PNG_FRAME_LIMIT = 10000;
export interface MediaOptions {
  start: number;
  end: number;
  fps: number;
  rate: number;
  width: number;
  height: number;
  format: "mp4" | "webm" | "png";
  background: string;
  overlays: boolean;
}
export function validateMedia(
  options: MediaOptions,
  range?: readonly [number, number] | null,
): ReturnType<typeof frameSchedule> {
  if (![24, 30, 60].includes(options.fps))
    throw new RangeError("Choose 24, 30 or 60 fps");
  if (
    ![options.width, options.height].every(
      (n) => Number.isInteger(n) && n >= 2 && n % 2 === 0,
    ) ||
    options.width > 3840 ||
    options.height > 2160
  )
    throw new RangeError("Use even dimensions up to 3840 × 2160");
  if (!/^#[\da-f]{6}$/i.test(options.background))
    throw new RangeError("Invalid background color");
  if (
    range !== undefined &&
    (!range || options.start < range[0] || options.end > range[1])
  )
    throw new RangeError(
      "Export interval must be inside the recording’s finite time range",
    );
  const schedule = frameSchedule(
    options.start,
    options.end,
    options.fps,
    options.rate,
  );
  if (options.format === "png" && schedule.count > PNG_FRAME_LIMIT)
    throw new RangeError(
      "PNG archives are limited to 10,000 frames to bound ZIP directory memory. Use video for longer exports.",
    );
  return schedule;
}
export async function codecFor(
  options: MediaOptions,
): Promise<"avc" | "vp9" | "vp8" | null> {
  validateMedia(options);
  if (options.format === "png") return null;
  if (typeof VideoEncoder === "undefined")
    throw new Error("WebCodecs encoding is unavailable. Choose PNG frames.");
  const candidates =
    options.format === "mp4"
      ? [["avc", "avc1.420033"]]
      : [
          ["vp9", "vp09.00.41.08"],
          ["vp8", "vp8"],
        ];
  for (const [name, codec] of candidates) {
    try {
      const support = await VideoEncoder.isConfigSupported({
        codec: codec!,
        width: options.width,
        height: options.height,
        framerate: options.fps,
        bitrate: 8_000_000,
        latencyMode: "quality",
      });
      if (support.supported) return name as "avc" | "vp9" | "vp8";
    } catch {
      /* Try the next concrete codec. */
    }
  }
  throw new Error(
    `${options.format.toUpperCase()} encoding is unavailable at this resolution. Try WebM or PNG frames.`,
  );
}
export async function exportMedia(
  recording: RecordingView,
  options: MediaOptions,
  sink: Destination,
  signal: AbortSignal,
  progress: (frame: number, total: number) => void,
  presentation: LayoutBundle | null = null,
  snapshot: ExportSnapshot | null = null,
): Promise<ExportFile> {
  // A presentation snapshot pins the layout, the selected series and the framing of every frame.
  const session = new Session(recording, snapshot ? snapshot.manifest.layout : presentation),
    scene = new Scene() as PresentationScene;
  if (snapshot) session.setSeries(snapshot.manifest.measurements);
  const area = snapshot ? animationArea(options.width, options.height, snapshot.options.panel) : { width: options.width, height: options.height, panelWidth: 0 };
  const callerSignal = signal,
    lifetime = new AbortController();
  const cancel = () => lifetime.abort(callerSignal.reason);
  if (callerSignal.aborted) cancel();
  else callerSignal.addEventListener("abort", cancel, { once: true });
  signal = lifetime.signal;
  scene.onError = (message) =>
    lifetime.abort(new Error(`Export rendering failed: ${message}`));
  const host = document.createElement("div");
  let output: Output | undefined;
  let zip: ZipWriter<unknown> | undefined;
  try {
    const schedule = validateMedia(options);
    if (
      !recording.finiteRange ||
      options.start < recording.finiteRange[0] ||
      options.end > recording.finiteRange[1]
    )
      throw new RangeError(
        "Export interval must be inside the recording’s finite time range",
      );
    const codec = await codecFor(options);
    signal.throwIfAborted();
    await scene.init(host, area.width, area.height);
    signal.throwIfAborted();
    scene.app.renderer.background.color = options.background;
    const canvas =
      typeof OffscreenCanvas === "undefined"
        ? document.createElement("canvas")
        : new OffscreenCanvas(options.width, options.height);
    canvas.width = options.width;
    canvas.height = options.height;
    const context = canvas.getContext("2d") as
      | CanvasRenderingContext2D
      | OffscreenCanvasRenderingContext2D
      | null;
    if (!context) throw new Error("Canvas rendering is unavailable");
    let source: CanvasSource | undefined,
      offset = 0;
    if (codec) {
      output = new Output({
        format:
          options.format === "mp4"
            ? new Mp4OutputFormat({ fastStart: false })
            : new WebMOutputFormat(),
        target: new StreamTarget(
          new WritableStream({
            write: (chunk) => sink.write(chunk.data, chunk.position),
          }),
          { chunked: true, chunkSize: 1024 * 1024 },
        ),
      });
      source = new CanvasSource(canvas, {
        codec,
        bitrate: 8_000_000,
        latencyMode: "quality",
        fullCodecString:
          codec === "avc"
            ? "avc1.420033"
            : codec === "vp9"
              ? "vp09.00.41.08"
              : "vp8",
        keyFrameInterval: 2,
      });
      output.addVideoTrack(source, { frameRate: options.fps });
      await output.start();
    } else {
      zip = new ZipWriter(
        new WritableStream<Uint8Array<ArrayBuffer>>({
          async write(data) {
            await sink.write(data, offset);
            offset += data.length;
          },
        }),
        { zip64: true, level: 0, bufferedWrite: false, useWebWorkers: false },
      );
      await zip.add(
        "timing.json",
        new TextReader(
          JSON.stringify({
            ...options,
            count: schedule.count,
            timestamp: "round(i*1000000/fps)",
            duration: "timestamp(i+1)-timestamp(i)",
            time: "i == count-1 ? end : start+i*rate/fps",
            terminalHold: "one frame",
          }),
        ),
      );
    }
    for (let i = 0; i < schedule.count; i++) {
      signal.throwIfAborted();
      const frame = schedule.frame(i);
      const prepared = await session.prepare(
        { mode: "continuous", time: frame.time, cursor: null },
        signal,
      );
      if (snapshot) {
        await renderPresentationFrame(snapshot, frame.time, context, scene, session, {
          frame: prepared,
          transferClock: i,
          transferLifetime: 6,
          background: options.background,
        });
      } else {
        scene.render(prepared, { transferClock: i, transferLifetime: 6 });
        context.fillStyle = options.background;
        context.fillRect(0, 0, canvas.width, canvas.height);
        context.drawImage(scene.canvas, 0, 0);
      }
      signal.throwIfAborted();
      if (options.overlays) {
        context.fillStyle = "#172b23";
        context.font = `${Math.max(14, options.width / 80)}px system-ui`;
        context.fillText(
          `simulatte · t = ${frame.time.toPrecision(7)}`,
          20,
          canvas.height - 24,
        );
      }
      if (source) await source.add(frame.timestamp / 1e6, frame.duration / 1e6);
      else {
        const png =
          "convertToBlob" in canvas
            ? await canvas.convertToBlob({ type: "image/png" })
            : await new Promise<Blob>((resolve, reject) =>
                canvas.toBlob(
                  (blob) =>
                    blob
                      ? resolve(blob)
                      : reject(new Error("PNG encoding failed")),
                  "image/png",
                ),
              );
        signal.throwIfAborted();
        await zip!.add(
          `frame-${String(i).padStart(6, "0")}.png`,
          new BlobReader(png),
          { signal },
        );
      }
      progress(i + 1, schedule.count);
      // Keep cancellation and browser UI responsive even with an immediate mock encoder.
      if (i % 8 === 0) await new Promise((resolve) => setTimeout(resolve, 0));
    }
    signal.throwIfAborted();
    if (output) await output.finalize();
    else await zip!.close();
    signal.throwIfAborted();
    const result = await sink.finish();
    if (signal.aborted) {
      await result.remove();
      signal.throwIfAborted();
    }
    return result;
  } catch (error) {
    try {
      await output?.cancel();
    } catch {
      /* Preserve the original error. */
    }
    try {
      await sink.abort();
    } catch (cleanup) {
      throw new AggregateError(
        [error, cleanup],
        "Export failed and cleanup failed; an incomplete output may remain. Remove it from the chosen destination or browser site data.",
      );
    }
    throw error;
  } finally {
    callerSignal.removeEventListener("abort", cancel);
    scene.onError = null;
    session.dispose();
    scene.dispose();
  }
}
