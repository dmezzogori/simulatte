import { describe, expect, it } from "vitest";
import type { PreparedFrame } from "@simulatte/playback";
import { Projector } from "../src/project";
import type { MapModel, OrderItem, VehicleItem } from "../src/map/model";
import { overview, type OverviewOptions } from "../src/map/overview";
import { loadFrame } from "./fixtures";

const mapOf = (name: string, frame: PreparedFrame = loadFrame(name)): MapModel => {
  const map = new Projector(frame.initialState ?? frame.state).project(frame).map;
  if (!map) throw new Error(`${name} has no map`);
  return map;
};
const none: OverviewOptions = { filter: "", waitingOnly: false };
const group = (model: MapModel, fleet = "tuggers", options = none) =>
  overview(model, options).vehicles.find((g) => g.fleet === fleet)!;
/** Copies of one vehicle, with their own ids, to fill a fleet. */
const copies = (model: MapModel, count: number, patch: (i: number) => Partial<VehicleItem> = () => ({})): MapModel => {
  const base = model.vehicles.find((v) => v.id === "AGV-3")!;
  return {
    ...model,
    vehicles: Array.from({ length: count }, (_, i) => ({ ...base, id: `V-${i}`, label: `V-${i}`, ...patch(i) })),
  };
};

describe("corridor-wait", () => {
  const model = mapOf("corridor-wait");
  it("writes a vehicle line from the model's own texts", () => {
    const lines = group(model).lines;
    expect(lines[0]).toEqual({
      id: "AGV-1",
      shown: "waiting",
      text: "AGV-1 · loaded, 1 crate → East · waiting 6 s for AGV-2",
    });
    expect(lines.map((l) => l.id)).toEqual(["AGV-1", "AGV-2", "AGV-3"]);
    expect(lines[2]!.text).toBe("AGV-3 · loaded, 1 crate · loading");
    expect(lines[1]!.text).toMatch(/^AGV-2 · loaded, 1 crate → West · moving$/);
  });
  it("counts every state in a group heading", () => {
    expect(group(model).counts).toEqual({
      stranded: 0,
      waiting: 1,
      moving: 1,
      working: 1,
      idle: 0,
      charging: 0,
      resting: 0,
      unknown: 0,
    });
    expect(group(model).hidden).toBe(0);
  });
  it("sums the traffic", () => {
    expect(overview(model, none).traffic).toBe("1 waiting · longest 6 s · waits 2 · stuck 0 · deadlocks 0");
  });
  it("narrows by the waiting-only switch and by a case-insensitive filter of the text", () => {
    expect(group(model, "tuggers", { filter: "", waitingOnly: true }).lines.map((l) => l.id)).toEqual(["AGV-1"]);
    expect(group(model, "tuggers", { filter: "LOADING", waitingOnly: false }).lines.map((l) => l.id)).toEqual(["AGV-3"]);
    expect(group(model, "tuggers", { filter: "east", waitingOnly: false }).lines.map((l) => l.id)).toEqual(["AGV-1"]);
    expect(group(model, "tuggers", { filter: "zzz", waitingOnly: false })).toMatchObject({ lines: [], hidden: 0 });
    // the counts describe the fleet, not the filter
    expect(group(model, "tuggers", { filter: "zzz", waitingOnly: false }).counts.waiting).toBe(1);
  });
  it("lists the open orders, oldest first, with counts by status", () => {
    const goods = overview(model, none).goods;
    expect(goods.counts).toEqual({ IN_TRANSIT: 2, PICKING: 1, PENDING: 2 });
    expect(goods.lines.map((l) => l.id)).toEqual(["order-0", "order-1", "order-2", "order-3", "order-4"]);
    expect(goods.lines[0]).toEqual({ id: "order-0", shown: null, text: "order-0 · 1 crate West → East · on AGV-1" });
    expect(goods.lines[2]!.text).toBe("order-2 · 1 crate West → East · on AGV-3");
    expect(goods.lines[3]!.text).toBe("order-3 · 1 crate East → West · unassigned");
    expect(goods.hidden).toBe(0);
  });
  it("lists the facilities in id order", () => {
    expect(overview(model, none).facilities).toEqual([
      { id: "East", shown: null, text: "East · crate 49" },
      { id: "Siding", shown: null, text: "Siding · 0 / 3" },
      { id: "West", shown: null, text: "West · crate 48" },
    ]);
  });
  it("names the vehicle of an order not yet picked, and writes an empty vehicle as empty", () => {
    const orders = model.orders.map((o) => (o.id === "order-2" ? { ...o, picked: false } : o));
    const vehicles = model.vehicles.map((v) => (v.id === "AGV-3" ? { ...v, load: {}, loaded: false } : v));
    const changed = overview({ ...model, orders, vehicles }, none);
    expect(changed.goods.lines[2]!.text).toBe("order-2 · 1 crate West → East · AGV-3, not picked");
    expect(changed.vehicles[0]!.lines[2]!.text).toBe("AGV-3 · empty · loading");
  });
  it("leaves out a closed order", () => {
    const orders: OrderItem[] = model.orders.map((o, i) => (i === 0 ? { ...o, status: "COMPLETED", open: false } : o));
    const goods = overview({ ...model, orders }, none).goods;
    expect(goods.lines.map((l) => l.id)).not.toContain("order-0");
    expect(goods.counts["COMPLETED"]).toBeUndefined();
  });
});

