import { describe, expect, it } from "vitest";
import type { PreparedFrame } from "@simulatte/playback";
import { Projector } from "../src/project";
import { SYMBOL_PX, detailAt, type MapModel } from "../src/map/model";
import { buildFloor } from "../src/map/floor";
import { loadFrame } from "./fixtures";

const project = (name: string, frame: PreparedFrame = loadFrame(name)) =>
  new Projector(frame.initialState ?? frame.state).project(frame);
const mapOf = (name: string): MapModel => {
  const map = project(name).map;
  if (!map) throw new Error(`${name} has no map`);
  return map;
};
const ONE_WAY = ["J1~W_N", "W~W_N", "W~W_S", "J1~W_S", "E_S~J2", "E~E_S", "E~E_N", "E_N~J2"];
const TWO_WAY = ["J1~M1", "C~M1", "C~D", "D~M2", "J2~M2", "C~S", "D~S"];

describe("the floor of corridor-wait", () => {
  const map = mapOf("corridor-wait");
  it("has 13 nodes and 15 strips", () => {
    expect(map.nodes).toHaveLength(13);
    expect(map.strips).toHaveLength(15);
  });
  it("tells one-way strips from two-way strips", () => {
    expect(map.strips.filter((s) => s.oneWay).map((s) => s.id).sort()).toEqual([...ONE_WAY].sort());
    expect(map.strips.filter((s) => !s.oneWay).map((s) => s.id).sort()).toEqual([...TWO_WAY].sort());
    expect(map.strips.some((s) => s.passing)).toBe(false);
  });
  it("names a strip by its two nodes, smaller first, and positions its ends", () => {
    for (const strip of map.strips) {
      const [a, b] = strip.id.split("~") as [string, string];
      expect(a < b).toBe(true);
      const at = (id: string) => map.nodes.find((n) => n.id === id)!;
      const ends = [strip.a, strip.b];
      expect(ends).toContainEqual({ x: at(a).x, y: at(a).y });
      expect(ends).toContainEqual({ x: at(b).x, y: at(b).y });
    }
  });
  it("runs a one-way strip from the source of its arc to the target", () => {
    const strip = map.strips.find((s) => s.id === "W~W_N")!;
    // The arc is W_N -> W (graph order), so the strip starts at W_N.
    expect(strip.a).toEqual({ x: 0, y: 5 });
    expect(strip.b).toEqual({ x: -5, y: 0 });
  });
  it("sizes the lane from the vehicle footprint", () => {
    expect(map.laneWidth).toBeCloseTo(0.84, 9);
    expect(map.strips.every((s) => Math.abs(s.width - 0.84) < 1e-9)).toBe(true);
    expect(map.threshold).toBeCloseTo(22 / 1.2, 12);
  });
  it("switches level of detail at the threshold", () => {
    expect(detailAt(map, 18)).toBe("symbol");
    expect(detailAt(map, 18.4)).toBe("true");
    expect(detailAt(map, map.threshold)).toBe("true");
  });
});

it("lists the distinct fleets of the vehicles, sorted", () => {
  expect(mapOf("two-fleets").fleets).toEqual(["shuttles", "tuggers"]);
});

it("uses a 1 m vehicle where the footprint is the default", () => {
  const map = mapOf("bare-layout");
  expect(map.laneWidth).toBeCloseTo(1.4, 9);
  expect(map.threshold).toBe(22);
});

it("uses 1 m and 22 px/m with no vehicle in the state", () => {
  const frame = loadFrame("corridor-wait");
  const strip = (state: PreparedFrame["state"]) =>
    Object.fromEntries(Object.entries(state).filter(([, v]) => v["$kind"] !== "agv"));
  const bare = { ...frame, state: strip(frame.state), initialState: strip(frame.initialState!) };
  const map = project("corridor-wait", bare).map!;
  expect(map.laneWidth).toBe(1);
  expect(map.threshold).toBe(22);
});

it("draws a passing strip twice as wide", () => {
  const map = mapOf("sprites");
  const passing = map.strips.filter((s) => s.passing);
  expect(passing.length).toBeGreaterThan(0);
  for (const strip of passing) expect(strip.width).toBeCloseTo(2 * map.laneWidth, 9);
  for (const strip of map.strips.filter((s) => !s.passing)) expect(strip.width).toBeCloseTo(map.laneWidth, 9);
});

