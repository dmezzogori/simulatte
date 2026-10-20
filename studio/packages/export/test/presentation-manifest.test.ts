import { readFileSync } from "node:fs";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
const calls = vi.hoisted(() => ({
  init: vi.fn(),
  fitBounds: vi.fn(),
  setVisibility: vi.fn(),
  render: vi.fn(),
  setSeries: vi.fn(),
  add: vi.fn(async () => {}),
}));
vi.mock("@simulatte/scene", () => ({
  Scene: class {
    app = { renderer: { background: { color: "" } } };
    canvas = { width: 1, height: 1 };
    camera = { x: 0, y: 0, scale: 1 };
    onError: ((message: string) => void) | null = null;
    async init(...args: unknown[]) {
      calls.init(...args);
    }
    fitBounds(bounds: unknown) {
      calls.fitBounds(bounds);
    }
    setVisibility(selection: unknown) {
      calls.setVisibility(selection);
    }
    render() {
      calls.render();
    }
    fit() {}
    dispose() {}
  },
}));
vi.mock("@simulatte/playback", () => ({
  Session: class {
    constructor(
      readonly recording: unknown,
      readonly layout: unknown,
    ) {}
    setSeries(keys: string[]) {
      calls.setSeries(keys);
    }
    async prepare() {
      return { kpis: { "shop/wip": { value: 3 }, "shop/tp": 0.5 } };
    }
    dispose() {}
  },
  freezePresentation: (layout: unknown) => layout,
}));
vi.mock("mediabunny", () => ({
  CanvasSource: class {
    add(...args: unknown[]) {
      return calls.add(...(args as []));
    }
  },
  Output: class {
    addVideoTrack() {}
    async start() {}
    async finalize() {}
    async cancel() {}
  },
  Mp4OutputFormat: class {},
  WebMOutputFormat: class {},
  StreamTarget: class {},
}));
import { Sha256, canonicalHash, canonicalPack, sha256Blob } from "../src/canonical";
import { offlineHtml, presentationBlock } from "../src/index";
import { exportMedia } from "../src/media";
import {
  ExportRefusal,
  outputContentHash,
  animationArea,
  containBounds,
  imageSize,
  layoutPanel,
  prepareExport,
  renderPresentationFrame,
  snapshotHash,
  staticIdentities,
  wrapText,
  type EvidenceOutput,
  type EvidenceSelection,
  type EvidenceSource,
  type ExportSnapshot,
  type PresentationManifest,
  type VideoOptions,
} from "../src/presentation";
import type { Recording } from "@simulatte/trace";

