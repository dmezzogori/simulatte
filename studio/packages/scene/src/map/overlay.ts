import type { Point } from "../project";
import type { MapModel, VehicleItem, WaitLine } from "./model";

/** What a selection highlights on the map (spec section 5.5). */
export interface Overlay {
  /** Ids of the nodes, segments and zones the selected vehicle holds. */
  holds: readonly string[];
  /** The route still to travel, from the vehicle's position along the strips to its destination. */
  route: readonly Point[];
  waitLine: WaitLine | null;
  /** The origin-to-destination line of an order. */
  flow: readonly [Point, Point] | null;
  /** Ids of the other items to highlight: the vehicle of an order, the vehicles bound for a facility. */
  related: readonly string[];
}

const NONE: Overlay = { holds: [], route: [], waitLine: null, flow: null, related: [] };

/**
 * The part of the vehicle's route still ahead (spec section 6.2): found by node id. A vehicle on a node
 * continues from that node's index; a moving one from the node it is heading to, preceded by its position.
 * A vehicle that is not on its route has none. Nodes the layout does not know are skipped.
 */
function routeAhead(model: MapModel, vehicle: VehicleItem): Point[] {
  const nodes = vehicle.route?.nodes ?? [];
  const arc = vehicle.arc;
  let from = -1;
  if (arc) {
    const leg = nodes.findIndex((id, i) => id === arc.from && nodes[i + 1] === arc.to);
    from = leg >= 0 ? leg + 1 : nodes.indexOf(arc.to);
  } else if (vehicle.node !== null) from = nodes.indexOf(vehicle.node);
  if (from < 0) return [];
  const at = new Map(model.nodes.map((n) => [n.id, { x: n.x, y: n.y }]));
  const ahead = nodes.slice(arc ? from : from + 1).flatMap((id) => at.get(id) ?? []);
  return ahead.length ? [{ x: vehicle.x, y: vehicle.y }, ...ahead] : [];
}

/** The node and segment ids a list of holds covers: a zone id stands for its members, any other id for itself. */
export function expandHolds(model: MapModel, ids: readonly string[]): Set<string> {
  const covered = new Set<string>();
  for (const id of ids)
    for (const member of Object.hasOwn(model.zones, id) ? model.zones[id]! : [id]) covered.add(member);
  return covered;
}

/**
 * Where `scene.focus` centres on a map: a vehicle or a facility on its position, an order on its vehicle, else on
 * its origin facility. Null for any other id, or an order with neither in the frame.
 */
export function focusPoint(model: MapModel, id: string): Point | null {
  const order = model.orders.find((o) => o.id === id);
  const item =
    model.vehicles.find((v) => v.id === (order ? order.agv : id)) ??
    model.facilities.find((f) => f.id === (order ? order.origin : id));
  return item ? { x: item.x, y: item.y } : null;
}

/** The highlights of a selection; null for no selection, an unknown id or a kind the map does not select. */
export function overlayFor(model: MapModel, id: string | null): Overlay | null {
  if (id === null) return null;
  const flowOf = (orderId: string | null): readonly [Point, Point] | null => {
    const order = model.orders.find((o) => o.id === orderId);
    if (!order?.open) return null;
    const from = model.facilities.find((f) => f.id === order?.origin),
      to = model.facilities.find((f) => f.id === order?.destination);
    return from && to ? [{ x: from.x, y: from.y }, { x: to.x, y: to.y }] : null;
  };
  const vehicle = model.vehicles.find((v) => v.id === id);
  if (vehicle)
    return {
      ...NONE,
      holds: vehicle.holds,
      route: routeAhead(model, vehicle),
      waitLine: model.waitLines.find((line) => line.agv === id) ?? null,
      flow: flowOf(vehicle.order),
    };
  const order = model.orders.find((o) => o.id === id);
  if (order)
    return {
      ...NONE,
      flow: flowOf(id),
      related: model.vehicles.some((v) => v.id === order.agv) ? [order.agv!] : [],
    };
  const facility = model.facilities.find((f) => f.id === id);
  if (facility)
    return {
      ...NONE,
      related: model.vehicles
        .filter((v) => v.route && facility.nodes.includes(v.route.destination))
        .map((v) => v.id),
    };
  return null;
}
