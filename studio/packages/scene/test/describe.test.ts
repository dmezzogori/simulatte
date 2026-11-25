import { describe, expect, it } from "vitest";
import type { PreparedFrame } from "@simulatte/playback";
import { Projector } from "../src/project";
import type { MapModel } from "../src/map/model";
import { describe as describeEntity, type DetailLine } from "../src/map/describe";
import { loadFrame } from "./fixtures";

const mapOf = (frame: PreparedFrame): MapModel => {
  const map = new Projector(frame.initialState ?? frame.state).project(frame).map;
  if (!map) throw new Error("no map");
  return map;
};
const detail = (name: string, id: string, frame: PreparedFrame = loadFrame(name)): readonly DetailLine[] | null =>
  describeEntity(mapOf(frame), frame.state, id);
const texts = (lines: readonly DetailLine[] | null): string[] => (lines ?? []).map((l) => l.text);
const line = (lines: readonly DetailLine[] | null, start: string): DetailLine =>
  (lines ?? []).find((l) => l.text.startsWith(start)) ?? { text: `no line starts with "${start}"` };

describe("a vehicle on corridor-wait", () => {
  const lines = detail("corridor-wait", "AGV-1")!;
  it("gives the lines of spec section 5.7 in their order", () => {
    expect(texts(lines)).toEqual([
      "AGV-1 · fleet tuggers · TRAVELING_LOADED",
      "Load 1 crate · order-0, West → East, picked at 63.8",
      "At node C · heading 0°",
      "Battery 900.1 (90 %)",
      "Route W → W_S → J1 → M1 → C → [D] → M2 → J2 → E_S → E",
      "Waiting 6 s (since 121.5) to enter D",
      "Needs corridor:M2, C~D, D",
      "Blocked by AGV-2",
      "Holds C, C~M1, corridor:M1",
      "Size 1.2 × 0.6 m",
    ]);
  });
  it("lists the ids a line names that can be selected, in order of appearance", () => {
    expect(line(lines, "Load").refs).toEqual(["order-0", "West", "East"]);
    expect(line(lines, "At node").refs).toEqual(["C"]);
    expect(line(lines, "Route").refs).toEqual(["W", "W_S", "J1", "M1", "C", "D", "M2", "J2", "E_S", "E"]);
    expect(line(lines, "Waiting").refs).toEqual(["D"]);
    expect(line(lines, "Needs").refs).toEqual(["corridor:M2", "C~D", "D"]);
    expect(line(lines, "Blocked by").refs).toEqual(["AGV-2"]);
    expect(line(lines, "Holds").refs).toEqual(["C", "C~M1", "corridor:M1"]);
    expect(line(lines, "AGV-1").refs ?? []).toEqual([]);
  });
  it("names an arc, a speed and the next node of a vehicle under way", () => {
    const moving = detail("corridor-wait", "AGV-2")!;
    expect(texts(moving)[2]).toMatch(/^On M2 → D · heading 180° · speed 1\.5 m\/s$/);
    expect(line(moving, "On").refs).toEqual(["M2", "D"]);
    expect(texts(moving).find((t) => t.startsWith("Route"))).toMatch(/→ \[D\] →/);
    expect(moving.some((l) => l.text.startsWith("Waiting"))).toBe(false);
  });
  it("says Empty, and names an open order it has", () => {
    const frame = loadFrame("advanced-charging");
    const empty = detail("advanced-charging", "AGV-1", frame)!;
    expect(texts(empty)[1]).toBe("Empty");
    const charging = detail("advanced-charging", "AGV-3", frame)!;
    // order-50 is COMPLETED and still in the state: not an open order
    expect(texts(charging)[1]).toBe("Empty");
  });
  it("names an id that is not in the frame without a ref", () => {
    const frame = loadFrame("corridor-wait");
    const state = structuredClone(frame.state);
    (state["AGV-1"]!["wait"] as { blockers: string[] }).blockers = ["AGV-9", "AGV-2"];
    (state["AGV-1"]!["route"] as { nodes: string[] }).nodes = ["W", "Z9", "C", "D"];
    const lines = describeEntity(mapOf({ ...frame, state }), state, "AGV-1")!;
    expect(line(lines, "Blocked by")).toEqual({ text: "Blocked by AGV-9, AGV-2", refs: ["AGV-2"] });
    expect(line(lines, "Route")).toMatchObject({ text: "Route W → Z9 → C → [D] → E" });
    expect(line(lines, "Route").refs).toEqual(["W", "C", "D", "E"]);
  });
  it("uses the seconds of the map's own wait text", () => {
    const frame = loadFrame("corridor-wait");
    const model = mapOf(frame);
    const agv = model.vehicles.find((v) => v.id === "AGV-1")!;
    expect(agv.doing).toBe("waiting 6 s for AGV-2");
    expect(texts(describeEntity(model, frame.state, "AGV-1"))).toContain("Waiting 6 s (since 121.5) to enter D");
    const early = { ...model, time: 100 };
    expect(texts(describeEntity(early, frame.state, "AGV-1"))).toContain("Waiting 0 s (since 121.5) to enter D");
  });
});