describe("held resources", () => {
  it("tints the strips and nodes a reservation holds on corridor-wait", () => {
    const map = mapOf("corridor-wait");
    expect(map.strips.filter((s) => s.held).map((s) => s.id).sort()).toEqual(
      ["C~M1", "J1~M1", "D~M2", "J2~M2", "W~W_N"].sort(),
    );
    expect(map.nodes.filter((n) => n.held).map((n) => n.id).sort()).toEqual(["C", "D", "M1", "M2", "W"]);
  });
  it("holds nothing without a reservation controller", () => {
    const map = mapOf("advanced-charging");
    expect(map.strips.some((s) => s.held)).toBe(false);
    expect(map.nodes.some((n) => n.held)).toBe(false);
    expect(map.zones).toEqual({});
  });
  it("lists the members of every zone, held or not", () => {
    expect(mapOf("corridor-wait").zones).toEqual({
      "corridor:M1": ["C~M1", "M1", "J1~M1"],
      "corridor:M2": ["D~M2", "M2", "J2~M2"],
    });
  });
  it("keeps only the string members of a zone", () => {
    const frame = loadFrame("corridor-wait");
    const zone = { ...frame.state["corridor:M1"]!, members: ["M1", 3, null, "C~M1"] };
    const odd = { ...frame, state: { ...frame.state, "corridor:M1": zone, "corridor:M2": { $kind: "zone" } } };
    expect(project("corridor-wait", odd).map!.zones).toEqual({ "corridor:M1": ["M1", "C~M1"], "corridor:M2": [] });
  });
});

describe("a frame without a map", () => {
  it.each(["production", "no-layout"])("%s has none", (name) => {
    expect(project(name).map ?? null).toBeNull();
  });
});

it("gives the vehicle, facility and strip symbol sizes", () => {
  expect(SYMBOL_PX).toEqual({ vehicle: 22, facility: 36, strip: 10 });
});

describe("the static part of the map model", () => {
  it("is reused for frames of one layout object without changing a model", () => {
    const first = loadFrame("corridor-wait");
    // another moment of the same layout object: the held floor, the vehicles and the facilities differ
    const second: PreparedFrame = { ...loadFrame("corridor-idle"), layout: first.layout };
    const shared = new Projector(first.initialState ?? first.state);
    const models = [first, second, first].map((frame) => shared.project(frame).map);
    expect(models[0]).toEqual(project("corridor-wait", first).map);
    expect(models[1]).toEqual(project("corridor-idle", second).map);
    expect(models[2]).toEqual(models[0]);
    expect(models[1]!.strips.filter((s) => s.held).map((s) => s.id)).not.toEqual(
      models[0]!.strips.filter((s) => s.held).map((s) => s.id),
    );
    // the facilities are read from each frame too: stock, occupancy and waiting orders differ
    const facility = (i: number, id: string) => models[i]!.facilities.find((f) => f.id === id)!;
    expect(facility(0, "East")).toMatchObject({ count: "49", stock: { crate: 49 }, badge: 1 });
    expect(facility(1, "East")).toMatchObject({ count: "50", stock: { crate: 50 }, badge: 0 });
    expect(facility(0, "Siding").count).toBe("0 / 3");
    expect(facility(1, "Siding").count).toBe("2 / 3");
  });
  it("is rebuilt for another layout object on a reused projector, equal or with a node moved", () => {
    const first = loadFrame("corridor-wait");
    const shared = new Projector(first.initialState ?? first.state);
    const before = shared.project(first).map!;
    const copy = structuredClone(first.layout)!;
    expect(shared.project({ ...first, layout: copy }).map).toEqual(before);
    const moved = structuredClone(first.layout)!;
    const placements = (moved["resolved"] as { placements: Record<string, { position: number[] }> }).placements;
    placements["C"]!.position = [35, 3, 0];
    const after = shared.project({ ...first, layout: moved }).map!;
    expect(after.nodes.find((n) => n.id === "C")).toMatchObject({ x: 35, y: 3 });
    const cd = after.strips.find((s) => s.id === "C~D")!;
    expect([cd.a, cd.b]).toContainEqual({ x: 35, y: 3 });
    // and back to the first layout object
    expect(shared.project(first).map).toEqual(before);
  });
});

describe("strip ids", () => {
  it("order the two node ids by code unit, not by locale", () => {
    const positions = new Map([
      ["a", { x: 0, y: 0 }],
      ["B", { x: 1, y: 0 }],
      ["a_b", { x: 0, y: 1 }],
      ["aB", { x: 1, y: 1 }],
    ]);
    const arcs = [
      { source: "a", target: "B" },
      { source: "a_b", target: "aB" },
    ];
    const nodes = [...positions.keys()].map((id) => ({ id }));
    const layout = { network: { graph: { arcs, nodes } } };
    const floor = buildFloor({ time: 0, layout, state: {}, positions, vehicles: [] })!;
    // localeCompare would give "a~B" and "a_b~aB"
    expect(floor.strips.map((s) => s.id)).toEqual(["B~a", "aB~a_b"]);
  });
});