describe("other frames", () => {
  it("reports no waits on a bare layout", () => {
    expect(overview(mapOf("bare-layout"), none).traffic).toBe("0 waiting · longest 0 s");
  });
  it("gives two fleets two groups", () => {
    const groups = overview(mapOf("two-fleets"), none).vehicles;
    expect(groups).toHaveLength(2);
    expect(groups.map((g) => g.fleet)).toEqual([...mapOf("two-fleets").fleets]);
  });
  it("puts a vehicle without a fleet in a group named by the empty string", () => {
    const model = mapOf("corridor-wait");
    const vehicles = model.vehicles.map((v) => (v.id === "AGV-3" ? { ...v, fleet: null } : v));
    const groups = overview({ ...model, vehicles }, none).vehicles;
    expect(groups.map((g) => g.fleet)).toEqual(["tuggers", ""]);
    expect(groups[1]!.lines.map((l) => l.id)).toEqual(["AGV-3"]);
    expect(groups[1]!.counts.working).toBe(1);
  });
  it("lists a warehouse without a recorded stock by its count", () => {
    const model = mapOf("corridor-wait");
    const facilities = model.facilities.map((f) => (f.id === "East" ? { ...f, stock: {} } : f));
    expect(overview({ ...model, facilities }, none).facilities[0]!.text).toBe("East · 49");
  });
});

describe("order inside a group", () => {
  const base = mapOf("corridor-wait");
  it("puts stranded first, then waiting by longest wait, then the others by natural id", () => {
    const wait = (since: number) => ({ since, node: "D", resources: [], blockers: [] });
    const model = copies(base, 5, (i) => [
      { shown: "moving" as const, doing: "moving" },
      { shown: "waiting" as const, doing: "waiting 3 s", wait: wait(base.time - 3) },
      { shown: "stranded" as const, doing: "stranded" },
      { shown: "waiting" as const, doing: "waiting 9 s", wait: wait(base.time - 9) },
      { shown: "idle" as const, doing: "idle" },
    ][i]!);
    expect(group({ ...model, time: base.time }, "tuggers").lines.map((l) => l.id)).toEqual(["V-2", "V-3", "V-1", "V-0", "V-4"]);
  });
  it("compares ids naturally", () => {
    const model = copies(base, 12);
    const ids = group(model).lines.map((l) => l.id);
    expect(ids.slice(0, 4)).toEqual(["V-0", "V-1", "V-2", "V-3"]);
    expect(ids.indexOf("V-2")).toBeLessThan(ids.indexOf("V-10"));
  });
});

describe("caps", () => {
  const base = mapOf("corridor-wait");
  it("caps a group at 200 lines and says how many are hidden", () => {
    const g = group(copies(base, 250));
    expect(g.lines).toHaveLength(200);
    expect(g.hidden).toBe(50);
    expect(g.counts.working).toBe(250);
  });
  it("takes a smaller cap and counts the hidden after the filter", () => {
    const model = copies(base, 20);
    const g = group(model, "tuggers", { filter: "V-1", waitingOnly: false, cap: 5 });
    // V-1 and V-10 .. V-19 match: 11 lines, 5 shown
    expect(g.lines).toHaveLength(5);
    expect(g.hidden).toBe(6);
  });
  it("caps the open orders, oldest first", () => {
    const orders: OrderItem[] = Array.from({ length: 230 }, (_, i) => ({
      id: `o-${i}`,
      sku: "crate",
      quantity: 1,
      origin: "A",
      destination: "B",
      status: "PENDING",
      agv: null,
      picked: false,
      createdAt: 230 - i,
      open: true,
    }));
    const goods = overview({ ...base, orders }, none).goods;
    expect(goods.lines).toHaveLength(200);
    expect(goods.hidden).toBe(30);
    expect(goods.lines[0]!.id).toBe("o-229");
    expect(goods.counts).toEqual({ PENDING: 230 });
  });
});
