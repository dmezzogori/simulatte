import { describe, expect, it } from "vitest";
import type { PreparedFrame } from "@simulatte/playback";
import { Projector } from "../src/project";
import { motionSpeed } from "../src/motion";
import { overlayFor } from "../src/map/overlay";
import type { MapModel, Shown, VehicleItem } from "../src/map/model";
import { loadFrame } from "./fixtures";

const projectFrame = (frame: PreparedFrame) => new Projector(frame.initialState ?? frame.state).project(frame);
const mapOf = (name: string, frame: PreparedFrame = loadFrame(name)): MapModel => {
  const map = projectFrame(frame).map;
  if (!map) throw new Error(`${name} has no map`);
  return map;
};
const vehicle = (map: MapModel, id: string): VehicleItem => {
  const found = map.vehicles.find((v) => v.id === id);
  if (!found) throw new Error(`no vehicle ${id}`);
  return found;
};
const facility = (map: MapModel, id: string) => map.facilities.find((f) => f.id === id)!;
/** The frame without one entity, in the state and in the initial state. */
const without = (frame: PreparedFrame, id: string): PreparedFrame => {
  const drop = (state: PreparedFrame["state"]) => Object.fromEntries(Object.entries(state).filter(([key]) => key !== id));
  return { ...frame, state: drop(frame.state), initialState: frame.initialState ? drop(frame.initialState) : null };
};
const edit = (frame: PreparedFrame, id: string, fields: Record<string, unknown>): PreparedFrame => ({
  ...frame,
  state: { ...frame.state, [id]: { ...frame.state[id]!, ...fields } },
});

describe("corridor-wait", () => {
  const map = mapOf("corridor-wait");
  it("shows AGV-1 waiting for AGV-2", () => {
    const agv = vehicle(map, "AGV-1");
    expect(agv.shown).toBe("waiting");
    expect(agv.loaded).toBe(true);
    expect(agv.load).toEqual({ crate: 1 });
    expect(agv.doing).toBe("waiting 6 s for AGV-2");
    expect(agv.line).toBe("AGV-1 · waiting 6 s for AGV-2");
    expect(agv.short).toBe("1");
    expect(agv.goal).toBe("East");
    expect(agv.holds).toEqual(expect.arrayContaining(["C", "C~M1", "corridor:M1"]));
    expect(agv).toMatchObject({ x: 35, y: 0, heading: 0, order: "order-0", fleet: "tuggers", fleetIndex: null });
    expect(agv.wait).toMatchObject({ node: "D", blockers: ["AGV-2"], resources: ["corridor:M2", "C~D", "D"] });
    expect(agv.route).toMatchObject({ destination: "E", reason: "travel" });
    expect(agv.battery).toBeCloseTo(900.05, 2);
    expect(agv.batteryCapacity).toBe(1000);
    expect(agv.speed).toBeNull();
  });
  it("shows AGV-2 moving along its arc", () => {
    const agv = vehicle(map, "AGV-2");
    expect(agv.shown).toBe("moving");
    expect(Math.abs(agv.x - 57.53)).toBeLessThan(0.01);
    expect(agv.y).toBe(0);
    expect(agv.speed).toBeCloseTo(1.5, 9);
    expect(agv.heading).toBeCloseTo(Math.PI, 9);
    expect(agv.doing).toBe("moving");
    expect(agv.line).toBe("AGV-2 · to West");
  });
  it("shows AGV-3 working at a bay", () => {
    const agv = vehicle(map, "AGV-3");
    expect(agv.shown).toBe("working");
    expect(agv.doing).toBe("loading");
    expect(agv.route).toBeNull();
    expect(agv.heading).toBeCloseTo(-2.356194490192345, 12);
    expect(agv.goal).toBeNull();
    expect(agv.line).toBe("AGV-3 · loading");
  });
  it("sizes vehicles from the footprint of their placement", () => {
    expect(vehicle(map, "AGV-1")).toMatchObject({ length: 1.2, width: 0.6 });
  });
  it("draws one wait line, from AGV-1 to AGV-2", () => {
    const agv2 = vehicle(map, "AGV-2");
    expect(map.waitLines).toEqual([{ agv: "AGV-1", blocker: "AGV-2", from: { x: 35, y: 0 }, to: { x: agv2.x, y: agv2.y } }]);
  });
  it("summarizes the traffic", () => {
    expect(map.traffic).toMatchObject({ waiting: 1, waits: 2, stuckWaits: 0, deadlocks: 0 });
    expect(map.traffic.longestWait).toBeCloseTo(6.457, 3);
  });
  it("counts stock and parking at the facilities", () => {
    expect(facility(map, "East")).toMatchObject({ count: "49", badge: 1, kind: "warehouse", nodes: ["E"] });
    expect(facility(map, "West")).toMatchObject({ count: "48", badge: 1 });
    expect(facility(map, "Siding")).toMatchObject({ count: "0 / 3", badge: 0, kind: "parking_area", nodes: ["S"] });
    expect(facility(map, "East")).toMatchObject({ x: 96.5, y: 0, width: 1, depth: 1, rotation: 0 });
  });
  it("lists the open orders", () => {
    expect(map.orders).toHaveLength(5);
    expect(map.orders.find((o) => o.id === "order-0")).toEqual({
      id: "order-0",
      sku: "crate",
      quantity: 1,
      origin: "West",
      destination: "East",
      status: "IN_TRANSIT",
      agv: "AGV-1",
      picked: true,
      createdAt: 0,
    });
    expect(map.orders.find((o) => o.id === "order-3")).toMatchObject({ agv: null, picked: false, status: "PENDING" });
  });
  it("gives a lone vehicle no fan-out", () => {
    for (const agv of map.vehicles) expect(agv.offset).toEqual({ x: 0, y: 0 });
  });
});

