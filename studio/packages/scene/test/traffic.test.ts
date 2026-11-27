import { readFileSync } from "node:fs";
import { expect, it } from "vitest";
import { Session, type PreparedFrame } from "@simulatte/playback";
import { openRecording, type State } from "@simulatte/trace";
import { Projector } from "../src/project";

const state: State = {
  "n1": { $kind: "node", x: 0, y: 0 },
  "n2": { $kind: "node", x: 4, y: 0 },
  "agv-1": { $kind: "agv", node: "n1" },
  "n1~n2": { $kind: "segment", reserved_by: [] },
  "corridor:n2": { $kind: "zone", reserved_by: ["agv-1"] },
};
const frame = (layout: PreparedFrame["layout"]): PreparedFrame => ({
  time: 0,
  cursor: [0, 0],
  mode: "continuous",
  state,
  layout,
  catalog: {},
  kpis: {},
  diagnostics: [],
  crossedEvents: [],
  crossingsComplete: true,
});
const placedLayout = {
  schema_version: "1.1",
  resolved: { placements: { "n1~n2": { position: [1, 1, 0] }, "corridor:n2": { position: [2, 2, 0] } } },
} as unknown as PreparedFrame["layout"];

it.each([
  ["without a layout", null],
  ["with a placement that names them", placedLayout],
])("projects no item for segment and zone entities %s", (_name, layout) => {
  const projection = new Projector(state).project(frame(layout));
  const ids = projection.glyphs.map((glyph) => glyph.id);
  expect(ids).not.toContain("n1~n2");
  expect(ids).not.toContain("corridor:n2");
  expect(ids).toEqual(expect.arrayContaining(["n1", "n2", "agv-1"]));
  expect(projection.glyphs.every((glyph) => !glyph.unplaced || glyph.kind !== "segment")).toBe(true);
});

it("leaves the fallback slots of the other entities as they were without traffic entities", () => {
  const { "n1~n2": _s, "corridor:n2": _z, ...plain } = state;
  const withTraffic = new Projector(state).project(frame(null));
  const without = new Projector(plain).project({ ...frame(null), state: plain });
  expect(withTraffic.glyphs).toEqual(without.glyphs);
});

it("uses a 1.1 layout bundle like a 1.0 one", () => {
  const layout = { schema_version: "1.1", resolved: { placements: { n1: { position: [3, 0, 0] } } }, network: null } as unknown as PreparedFrame["layout"];
  const projection = new Projector(state).project(frame(layout));
  expect(projection.fallback).toBe(false);
  expect(projection.glyphs.find((glyph) => glyph.id === "n1")).toMatchObject({ x: 3, y: 0 });
});

async function projectRecorded(directory: string, name: string) {
  const bytes = readFileSync(new URL(`../../../../tests/fixtures/traces/${directory}/${name}.simtrace`, import.meta.url));
  const recording = await openRecording(new Uint8Array(bytes).buffer);
  const session = new Session(recording);
  const frame = await session.prepare({ mode: "activation", time: recording.trace.activation![0], cursor: recording.trace.activation });
  session.dispose();
  return new Projector(frame.state).project(frame);
}

it("projects the recorded traffic example with its links and without the grid fallback", async () => {
  const projection = await projectRecorded("generated", "example_traffic");
  expect(projection.fallback).toBe(false);
  expect(projection.links.length).toBeGreaterThan(0);
});

it("projects a trace of an earlier version, no layout, on the grid fallback", async () => {
  const projection = await projectRecorded("frozen", "shop_small");
  expect(projection.fallback).toBe(true);
  expect(projection.links).toEqual([]);
});
