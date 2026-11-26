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

import { readFileSync } from "node:fs";
const layoutExpected = JSON.parse(readFileSync(new URL("../../../../tests/fixtures/traces/generated/layout_full.expected.json", import.meta.url), "utf8")) as { layout: Record<string, unknown> };

it("exposes immutable layout metadata, including unknown schemas, without interpreting future data", async () => {
  const layout = layoutExpected.layout;
  const trace = await openTrace(buildTrace({ ...INITIAL, layout }));
  expect(trace.layout).toEqual(layout);
  expect(Reflect.set(trace, "layout", null)).toBe(false);
  expect(Reflect.set(trace.layout!, "physical_hash", "changed")).toBe(false);
  expect(Reflect.set(trace.layout!["targets"] as object, "station", null)).toBe(false);
  const opaque = await openTrace(buildTrace({ ...INITIAL, layout: { schema_version: "9.0", future: [1] } }));
  expect(opaque.layout).toEqual({ schema_version: "9.0", future: [1] });
  expect((await openTrace(buildTrace(INITIAL))).layout).toBeNull();
});

it.each([
  ["schema_version", 1], ["algorithms", {}], ["full_hash", "bad"], ["stages", []],
  ["aliases", {}], ["network", []], ["dependencies", {}], ["targets", { x: { kind: "node" } }],
  ["layers", { file: {}, code: [] }], ["auto", null],
])("rejects malformed known layout %s", async (key, value) => {
  await expect(openTrace(buildTrace({ ...INITIAL, layout: { ...layoutExpected.layout, [key as string]: value } }))).rejects.toThrow(TraceCorrupted);
});

it("rejects missing layout metadata and non-finite saved values", async () => {
  const layout = { ...layoutExpected.layout };
  delete layout["resolved"];
  await expect(openTrace(buildTrace({ ...INITIAL, layout }))).rejects.toThrow(TraceCorrupted);
  await expect(openTrace(buildTrace({ ...INITIAL, layout: { ...layoutExpected.layout, bindings: { invalid: Infinity } } }))).rejects.toThrow(TraceCorrupted);
});

it.each([
  [["resolved", "placements", "station", "position"], "bad"],
  [["resolved", "placements", "station", "footprint"], { width: "bad" }],
  [["resolved", "placements", "station", "ports"], { p: { position: [0, 0, 1] } }],
  [["resolved", "placements", "station", "visual"], { color: 1 }],
  [["resolved", "placements", "station", "obstacle"], 1],
  [["network", "clearance"], "bad"],
  [["network", "nodes", 0, "address"], { kind: "lattice" }],
  [["network", "nodes", 0, "address"], { kind: "explicit", id: 1 }],
  [["network", "nodes", 0, "address"], { kind: "lattice", i: 0, j: 0, extra: 1 }],
  [["network", "graph", "nodes", 0, "capacity"], -1],
  [["network", "graph", "arcs", 0, "speed_limit"], "bad"],
  [["auto", "targets", "station", "graph_position"], [0, 0, 0]],
  [["auto", "topology", "flows"], [42]],
])("rejects nested known-layout schema corruption %j", async (path, value) => {
  const layout = structuredClone(layoutExpected.layout);
  let parent: any = layout;
  for (const key of (path as (string | number)[]).slice(0, -1)) parent = parent[key];
  parent[(path as (string | number)[]).at(-1)!] = value;
  await expect(openTrace(buildTrace({ ...INITIAL, layout }))).rejects.toThrow(TraceCorrupted);
});

it.each([
  ["predicate", "graph"], ["predicate", "value"], ["flags", { station: 42 }], ["members", [42]],
  ["flags", { station: { state: "present", value: 1 } }],
])("rejects malformed known selection %s", async (field, value) => {
  const layout: any = structuredClone(layoutExpected.layout);
  const query = layout.dependencies.queries.find((q: any) => q.descriptor.predicate === "obstacle");
  if (field === "predicate") query.descriptor.predicate = value;
  else query.observed[field as string] = value;
  await expect(openTrace(buildTrace({ ...INITIAL, layout }))).rejects.toThrow(TraceCorrupted);
});

const trafficExpected = JSON.parse(readFileSync(new URL("../../../../tests/fixtures/traces/generated/layout_traffic.expected.json", import.meta.url), "utf8")) as { layout: Record<string, any> };

it("opens a schema 1.1 layout bundle and validates it like a 1.0 bundle", async () => {
  expect(trafficExpected.layout["schema_version"]).toBe("1.1");
  const trace = await openTrace(buildTrace({ ...INITIAL, layout: trafficExpected.layout }));
  expect(trace.layout).toEqual(trafficExpected.layout);
  const zones = (trafficExpected.layout["network"].graph.zones as { members: unknown[] }[]);
  expect(zones.length).toBe(1);
  expect(zones[0]!.members.some((member) => Array.isArray(member))).toBe(true);
});

it("opens a schema 1.1 layout whose arcs record a declared length", async () => {
  const layout = structuredClone(trafficExpected.layout);
  layout["network"].graph.arcs[0].length = 12.5;
  const trace = await openTrace(buildTrace({ ...INITIAL, layout }));
  expect(trace.layout?.["network"]).toEqual(layout["network"]);
  expect((trace.layout?.["network"] as any).graph.arcs[0].length).toBe(12.5);
});

it.each([
  ["full_hash", "bad"], ["algorithms", {}], ["stages", []],
])("rejects malformed 1.1 layout %s", async (key, value) => {
  await expect(openTrace(buildTrace({ ...INITIAL, layout: { ...trafficExpected.layout, [key as string]: value } }))).rejects.toThrow(TraceCorrupted);
});