describe("sprites", () => {
  const map = mapOf("sprites");
  it.each<[string, Shown, string]>([
    ["A1", "stranded", "stranded"],
    ["A2", "waiting", "waiting 5 s for A3"],
    ["A3", "moving", "moving"],
    ["A4", "working", "loading"],
    ["H3", "working", "unloading"],
    ["A5", "idle", "idle"],
    ["A6", "charging", "charging"],
    ["A7", "resting", "stopped"],
    ["A8", "unknown", "rebooting"],
  ])("shows %s as %s, doing %s", (id, shown, doing) => {
    expect(vehicle(map, id)).toMatchObject({ shown, doing });
  });
  it("takes the heading from the arc, else the record, else 0", () => {
    expect(vehicle(map, "A3").heading).toBe(0);
    expect(vehicle(map, "A5").heading).toBe(0);
    expect(vehicle(map, "A6").heading).toBeCloseTo(Math.PI / 2, 12);
    expect(vehicle(map, "H3").heading).toBeCloseTo(Math.PI, 12);
  });
  it("does not count a stranded vehicle as waiting", () => {
    expect(map.traffic.waiting).toBe(1);
    expect(map.waitLines.map((l) => l.agv).sort()).toEqual(["A1", "A2"]);
  });
  it("keeps the recorded state of an unknown one", () => {
    expect(vehicle(map, "A8").state).toBe("REBOOTING");
  });
  it("spreads the vehicles on one node", () => {
    const offsets = ["P1", "P2", "P3"].map((id) => vehicle(map, id).offset);
    expect(offsets.map((o) => o.x)).toEqual([-24, 0, 24]);
    expect(new Set(offsets.map((o) => o.x)).size).toBe(3);
  });
  it("spreads the charging stations on one node", () => {
    expect(facility(map, "Charger-A").offset).toEqual({ x: -19, y: 0 });
    expect(facility(map, "Charger-B").offset).toEqual({ x: 19, y: 0 });
    expect(facility(map, "Charger-A")).toMatchObject({ count: "1 / 2", nodes: ["n8"] });
    expect(facility(map, "Charger-B").count).toBe("0 / 1");
  });
  it("counts the SKUs of a warehouse and its waiting orders", () => {
    expect(facility(map, "Store")).toMatchObject({ count: "3 SKUs", badge: 2, nodes: ["n9", "n10"] });
    expect(facility(map, "Dock")).toMatchObject({ count: "3", badge: 0 });
    expect(facility(map, "Lot")).toMatchObject({ count: "3 / 4" });
  });
  it("leaves the conveyor to the glyphs", () => {
    const projection = projectFrame(loadFrame("sprites"));
    expect(projection.glyphs.some((g) => g.id === "Belt")).toBe(true);
    const m = projection.map!;
    const listed = [...m.vehicles, ...m.facilities, ...m.orders].map((item) => item.id);
    expect(listed).not.toContain("Belt");
    expect(JSON.stringify(m)).not.toContain("Belt");
  });
  it("keeps hostile labels unchanged", () => {
    const state = loadFrame("sprites").state;
    for (const id of ["H1", "H2", "H3"]) expect(vehicle(map, id).label).toBe(state[id]!["label"]);
    expect(vehicle(map, "H2").label.length).toBeGreaterThan(400);
    expect(vehicle(map, "H1").short).toBe("1");
  });
  it("gives each vehicle a fleet index only with two fleets", () => {
    expect(vehicle(map, "A1").fleetIndex).toBeNull();
    const two = mapOf("two-fleets");
    expect(vehicle(two, "AGV-1").fleetIndex).toBe(1);
    expect(vehicle(two, "AGV-3").fleetIndex).toBe(0);
  });
});

