/**
 * Replay value equality (ruling R31, spec §6.2, §9.1): `remove` and `move` find the first item whose canonical
 * encoding equals the value's, as in the Python reader (`tests/core/test_events.py`, `test_trace_reader.py`):
 * `true` is not `1`, `1` is not `1.0`, `-0.0` is not `0.0`, NaN equals NaN.
 */

import { describe, expect, it } from "vitest";
import { TraceCorrupted, openTrace } from "../src/index";
import { decodeWire, isFloatAt, wireEquals } from "../src/wire";
import { F64, buildTrace, f64, pack } from "./build";

const LIMITS = { maxDepth: 64, maxLen: 1000 };

async function replay(values: unknown[], op: unknown[]): Promise<unknown[]> {
  const state = { cell: { $kind: "cell", values } };
  const initial = { state, cursor: [0, -1], manifest: {} };
  const trace = await openTrace(buildTrace(initial, [{ events: [[0, 0, "tick", 0, {}, [op]]], snapshot: state }]));
  const end = trace.cursorRange![1];
  await trace.prepare(end);
  return trace.stateAt(end)["cell"]!["values"] as unknown[];
}

/** Each number with its encoding (`i:` or `f:`), so int 1 and float 1.0 (equal JavaScript numbers) can be told apart. */
function encoded(values: readonly unknown[]): string {
  return render(values);
}

function render(value: unknown, float = false): string {
  if (value instanceof F64) return render(value.value, true);
  if (typeof value === "number") {
    const isFloat = float || !Number.isSafeInteger(value) || Object.is(value, -0);
    return `${isFloat ? "f" : "i"}:${Object.is(value, -0) ? "-0" : String(value)}`;
  }
  if (Array.isArray(value)) return `[${value.map((item, i) => render(item, isFloatAt(value, i))).join(",")}]`;
  if (value !== null && typeof value === "object") {
    const map = value as Record<string, unknown>;
    return `{${Object.keys(map).sort().map((key) => `${key}:${render(map[key], isFloatAt(map, key))}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

describe("remove and move compare canonical encodings", () => {
  it.each([
    ["bool before int", [true, 1], ["remove", "cell", "values", 1], [true]],
    ["int before bool", [1, true], ["remove", "cell", "values", true], [1]],
    ["int before float", [1, f64(1)], ["remove", "cell", "values", f64(1)], [1]],
    ["float before int", [f64(1), 1], ["remove", "cell", "values", 1], [f64(1)]],
    ["zero before negative zero", [f64(0), -0], ["remove", "cell", "values", -0], [f64(0)]],
    ["negative zero before zero", [-0, f64(0)], ["remove", "cell", "values", f64(0)], [-0]],
    ["NaN", ["a", NaN], ["remove", "cell", "values", NaN], ["a"]],
    ["NaN in an array", [[1, NaN], 2], ["remove", "cell", "values", [1, NaN]], [2]],
    ["NaN in a map", [{ x: NaN }, 1], ["remove", "cell", "values", { x: NaN }], [1]],
    ["move of NaN", [1, NaN], ["move", "cell", "values", NaN, 0], [NaN, 1]],
    ["move of a float", [1, f64(1), 2], ["move", "cell", "values", f64(1), 2], [1, 2, f64(1)]],
  ])("%s", async (_, values, op, expected) => {
    const result = await replay(values as unknown[], op as unknown[]);
    expect(encoded(result)).toBe(encoded(expected as unknown[]));
  });

  it.each([
    ["float, int removed", [f64(1)], 1],
    ["int, float removed", [1], f64(1)],
    ["bool, int removed", [true], 1],
    ["int, bool removed", [0], false],
    ["zero, negative zero removed", [f64(0)], -0],
    ["NaN absent", [1, 2], NaN],
    ["array of a float", [[1]], [f64(1)]],
  ])("a value with another encoding is not found: %s", async (_, values, value) => {
    await expect(replay(values as unknown[], ["remove", "cell", "values", value])).rejects.toThrow(TraceCorrupted);
  });

  it("keeps the encoding of the remaining items across operations", async () => {
    // [1.0, 1]: remove 1 keeps the float; removing the int 1 again must then fail, as in Python.
    const state = { cell: { $kind: "cell", values: [f64(1), 1] } };
    const initial = { state, cursor: [0, -1], manifest: {} };
    const events = [
      [0, 0, "tick", 0, {}, [["insert", "cell", "values", 0, 7]]],
      [1, 1, "tick", 0, {}, [["remove", "cell", "values", 1]]],
      [2, 2, "tick", 0, {}, [["remove", "cell", "values", 1]]],
    ];
    const trace = await openTrace(buildTrace(initial, [{ events, snapshot: state }]));
    await trace.prepare([0, 1]);
    expect(encoded(trace.stateAt([0, 1])["cell"]!["values"] as unknown[])).toBe(encoded([7, f64(1)]));
    expect(() => trace.stateAt([0, 2])).toThrow(TraceCorrupted);
  });
});

describe("wireEquals", () => {
  const decoded = (value: unknown) => decodeWire(pack(value), LIMITS) as readonly unknown[];

  it("follows the canonical encoding of decoded values", () => {
    const items = decoded([1, f64(1), true, NaN, -0, f64(0)]);
    expect(wireEquals(items, decoded([1, f64(1), true, NaN, -0, f64(0)]))).toBe(true);
    expect(items.map((_, i) => isFloatAt(items, i))).toEqual([false, true, false, false, false, true]);
    expect(wireEquals(decoded([1]), decoded([f64(1)]))).toBe(false);
    expect(wireEquals(decoded([true]), decoded([1]))).toBe(false);
    expect(wireEquals(decoded([-0]), decoded([f64(0)]))).toBe(false);
    expect(wireEquals(decoded([NaN, { k: NaN }]), decoded([NaN, { k: NaN }]))).toBe(true);
    expect(wireEquals(decoded({ k: [1] }), decoded({ k: [f64(1)] }))).toBe(false);
  });

  it("treats undecoded integral numbers as integers", () => {
    expect(wireEquals(1, 1)).toBe(true);
    expect(wireEquals(1, 1, false, true)).toBe(false);
    expect(wireEquals(0, -0)).toBe(false);
    expect(wireEquals(NaN, NaN)).toBe(true);
    expect(wireEquals(1.5, 1.5)).toBe(true);
  });
});