const fixture = JSON.parse(readFileSync(new URL("./canonical-fixture.json", import.meta.url), "utf8")) as {
  samples: Record<string, { value: unknown; pack_hex: string; hash: string }>;
};
const hex = (bytes: Uint8Array) => Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
const png = (width: number, height: number) => {
  const bytes = new Uint8Array(64);
  bytes.set([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0, 0, 0, 13, 0x49, 0x48, 0x44, 0x52]);
  new DataView(bytes.buffer).setUint32(16, width);
  new DataView(bytes.buffer).setUint32(20, height);
  return new Blob([bytes], { type: "image/png" });
};
const labels = (name: string, unit: string | null = null) => ({
  name,
  unit,
  population: "completed jobs",
  sample: { planned: 24, available: 8, partial: true },
  convention: "descriptive",
  source: { analysis_name: "Lateness", snapshot_hash: "f".repeat(64), execution_id: "e".repeat(8) + "-0000-4000-8000-" + "0".repeat(12) },
});
const contentHashes = new Map<string, string>();
const evidence = (id: string, kind: EvidenceSelection["kind"], selection: EvidenceSelection["selection"] = {}): EvidenceSelection => ({
  id,
  execution_id: "11111111-2222-4333-8444-555555555555",
  output_id: id.padEnd(64, "0"),
  content_hash: contentHashes.get(id) ?? id.padEnd(64, "0"),
  kind,
  selection,
  labels: labels(id, kind === "number" ? "h" : null),
});
const manifest = (overrides: Partial<PresentationManifest> = {}): PresentationManifest => ({
  schema: 1,
  id: "7f8e2a1c-0c5d-4e3a-9b1e-2d6f8a0b1c2d",
  revision: 2,
  name: "Lateness",
  notes: "",
  recording: { attempt_id: "a".repeat(32), request_id: "b".repeat(64), trace_sha256: "c".repeat(64) },
  layout: { schema_version: "1.0" } as PresentationManifest["layout"],
  camera: { bounds: [-1.5, 0, 12, 8.25] },
  visibility: { entities: null, labels: ["wc-1"] },
  measurements: ["shop/wip"],
  time: { start: 0, end: 10.5 },
  rate: 1,
  annotations: [{ id: "9d1b2c3e-4f5a-4b6c-8d7e-0f1a2b3c4d5e", text: "Peak", time: 3.25 }],
  evidence: [],
  created: 1760000000,
  ...overrides,
});
const video = (overrides: Partial<VideoOptions> = {}): VideoOptions => ({
  start: 0,
  end: 10.5,
  fps: 30,
  rate: 1,
  width: 1920,
  height: 1080,
  format: "mp4",
  background: "#f4f5f0",
  overlays: true,
  panel: true,
  ...overrides,
});
const outputs: Record<string, EvidenceOutput> = {
  total: { id: "total".padEnd(64, "0"), name: "total", kind: "number", artifacts: [], metadata: { unit: "h" }, renderer: null },
  values: {
    id: "values".padEnd(64, "0"),
    name: "values",
    kind: "table",
    artifacts: [],
    metadata: { schema: { fields: [{ name: "family", dtype: "string" }, { name: "mean", dtype: "float64" }, { name: "big", dtype: "int64" }] }, row_count: 10000 },
    renderer: null,
  },
  spread: { id: "spread".padEnd(64, "0"), name: "spread", kind: "distribution", artifacts: [], metadata: {}, renderer: null },
  drawing: { id: "drawing".padEnd(64, "0"), name: "drawing", kind: "image", artifacts: [{ path: "drawing/image.png", length: 64, sha256: "1".repeat(64) }], metadata: { media_type: "image/png" }, renderer: null },
  figure: { id: "figure".padEnd(64, "0"), name: "figure", kind: "figure", artifacts: [{ path: "figure/figure.json", length: 10, sha256: "2".repeat(64) }], metadata: {}, renderer: { name: "plotly.js", version: "4.1.1" } },
};
beforeAll(async () => {
  for (const [name, output] of Object.entries(outputs)) contentHashes.set(name, await outputContentHash(output));
});
const source = (overrides: Partial<EvidenceSource> = {}): EvidenceSource => ({
  async output(_execution, outputId) {
    const found = Object.values(outputs).find((o) => o.id === outputId);
    if (!found) throw new Error("Unknown table output");
    return found;
  },
  async value() {
    return 9223372036854775807n;
  },
  async rows(_e, _o, offset, limit) {
    return Array.from({ length: limit }, (_, i) => ({ family: i % 2 ? "B" : "A", mean: i === 1 ? NaN : (offset + i) / 2, big: 2n ** 62n + BigInt(offset + i) }));
  },
  async summary() {
    return { representation: "exact", count: 8, median: 1.5 };
  },
  async file() {
    return png(640, 360);
  },
  async capture(_e, _o, width, height) {
    return png(width, height);
  },
  ...overrides,
});
const full = () =>
  manifest({
    evidence: [
      evidence("total", "number"),
      evidence("values", "table", { rows: { offset: 0, limit: 3, columns: ["family", "mean", "big"] } }),
      evidence("spread", "distribution"),
      evidence("drawing", "image"),
      evidence("figure", "figure", { figure: { width: 800, height: 450 } }),
    ],
  });
const refusal = async (promise: Promise<unknown>) => {
  try {
    await promise;
  } catch (error) {
    expect(error).toBeInstanceOf(ExportRefusal);
    return error as ExportRefusal;
  }
  throw new Error("expected a refusal");
};

