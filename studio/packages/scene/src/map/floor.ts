import type { Point } from "../project";
import { SYMBOL_PX, type MapModel, type MapNode, type StaticMap, type StaticStrip } from "./model";

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

type StaticFloor = NonNullable<StaticMap["floor"]>;

/** The geometry of the floor: what the layout, the positions and the vehicles' footprints decide, not a frame. */
function staticFloor(input: FloorInput, graph: Fields, arcs: readonly unknown[]): StaticFloor {
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
  const strips: StaticStrip[] = [];
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
    });
  }

  const nodes: Omit<MapNode, "held">[] = [];
  for (const raw of Array.isArray(graph["nodes"]) ? graph["nodes"] : []) {
    const id = String(object(raw)["id"]),
      at = input.positions.get(id);
    if (at) nodes.push({ id, x: at.x, y: at.y });
  }
  return { laneWidth, threshold, strips, nodes };
}

/** Whether a cached floor still holds: every node it placed is where it was, and no other graph node is placed now. */
function floorHolds(floor: StaticFloor, graph: Fields, positions: ReadonlyMap<string, Point>): boolean {
  for (const node of floor.nodes) {
    const at = positions.get(node.id);
    if (!at || at.x !== node.x || at.y !== node.y) return false;
  }
  let count = 0;
  for (const raw of Array.isArray(graph["nodes"]) ? graph["nodes"] : [])
    if (positions.has(String(object(raw)["id"]))) count++;
  return count === floor.nodes.length;
}

/**
 * The floor of the map: strips, nodes, sizes and held resources. Null without arcs. The geometry is computed once per
 * layout when a `cache` for it is given; `held` and the zones are read from every frame.
 */
export function buildFloor(input: FloorInput, cache?: StaticMap): MapModel | null {
  const graph = object(object(input.layout["network"])["graph"]);
  const arcs = Array.isArray(graph["arcs"]) ? graph["arcs"] : [];
  if (!arcs.length) return null;
  let floor = cache?.floor;
  if (!floor || !floorHolds(floor, graph, input.positions)) {
    floor = staticFloor(input, graph, arcs);
    if (cache) cache.floor = floor;
  }

  const heldIds = new Set<string>();
  // no prototype: a zone may be named "__proto__", and a hold id such as "constructor" is not a zone
  const zones: Record<string, readonly string[]> = Object.create(null);
  for (const [id, fields] of Object.entries(input.state)) {
    const kind = fields["$kind"];
    if (kind === "segment" && (held(fields["forward"]) || held(fields["backward"]))) heldIds.add(id);
    else if (kind === "node" && held(fields["reserved_by"])) heldIds.add(id);
    else if (kind === "zone") {
      zones[id] = ids(fields["members"]);
      if (held(fields["reserved_by"])) for (const member of zones[id]) heldIds.add(member);
    }
  }

  const fleets = new Set<string>();
  for (const id of input.vehicles) {
    const fleet = input.state[id]?.["fleet"];
    if (typeof fleet === "string") fleets.add(fleet);
  }
  return {
    time: input.time,
    laneWidth: floor.laneWidth,
    threshold: floor.threshold,
    strips: floor.strips.map((strip) => ({ ...strip, held: heldIds.has(strip.id) })),
    nodes: floor.nodes.map((node) => ({ ...node, held: heldIds.has(node.id) })),
    zones,
    vehicles: [],
    facilities: [],
    waitLines: [],
    orders: [],
    fleets: [...fleets].sort((x, y) => (x < y ? -1 : x > y ? 1 : 0)),
    traffic: { waiting: 0, longestWait: 0, waits: null, stuckWaits: null, deadlocks: null },
  };
}
