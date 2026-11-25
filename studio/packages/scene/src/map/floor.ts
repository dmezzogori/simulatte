import type { Point } from "../project";
import { SYMBOL_PX, type MapModel, type MapNode, type Strip } from "./model";

type Fields = Record<string, unknown>;
const object = (v: unknown): Fields =>
  v !== null && typeof v === "object" && !Array.isArray(v) ? (v as Fields) : {};
const positive = (v: unknown, fallback: number): number =>
  typeof v === "number" && Number.isFinite(v) && v > 0 ? v : fallback;
const ids = (v: unknown): string[] =>
  Array.isArray(v) ? v.filter((id): id is string => typeof id === "string") : [];
const held = (v: unknown): boolean => ids(v).length > 0;
/** Code-unit order, as `network.py` joins a segment's two node ids. */
const stripId = (a: string, b: string): string => (a < b ? `${a}~${b}` : `${b}~${a}`);

export interface FloorInput {
  time: number;
  layout: Fields;
  state: Readonly<Record<string, Fields>>;
  /** Positions of every placed entity, node ids included. */
  positions: ReadonlyMap<string, Point>;
  /** Ids of the vehicles of the initial state: their footprints size the lane. */
  vehicles: readonly string[];
}

/** The floor of the map: strips, nodes, sizes and held resources. Null without arcs. */
export function buildFloor(input: FloorInput): MapModel | null {
  const graph = object(object(input.layout["network"])["graph"]);
  const arcs = Array.isArray(graph["arcs"]) ? graph["arcs"] : [];
  if (!arcs.length) return null;
  const placements = object(object(input.layout["resolved"])["placements"]);
  let depth = 0,
    length = 0;
  for (const id of input.vehicles) {
    const footprint = object(object(placements[id])["footprint"]);
    depth = Math.max(depth, positive(footprint["depth"], 1));
    length = Math.max(length, positive(footprint["width"], 1));
  }
  const laneWidth = depth ? depth + 2 * 0.2 * depth : 1;
  const threshold = SYMBOL_PX.vehicle / (length || 1);

  const heldIds = new Set<string>();
  const zones: Record<string, readonly string[]> = {};
  for (const [id, fields] of Object.entries(input.state)) {
    const kind = fields["$kind"];
    if (kind === "segment" && (held(fields["forward"]) || held(fields["backward"]))) heldIds.add(id);
    else if (kind === "node" && held(fields["reserved_by"])) heldIds.add(id);
    else if (kind === "zone") {
      zones[id] = ids(fields["members"]);
      if (held(fields["reserved_by"])) for (const member of zones[id]) heldIds.add(member);
    }
  }

  // Both directions between two nodes make one strip; its first arc fixes the direction of a one-way one.
  const seen = new Map<string, { source: string; target: string; both: boolean; passing: boolean }>();
  for (const raw of arcs) {
    const arc = object(raw),
      source = String(arc["source"]),
      target = String(arc["target"]),
      id = stripId(source, target),
      known = seen.get(id);
    const passing = arc["passing"] === true;
    if (!known) seen.set(id, { source, target, both: false, passing });
    else {
      if (known.source !== source) known.both = true;
      known.passing ||= passing;
    }
  }
  const strips: Strip[] = [];
  for (const [id, strip] of seen) {
    const a = input.positions.get(strip.source),
      b = input.positions.get(strip.target);
    if (!a || !b) continue;
    const oneWay = !strip.both;
    // A two-way strip has no direction: its ends follow its id.
    const [from, to] = oneWay || strip.source < strip.target ? [a, b] : [b, a];
    strips.push({
      id,
      a: { x: from.x, y: from.y },
      b: { x: to.x, y: to.y },
      width: strip.passing ? 2 * laneWidth : laneWidth,
      oneWay,
      passing: strip.passing,
      held: heldIds.has(id),
    });
  }

  const nodes: MapNode[] = [];
  for (const raw of Array.isArray(graph["nodes"]) ? graph["nodes"] : []) {
    const id = String(object(raw)["id"]),
      at = input.positions.get(id);
    if (at) nodes.push({ id, x: at.x, y: at.y, held: heldIds.has(id) });
  }

  const fleets = new Set<string>();
  for (const id of input.vehicles) {
    const fleet = input.state[id]?.["fleet"];
    if (typeof fleet === "string") fleets.add(fleet);
  }
  return {
    time: input.time,
    laneWidth,
    threshold,
    strips,
    nodes,
    zones,
    vehicles: [],
    facilities: [],
    waitLines: [],
    orders: [],
    fleets: [...fleets].sort((x, y) => (x < y ? -1 : x > y ? 1 : 0)),
    traffic: { waiting: 0, longestWait: 0, waits: null, stuckWaits: null, deadlocks: null },
  };
}