describe("canonical hashing matches Python", () => {
  for (const [name, sample] of Object.entries(fixture.samples))
    it(`packs and hashes the ${name} sample byte for byte`, async () => {
      expect(hex(canonicalPack(sample.value))).toBe(sample.pack_hex);
      expect(await canonicalHash("export-snapshot-v1", sample.value)).toBe(sample.hash);
    });
  it("matches the shared Python fixtures for the export snapshot and the evidence output content", async () => {
    const shared = new URL("../../../../tests/fixtures/presentations/", import.meta.url);
    const exportFixture = JSON.parse(readFileSync(new URL("export-snapshot-hash.json", shared), "utf8")) as { input: { manifest: PresentationManifest; target: "video"; options: VideoOptions; statics: { evidence_id: string; media_type: string; sha256: string; size: number }[] }; hash: string };
    expect(await snapshotHash(exportFixture.input.manifest, exportFixture.input.target, exportFixture.input.options, exportFixture.input.statics)).toBe(exportFixture.hash);
    const content = JSON.parse(readFileSync(new URL("output-content-hash.json", shared), "utf8")) as { input: EvidenceOutput; hash: string };
    expect(await outputContentHash(content.input)).toBe(content.hash);
  });
  it("derives the export snapshot hash from manifest, target, options and static identities", async () => {
    const sample = fixture.samples.snapshot!.value as { manifest: PresentationManifest; options: VideoOptions; statics: { evidence_id: string; media_type: string; sha256: string; size: number }[] };
    expect(await snapshotHash(sample.manifest, "video", sample.options, sample.statics)).toBe(fixture.samples.snapshot!.hash);
  });
});

describe("streaming digest", () => {
  it("matches crypto.subtle across block boundaries and large blobs", async () => {
    for (const size of [0, 1, 55, 56, 63, 64, 65, 1000, 70000]) {
      const bytes = Uint8Array.from({ length: size }, (_, i) => (i * 31 + 7) & 0xff);
      const expected = hex(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)));
      expect(new Sha256().update(bytes.subarray(0, 10)).update(bytes.subarray(10)).hex()).toBe(expected);
      expect(await sha256Blob(new Blob([bytes]), 1024)).toBe(expected);
    }
  });
});

describe("framing and panel geometry", () => {
  it("contains the saved bounds in any aspect ratio without cropping", () => {
    const bounds = [0, 0, 16, 9] as const;
    for (const [w, h] of [[1920, 1080], [1080, 1920], [600, 600], [3840, 400]] as const) {
      const camera = containBounds(bounds, w, h);
      const corners = [
        [0, 0],
        [16, 9],
      ].map(([x, y]) => [camera.x + x! * camera.scale, camera.y - y! * camera.scale]);
      for (const [sx, sy] of corners) {
        expect(sx).toBeGreaterThanOrEqual(-1e-6);
        expect(sx).toBeLessThanOrEqual(w + 1e-6);
        expect(sy).toBeGreaterThanOrEqual(-1e-6);
        expect(sy).toBeLessThanOrEqual(h + 1e-6);
      }
      expect(Math.abs(camera.scale * 16 - w) < 1e-6 || Math.abs(camera.scale * 9 - h) < 1e-6).toBe(true);
    }
    expect(() => containBounds([1, 0, 1, 1], 10, 10)).toThrow(RangeError);
  });
  it("reserves a 30 % panel of at least 320 px", () => {
    expect(animationArea(1920, 1080, true)).toEqual({ width: 1344, height: 1080, panelWidth: 576 });
    expect(animationArea(640, 360, true)).toEqual({ width: 320, height: 360, panelWidth: 320 });
    expect(animationArea(640, 360, false)).toEqual({ width: 640, height: 360, panelWidth: 0 });
    expect(wrapText("one two three four", 9)).toEqual(["one two", "three", "four"]);
    expect(wrapText("abcdefghijkl", 5)).toEqual(["abcde", "fghij", "kl"]);
    expect(imageSize("image/svg+xml", new TextEncoder().encode('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 48"></svg>'))).toEqual({ width: 120, height: 48 });
  });
});

