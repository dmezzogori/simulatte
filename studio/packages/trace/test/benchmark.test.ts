import { expect, it } from "vitest";
import { benchmarkTrace } from "../benchmark/run";
import { buildTrace } from "./build";

it("benchmarks open, wire decoding, cold seeks and warm replay with bounded samples", async () => {
  const state = { cell: { $kind: "cell", value: 0 } };
  const data = buildTrace({ state, cursor: [0, -1], manifest: {} }, [
    { snapshot: state, events: [[0, 0, "tick", 1, {}, [["set", "cell", "value", 1]]]] },
  ]);
  const report = await benchmarkTrace(data, 2, 1);
  expect(report.bytes).toBe(data.byteLength);
  expect(report.chunks).toBe(1);
  expect(report.cursors).toEqual([[1, 0]]);
  expect(report.decode.record).toBe("CHUNK");
  for (const timing of [report.open, report.decode, report.coldSeek, report.warmSeek]) {
    expect(timing!.samples).toBe(2);
    expect(timing!.minMs).toBeGreaterThanOrEqual(0);
    expect(timing!.maxMs).toBeGreaterThanOrEqual(timing!.medianMs);
  }
});