describe("the other kinds on corridor-wait", () => {
  it("describes an order in a sentence per field, and its vehicle", () => {
    const lines = detail("corridor-wait", "order-0")!;
    expect(texts(lines)).toEqual([
      "order-0 · IN_TRANSIT",
      "Carries 1 crate",
      "From West",
      "To East",
      "Created at 0.0",
      "Dispatched at 0.0",
      "Picked at 63.8",
      "Vehicle AGV-1",
    ]);
    expect(lines.flatMap((l) => l.refs ?? [])).toEqual(["West", "East", "AGV-1"]);
    expect(texts(detail("corridor-wait", "order-3")).at(-1)).toBe("No vehicle yet");
  });
  it("describes a warehouse: stock, its nodes, the vehicles at them and its open orders", () => {
    const lines = detail("corridor-wait", "East")!;
    expect(texts(lines)).toEqual([
      "East · warehouse",
      "Stock 49 crate",
      "Slots in use 0",
      "Bays at E",
      "Vehicles at it: none",
      "Open orders from it: order-1, order-3",
      "Open orders to it: order-0, order-2, order-4",
    ]);
    expect(line(lines, "Bays").refs).toEqual(["E"]);
    expect(line(lines, "Open orders from").refs).toEqual(["order-1", "order-3"]);
    expect(texts(detail("corridor-wait", "West"))).toContain("Vehicles at it: AGV-3");
    expect(line(detail("corridor-wait", "West"), "Vehicles at").refs).toEqual(["AGV-3"]);
  });
  it("describes parking and charging by occupancy", () => {
    expect(texts(detail("corridor-wait", "Siding"))).toEqual([
      "Siding · parking area",
      "Parked 0 of 3",
      "Node S",
      "Vehicles at it: none",
    ]);
    const frame = loadFrame("advanced-charging");
    expect(texts(detail("advanced-charging", "Parking", frame))).toEqual([
      "Parking · parking area",
      "Parked 3 of 3: AGV-1, AGV-2, AGV-5",
      "Node PARK",
      "Vehicles at it: AGV-1, AGV-2, AGV-5",
    ]);
    expect(texts(detail("advanced-charging", "Charger", frame))).toEqual([
      "Charger · charging station",
      "Slots in use 1 of 2",
      "Node CHRG",
      "Vehicles at it: AGV-3",
    ]);
  });
  it("describes a node: who stands on it, who holds it, the facility bound to it", () => {
    const lines = detail("corridor-wait", "C")!;
    expect(texts(lines)).toEqual(["Node C", "Vehicles on it: AGV-1", "Held by: AGV-1"]);
    expect(lines.flatMap((l) => l.refs ?? [])).toEqual(["AGV-1", "AGV-1"]);
    expect(texts(detail("corridor-wait", "E"))).toEqual([
      "Node E",
      "Vehicles on it: none",
      "Held by: nobody",
      "Bound to East",
    ]);
  });
  it("describes a segment: its ends, length and holders per direction", () => {
    const lines = detail("corridor-wait", "C~M1")!;
    expect(texts(lines)).toEqual([
      "Segment C~M1",
      "Between C and M1",
      "Length 15.0 m",
      "Held C → M1 by: nobody",
      "Held M1 → C by: AGV-1",
    ]);
    expect(lines.flatMap((l) => l.refs ?? [])).toEqual(["C", "M1", "AGV-1"]);
  });
  it("describes a zone: rule, members, holders, orientation", () => {
    const lines = detail("corridor-wait", "corridor:M1")!;
    expect(texts(lines)).toEqual([
      "Zone corridor:M1",
      "Rule same_direction",
      "Members: C~M1, M1, J1~M1",
      "Held by: AGV-1",
      "Orientation backward",
    ]);
    expect(line(lines, "Members").refs).toEqual(["C~M1", "M1", "J1~M1"]);
    expect(line(lines, "Held by").refs).toEqual(["AGV-1"]);
  });
  it("returns null for a server, a fleet and an unknown id", () => {
    const frame = loadFrame("corridor-wait");
    const state = { ...frame.state, "server-0": { $kind: "server", label: "server-0" } };
    expect(describeEntity(mapOf(frame), state, "server-0")).toBeNull();
    expect(describeEntity(mapOf(frame), state, "tuggers")).toBeNull();
    expect(describeEntity(mapOf(frame), state, "nobody")).toBeNull();
    expect(describeEntity(mapOf(frame), state, "__proto__")).toBeNull();
    expect(describeEntity(mapOf(frame), state, "constructor")).toBeNull();
  });
});

