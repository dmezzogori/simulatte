import type { Point } from "../project";

/** How the scene draws vehicles and facilities: a symbol of fixed pixel size, or the true footprint. */
export type Detail = "symbol" | "true";

/** One lane of the floor: the arcs between two nodes. A one-way strip runs from `a` to `b`. */
export interface Strip {
  id: string;
  a: Point;
  b: Point;
  /** Nominal width in metres. */
  width: number;
  oneWay: boolean;
  passing: boolean;
  /** A reservation holds it (spec section 5.5); not part of the static layer. */
  held: boolean;
}
export interface MapNode extends Point {
  id: string;
  held: boolean;
}

/** The state a vehicle is drawn in: the first row of the table of spec section 5.4 that applies. */
export type Shown =
  | "stranded"
  | "waiting"
  | "moving"
  | "working"
  | "idle"
  | "charging"
  | "resting"
  | "unknown";
/** A vehicle's recorded wait (spec section 6.2). */
export interface Wait {
  since: number;
  node: string;
  resources: readonly string[];
  blockers: readonly string[];
}
/** A vehicle's recorded route (spec section 6.2). */
export interface Route {
  nodes: readonly string[];
  destination: string;
  reason: string;
}
export interface VehicleItem extends Point {
  id: string;
  label: string;
  /** The label when it has at most four characters, else the trailing digits of the id, else "". */
  short: string;
  /** Radians, counter-clockwise from east, in the plane of the layout. */
  heading: number;
  /** Metres along the heading. */
  length: number;
  /** Metres across it. */
  width: number;
  /** The footprint came from the vehicle's type (the layout target carries `size`), not from the default. */
  sized: boolean;
  /** The heading is known: an arc under way fixes it, else a recorded finite `heading` (else `heading` is 0). */
  headingKnown: boolean;
  shown: Shown;
  /** The recorded state. */
  state: string;
  load: Readonly<Record<string, number>>;
  loaded: boolean;
  fleet: string | null;
  /** Index in `MapModel.fleets`; null with fewer than two fleets. */
  fleetIndex: number | null;
  order: string | null;
  /** The recorded node the vehicle is at; null when none is recorded. */
  node: string | null;
  /** The arc under way, by node id; null without a motion. */
  arc: { from: string; to: string } | null;
  wait: Wait | null;
  route: Route | null;
  /** The label of the facility bound to the route's destination, else that node id, else null. */
  goal: string | null;
  /** Ids of the nodes, segments and zones whose holder lists contain the vehicle. */
  holds: readonly string[];
  /** Metres per second, evaluated from the recorded motion; null when there is none or it cannot be. */
  speed: number | null;
  battery: number | null;
  batteryCapacity: number | null;
  /** Pixels: the fan-out of vehicles sharing a position, at symbol level. */
  offset: Point;
  /** What the vehicle is doing, in words. */
  doing: string;
  /** The map label at true-size level. */
  line: string;
}
export interface FacilityItem extends Point {
  id: string;
  kind: "warehouse" | "parking_area" | "charging_station";
  label: string;
  /** Metres. */
  width: number;
  depth: number;
  /** Radians. */
  rotation: number;
  /** The nodes it is bound to, in binding order, each once. */
  nodes: readonly string[];
  /** Stock or occupancy as shown under the name; "" when the capacity is not recorded. */
  count: string;
  /** Stock per SKU of a warehouse; empty for the others and when the inventory is not recorded. */
  stock: Readonly<Record<string, number>>;
  /** Orders waiting at it: its own, with status PENDING. */
  badge: number;
  /** Pixels: the fan-out of facilities sharing a position, at symbol level. */
  offset: Point;
}
/** A vehicle's dashed line to the first of its blockers that is in the frame. */
export interface WaitLine {
  agv: string;
  blocker: string;
  from: Point;
  to: Point;
}
export interface OrderItem {
  id: string;
  sku: string;
  quantity: number;
  origin: string;
  destination: string;
  status: string;
  agv: string | null;
  picked: boolean;
  createdAt: number;
  /** False once the order is `COMPLETED`, `FAILED` or `CANCELLED`: it may stay in the state a while. */
  open: boolean;
}
export interface TrafficSummary {
  /** Vehicles shown as waiting; a stranded vehicle with a stale wait is not counted. */
  waiting: number;
  /** The longest of their waits so far, in seconds. */
  longestWait: number;
  /** Counters summed over the fleets; null when no fleet records them. */
  waits: number | null;
  stuckWaits: number | null;
  deadlocks: number | null;
}

export interface MapModel {
  time: number;
  /** Metres, nominal (spec section 5.3). */
  laneWidth: number;
  /** Pixels per metre at which true size begins (spec section 5.2). */
  threshold: number;
  strips: readonly Strip[];
  nodes: readonly MapNode[];
  /** The members (node and segment ids) of every zone of the frame, held or not, by zone id. */
  zones: Readonly<Record<string, readonly string[]>>;
  vehicles: readonly VehicleItem[];
  facilities: readonly FacilityItem[];
  waitLines: readonly WaitLine[];
  orders: readonly OrderItem[];
  /** The distinct `fleet` values of the vehicles, sorted. */
  fleets: readonly string[];
  traffic: TrafficSummary;
}

/** Pixel sizes of the symbols and the thinnest strip at symbol level. */
export const SYMBOL_PX = { vehicle: 22, facility: 36, strip: 10 } as const;

/** True size begins at the threshold, so a symbol never jumps in size at the switch. */
export const detailAt = (model: MapModel, scale: number): Detail =>
  scale >= model.threshold ? "true" : "symbol";

/** Code-unit order with digit runs compared as numbers, so `AGV-2` comes before `AGV-10`. */
export function naturalCompare(a: string, b: string): number {
  const x = a.match(/\d+|\D+/g) ?? [],
    y = b.match(/\d+|\D+/g) ?? [];
  for (let i = 0; i < Math.min(x.length, y.length); i++) {
    const p = x[i]!,
      q = y[i]!;
    if (p === q) continue;
    if (/^\d/.test(p) && /^\d/.test(q)) {
      const m = p.replace(/^0+(?=\d)/, ""),
        n = q.replace(/^0+(?=\d)/, "");
      if (m.length !== n.length) return m.length - n.length;
      if (m !== n) return m < n ? -1 : 1;
    } else return p < q ? -1 : 1;
  }
  return x.length - y.length || (a < b ? -1 : a > b ? 1 : 0);
}

/**
 * Items sharing a position are spread along x, in natural id order and centred, by `symbolPx` plus 2 px
 * (spec section 5.2). Returns the pixel offset of each id.
 */
export function fanOut(
  items: readonly { id: string; x: number; y: number }[],
  symbolPx: number,
): Map<string, Point> {
  const groups = new Map<string, string[]>();
  for (const item of items) {
    const key = `${item.x.toFixed(6)}|${item.y.toFixed(6)}`;
    (groups.get(key) ?? groups.set(key, []).get(key)!).push(item.id);
  }
  const offsets = new Map<string, Point>();
  for (const group of groups.values()) {
    group.sort(naturalCompare);
    group.forEach((id, i) => offsets.set(id, { x: (i - (group.length - 1) / 2) * (symbolPx + 2), y: 0 }));
  }
  return offsets;
}