describe("bare-layout", () => {
  const map = mapOf("bare-layout");
  it("shows the state and motion of every vehicle", () => {
    expect(map.vehicles.map((v) => [v.id, v.shown, v.wait, v.route])).toEqual([
      ["AGV-1", "resting", null, null],
      ["AGV-2", "moving", null, null],
      ["AGV-3", "working", null, null],
    ]);
    expect(vehicle(map, "AGV-1")).toMatchObject({ goal: null, line: "AGV-1 · stopped", length: 1, width: 1 });
  });
  it("leaves the capacity and the totals unknown", () => {
    expect(facility(map, "Siding").count).toBe("");
    expect(map.traffic).toEqual({ waiting: 0, longestWait: 0, waits: null, stuckWaits: null, deadlocks: null });
    expect(vehicle(map, "AGV-1").batteryCapacity).toBeNull();
  });
});

describe("items that are not in the frame", () => {
  it("draws no wait line for a blocker that is gone, and still names it", () => {
    const map = mapOf("corridor-wait", without(loadFrame("corridor-wait"), "AGV-2"));
    expect(map.waitLines).toEqual([]);
    expect(vehicle(map, "AGV-1").doing).toBe("waiting 6 s for AGV-2");
    expect(map.traffic.waiting).toBe(1);
  });
  it("skips a route node the layout does not know", () => {
    const frame = loadFrame("corridor-wait");
    const route = { ...(frame.state["AGV-1"]!["route"] as object), nodes: ["W", "nowhere", "J1", "M1", "C", "D", "E"] };
    const map = mapOf("corridor-wait", edit(frame, "AGV-1", { route }));
    const overlay = overlayFor(map, "AGV-1")!;
    const nodeAt = (id: string) => map.nodes.find((n) => n.id === id)!;
    expect(overlay.route[0]).toEqual({ x: 35, y: 0 });
    expect(overlay.route.at(-1)).toEqual({ x: nodeAt("E").x, y: nodeAt("E").y });
    expect(overlay.route.every((p) => Number.isFinite(p.x) && Number.isFinite(p.y))).toBe(true);
  });
  it("shows the node id as the goal when no facility is bound to it", () => {
    const frame = loadFrame("corridor-wait");
    const route = { ...(frame.state["AGV-1"]!["route"] as object), destination: "M1" };
    expect(vehicle(mapOf("corridor-wait", edit(frame, "AGV-1", { route })), "AGV-1").goal).toBe("M1");
  });
});

describe("overlayFor on corridor-wait", () => {
  const map = mapOf("corridor-wait");
  const at = (id: string) => map.facilities.find((f) => f.id === id)!;
  it("describes the selected vehicle", () => {
    const overlay = overlayFor(map, "AGV-1")!;
    expect(overlay.route[0]).toEqual({ x: 35, y: 0 });
    const e = map.nodes.find((n) => n.id === "E")!;
    expect(overlay.route.at(-1)).toEqual({ x: e.x, y: e.y });
    expect(overlay.route.length).toBeGreaterThan(2);
    expect(overlay.holds).toEqual(vehicle(map, "AGV-1").holds);
    expect(overlay.holds).toEqual(expect.arrayContaining(["C", "C~M1", "corridor:M1"]));
    expect(overlay.waitLine).toEqual(map.waitLines[0]);
    expect(overlay.flow).toEqual([{ x: at("West").x, y: at("West").y }, { x: at("East").x, y: at("East").y }]);
  });
  it("starts the route of a moving vehicle at the vehicle, on the arc under way", () => {
    const agv2 = vehicle(map, "AGV-2");
    const overlay = overlayFor(map, "AGV-2")!;
    const d = map.nodes.find((n) => n.id === "D")!;
    expect(overlay.route[0]).toEqual({ x: agv2.x, y: agv2.y });
    expect(overlay.route[1]).toEqual({ x: d.x, y: d.y });
    expect(overlay.waitLine).toBeNull();
  });
  it("describes the selected order", () => {
    const overlay = overlayFor(map, "order-0")!;
    expect(overlay.flow).toEqual(overlayFor(map, "AGV-1")!.flow);
    expect(overlay.related).toEqual(["AGV-1"]);
    expect(overlay.holds).toEqual([]);
    expect(overlay.route).toEqual([]);
    expect(overlayFor(map, "order-3")!.related).toEqual([]);
  });
  it("relates a facility to the vehicles bound for it", () => {
    expect(overlayFor(map, "East")!.related).toEqual(["AGV-1"]);
    expect(overlayFor(map, "West")!.related).toEqual(["AGV-2"]);
    expect(overlayFor(map, "Siding")!.related).toEqual([]);
  });
  it("selects nothing for null, an unknown id or a server", () => {
    expect(overlayFor(map, null)).toBeNull();
    expect(overlayFor(map, "nobody")).toBeNull();
    expect(overlayFor(mapOf("sprites"), "Belt")).toBeNull();
    expect(overlayFor(mapOf("corridor-wait"), "C")).toBeNull();
  });
});

