/**
 * Record-level damage both readers report as TraceCorrupted (the Python side is in
 * `tests/core/test_trace_reader.py`): NaN times (ruling R35).
 */

import { describe, expect, it } from "vitest";
import { DeltaError, TraceCorrupted, applyDeltas, openTrace } from "../src/index";
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

describe("malformed operations are rejected like the Python reader rejects them", () => {
  const state = () => ({
    e: Object.assign(Object.create(null), { $kind: "k", text: "ab", list: [1, 2], map: { k: 1 } }),
  });

  it.each([
    ["remove_on_string", ["remove", "e", "text", "a"]],
    ["move_on_string", ["move", "e", "text", "a", 0]],
    ["insert_on_map", ["insert", "e", "map", 0, 1]],
    ["put_on_list", ["put", "e", "list", "k", 1]],
    ["delete_on_list", ["delete", "e", "list", "k"]],
    ["create_with_array_state", ["create", "new", "k", []]],
    ["create_with_number_kind", ["create", "new", 42, {}]],
    ["set_without_value", ["set", "e", "fresh"]],
    ["insert_without_value", ["insert", "e", "list", 0]],
    ["remove_with_extra_item", ["remove", "e", "list", 1, 2]],
    ["fractional_index", ["insert", "e", "list", 0.5, 9]],
    ["boolean_index", ["insert", "e", "list", true, 9]],
    ["number_entity", ["set", 5, "fresh", 1]],
    ["number_field", ["set", "e", 5, 1]],
    ["number_key", ["put", "e", "map", 5, 1]],
    ["number_name", [5, "e"]],
  ])("%s", (_, op) => {
    expect(() => applyDeltas(state(), [op as unknown[]])).toThrow(DeltaError);
  });

  it.each([
    ["remove_on_string", { state: { cell: { $kind: "cell", values: "ab" } }, cursor: [0, -1], manifest: {} }, tick(1, [["remove", "cell", "values", "a"]])],
    ["create_with_array_state", initial(), tick(1, [["create", "new", "cell", []]])],
    ["create_with_number_kind", initial(), tick(1, [["create", "new", 42, {}]])],
  ])("in a trace: %s", async (_, start, event) => {
    const snapshot = (start as { state: unknown }).state;
    const trace = await openTrace(buildTrace(start, [{ events: [event as unknown[]], snapshot }]));
    const end = trace.cursorRange![1];
    await trace.prepare(end);
    expect(() => trace.stateAt(end)).toThrow(TraceCorrupted);
  });

  it.each([
    ["kpi_not_a_map", [1, 2]],
    ["kpi_scalars_not_a_map", { scalars: [1] }],
    ["kpi_samples_not_an_array", { samples: { a: 1 } }],
    ["kpi_key_not_a_string", { samples: [[0, 1, 5, 1]] }],
    ["kpi_value_not_a_number", { samples: [[0, 1, "cell/kpi", "1"]] }],
  ])("KPI record: %s", async (_, record) => {
    await expect(readEverything(initial(), tick(1), { records: [[KPI, record]] })).rejects.toThrow(TraceCorrupted);
  });

  it("accepts integral float indices", () => {
    const s = state();
    applyDeltas(s, [["insert", "e", "list", 1, 9], ["move", "e", "list", 9, 0]]);
    expect(s.e["list"]).toEqual([9, 1, 2]);
  });
});