describe("prepareExport", () => {
  it("freezes verified statics, excerpts and the snapshot hash for a complete selection", async () => {
    const snapshot = await prepareExport(full(), "video", video(), source(), new AbortController().signal);
    expect(snapshot.statics.map((s) => [s.evidence_id, s.media_type, s.width, s.height, s.size])).toEqual([
      ["drawing", "image/png", 640, 360, 64],
      ["figure", "image/png", 800, 450, 64],
    ]);
    expect(snapshot.evidence.map((e) => e.kind)).toEqual(["number", "table", "distribution", "image", "figure"]);
    const table = snapshot.evidence[1];
    if (table?.kind !== "table") throw new Error("table");
    expect(table.rows).toEqual([
      ["A", "0", "4611686018427387904"],
      ["B", "NaN", "4611686018427387905"],
      ["A", "1", "4611686018427387906"],
    ]);
    expect(table.total).toBe(10000);
    expect(snapshot.evidence[0]).toEqual({ id: "total", kind: "number", value: "9223372036854775807" });
    expect(snapshot.hash).toBe(await snapshotHash(snapshot.manifest, "video", video(), staticIdentities(snapshot.statics)));
    expect(snapshot.limits).toMatchObject({ frames: 316, animation: { width: 1344, height: 1080 }, panelWidth: 576, staticBytes: 128 });
    const layout = layoutPanel(snapshot, 1920, 1080, { "shop/wip": "3" });
    const texts = layout.items.filter((i) => i.item.kind === "text").map((i) => (i.item as { text: string }).text);
    expect(texts).toContain("shop/wip: 3");
    expect(texts).toContain("t = 3.25 · Peak");
    expect(texts.some((t) => t.includes("8 of 24 replications · partial snapshot"))).toBe(true);
    expect(layout.items.filter((i) => i.item.kind === "image")).toHaveLength(2);
  });
  it("names the item that does not fit instead of dropping or truncating it", async () => {
    const long = manifest({ annotations: [{ id: "a", text: "word ".repeat(400).trim(), time: null }] });
    const refused = await refusal(prepareExport(long, "video", video({ width: 640, height: 200 }), source(), new AbortController().signal));
    expect(refused.item).toBe("annotation 1");
    expect(refused.reason).toContain("does not fit");
    const fits = await prepareExport(long, "video", video({ width: 3840, height: 2160 }), source(), new AbortController().signal);
    expect(fits.limits.animation).toEqual({ width: 2688, height: 2160 });
    const big = manifest({ evidence: [evidence("values", "table", { rows: { offset: 100, limit: 50, columns: ["family", "mean"] } })] });
    expect((await refusal(prepareExport(big, "video", video({ width: 640, height: 360 }), source(), new AbortController().signal))).item).toBe("table values");
    const ok = await prepareExport(big, "html", { panel: true, traceSize: 1000 }, source(), new AbortController().signal);
    expect(ok.evidence[0]).toMatchObject({ kind: "table", offset: 100, total: 10000 });
    expect((ok.evidence[0] as { rows: string[][] }).rows).toHaveLength(50);
    const wide = manifest({ evidence: [evidence("values", "table", { rows: { offset: 0, limit: 1, columns: ["family", "mean", "big"] } })] });
    const longCells = source({ async rows() { return [{ family: "a family name far too long for a narrow column", mean: 1, big: 1n }]; } });
    const narrow = await refusal(prepareExport(wide, "video", video({ width: 480, height: 270 }), longCells, new AbortController().signal));
    expect(narrow.item).toBe("table values");
    expect(narrow.reason).toContain("wider than its");
  });
  it("refuses missing static images, unsupported figures and changed outputs by name", async () => {
    const missing = manifest({ evidence: [evidence("drawing", "image")] });
    const gone = source({ async file() { throw new Error("Unknown analysis artifact"); } });
    const refused = await refusal(prepareExport(missing, "html", { panel: true, traceSize: 10 }, gone, new AbortController().signal));
    expect(refused.item).toBe("image drawing");
    expect(refused.reason).toContain("static image is missing");
    const figure = manifest({ evidence: [evidence("figure", "figure")] });
    const unsupported = source({ async capture() { throw new Error("layout.images[0].source: URL sources are not allowed"); } });
    const noView = await refusal(prepareExport(figure, "video", video(), unsupported, new AbortController().signal));
    expect(noView.item).toBe("figure figure");
    expect(noView.reason).toContain("URL sources are not allowed");
    expect(noView.remedy).toContain("static image");
    const changed = manifest({ evidence: [{ ...evidence("total", "number"), content_hash: "9".repeat(64) }] });
    expect((await refusal(prepareExport(changed, "html", { panel: true, traceSize: 10 }, source(), new AbortController().signal))).reason).toContain("changed since it was selected");
  });
  it("keeps the 100 MiB / 150 MiB HTML caps including evidence and the frame caps", async () => {
    const m = manifest({ evidence: [evidence("drawing", "image")] });
    const bytes = new Uint8Array(14 * 1024 * 1024);
    bytes.set([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0, 0, 0, 13, 0x49, 0x48, 0x44, 0x52, 0, 0, 0, 10, 0, 0, 0, 10]);
    const large = source({ async file() { return new Blob([bytes]); } });
    const html = await refusal(prepareExport(m, "html", { panel: true, traceSize: 100 * 1024 * 1024 }, large, new AbortController().signal));
    expect(html.item).toBe("offline HTML");
    expect(html.reason).toContain("150 MiB");
    expect((await refusal(prepareExport(m, "html", { panel: true, traceSize: 100 * 1024 * 1024 + 1 }, source(), new AbortController().signal))).reason).toContain("100 MiB");
    expect((await refusal(prepareExport(manifest(), "video", video({ end: 5000, rate: 1, fps: 30 }), source(), new AbortController().signal))).reason).toContain("100,000");
    expect((await refusal(prepareExport(manifest(), "video", video({ end: 400, format: "png" }), source(), new AbortController().signal))).reason).toContain("10,000");
    expect((await refusal(prepareExport(manifest(), "video", video({ width: 400, height: 300 }), source(), new AbortController().signal))).item).toBe("video size");
  });
});

