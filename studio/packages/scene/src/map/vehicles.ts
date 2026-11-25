import { motionSpeed } from "../motion";
import type { Point } from "../project";
import {
  SYMBOL_PX,
  fanOut,
  naturalCompare,
  type FacilityItem,
  type Route,
  type Shown,
  type TrafficSummary,
  type VehicleItem,
  type Wait,
  type WaitLine,
} from "./model";

type Fields = Record<string, unknown>;
const object = (v: unknown): Fields =>
  v !== null && typeof v === "object" && !Array.isArray(v) ? (v as Fields) : {};
const finite = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
const text = (v: unknown): string | null => (typeof v === "string" ? v : null);
const named = (v: unknown): string | null => (typeof v === "string" && v !== "" ? v : null);
const finiteTime = (t: number): number => (Number.isFinite(t) ? t : 0);
const strings = (v: unknown): string[] =>
  Array.isArray(v) ? v.filter((id): id is string => typeof id === "string") : [];

/** Where a vehicle is on the map and the arc it is on, when it has a motion. */
export interface VehiclePlace {
  point: Point;
  arc: readonly [Point, Point] | null;
}

export interface VehicleInput {
  time: number;
  layout: Fields;
  state: Readonly<Record<string, Fields>>;
  /** The vehicles that have a position on the map. */
  places: ReadonlyMap<string, VehiclePlace>;
  fleets: readonly string[];
  facilities: readonly FacilityItem[];
}

const parseWait = (v: unknown, time: number): Wait | null => {
  if (v === null || typeof v !== "object" || Array.isArray(v)) return null;
  const w = object(v);
  return {
    since: finite(w["since"]) ?? time,
    node: text(w["node"]) ?? "",
    resources: strings(w["resources"]),
    blockers: strings(w["blockers"]),
  };
};
const parseRoute = (v: unknown): Route | null => {
  const r = object(v),
    destination = text(r["destination"]);
  if (destination === null || !Array.isArray(r["nodes"])) return null;
  return { nodes: strings(r["nodes"]), destination, reason: text(r["reason"]) ?? "" };
};

/** The first row of the table of spec section 5.4 that applies. */
function shownOf(state: string, wait: Wait | null, moving: boolean): Shown {
  if (state === "STRANDED") return "stranded";
  if (wait) return "waiting";
  if (moving) return "moving";
  if (state === "WAITING_LOAD" || state === "WAITING_UNLOAD") return "working";
  if (state === "IDLE") return "idle";
  if (state === "CHARGING") return "charging";
  if (state.startsWith("TRAVELING_")) return "resting";
  return "unknown";
}

/** Ids of the nodes, segments and zones whose holder lists contain each vehicle, in state order. */
function holdings(state: Readonly<Record<string, Fields>>): Map<string, string[]> {
  const held = new Map<string, string[]>();
  const add = (holder: string, id: string) => (held.get(holder) ?? held.set(holder, []).get(holder)!).push(id);
  for (const [id, fields] of Object.entries(state)) {
    const kind = fields["$kind"];
    const holders =
      kind === "node" || kind === "zone"
        ? strings(fields["reserved_by"])
        : kind === "segment"
          ? [...strings(fields["forward"]), ...strings(fields["backward"])]
          : [];
    for (const holder of new Set(holders)) add(holder, id);
  }
  return held;
}

