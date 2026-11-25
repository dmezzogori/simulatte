import type { Graphics } from "pixi.js";
import type { Point } from "../project";
import type { Detail, MapModel } from "../map/model";
import type { Overlay } from "../map/overlay";
import type { FacilityItem, MapNode } from "../map/model";
import { BAY_PX, boundNodes, hitsFacility, type FacilityBox } from "./facility";
import { PALETTE } from "./palette";
import { drawWaitLine, reachPx } from "./traffic";
import { vehicleFrame } from "./vehicle";

/** Pixels of the route line and of its light casing; the radius of the dot at its destination. */
const ROUTE_PX = 4;
const CASING_PX = 1;
const ROUTE_END_PX = 4;
/** Pixels of the selected vehicle's wait line: thicker than the others, thinner than the route it may lie on. */
const SELECTED_WAIT_PX = 3;
/** Pixels of the flow arrow: its line, its head, its largest bow and the gap kept to the facilities. */
const FLOW_PX = 1.5;
const HEAD_LENGTH_PX = 10;
const HEAD_HALF_WIDTH_PX = 5;
const FLOW_BOW_PX = 70;
const FLOW_GAP_PX = 4;
/** Pixels of the selection outline and its gap to the element. */
const OUTLINE_PX = 2.5;
const OUTLINE_GAP_PX = 3;
/** Pixels the selection outline of a vehicle reaches beyond its wait ring or its body: labels go outside it. */
export const OUTLINE_REACH_PX = OUTLINE_GAP_PX + OUTLINE_PX;

export interface OverlayContext {
  map: MapModel;
  scale: number;
  detail: Detail;
  /** Where each facility drawn as a sprite is drawn. */
  boxes: ReadonlyMap<string, FacilityBox>;
}

/**
 * The selected vehicle's route ahead (spec section 5.5): a line along the strips from outside its symbol to a dot
 * at its destination. It is drawn just below the vehicles, so that it never hides one standing on it.
 */
export function drawRouteAhead(g: Graphics, overlay: Overlay, selected: string, context: OverlayContext): void {
  const { map, scale, detail } = context;
  const vehicle = map.vehicles.find((v) => v.id === selected);
  if (!vehicle || overlay.route.length < 2) return;
  const frame = vehicleFrame(vehicle, scale, detail),
    points = [{ x: frame.x, y: frame.y }, ...overlay.route.slice(1)];
  drawRoute(g, trimStart(points, reachPx(vehicle, scale, detail) / scale), scale);
}

/**
 * The rest of the selection overlay (spec section 5.5), above the vehicles: the selected vehicle's wait line
 * thicker, the flow of its order or of the selected order, and a ring around the selected vehicle and the related
 * ones. What the vehicle holds is tinted with the held floor, below the sprites; a facility's outline is drawn
 * below its sprite (`outlineFacility`).
 */
export function drawOverlay(g: Graphics, overlay: Overlay, selected: string, context: OverlayContext): void {
  const { map, scale, detail } = context;
  const vehicle = map.vehicles.find((v) => v.id === selected);
  if (overlay.waitLine) drawWaitLine(g, overlay.waitLine, map, scale, detail, SELECTED_WAIT_PX);
  if (overlay.flow) {
    // the flow's ends are the order's facilities, where they are drawn
    const order = map.orders.find((o) => o.id === (vehicle ? vehicle.order : selected));
    drawFlow(g, overlay.flow, context.boxes.get(order?.origin ?? ""), context.boxes.get(order?.destination ?? ""), scale);
  }
  for (const id of [selected, ...overlay.related]) outline(g, id, context);
}

/** The polyline without its first `distance` metres. */
function trimStart(points: readonly Point[], distance: number): Point[] {
  let left = distance;
  for (let i = 1; i < points.length; i++) {
    const a = points[i - 1]!,
      b = points[i]!,
      length = Math.hypot(b.x - a.x, b.y - a.y);
    if (length > left) {
      const t = left / length;
      return [{ x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t }, ...points.slice(i)];
    }
    left -= length;
  }
  return [];
}

function drawRoute(g: Graphics, points: readonly Point[], scale: number): void {
  if (points.length < 2) return;
  const px = 1 / scale,
    end = points.at(-1)!;
  const path = (): void => {
    g.moveTo(points[0]!.x, points[0]!.y);
    for (const p of points.slice(1)) g.lineTo(p.x, p.y);
  };
  path();
  g.stroke({ color: PALETTE.outline, width: (ROUTE_PX + 2 * CASING_PX) * px, cap: "round", join: "round" });
  path();
  g.stroke({ color: PALETTE.route, width: ROUTE_PX * px, cap: "round", join: "round" });
  g.circle(end.x, end.y, ROUTE_END_PX * px)
    .fill(PALETTE.route)
    .stroke({ color: PALETTE.outline, width: CASING_PX * px });
}

