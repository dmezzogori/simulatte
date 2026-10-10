/**
 * Untrusted traces that both readers must reject with TraceCorrupted (Codex probe_hostile; the Python side is in
 * `tests/core/test_trace_reader.py::test_hostile_records_are_corruption`).
 */

import { describe, expect, it } from "vitest";
import { TraceCorrupted, openTrace } from "../src/index";
import { WireError, decodeWire } from "../src/wire";
import { RawMap, buildTrace, f64, pack } from "./build";

const LIMITS = { maxDepth: 64, maxLen: 1000 };

describe("map keys", () => {
  it("rejects a map that repeats a raw key", () => {
    expect(() => decodeWire(pack(new RawMap([["value", 1], ["value", 999]])), LIMITS)).toThrow(WireError);
    expect(() => decodeWire(pack([new RawMap([["a", 1], ["b", 2], ["a", 3]])]), LIMITS)).toThrow(/duplicate map key/);
  });

  it("rejects keys that are not strings", () => {
    for (const key of [1, -1, f64(1.5), true, null]) {
      expect(() => decodeWire(pack(new RawMap([[key, 999]])), LIMITS), String(key)).toThrow(/map keys must be str/);
    }
  });

  it("keeps the order and the values of distinct keys", () => {
    const decoded = decodeWire(pack(new RawMap([["b", 1], ["0", 2], ["a", 3], ["~~x", 4]])), LIMITS);
    expect(Object.keys(decoded as object)).toEqual(["0", "b", "a", "~x"]);
    expect(decoded).toEqual({ b: 1, "0": 2, a: 3, "~x": 4 });
  });
});

describe("hostile records", () => {
  const initial = (state: unknown, cursor: unknown = [0, -1]) => new RawMap([["state", state], ["cursor", cursor], ["manifest", {}]]);

  it.each([
    ["duplicate key", initial({ cell: new RawMap([["$kind", "cell"], ["value", 1], ["value", 999]]) })],
    ["integer key", initial({ cell: new RawMap([["$kind", "cell"], [1, 999]]) })],
    ["state that is a list", initial([])],
    ["entity that is not a map", initial({ cell: 5 })],
    ["entity without a kind", initial({ cell: { value: 1 } })],
    ["entity with a kind that is not a string", initial({ cell: { $kind: 1 } })],
    ["infinite seq", initial({}, [0, Infinity])],
    ["fractional seq", initial({}, [0, 0.5])],
    ["time that is a string", initial({}, ["0", -1])],
    ["cursor that is not a pair", initial({}, [-1])],
  ])("rejects an INITIAL record with a %s", async (_, record) => {
    await expect(openTrace(buildTrace(record))).rejects.toThrow(TraceCorrupted);
  });

  it("accepts the same INITIAL record when well formed", async () => {
    const trace = await openTrace(buildTrace(initial({ cell: { $kind: "cell", value: 1 } })));
    expect(trace.stateAt([0, -1])).toEqual({ cell: { $kind: "cell", value: 1 } });
    // An integral float seq is an integer for both readers (JavaScript cannot tell -1.0 from -1).
    const floatSeq = await openTrace(buildTrace(initial({}, [f64(0), f64(-1)])));
    expect(floatSeq.cursorRange).toEqual([[0, -1], [0, -1]]);
  });
});