describe("standalone HTML and video share the snapshot", () => {
  let snapshot: ExportSnapshot;
  beforeEach(async () => {
    calls.init.mockClear();
    calls.fitBounds.mockClear();
    calls.setVisibility.mockClear();
    calls.render.mockClear();
    calls.setSeries.mockClear();
    calls.add.mockReset().mockResolvedValue();
    vi.stubGlobal("VideoEncoder", { isConfigSupported: async () => ({ supported: true }) });
    vi.stubGlobal("createImageBitmap", async () => ({ bitmap: true }));
    snapshot = await prepareExport(full(), "video", video({ end: 0.1, width: 1280, height: 720, panel: true }), source(), new AbortController().signal);
  });
  afterEach(() => vi.unstubAllGlobals());
  const context = () => {
    const texts: string[] = [];
    const images: unknown[][] = [];
    return {
      texts,
      images,
      ctx: {
        canvas: { width: 1280, height: 720 },
        fillStyle: "",
        font: "",
        textBaseline: "",
        textAlign: "",
        fillRect() {},
        fillText(text: string) {
          texts.push(text);
        },
        drawImage(...args: unknown[]) {
          images.push(args);
        },
      } as unknown as CanvasRenderingContext2D,
    };
  };
  it("embeds a version-2 block with statics, excerpts and the frozen manifest, escaped and size-checked", async () => {
    const trace = new Blob(["recording bytes"]);
    const digest = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", await trace.arrayBuffer())), (b) => b.toString(16).padStart(2, "0")).join("");
    const htmlSnapshot = { ...snapshot, manifest: { ...snapshot.manifest, recording: { ...snapshot.manifest.recording, trace_sha256: digest }, notes: "</script><script>alert(1)</script>" } };
    const text = await (await offlineHtml(trace, "console.log(1)", "", undefined, null, htmlSnapshot)).text();
    expect(text).not.toContain("<script>alert");
    const block = JSON.parse(text.match(/<script id="presentation" type="application\/json">(.*?)<\/script>/s)![1]!);
    expect(Object.keys(block).sort()).toEqual(["evidence", "manifest", "snapshot_hash", "statics", "trace_sha256", "version"]);
    expect(block.version).toBe(2);
    expect(block.trace_sha256).toBe(digest);
    expect(block.snapshot_hash).toBe(snapshot.hash);
    expect(block.manifest.notes).toContain("alert(1)");
    expect(block.statics).toHaveLength(2);
    expect(Buffer.from(block.statics[0].data_base64, "base64").subarray(0, 4)).toEqual(Buffer.from([0x89, 0x50, 0x4e, 0x47]));
    expect(block.evidence[0]).toEqual({ id: "total", kind: "number", value: "9223372036854775807" });
    expect(text.match(/<script/g)).toHaveLength(3);
    await expect(offlineHtml(trace, "", "", undefined, null, snapshot)).rejects.toThrow("differ from the presentation");
    const huge = { ...htmlSnapshot, statics: [{ ...snapshot.statics[0]!, bytes: new Uint8Array(0), size: 0 }] };
    expect(presentationBlock(huge, digest)).toContain('"version":2');
  });
  it("renders the animation at the saved framing and the panel with measurements and provenance", async () => {
    const { ctx, texts, images } = context();
    const scene = new (await import("@simulatte/scene")).Scene() as unknown as import("../src/presentation").PresentationScene;
    const session = new (await import("@simulatte/playback")).Session({} as Recording, null);
    await renderPresentationFrame(snapshot, 0.05, ctx, scene, session);
    await renderPresentationFrame(snapshot, 0.06, ctx, scene, session);
    expect(calls.fitBounds).toHaveBeenCalledTimes(1);
    expect(calls.fitBounds).toHaveBeenCalledWith([-1.5, 0, 12, 8.25]);
    expect(calls.setVisibility).toHaveBeenCalledWith({ entities: null, labels: new Set(["wc-1"]) });
    expect(texts).toContain("shop/wip: 3");
    expect(texts).toContain("t = 3.25 · Peak");
    expect(texts.some((t) => t.startsWith("presentation 7f8e2a1c r2 · recording aaaaaaaa · snapshot " + snapshot.hash.slice(0, 12)))).toBe(true);
    expect(images[0]).toEqual([scene.canvas, 0, 0, 896, 720]);
    expect(images.filter((i) => (i[0] as { bitmap?: boolean }).bitmap)).toHaveLength(4);
  });
  it("exportMedia initializes the scene at the animation size and composes every frame through the shared renderer", async () => {
    const texts: string[] = [];
    vi.stubGlobal("document", {
      createElement: () => ({
        getContext: () => ({ canvas: { width: 1280, height: 720 }, fillRect() {}, drawImage() {}, fillText(text: string) { texts.push(text); } }),
      }),
    });
    const sink = { write: async () => {}, finish: async () => ({ name: "clip", kind: "temporary" as const, remove: async () => {} }), abort: async () => {} };
    const recording = { finiteRange: [0, 1] } as unknown as Recording;
    await exportMedia(recording, snapshot.options as VideoOptions, sink, new AbortController().signal, () => {}, null, snapshot);
    expect(calls.init).toHaveBeenCalledWith(expect.anything(), 896, 720);
    expect(calls.setSeries).toHaveBeenCalledWith(["shop/wip"]);
    expect(calls.add).toHaveBeenCalledTimes(4);
    expect(calls.fitBounds).toHaveBeenCalledTimes(1);
    expect(texts.filter((t) => t.startsWith("presentation 7f8e2a1c r2"))).toHaveLength(4);
    expect(texts.filter((t) => t.startsWith("simulatte · t = "))).toHaveLength(4);
  });
});
