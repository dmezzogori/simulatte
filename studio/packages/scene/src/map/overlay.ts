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

/** The part of the vehicle's route that is still ahead, starting at the vehicle. */
function routeAhead(model: MapModel, vehicle: VehicleItem): Point[] {
  const at = new Map(model.nodes.map((n) => [n.id, { x: n.x, y: n.y }]));
  const path = (vehicle.route?.nodes ?? []).flatMap((id) => at.get(id) ?? []);
  const here = { x: vehicle.x, y: vehicle.y };
  // The route segment nearest the vehicle is the one it is on; the first of equals, so that a vehicle
  // standing on a node continues from the segment that ends there.
  let best = -1,
    bestDistance = Infinity;
  for (let i = 0; i + 1 < path.length; i++) {
    const a = path[i]!,
      b = path[i + 1]!;
    const dx = b.x - a.x,
      dy = b.y - a.y,
      length = dx * dx + dy * dy;
    const t = length ? Math.max(0, Math.min(1, ((here.x - a.x) * dx + (here.y - a.y) * dy) / length)) : 0;
    const distance = Math.hypot(here.x - (a.x + t * dx), here.y - (a.y + t * dy));
    if (distance < bestDistance - 1e-9) [best, bestDistance] = [i, distance];
  }
  if (best < 0) return [];
  const ahead = [here, ...path.slice(best + 1)];
  // Drop a node the vehicle stands on, so the line has no zero-length first piece.
  const points = ahead.filter((p, i) => i === 0 || p.x !== ahead[i - 1]!.x || p.y !== ahead[i - 1]!.y);
  return points.length > 1 ? points : [];
}

/** The highlights of a selection; null for no selection, an unknown id or a kind the map does not select. */
export function overlayFor(model: MapModel, id: string | null): Overlay | null {
  if (id === null) return null;
  const flowOf = (orderId: string | null): readonly [Point, Point] | null => {
    const order = model.orders.find((o) => o.id === orderId);
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