describe("a crowd", () => {
  it("gives 100 vehicles on one node 100 distinct offsets, in natural order", () => {
    const frame = loadFrame("corridor-idle");
    const state = { ...frame.state };
    const initial = { ...(frame.initialState ?? frame.state) };
    for (const id of Object.keys(state)) if (state[id]!["$kind"] === "agv") delete state[id];
    for (const id of Object.keys(initial)) if (initial[id]!["$kind"] === "agv") delete initial[id];
    for (let i = 1; i <= 100; i++) {
      const agv = { $kind: "agv", node: "C", state: "IDLE", label: `V${i}`, fleet: "f", load: null, motion: null };
      state[`AGV-${i}`] = agv;
      initial[`AGV-${i}`] = agv;
    }
    const map = mapOf("corridor-idle", { ...frame, state, initialState: initial });
    expect(map.vehicles).toHaveLength(100);
    expect(map.vehicles.slice(0, 3).map((v) => v.id)).toEqual(["AGV-1", "AGV-2", "AGV-3"]);
    expect(new Set(map.vehicles.map((v) => v.offset.x)).size).toBe(100);
    const xs = map.vehicles.map((v) => v.offset.x);
    expect(xs[0]).toBe(-49.5 * 24);
    expect(xs[99]).toBe(49.5 * 24);
  });
});

describe("motionSpeed", () => {
  const piecewise = {
    t_start: 10,
    t_end: 20,
    description: { curve: "piecewise", distance: 14, v0: 2, phases: [[4, 0], [6, -1 / 3]] },
  };
  it("integrates the phases up to the time", () => {
    expect(motionSpeed(piecewise, 10)).toBe(2);
    expect(motionSpeed(piecewise, 13)).toBe(2);
    expect(motionSpeed(piecewise, 16)).toBeCloseTo(4 / 3, 12);
    expect(motionSpeed(piecewise, 20)).toBeCloseTo(0, 12);
    expect(motionSpeed(piecewise, 25)).toBeCloseTo(0, 12);
  });
  it("divides the distance by the duration for a constant or linear curve", () => {
    for (const curve of ["constant", "linear"])
      expect(motionSpeed({ t_start: 0, t_end: 4, description: { curve, distance: 10 } }, 1)).toBe(2.5);
    expect(motionSpeed({ t_start: 0, t_end: 4, description: { curve: "linear" } }, 1)).toBeNull();
  });
  it("is null wherever the progress is approximate or cannot be evaluated", () => {
    const bad = { ...piecewise, description: { ...piecewise.description, phases: [[4, 0]] } };
    expect(motionSpeed(bad, 12)).toBeNull();
    expect(motionSpeed({ t_start: 5, t_end: 1, description: { curve: "linear", distance: 3 } }, 2)).toBeNull();
    expect(motionSpeed({ t_start: 0, t_end: 4, description: { curve: "linear", distance: 3, approximate: true } }, 2)).toBeNull();
    expect(motionSpeed({ t_start: 0, t_end: 4, description: { curve: "wavy" } }, 2)).toBeNull();
    expect(motionSpeed({ t_start: 0, t_end: 4, description: { curve: "linear", distance: 3 } }, NaN)).toBeNull();
    expect(motionSpeed(null, 1)).toBeNull();
  });
});
