import { describe, expect, it } from "vitest";
import type { PreparedFrame } from "@simulatte/playback";
import { Projector } from "../src/project";
import { motionSpeed } from "../src/motion";
import { expandHolds, focusPoint, overlayFor, strongHolds } from "../src/map/overlay";
import { nameOf, routeIndex, type MapModel, type Shown, type VehicleItem } from "../src/map/model";
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

const HOLDS_AGV1 = ["C", "C~M1", "corridor:M1"];
const HOLDS_IDLE_AGV1 = ["J1", "J1~W_S", "W", "W_S", "W~W_N", "W~W_S", "corridor:M1"];

describe("corridor-wait", () => {
  it("carries the time of the frame", () => {
    expect(mapOf("corridor-wait").time).toBe(loadFrame("corridor-wait").time);
  });
  it("opens the orders that have not ended", () => {
    const map = mapOf("corridor-wait");
    expect(map.orders.every((o) => o.open)).toBe(true);
  });
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
    expect(agv.holds).toEqual(HOLDS_AGV1);
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
    // whole seconds, the vehicles' own `waited` (6.457 s so far)
    expect(map.traffic.longestWait).toBe(6);
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
      open: true,
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
    expect(map.waitLines.map((l) => l.agv)).toEqual(["A2"]);
  });
  it("draws no wait line for a vehicle that is not shown as waiting", () => {
    expect(vehicle(map, "A1").wait).not.toBeNull();
    expect(map.waitLines.some((l) => l.agv === "A1")).toBe(false);
    expect(overlayFor(map, "A1")!.waitLine).toBeNull();
  });
  it("shows a resting vehicle with a route as heading for its goal", () => {
    expect(vehicle(map, "A7")).toMatchObject({ shown: "resting", goal: "n7", line: "A7 · to n7" });
  });
  it("keeps a stranded vehicle stranded in its line", () => {
    expect(vehicle(map, "A1").line).toBe("A1 · stranded");
  });
  it("gives a short label as it is, else the trailing digits of the id, else nothing", () => {
    expect(vehicle(map, "A1").short).toBe("A1");
    const frame = loadFrame("corridor-wait");
    const renamed = edit(edit(frame, "AGV-1", { label: "Forklift" }), "AGV-2", { label: "Forklift" });
    const named = { ...renamed, state: { ...renamed.state } };
    named.state["Tug"] = { ...frame.state["AGV-3"]!, label: "Forklift" };
    const items = mapOf("corridor-wait", { ...named, initialState: { ...(frame.initialState ?? frame.state), Tug: named.state["Tug"]! } });
    expect(vehicle(items, "AGV-1").short).toBe("1");
    expect(vehicle(items, "Tug").short).toBe("");
  });
  it("falls back to the id for an empty label and to unknown for a missing state", () => {
    const frame = edit(edit(loadFrame("corridor-wait"), "AGV-3", { label: "" }), "AGV-2", { state: undefined, motion: null });
    const map = mapOf("corridor-wait", frame);
    expect(vehicle(map, "AGV-3").label).toBe("AGV-3");
    expect(vehicle(map, "AGV-2")).toMatchObject({ shown: "unknown", doing: "unknown", state: "" });
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

describe("rules the fixtures do not exercise", () => {
  const frame = loadFrame("corridor-wait");
  it("shows a vehicle with a wait and a motion as waiting", () => {
    const moving = frame.state["AGV-2"]!["motion"];
    const map = mapOf("corridor-wait", edit(frame, "AGV-1", { motion: moving }));
    expect(vehicle(map, "AGV-1").shown).toBe("waiting");
  });
  it("draws the wait line to the first blocker that is in the frame", () => {
    const wait = { ...(frame.state["AGV-1"]!["wait"] as object), blockers: ["ghost", "AGV-2"] };
    const map = mapOf("corridor-wait", edit(frame, "AGV-1", { wait }));
    expect(map.waitLines.map((l) => [l.agv, l.blocker])).toEqual([["AGV-1", "AGV-2"]]);
    expect(vehicle(map, "AGV-1").doing).toBe("waiting 6 s for ghost");
  });
  it("takes the heading of a moving vehicle from its arc, not from the record", () => {
    const map = mapOf("corridor-wait", edit(frame, "AGV-2", { heading: 1 }));
    expect(vehicle(map, "AGV-2").heading).toBeCloseTo(Math.PI, 9);
  });
  it("has an empty load for a null record", () => {
    expect(vehicle(mapOf("sprites"), "A5")).toMatchObject({ load: {}, loaded: false });
  });
  it("names the goal by the label of the facility, not its id", () => {
    const facilityLabelled: PreparedFrame = {
      ...frame,
      state: { ...frame.state, East: { ...frame.state["East"]!, label: "East dock" } },
    };
    expect(vehicle(mapOf("corridor-wait", facilityLabelled), "AGV-1").goal).toBe("East dock");
  });
  it("holds what is reserved through the forward list of a segment, each id once", () => {
    const idle = mapOf("corridor-idle");
    expect(vehicle(idle, "AGV-1").holds).toEqual(HOLDS_IDLE_AGV1);
    const twice = edit(loadFrame("corridor-wait"), "C", { reserved_by: ["AGV-1", "AGV-1"] });
    expect(vehicle(mapOf("corridor-wait", twice), "AGV-1").holds).toEqual(HOLDS_AGV1);
  });
  it("treats a non-finite time as 0 and never shows negative seconds", () => {
    const nan = mapOf("corridor-wait", { ...frame, time: Number.NaN });
    expect(nan.traffic.longestWait).toBe(0);
    expect(vehicle(nan, "AGV-1").doing).toBe("waiting 0 s for AGV-2");
    const early = mapOf("corridor-wait", { ...frame, time: 100 });
    expect(early.traffic.longestWait).toBe(0);
    expect(vehicle(early, "AGV-1").doing).toBe("waiting 0 s for AGV-2");
  });
});

describe("completed orders", () => {
  const map = mapOf("advanced-charging");
  it("are listed but not open, and draw no flow", () => {
    expect(map.orders.find((o) => o.id === "order-50")!.open).toBe(false);
    expect(vehicle(map, "AGV-3").order).toBe("order-50");
    expect(overlayFor(map, "AGV-3")!.flow).toBeNull();
    expect(overlayFor(map, "order-50")!.flow).toBeNull();
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
    expect(overlay.holds).toEqual(HOLDS_AGV1);
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

describe("expandHolds", () => {
  const map = mapOf("corridor-wait");
  it("replaces a zone by its members and keeps nodes and segments", () => {
    expect([...expandHolds(map, HOLDS_AGV1)].sort()).toEqual(["C", "C~M1", "J1~M1", "M1"]);
  });
  it("keeps an id that is neither a zone nor on the floor, and expands nothing for none", () => {
    expect([...expandHolds(map, ["nowhere"])]).toEqual(["nowhere"]);
    expect(expandHolds(map, []).size).toBe(0);
  });
  it("reads no inherited property as a zone, and keeps a zone named __proto__", () => {
    expect([...expandHolds(map, ["constructor", "toString"])]).toEqual(["constructor", "toString"]);
    const frame = loadFrame("corridor-wait");
    const odd = {
      ...frame,
      state: { ...frame.state, ["__proto__"]: { $kind: "zone", members: ["M1"], reserved_by: [] } },
    };
    const model = mapOf("corridor-wait", odd);
    expect(Object.hasOwn(model.zones, "__proto__")).toBe(true);
    expect([...expandHolds(model, ["__proto__"])]).toEqual(["M1"]);
  });
});

describe("focusPoint", () => {
  const map = mapOf("corridor-wait");
  const at = (id: string) => {
    const item = map.vehicles.find((v) => v.id === id) ?? map.facilities.find((f) => f.id === id)!;
    return { x: item.x, y: item.y };
  };
  it("centres a vehicle or a facility on its position", () => {
    expect(focusPoint(map, "AGV-2")).toEqual(at("AGV-2"));
    expect(focusPoint(map, "East")).toEqual(at("East"));
  });
  it("centres an order on its vehicle, else on its origin facility", () => {
    expect(focusPoint(map, "order-0")).toEqual(at("AGV-1"));
    expect(focusPoint(map, "order-3")).toEqual(at("East"));
    expect(focusPoint(map, "order-4")).toEqual(at("West"));
  });
  it("has no point for an order whose vehicle and origin are not in the frame, or another id", () => {
    const frame = loadFrame("corridor-wait");
    const order = { ...frame.state["order-3"]!, origin: "nowhere" };
    expect(focusPoint(mapOf("corridor-wait", edit(frame, "order-3", order)), "order-3")).toBeNull();
    expect(focusPoint(map, "C")).toBeNull();
    expect(focusPoint(map, "nobody")).toBeNull();
  });
});

describe("the route ahead", () => {
  const frame = loadFrame("corridor-wait");
  it("starts at the node the vehicle stands on, found by id", () => {
    const route = overlayFor(mapOf("corridor-wait"), "AGV-1")!.route;
    const map = mapOf("corridor-wait");
    const ids = ["D", "M2", "J2", "E_S", "E"];
    expect(route).toEqual([{ x: 35, y: 0 }, ...ids.map((id) => ({ x: map.nodes.find((n) => n.id === id)!.x, y: map.nodes.find((n) => n.id === id)!.y }))]);
  });
  it("is empty for a vehicle on a node that is not in its route", () => {
    const map = mapOf("corridor-wait", edit(frame, "AGV-1", { node: "S" }));
    expect(overlayFor(map, "AGV-1")!.route).toEqual([]);
  });
  it("is empty for a vehicle at the end of its route", () => {
    const map = mapOf("corridor-wait", edit(frame, "AGV-1", { node: "E" }));
    expect(overlayFor(map, "AGV-1")!.route).toEqual([]);
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
  it("follows a trapezoidal profile: zero at the start and the end, the peak in cruise", () => {
    // 2 m/s peak at 1 m/s^2 up and down over 10 m: 2 s up, 3 s cruise, 2 s down.
    const motion = { t_start: 0, t_end: 7, description: { curve: "trapezoidal", distance: 10, v_max: 2, accel: 1, decel: 1 } };
    expect(motionSpeed(motion, 0)).toBe(0);
    expect(motionSpeed(motion, 1)).toBeCloseTo(1, 12);
    expect(motionSpeed(motion, 3.5)).toBe(2);
    expect(motionSpeed(motion, 6)).toBeCloseTo(1, 12);
    expect(motionSpeed(motion, 7)).toBeCloseTo(0, 12);
    expect(motionSpeed({ ...motion, t_end: 8 }, 3)).toBeNull();
  });
  it("is null for keyframes", () => {
    const keyframes = { t_start: 0, t_end: 2, description: { curve: "keyframes", accuracy: 0, keyframes: [[0, 0], [2, 1]] } };
    expect(motionSpeed(keyframes, 1)).toBeNull();
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

describe("one source for names, waits, route positions and stock", () => {
  const frame = loadFrame("corridor-wait");
  const relabelled = edit(edit(frame, "East", { label: "East dock" }), "AGV-2", { label: "Tugger two" });
  it("names a vehicle or a facility by its label, anything else by its id", () => {
    const map = mapOf("corridor-wait", relabelled);
    expect(nameOf(map, "AGV-2")).toBe("Tugger two");
    expect(nameOf(map, "East")).toBe("East dock");
    expect(nameOf(map, "AGV-1")).toBe("AGV-1");
    expect(nameOf(map, "order-0")).toBe("order-0");
    expect(nameOf(map, "constructor")).toBe("constructor");
  });
  it("names the blocker of a wait by its label", () => {
    const agv = vehicle(mapOf("corridor-wait", relabelled), "AGV-1");
    expect(agv.doing).toBe("waiting 6 s for Tugger two");
    expect(agv.line).toBe("AGV-1 · waiting 6 s for Tugger two");
    expect(agv.wait!.blockers).toEqual(["AGV-2"]);
  });
  it("computes the whole seconds of a wait once, and the longest wait from them", () => {
    const map = mapOf("corridor-wait");
    expect(vehicle(map, "AGV-1").waited).toBe(6);
    expect(vehicle(map, "AGV-2").waited).toBeNull();
    expect(map.traffic.longestWait).toBe(6);
    expect(mapOf("corridor-wait", { ...frame, time: 100 }).traffic.longestWait).toBe(0);
  });
  it("leaves a wait without since unknown: no seconds, and no invented since", () => {
    const { since: _since, ...rest } = frame.state["AGV-1"]!["wait"] as Record<string, unknown>;
    const map = mapOf("corridor-wait", edit(frame, "AGV-1", { wait: rest }));
    const agv = vehicle(map, "AGV-1");
    expect(agv.wait!.since).toBeNull();
    expect(agv.waited).toBeNull();
    expect(agv.doing).toBe("waiting for AGV-2");
    expect(map.traffic).toMatchObject({ waiting: 1, longestWait: 0 });
  });
  it("keeps SKUs named like members of Object.prototype", () => {
    const odd = JSON.parse('{"__proto__": 3, "constructor": 2, "crate": 1}') as Record<string, number>;
    const map = mapOf("corridor-wait", edit(edit(frame, "AGV-1", { load: odd }), "East", { inventory: odd }));
    const load = vehicle(map, "AGV-1").load;
    expect(Object.keys(load).sort()).toEqual(["__proto__", "constructor", "crate"]);
    expect(load["constructor"]).toBe(2);
    const stock = facility(map, "East").stock!;
    expect(Object.keys(stock).sort()).toEqual(["__proto__", "constructor", "crate"]);
    expect(stock["__proto__"]).toBe(3);
    expect(facility(map, "East").count).toBe("3 SKUs");
  });
  it("gives stock to a warehouse that records an inventory, null to the others", () => {
    const map = mapOf("corridor-wait");
    expect(facility(map, "East").stock).toEqual({ crate: 49 });
    expect(facility(map, "Siding").stock).toBeNull();
    const unrecorded = facility(mapOf("corridor-wait", edit(frame, "East", { inventory: null })), "East");
    expect(unrecorded).toMatchObject({ stock: null, count: "" });
  });
});

describe("routeIndex", () => {
  const at = (nodes: string[], node: string | null, arc: { from: string; to: string } | null) =>
    routeIndex({ route: { nodes, destination: nodes.at(-1)!, reason: "" }, node, arc });
  it("is the node the vehicle stands on, or the one it heads to", () => {
    expect(at(["A", "B", "C"], "B", null)).toBe(1);
    expect(at(["A", "B", "C"], "A", { from: "A", to: "B" })).toBe(1);
  });
  it("prefers the occurrence of a revisited node that follows the arc's from, else the first", () => {
    expect(at(["A", "B", "C", "B", "D"], "C", { from: "C", to: "B" })).toBe(3);
    expect(at(["A", "B", "C", "B", "D"], "X", { from: "X", to: "B" })).toBe(1);
    expect(at(["A", "B", "C", "B", "D"], "B", null)).toBe(1);
  });
  it("is -1 off the route or without one", () => {
    expect(at(["A", "B"], "Z", null)).toBe(-1);
    expect(at(["A", "B"], null, null)).toBe(-1);
    expect(routeIndex({ route: null, node: "A", arc: null })).toBe(-1);
  });
  it("puts the route ahead of a vehicle on a revisited node after the arc's from", () => {
    const route = { nodes: ["W", "W_S", "J1", "M1", "C", "D", "C", "S"], destination: "S", reason: "travel" };
    const moving = frame0Moving(route);
    const overlay = overlayFor(moving, "AGV-2")!;
    const point = (id: string) => {
      const node = moving.nodes.find((n) => n.id === id)!;
      return { x: node.x, y: node.y };
    };
    const agv = vehicle(moving, "AGV-2");
    expect(overlay.route).toEqual([{ x: agv.x, y: agv.y }, point("C"), point("S")]);
  });
});

/** AGV-2 of corridor-wait under way from D to C on the given route. */
function frame0Moving(route: object): MapModel {
  const frame = loadFrame("corridor-wait");
  const motion = { ...(frame.state["AGV-2"]!["motion"] as object), from: "D", to: "C" };
  return mapOf("corridor-wait", edit(frame, "AGV-2", { route, motion, node: "D" }));
}

describe("focusPoint with a scale", () => {
  const map = mapOf("advanced-charging");
  it("centres on a vehicle where its symbol is drawn: its fan-out offset at symbol level", () => {
    const agv = vehicle(map, "AGV-1");
    expect(agv.offset.x).toBe(-24);
    expect(focusPoint(map, "AGV-1", 8)).toEqual({ x: agv.x - 3, y: agv.y });
    // at true size every vehicle is drawn where it is
    expect(focusPoint(map, "AGV-1", 30)).toEqual({ x: agv.x, y: agv.y });
    expect(focusPoint(map, "AGV-1")).toEqual({ x: agv.x, y: agv.y });
  });
  it("includes a facility's offset", () => {
    const sprites = mapOf("sprites");
    const charger = facility(sprites, "Charger-A");
    expect(focusPoint(sprites, "Charger-A", 9.5)).toEqual({ x: charger.x - 2, y: charger.y });
  });
});

describe("strongHolds", () => {
  const map = mapOf("corridor-wait");
  it("is what the selected vehicle holds", () => {
    expect([...strongHolds(map, "AGV-1", overlayFor(map, "AGV-1"))].sort()).toEqual(["C", "C~M1", "J1~M1", "M1"]);
  });
  it("is the selected segment, node or the members of the selected zone", () => {
    expect([...strongHolds(map, "C~D", overlayFor(map, "C~D"))]).toEqual(["C~D"]);
    expect([...strongHolds(map, "M2", overlayFor(map, "M2"))]).toEqual(["M2"]);
    expect([...strongHolds(map, "corridor:M2", overlayFor(map, "corridor:M2"))]).toEqual(["D~M2", "M2", "J2~M2"]);
  });
  it("is nothing for another kind, an unknown id or no selection", () => {
    expect(strongHolds(map, "order-0", overlayFor(map, "order-0")).size).toBe(0);
    expect(strongHolds(map, "East", overlayFor(map, "East")).size).toBe(0);
    expect(strongHolds(map, "nowhere", null).size).toBe(0);
    expect(strongHolds(map, "constructor", null).size).toBe(0);
    expect(strongHolds(map, null, null).size).toBe(0);
  });
});
