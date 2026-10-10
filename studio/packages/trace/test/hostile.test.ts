/**
 * Untrusted traces that both readers must reject with TraceCorrupted (Codex probe_hostile; the Python side is in
 * `tests/core/test_trace_reader.py::test_hostile_records_are_corruption`).
 */

import { describe, expect, it } from "vitest";
import { TraceCorrupted, openTrace } from "../src/index";
import { WireError, decodeWire } from "../src/wire";
import { Raw, RawMap, buildTrace, f64, pack } from "./build";

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

describe("strings", () => {
  const invalid = new Raw(new Uint8Array([0xa1, 0xff])); // a one-byte string that is not UTF-8

  it.each([
    ["value", new RawMap([["value", invalid]])],
    ["key", new RawMap([[invalid, 1]])],
    ["array item", ["ok", invalid]],
    ["overlong encoding", [new Raw(new Uint8Array([0xa2, 0xc0, 0x80]))]],
    ["surrogate", [new Raw(new Uint8Array([0xa3, 0xed, 0xa0, 0x80]))]],
  ])("rejects invalid UTF-8 in a %s, like the Python reader", (_, value) => {
    expect(() => decodeWire(pack(value), LIMITS)).toThrow(WireError);
  });

  // Keep the leading U+FEFF at every string size, matching Python's UTF-8 decoding.
  it.each([197, 198, 200, 1000])("keeps a leading U+FEFF in a value of %i + 3 bytes", (n) => {
    const text = "\ufeff" + "x".repeat(n);
    const decoded = decodeWire(pack([text, "x".repeat(n)]), LIMITS) as string[];
    expect(decoded[0]!.length).toBe(n + 1);
    expect(decoded[0]!.codePointAt(0)).toBe(0xfeff);
  });

  it.each([197, 198, 200, 1000])("keeps a leading U+FEFF in a key of %i + 3 bytes", (n) => {
    const decoded = decodeWire(pack({ ["\ufeff" + "k".repeat(n)]: 1, ["k".repeat(n)]: 2 }), LIMITS) as object;
    expect(Object.keys(decoded).length).toBe(2);
  });

  it("replays a remove next to a value that starts with U+FEFF", async () => {
    const bom = "\ufeff" + "x".repeat(300);
    const state = { cell: { $kind: "cell", values: [bom, "x".repeat(300)] } };
    const event = [0, 0, "tick", 1, {}, [["remove", "cell", "values", "x".repeat(300)]]];
    const trace = await openTrace(buildTrace({ state, cursor: [0, -1], manifest: {} }, [{ events: [event], snapshot: state }]));
    await trace.prepare([1, 0]);
    expect(trace.stateAt([1, 0])["cell"]!["values"]).toEqual([bom]);
  });

  it("decodes valid UTF-8", () => {
    expect(decodeWire(pack({ "clé": ["naïve", "𝄞", ""] }), LIMITS)).toEqual({ "clé": ["naïve", "𝄞", ""] });
  });
});

describe("hostile records", () => {
  const initial = (state: unknown, cursor: unknown = [0, -1]) => new RawMap([["state", state], ["cursor", cursor], ["manifest", {}]]);

  it.each([
    ["duplicate key", initial({ cell: new RawMap([["$kind", "cell"], ["value", 1], ["value", 999]]) })],
    ["integer key", initial({ cell: new RawMap([["$kind", "cell"], [1, 999]]) })],
    ["string that is not UTF-8", initial({ cell: new RawMap([["$kind", "cell"], ["value", new Raw(new Uint8Array([0xa1, 0xff]))]]) })],
    ["key that is not UTF-8", initial({ cell: new RawMap([["$kind", "cell"], [new Raw(new Uint8Array([0xa1, 0xff])), 1]]) })],
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
