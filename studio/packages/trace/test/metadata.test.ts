import { describe, expect, it } from "vitest";
import { openTrace, TraceCorrupted } from "../src/index";
import { buildTrace } from "./build";

const INITIAL = { state: { cell: { $kind: "cell", value: 0 } }, cursor: [0, -1], manifest: {} };
const DECLARATION = {
  name: "flow", unit: "time", kind: ["scalar", "series"], observation: "job", cohort: "completed_in_window",
  aggregation: "mean", clip: "none", censoring: "exclude", ema_reset: false, empty: null, description: "Flow time",
};
const chunks = [{ snapshot: INITIAL.state, events: [[0, 0, "tick", 1, {}, [["set", "cell", "value", 7]]]] }];

it("exposes getter-only, deeply frozen metadata without allowing replay changes", async () => {
  const trace = await openTrace(buildTrace(INITIAL, chunks));
  for (const key of ["header", "level", "index", "truncated", "outcome", "kpiDeclarations", "cursorRange"]) {
    expect(Reflect.set(trace, key, null)).toBe(false);
  }
  expect(Reflect.set(trace.header["manifest"] as object, "seed", "99")).toBe(false);
  expect(Reflect.set(trace.index, "0", null)).toBe(false);
  expect(Reflect.set(trace.index[0]!, "offset", 0)).toBe(false);
  expect(Reflect.set(trace.index[0]!.first, "0", 99)).toBe(false);
  expect(Reflect.set(trace.cursorRange![0], "0", 99)).toBe(false);
  await trace.prepare([1, 0]);
  expect(trace.stateAt([1, 0])["cell"]!["value"]).toBe(7);
});

describe("KPI declarations", () => {
  it("reads frozen declarations in complete and trailerless traces, with backward compatibility", async () => {
    const data = buildTrace(INITIAL, chunks, { records: [[7, { declarations: { "cell/flow": DECLARATION } }]] });
    for (const input of [data, data.slice(0, -16)]) {
      const trace = await openTrace(input);
      expect(trace.kpiDeclarations).toEqual({ "cell/flow": DECLARATION });
      expect(Reflect.set(trace.kpiDeclarations, "other/flow", DECLARATION)).toBe(false);
      expect(Reflect.set(trace.kpiDeclarations["cell/flow"]!, "unit", "jobs")).toBe(false);
      expect(Reflect.set(trace.kpiDeclarations["cell/flow"]!["kind"] as object, "0", "bad")).toBe(false);
    }
    expect((await openTrace(buildTrace(INITIAL))).kpiDeclarations).toEqual({});
  });

  it.each([
    ["name", ""], ["name", "a/b"], ["name", "a\0b"], ["name", "other"], ["unit", 1], ["description", null],
    ["observation", false], ["aggregation", []], ["kind", []], ["kind", ["scalar", "scalar"]],
    ["kind", ["bad"]], ["cohort", "bad"], ["clip", "bad"], ["censoring", "bad"], ["ema_reset", 0], ["empty", "0"],
  ])("rejects malformed field %s", async (key, value) => {
    const data = buildTrace(INITIAL, [], { records: [[7, { declarations: { "cell/flow": { ...DECLARATION, [key as string]: value } } }]] });
    await expect(openTrace(data)).rejects.toThrow(TraceCorrupted);
  });

  it("rejects missing fields and repeated declarations across records", async () => {
    const declaration = { ...DECLARATION } as Record<string, unknown>;
    delete declaration["unit"];
    await expect(openTrace(buildTrace(INITIAL, [], { records: [[7, { declarations: { "cell/flow": declaration } }]] }))).rejects.toThrow(TraceCorrupted);
    const record = [7, { declarations: { "cell/flow": DECLARATION } }] as const;
    await expect(openTrace(buildTrace(INITIAL, [], { records: [record, record] }))).rejects.toThrow(/duplicate KPI declaration/);
  });

  it("rejects an empty scope and unrecognized declaration fields", async () => {
    for (const declarations of [{ "/flow": DECLARATION }, { "cell/flow": { ...DECLARATION, extra: "unknown" } }]) {
      await expect(openTrace(buildTrace(INITIAL, [], { records: [[7, { declarations }]] }))).rejects.toThrow(TraceCorrupted);
    }
  });

  it.each([null, 0, NaN, Infinity, -Infinity])("allows numeric or null empty values %s", async (empty) => {
    const trace = await openTrace(buildTrace(INITIAL, [], { records: [[7, { declarations: { "cell/flow": { ...DECLARATION, empty } } }]] }));
    expect(trace.kpiDeclarations["cell/flow"]!["empty"]).toBe(empty);
  });
});
