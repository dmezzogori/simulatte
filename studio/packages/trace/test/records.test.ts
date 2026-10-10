/**
 * Record-level damage both readers report as TraceCorrupted (the Python side is in
 * `tests/core/test_trace_reader.py`): NaN times (ruling R35).
 */

import { describe, expect, it } from "vitest";
import { TraceCorrupted, openTrace } from "../src/index";
import { buildTrace, type TraceOptions } from "./build";

const STATE = { cell: { $kind: "cell", value: 0, values: ["a", "b"], map: { x: 1.5 } } };
const KPI = 7;

async function readEverything(initial: unknown, event: unknown[], options: TraceOptions = {}): Promise<void> {
  const trace = await openTrace(buildTrace(initial, [{ events: [event], snapshot: STATE }], options));
  const end = trace.cursorRange![1];
  await trace.prepare(end);
  trace.stateAt(end);
}

const initial = (cursor: unknown = [0, -1]) => ({ state: STATE, cursor, manifest: {} });
const tick = (t: number, deltas: unknown[] = []) => [0, 0, "test.record", t, {}, deltas];

describe("NaN times are corruption", () => {
  it.each([
    ["initial", () => readEverything(initial([NaN, -1]), tick(1))],
    ["footer", () => readEverything(initial(), tick(1), { footer: { cursor: [NaN, 0] } })],
    ["event", () => readEverything(initial(), tick(NaN))],
    ["index_first", () => readEverything(initial(), tick(1), { entry: { first: [NaN, 0] } })],
    ["index_t_start", () => readEverything(initial(), tick(1), { entry: { t_start: NaN } })],
    ["kpi_sample", () => readEverything(initial(), tick(1), { records: [[KPI, { samples: [[0, NaN, "cell/kpi", 1]] }]] })],
  ])("%s", async (_, read) => {
    await expect(read()).rejects.toThrow(TraceCorrupted);
  });

  it("accepts the same trace without NaN times, and a NaN KPI value", async () => {
    const kpi = { samples: [[0, 1, "cell/kpi", NaN]], scalars: { "cell/x": 1 } };
    await expect(readEverything(initial(), tick(1, [["set", "cell", "value", 7]]), { records: [[KPI, kpi]] })).resolves.toBeUndefined();
  });
});