it.each([
  [["network", "graph", "zones", 0, "capacity"], 0],
  [["network", "graph", "zones", 0, "hold_back"], "yes"],
  [["network", "graph", "zones", 0, "members"], [42]],
  [["network", "graph", "zones", 0, "members", 3], ["a"]],
  [["network", "graph", "zones", 0, "extra"], 1],
  [["network", "graph", "arcs", 0, "passing"], "yes"],
  [["network", "graph", "arcs", 0, "length"], 0],
  [["network", "graph", "arcs", 0, "length"], "long"],
  [["network", "graph", "arcs", 0, "extent"], 12.5],
  [["network", "edges", 0, "passing"], 1],
  [["network", "graph", "nodes", 0, "capacity"], -1],
])("rejects nested 1.1 layout corruption %j", async (path, value) => {
  const layout = structuredClone(trafficExpected.layout);
  let parent: any = layout;
  for (const key of (path as (string | number)[]).slice(0, -1)) parent = parent[key];
  parent[(path as (string | number)[]).at(-1)!] = value;
  await expect(openTrace(buildTrace({ ...INITIAL, layout }))).rejects.toThrow(TraceCorrupted);
});

// The corridor example: its vehicle type has length 1.2 and no width, so its bundle is layered-v2 (studio map spec §6.5).
const exampleExpected = JSON.parse(readFileSync(new URL("../../../../tests/fixtures/traces/generated/example_traffic.expected.json", import.meta.url), "utf8")) as { layout: Record<string, any> };

it("opens a layered-v2 bundle whose vehicles carry their size, and keeps layered-v1 for the others", async () => {
  const layout = exampleExpected.layout;
  expect(layout["algorithms"].auto).toBe("layered-v2");
  expect(layout["auto"].algorithm).toBe("layered-v2");
  expect(layout["targets"]["AGV-1"].size).toEqual([1.2, 0.6]);
  expect(layout["auto"].targets["AGV-1"].size).toEqual([1.2, 0.6]);
  expect(layout["resolved"].placements["AGV-1"].footprint).toEqual({ depth: 0.6, height: 0.0, width: 1.2 });
  expect(layout["targets"]["West"].size).toBeUndefined();
  expect((await openTrace(buildTrace({ ...INITIAL, layout }))).layout).toEqual(layout);
  expect(layoutExpected.layout["algorithms"]).toEqual({ auto: "layered-v1", network: "network-v1", hash: "layout-hash-v1" });
});

it("opens a layered-v2 bundle whose facility targets carry node_ids", async () => {
  const layout = structuredClone(layoutExpected.layout) as Record<string, any>;
  const facility = { kind: "warehouse", explicitly_placed: false };
  layout["targets"]["store"] = { ...facility, explicitly_named: true, node_ids: ["a", "b"] };
  layout["auto"].targets["store"] = { ...facility, node_ids: ["a", "b"] };
  layout["algorithms"].auto = layout["auto"].algorithm = "layered-v2";
  const trace = await openTrace(buildTrace({ ...INITIAL, layout }));
  expect((trace.layout?.["targets"] as any).store.node_ids).toEqual(["a", "b"]);
});

it.each([
  [["algorithms", "auto"], "layered-v3"],
  [["auto", "algorithm"], "layered-v3"],
  [["algorithms", "auto"], "layered-v1"],
  [["auto", "algorithm"], "layered-v1"],
  [["targets", "AGV-1", "size"], [1.2]],
  [["targets", "AGV-1", "size"], [1.2, 0]],
  [["targets", "AGV-1", "size"], [1.2, "wide"]],
  [["auto", "targets", "AGV-1", "size"], "big"],
  [["targets", "West", "size"], [1, 1]],
  [["targets", "AGV-1", "node_ids"], ["W"]],
  [["targets", "West", "node_ids"], []],
  [["auto", "targets", "West", "node_ids"], [7]],
  [["auto", "targets", "West", "node_ids"], "W"],
  [["targets", "West", "extent"], 1],
  [["auto", "targets", "AGV-1", "extent"], 1],
])("rejects other algorithm names and malformed target fields %j", async (path, value) => {
  const layout = structuredClone(exampleExpected.layout);
  let parent: any = layout;
  for (const key of (path as string[]).slice(0, -1)) parent = parent[key];
  parent[(path as string[]).at(-1)!] = value;
  await expect(openTrace(buildTrace({ ...INITIAL, layout }))).rejects.toThrow(TraceCorrupted);
});

it("rejects the layered-v2 name without its fields, and the fields under layered-v1", async () => {
  const unsized = structuredClone(layoutExpected.layout) as Record<string, any>;
  unsized["algorithms"].auto = unsized["auto"].algorithm = "layered-v2";
  await expect(openTrace(buildTrace({ ...INITIAL, layout: unsized }))).rejects.toThrow(TraceCorrupted);
  const renamed = structuredClone(exampleExpected.layout);
  renamed["algorithms"].auto = renamed["auto"].algorithm = "layered-v1";
  await expect(openTrace(buildTrace({ ...INITIAL, layout: renamed }))).rejects.toThrow(TraceCorrupted);
  const halved = structuredClone(exampleExpected.layout);
  for (const t of Object.values(halved["auto"].targets) as Record<string, unknown>[]) delete t["size"];
  await expect(openTrace(buildTrace({ ...INITIAL, layout: halved }))).rejects.toThrow(TraceCorrupted);
});