describe("a trace without the newer fields", () => {
  it("omits the wait, the route, the size and the percentage", () => {
    const frame = loadFrame("bare-layout");
    const lines = detail("bare-layout", "AGV-1", frame)!;
    expect(texts(lines)).toEqual([
      "AGV-1 · fleet tuggers · TRAVELING_LOADED",
      "Load 1 crate · order-0, West → East, picked at 63.8",
      "At node C · heading 0°",
      "Battery 900.1",
      "Holds C, C~M1, corridor:M1",
    ]);
  });
  it("describes every entity of every fixture without throwing", () => {
    for (const name of ["corridor-wait", "corridor-idle", "advanced-charging", "two-fleets", "bare-layout", "sprites"]) {
      const frame = loadFrame(name);
      const model = mapOf(frame);
      for (const id of Object.keys(frame.state)) {
        const lines = describeEntity(model, frame.state, id);
        for (const l of lines ?? []) expect(typeof l.text).toBe("string");
      }
    }
  });
  it("draws a vehicle without a battery, a heading, a load or a fleet", () => {
    const frame = loadFrame("bare-layout");
    const state = structuredClone(frame.state);
    for (const key of ["battery", "heading", "load", "order", "fleet"]) delete state["AGV-3"]![key];
    const lines = describeEntity(mapOf({ ...frame, state }), state, "AGV-3")!;
    expect(texts(lines)).toEqual(["AGV-3 · WAITING_LOAD", "Empty", "At node W · heading 0°", "Holds W, W~W_N"]);
  });
});

describe("hostile labels", () => {
  it("keeps a closing-script label as text of a line", () => {
    const frame = loadFrame("corridor-wait");
    const state = structuredClone(frame.state);
    state["AGV-1"]!["label"] = "</script><img src=x onerror=alert(1)>";
    const lines = describeEntity(mapOf({ ...frame, state }), state, "AGV-1")!;
    expect(lines[0]!.text.startsWith("</script><img src=x onerror=alert(1)> · fleet tuggers")).toBe(true);
  });
});
