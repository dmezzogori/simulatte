import playerScript from "../apps/player/dist/player.js?raw";
import playerStyle from "../apps/player/src/player.css?raw";
import { offlineHtml } from "@simulatte/export";
import {
  ZipWriter,
  Uint8ArrayReader,
} from "../packages/export/node_modules/@zip.js/zip.js/index.js";
import { openRecording, LiveRecording, type RecordingView, type PrefixDescriptor } from "@simulatte/trace";
import { sha256 } from "../packages/trace/node_modules/@noble/hashes/sha2.js";
import { Session, type PreparedFrame } from "@simulatte/playback";
import { Scene, Projector } from "@simulatte/scene";
import { exportMedia, destination } from "@simulatte/export";
const status = document.getElementById("status")!;
async function run() {
  const params = new URLSearchParams(location.search),
    mode = params.get("mode") ?? "video";
  if (mode === "zip") {
    const count = Number(params.get("frames") ?? 5000),
      start = performance.now();
    let size = 0,
      heapPeak = 0;
    const zip = new ZipWriter(
      new WritableStream({
        write(data) {
          size += data.length;
        },
      }),
      { zip64: true, level: 0, bufferedWrite: false, useWebWorkers: false },
    );
    for (let i = 0; i < count; i++) {
      await zip.add(
        `frame-${String(i).padStart(6, "0")}.png`,
        new Uint8ArrayReader(new Uint8Array()),
      );
      heapPeak = Math.max(
        heapPeak,
        (performance as unknown as { memory?: { usedJSHeapSize: number } })
          .memory?.usedJSHeapSize ?? 0,
      );
      if (i % 100 === 0) {
        status.textContent = String(i);
        await new Promise((resolve) => setTimeout(resolve, 0));
      }
    }
    await zip.close();
    return {
      mode,
      count,
      size,
      heapPeak,
      seconds: (performance.now() - start) / 1000,
      userAgent: navigator.userAgent,
    };
  }
  const bytes = await (await fetch("/fixture.simtrace")).blob();
  const start = performance.now();
  if (mode === "html") {
    const output = await offlineHtml(bytes, playerScript, playerStyle);
    await fetch("/artifact", { method: "POST", body: output });
    return {
      mode,
      sourceBytes: bytes.size,
      outputBytes: output.size,
      seconds: (performance.now() - start) / 1000,
      userAgent: navigator.userAgent,
    };
  }
  let recording: RecordingView;
  let liveEvidence: Record<string, unknown> | null = null;
  if (mode === "live") {
    const data = new Uint8Array(await bytes.arrayBuffer());
    const hash = sha256.create();
    const timings: number[] = [];
    let offset = 12, prior = 0, generation = 0, reader: LiveRecording | undefined;
    let scanReads = 0, scanBytes = 0, cachePeak = 0, chunkPeak = 0;
    let scanning = false;
    const provider = (descriptor: PrefixDescriptor) => ({size: descriptor.length,
      read: async (at: number, length: number) => {
        if (scanning) {
          if (at < prior) throw new Error("Live extension rescanned earlier bytes");
          scanReads++; scanBytes += length;
        }
        return data.slice(at, Math.min(at + length, descriptor.length));
      }});
    const extend = async (end: number) => {
      hash.update(data.subarray(prior, end));
      const descriptor = {attempt: "a".repeat(32), generation: ++generation, length: end,
        sha256: Array.from(hash.clone().digest(), x => x.toString(16).padStart(2, "0")).join("")};
      const mark = performance.now();
      scanning = true;
      if (reader) await reader.extend(descriptor); else reader = await LiveRecording.open(descriptor, provider);
      scanning = false;
      prior = end;
      const view = reader.snapshot();
      if (view.finiteRange) {
        const temporary = new Session(view);
        await temporary.prepare({mode: "continuous", time: view.finiteRange[1], cursor: null});
        temporary.dispose();
      }
      timings.push(performance.now() - mark);
      cachePeak = Math.max(cachePeak, view.trace.cacheStats.estimatedBytes);
      chunkPeak = Math.max(chunkPeak, view.trace.cacheStats.chunks);
      if (cachePeak > 128 << 20 || chunkPeak > 4) throw new Error("Live cache exceeded bounds");
    };
    while (offset < data.length - 16) {
      const type = data[offset + 4];
      offset += 9 + new DataView(data.buffer).getUint32(offset);
      if (type === 3 || (type === 6 && offset - prior >= 4 << 20)) await extend(offset);
    }
    await extend(data.length);
    recording = reader!.snapshot();
    liveEvidence = {generations: generation, scanReads, scanBytes, cachePeak, chunkPeak,
      extensionAndEdgeMaxMs: Math.max(...timings), extensionAndEdgeMs: timings};
  } else recording = await openRecording(bytes);
  const opened = performance.now() - start;
  if (mode === "video") {
    Object.defineProperty(globalThis, "showSaveFilePicker", {
      value: undefined,
    });
    const frames = Number(params.get("frames") ?? "4"),
      format = params.get("format") === "png" ? "png" : "mp4";
    const end = recording.finiteRange![1],
      rate = frames > 1 ? (end * 30) / (frames - 1) : 1;
    let heapPeak = 0;
    const file = await exportMedia(
      recording,
      {
        start: frames === 1 ? end : 0,
        end,
        rate,
        fps: 30,
        width: Number(params.get("width") ?? 320),
        height: Number(params.get("height") ?? 180),
        background: "#f4f5f0",
        overlays: true,
        format,
      },
      await destination("acceptance." + format, format === "png"),
      new AbortController().signal,
      (i, n) => {
        status.textContent = `${i}/${n}`;
        if (i % 1000 === 0) console.log(`progress ${i}/${n}`);
        heapPeak = Math.max(
          heapPeak,
          (performance as unknown as { memory?: { usedJSHeapSize: number } })
            .memory?.usedJSHeapSize ?? 0,
        );
      },
    );
    if (!file.blob) throw new Error("Expected staged output");
    await fetch("/artifact", { method: "POST", body: file.blob });
    const result = {
      mode,
      userAgent: navigator.userAgent,
      frames,
      size: file.blob.size,
      seconds: (performance.now() - start) / 1000,
      sink: file.kind,
      openMs: opened,
      heapPeak,
    };
    await file.remove();
    return result;
  }
  const host = document.createElement("div");
  host.id = "host";
  host.style.cssText = "width:1280px;height:720px";
  document.body.append(host);
  const scene = new Scene();
  await scene.init(host, 1280, 720);
  const session = new Session(recording),
    range = recording.finiteRange!;
  // ?nomap=1: every frame shown with its layout's network removed, so the scene builds no map model (the seeks and
  // the scene numbers then say what the map costs).
  const nomap = params.get("nomap") === "1";
  const show = (frame: PreparedFrame): PreparedFrame =>
    nomap && frame.layout ? { ...frame, layout: { ...frame.layout, network: null } as typeof frame.layout } : frame;
  // ?norender=1: the seeks only prepare their frames (decode and reconstruction), the scene draws none of them.
  const norender = params.get("norender") === "1",
    settle = params.get("settle") === "1",
    coldSettle: number[] = [],
    coldGc: number[] = [];
  const cold: number[] = [],
    coldPrepare: number[] = [],
    coldRender: number[] = [],
    warm: number[] = [],
    render: number[] = [],
    project: number[] = [];
  let seed = 312;
  const positions = Array.from({ length: Number(params.get("seeks") ?? 50) }, () => {
    seed = (seed * 1664525 + 1013904223) >>> 0;
    return range[0] + ((range[1] - range[0]) * seed) / 2 ** 32;
  });
  for (const time of positions) {
    const start = performance.now();
    const frame = await session.prepare({ mode: "continuous", time, cursor: null });
    const prepared = performance.now();
    if (!norender) scene.render(show(frame));
    const end = performance.now();
    // ?settle=1: wait for the next animation frame after each render and time that wait, so the work the render
    // leaves to the browser (and the graphics process) is counted apart from the next seek.
    const collector = (globalThis as unknown as { gc?: () => void }).gc;
    if (params.get("gc") === "1" && collector) {
      // needs the browser launched with --js-flags=--expose-gc: a full collection after each render, timed apart
      const mark = performance.now();
      collector();
      coldGc.push(performance.now() - mark);
    }
    if (settle) {
      await new Promise((resolve) => requestAnimationFrame(resolve));
      coldSettle.push(performance.now() - end);
    }
    cold.push(end - start);
    coldPrepare.push(prepared - start);
    coldRender.push(end - prepared);
    const next = performance.now();
    const again = await session.prepare({ mode: "continuous", time, cursor: null });
    if (!norender) scene.render(show(again));
    warm.push(performance.now() - next);
  }
  // Scene-only moving-glyph workload, independent of seek/decode time.
  const first = await session.prepare({ mode: "continuous", time: range[0], cursor: null });
  const projector = new Projector(first.initialState ?? first.state);
  for (let i = 0; i < 240; i++) {
    const frame = await session.prepare({
      mode: "continuous",
      time: range[0] + ((range[1] - range[0]) * i) / 240,
      cursor: null,
    });
    // The projection alone, by a projector of its own that follows the same frames, then the whole render.
    const mark = performance.now();
    projector.project(show(frame));
    project.push(performance.now() - mark);
    const start = performance.now();
    scene.render(show(frame));
    render.push(performance.now() - start);
    await new Promise((resolve) => requestAnimationFrame(resolve));
  }
  const percentile = (a: number[], p: number) =>
    [...a].sort((a, b) => a - b)[Math.floor((a.length - 1) * p)];
  const result = {
    mode,
    userAgent: navigator.userAgent,
    openMs: opened,
    coldSeekP50: percentile(cold, 0.5),
    coldSeekP95: percentile(cold, 0.95),
    // the first seek split: the playback session's prepare (decode and reconstruction) and the scene's render
    coldPrepareP50: percentile(coldPrepare, 0.5),
    coldPrepareP95: percentile(coldPrepare, 0.95),
    coldRenderP50: percentile(coldRender, 0.5),
    coldRenderP95: percentile(coldRender, 0.95),
    nomap,
    norender,
    coldGcP50: percentile(coldGc, 0.5),
    coldSettleP50: percentile(coldSettle, 0.5),
    coldSettleP95: percentile(coldSettle, 0.95),
    warmSeekP50: percentile(warm, 0.5),
    warmSeekP95: percentile(warm, 0.95),
    renderP95: percentile(render, 0.95),
    renderP50: percentile(render, 0.5),
    projectP50: percentile(project, 0.5),
    projectP95: percentile(project, 0.95),
    // render includes its own projection; the rest is what the scene does with it (transfers, drawing)
    restOfRenderP95: percentile(render.map((value, i) => value - project[i]), 0.95),
    traceBytes: bytes.size,
    live: liveEvidence,
    cache: recording.trace.cacheStats,
    heap: (() => {
      const m = (
        performance as unknown as {
          memory?: { usedJSHeapSize: number; totalJSHeapSize: number };
        }
      ).memory;
      return m ? { used: m.usedJSHeapSize, total: m.totalJSHeapSize } : null;
    })(),
  };
  if (params.has("shot")) {
    // Kept for the runner's screenshot: a frame late in the run, fitted. The scene is not disposed.
    scene.render(show(await session.prepare({ mode: "continuous", time: range[0] + (range[1] - range[0]) * 0.7, cursor: null })));
    scene.fit();
    await new Promise((resolve) => requestAnimationFrame(resolve));
    session.dispose();
    return result;
  }
  session.dispose();
  scene.dispose();
  return result;
}
void run()
  .then(async (result) => {
    status.textContent = JSON.stringify(result, null, 2);
    await fetch("/result", { method: "POST", body: JSON.stringify(result) });
  })
  .catch(async (error) => {
    status.textContent = String(error);
    await fetch("/result", {
      method: "POST",
      body: JSON.stringify({
        error: String(error),
        stack: error.stack,
        userAgent: navigator.userAgent,
      }),
    });
  });