/** The box grown by `grow` metres on every side. */
const grown = (box: FacilityBox | undefined, grow: number): FacilityBox | null =>
  box ? { ...box, hw: box.hw + grow, hh: box.hh + grow } : null;

/**
 * The flow of an order (M12): a thin dark curve from its origin facility to its destination, ending in an
 * arrowhead. It bows to the right of the direction of travel, so it leaves the lanes between the two facilities
 * free and two opposite flows do not cover each other.
 */
function drawFlow(
  g: Graphics,
  flow: readonly [Point, Point],
  origin: FacilityBox | undefined,
  destination: FacilityBox | undefined,
  scale: number,
): void {
  const px = 1 / scale,
    gap = FLOW_GAP_PX * px;
  const fromBox = grown(origin, gap),
    toBox = grown(destination, gap);
  const from = fromBox ?? flow[0],
    to = toBox ?? flow[1];
  const dx = to.x - from.x,
    dy = to.y - from.y,
    length = Math.hypot(dx, dy);
  if (!length) return;
  const bow = Math.min(0.15 * length, FLOW_BOW_PX * px),
    control = { x: (from.x + to.x) / 2 + (dy / length) * 2 * bow, y: (from.y + to.y) / 2 - (dx / length) * 2 * bow };
  const curve: Point[] = [];
  for (let i = 0; i <= 64; i++) {
    const t = i / 64,
      u = 1 - t;
    const p = {
      x: u * u * from.x + 2 * u * t * control.x + t * t * to.x,
      y: u * u * from.y + 2 * u * t * control.y + t * t * to.y,
    };
    if ((!fromBox || !hitsFacility(fromBox, p)) && (!toBox || !hitsFacility(toBox, p))) curve.push(p);
  }
  if (curve.length < 2) return;
  const tip = curve.at(-1)!,
    before = curve.at(-2)!,
    along = Math.hypot(tip.x - before.x, tip.y - before.y),
    ux = (tip.x - before.x) / along,
    uy = (tip.y - before.y) / along,
    head = HEAD_LENGTH_PX * px,
    half = HEAD_HALF_WIDTH_PX * px;
  // the line stops inside the head, so that its end does not show past the tip
  const shaft = trimStart([...curve].reverse(), head * 0.6).reverse();
  if (shaft.length > 1) {
    g.moveTo(shaft[0]!.x, shaft[0]!.y);
    for (const p of shaft.slice(1)) g.lineTo(p.x, p.y);
    g.stroke({ color: PALETTE.flow, width: FLOW_PX * px, cap: "round", join: "round" });
  }
  g.poly([
    tip.x,
    tip.y,
    tip.x - ux * head - uy * half,
    tip.y - uy * head + ux * half,
    tip.x - ux * head + uy * half,
    tip.y - uy * head - ux * half,
  ]).fill(PALETTE.flow);
}

/** The selection outline of a vehicle drawn as a sprite: a ring outside its wait ring or its body. */
function outline(g: Graphics, id: string, context: OverlayContext): void {
  const { map, scale, detail } = context,
    vehicle = map.vehicles.find((v) => v.id === id);
  if (!vehicle) return;
  const frame = vehicleFrame(vehicle, scale, detail);
  g.circle(frame.x, frame.y, (reachPx(vehicle, scale, detail) + OUTLINE_GAP_PX) / scale).stroke({
    color: PALETTE.selected,
    width: OUTLINE_PX / scale,
  });
}

/** The facility's box grown to the outside of its selection outline: its name and count go beyond it. */
export const outlinedBox = (box: FacilityBox, scale: number): FacilityBox =>
  grown(box, OUTLINE_REACH_PX / scale)!;

/**
 * The selection outline of a facility drawn as a sprite: a rectangle around its box, drawn before the facility so
 * that its badge stays on top.
 */
export function outlineFacility(g: Graphics, box: FacilityBox, scale: number): void {
  const ring = grown(box, (OUTLINE_GAP_PX + OUTLINE_PX / 2) / scale)!,
    c = Math.cos(ring.rotation),
    s = Math.sin(ring.rotation);
  g.poly(
    [
      [-1, -1],
      [1, -1],
      [1, 1],
      [-1, 1],
    ].flatMap(([a, b]) => [ring.x + a! * ring.hw * c - b! * ring.hh * s, ring.y + a! * ring.hw * s + b! * ring.hh * c]),
  ).stroke({ color: PALETTE.selected, width: OUTLINE_PX / scale, join: "round" });
}

/** The selection outline of a facility's bays (spec section 5.5): a square around each node it is bound to. */
export function outlineBays(
  g: Graphics,
  facility: FacilityItem,
  nodes: ReadonlyMap<string, MapNode>,
  scale: number,
): void {
  const half = (BAY_PX / 2 + OUTLINE_GAP_PX) / scale;
  for (const bay of boundNodes(facility, nodes))
    g.rect(bay.x - half, bay.y - half, 2 * half, 2 * half).stroke({
      color: PALETTE.selected,
      width: OUTLINE_PX / scale,
      join: "round",
    });
}