/** The vehicles of a frame, in natural id order. */
export function buildVehicles(input: VehicleInput): VehicleItem[] {
  const placements = object(object(input.layout["resolved"])["placements"]);
  const held = holdings(input.state);
  const time = finiteTime(input.time);
  const items: Omit<VehicleItem, "offset">[] = [];
  for (const [id, place] of input.places) {
    const fields = input.state[id];
    if (!fields || fields["$kind"] !== "agv") continue;
    const footprint = object(object(placements[id])["footprint"]);
    const size = (v: unknown) => {
      const n = finite(v);
      return n !== null && n > 0 ? n : 1;
    };
    const motion = object(fields["motion"]);
    const moving = Object.keys(motion).length > 0;
    const state = text(fields["state"]) ?? "";
    const wait = parseWait(fields["wait"], time);
    const route = parseRoute(fields["route"]);
    const shown = shownOf(state, wait, moving);
    const label = named(object(object(placements[id])["visual"])["label"]) ?? named(fields["label"]) ?? id;
    const load: Record<string, number> = {};
    for (const [sku, quantity] of Object.entries(object(fields["load"])))
      if (finite(quantity) !== null) load[sku] = quantity as number;
    const fleet = text(fields["fleet"]);
    const fleetIndex = input.fleets.length > 1 && fleet !== null ? input.fleets.indexOf(fleet) : -1;
    const goal =
      route === null
        ? null
        : (input.facilities.find((f) => f.nodes.includes(route.destination))?.label ?? route.destination);
    const heading =
      place.arc && (place.arc[0].x !== place.arc[1].x || place.arc[0].y !== place.arc[1].y)
        ? Math.atan2(place.arc[1].y - place.arc[0].y, place.arc[1].x - place.arc[0].x)
        : (finite(fields["heading"]) ?? 0);
    const doing =
      shown === "stranded"
        ? "stranded"
        : shown === "waiting"
          ? `waiting ${Math.max(0, Math.floor(time - wait!.since))} s${wait!.blockers.length ? ` for ${wait!.blockers[0]}` : ""}`
          : shown === "charging"
            ? "charging"
            : state === "WAITING_LOAD"
              ? "loading"
              : state === "WAITING_UNLOAD"
                ? "unloading"
                : shown === "moving"
                  ? "moving"
                  : shown === "resting"
                    ? "stopped"
                    : shown === "idle"
                      ? "idle"
                      : (state.toLowerCase() || "unknown");
    items.push({
      id,
      x: place.point.x,
      y: place.point.y,
      label,
      short: [...label].length <= 4 ? label : (/\d+$/.exec(id)?.[0] ?? ""),
      heading,
      length: size(footprint["width"]),
      width: size(footprint["depth"]),
      shown,
      state,
      load,
      loaded: Object.keys(load).length > 0,
      fleet,
      fleetIndex: fleetIndex >= 0 ? fleetIndex : null,
      order: text(fields["order"]),
      node: text(fields["node"]),
      arc: moving && text(motion["from"]) !== null && text(motion["to"]) !== null
        ? { from: text(motion["from"])!, to: text(motion["to"])! }
        : null,
      wait,
      route,
      goal,
      holds: held.get(id) ?? [],
      speed: moving ? motionSpeed(fields["motion"], time) : null,
      battery: finite(fields["battery"]),
      batteryCapacity: finite(fields["battery_capacity"]),
      doing,
      line: `${label} · ${(shown === "moving" || shown === "resting") && goal !== null ? `to ${goal}` : doing}`,
    });
  }
  items.sort((a, b) => naturalCompare(a.id, b.id));
  const offsets = fanOut(items, SYMBOL_PX.vehicle);
  return items.map((item) => ({ ...item, offset: offsets.get(item.id)! }));
}

/** A line from each waiting vehicle to its first blocker that is in the frame. */
export function buildWaitLines(vehicles: readonly VehicleItem[]): WaitLine[] {
  const at = new Map(vehicles.map((v) => [v.id, v]));
  const lines: WaitLine[] = [];
  for (const vehicle of vehicles) {
    const blocker = vehicle.wait?.blockers.find((id) => at.has(id));
    if (blocker === undefined) continue;
    const to = at.get(blocker)!;
    lines.push({ agv: vehicle.id, blocker, from: { x: vehicle.x, y: vehicle.y }, to: { x: to.x, y: to.y } });
  }
  return lines;
}

/** Waits now, and the fleets' counters summed (null when no fleet records them). */
export function summarizeTraffic(
  time: number,
  vehicles: readonly VehicleItem[],
  state: Readonly<Record<string, Fields>>,
): TrafficSummary {
  const now = finiteTime(time);
  const waiting = vehicles.filter((v) => v.shown === "waiting");
  const total = (field: string): number | null => {
    let sum: number | null = null;
    for (const fields of Object.values(state)) {
      const n = fields["$kind"] === "fleet" ? finite(fields[field]) : null;
      if (n !== null) sum = (sum ?? 0) + n;
    }
    return sum;
  };
  return {
    waiting: waiting.length,
    longestWait: waiting.reduce((m, v) => Math.max(m, now - v.wait!.since), 0),
    waits: total("waits"),
    stuckWaits: total("stuck_waits"),
    deadlocks: total("deadlocks"),
  };
}
