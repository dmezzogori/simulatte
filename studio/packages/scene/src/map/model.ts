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

// The item types below are filled by the vehicles, facilities and overlay of the next task.
export interface VehicleItem {}
export interface FacilityItem {}
export interface WaitLine {}
export interface OrderItem {}
export interface TrafficSummary {
  waiting: number;
  longestWait: number;
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
