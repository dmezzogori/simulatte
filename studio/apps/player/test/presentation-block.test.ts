import { describe, expect, it } from "vitest";
import { playControl, readPresentationBlock, viewRange } from "../src/presentation-block";

const hash = "c".repeat(64);
const layout = { schema_version: "1.0" };
const manifest = {
  schema: 1,
  id: "7f8e2a1c-0c5d-4e3a-9b1e-2d6f8a0b1c2d",
  revision: 2,
  name: "Lateness",
  notes: "",
  recording: { attempt_id: "a".repeat(32), request_id: "b".repeat(64), trace_sha256: hash },
  layout,
  camera: { bounds: [0, 0, 16, 9] },
  visibility: { entities: null, labels: null },
  measurements: ["shop/wip"],
  time: { start: 2, end: 8 },
  rate: 2,
  annotations: [{ id: "x", text: "Peak", time: 3 }],
  evidence: [],
  created: 1,
};

describe("presentation block", () => {
  it("accepts version 1 (layout only) bound to the trace hash", () => {
    const block = readPresentationBlock({ version: 1, trace_sha256: hash, layout }, hash);
    expect(block).toEqual({ version: 1, layout, manifest: null, statics: [], evidence: [], snapshot_hash: null });
    expect(() => readPresentationBlock({ version: 1, trace_sha256: "d".repeat(64), layout }, hash)).toThrow("does not match");
    expect(() => readPresentationBlock({ version: 1, trace_sha256: hash, layout, extra: 1 }, hash)).toThrow("does not match");
  });
  it("accepts version 2 with exactly its keys, statics as data URLs and excerpts", () => {
    const data = { version: 2, trace_sha256: hash, snapshot_hash: "9".repeat(64), manifest, statics: [{ evidence_id: "e1", media_type: "image/png", sha256: "1".repeat(64), size: 3, width: 2, height: 2, data_base64: "AAAA" }], evidence: [{ id: "n", kind: "number", value: "3" }] };
    const block = readPresentationBlock(data, hash);
    expect(block.version).toBe(2);
    expect(block.layout).toBe(layout);
    expect(block.manifest?.rate).toBe(2);
    expect(block.statics[0]?.url).toBe("data:image/png;base64,AAAA");
    expect(block.snapshot_hash).toBe("9".repeat(64));
    expect(() => readPresentationBlock({ ...data, extra: true }, hash)).toThrow("does not match");
    expect(() => readPresentationBlock({ ...data, manifest: { ...manifest, recording: { ...manifest.recording, trace_sha256: "d".repeat(64) } } }, hash)).toThrow("does not match");
    expect(() => readPresentationBlock({ ...data, statics: [{ evidence_id: "e1", media_type: "text/html", sha256: "1".repeat(64), size: 1, width: 1, height: 1, data_base64: "AA==" }] }, hash)).toThrow("static view");
    expect(() => readPresentationBlock({ ...data, version: 3 }, hash)).toThrow("does not match");
  });
  it("clamps the saved time range to the recording and pauses at its end", () => {
    expect(viewRange(manifest, [0, 10])).toEqual({ start: 2, end: 8 });
    expect(viewRange(manifest, [3, 5])).toEqual({ start: 3, end: 5 });
    expect(viewRange(null, [3, 5])).toEqual({ start: 3, end: 5 });
    expect(viewRange({ ...manifest, time: { start: 9, end: 12 } }, [0, 10])).toEqual({ start: 9, end: 10 });
  });
});
describe("play control at the saved end", () => {
  const view = { start: 2, end: 8 };
  it("keeps Play enabled at the end of the saved view and replays from its start", () => {
    expect(playControl({ frame: true, finiteRange: true, time: 8, status: "paused" }, view)).toEqual({ disabled: false, restart: true });
    expect(playControl({ frame: true, finiteRange: true, time: 8, status: "ended" }, view)).toEqual({ disabled: false, restart: true });
    expect(playControl({ frame: true, finiteRange: true, time: 5, status: "paused" }, view)).toEqual({ disabled: false, restart: false });
  });
  it("disables Play without a frame, a finite range, or a view to replay once the trace ended", () => {
    expect(playControl({ frame: false, finiteRange: true, time: 5, status: "paused" }, view).disabled).toBe(true);
    expect(playControl({ frame: true, finiteRange: false, time: 5, status: "paused" }, view).disabled).toBe(true);
    expect(playControl({ frame: true, finiteRange: true, time: NaN, status: "paused" }, view).disabled).toBe(true);
    expect(playControl({ frame: true, finiteRange: true, time: 20, status: "ended" }, null)).toEqual({ disabled: true, restart: false });
  });
});
