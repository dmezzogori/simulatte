import type { Graphics } from "pixi.js";
import type { Point } from "../project";
import { SYMBOL_PX, type Detail, type MapModel, type VehicleItem, type WaitLine } from "../map/model";
import { stripPx } from "./floor";
import { PALETTE } from "./palette";
import { ringPx, vehicleFrame } from "./vehicle";

/** Pixels a held node's disc reaches beyond the half-width of its widest strip. */
const NODE_REACH_PX = 2;
/** Pixels of a wait line: its width, and its dashes and gaps. */
export const WAIT_LINE_PX = 2;
const DASH_PX = 5;
const GAP_PX = 4;

const key = (p: Point): string => `${p.x}|${p.y}`;

/**
 * The held floor (M10): every held strip over its drawn width and every held node as a disc, in one translucent
 * colour; the ids in `selected` (what the selected vehicle holds) in a stronger one. Each is drawn once.
 */
export function drawHeld(
  g: Graphics,
  map: MapModel,
  scale: number,
  detail: Detail,
  selected: ReadonlySet<string>,
): void {
  const widest = new Map<string, number>();
  for (const strip of map.strips) {
    const px = stripPx(strip, scale, detail);
    for (const end of [strip.a, strip.b]) widest.set(key(end), Math.max(widest.get(key(end)) ?? 0, px));
  }
  const alpha = (id: string, held: boolean): number | null =>
    selected.has(id) ? PALETTE.heldSelectedAlpha : held ? PALETTE.heldAlpha : null;
  for (const strip of map.strips) {
    const a = alpha(strip.id, strip.held);
    if (a === null) continue;
    // butt ends: the tint stops at a node it does not hold
    g.moveTo(strip.a.x, strip.a.y)
      .lineTo(strip.b.x, strip.b.y)
      .stroke({ color: PALETTE.held, alpha: a, width: stripPx(strip, scale, detail) / scale, cap: "butt" });
  }
  for (const node of map.nodes) {
    const a = alpha(node.id, node.held);
    if (a === null) continue;
    // a node on no strip gets the width of a lane
    const lane = detail === "symbol" ? Math.max(map.laneWidth * scale, SYMBOL_PX.strip) : map.laneWidth * scale,
      radius = (widest.get(key(node)) ?? lane) / 2;
    g.circle(node.x, node.y, (radius + NODE_REACH_PX) / scale).fill({ color: PALETTE.held, alpha: a });
  }
}

/** Pixels from a vehicle's centre to where a line to or from it starts: outside its wait ring or its body. */
export function reachPx(vehicle: VehicleItem, scale: number, detail: Detail): number {
  const frame = vehicleFrame(vehicle, scale, detail);
  return vehicle.shown === "waiting" ? ringPx(frame, scale) : (Math.hypot(frame.length, frame.width) / 2) * scale + 2;
}

/**
 * A wait line (M11): dashed amber, from outside the waiting vehicle's ring to the edge of its blocker, both where
 * they are drawn (with the fan-out of the symbol level). `widthPx` is thicker for the selected vehicle.
 */
export function drawWaitLine(
  g: Graphics,
  line: WaitLine,
  map: MapModel,
  scale: number,
  detail: Detail,
  widthPx = WAIT_LINE_PX,
): void {
  const end = (id: string, at: Point): { at: Point; reach: number } => {
    const vehicle = map.vehicles.find((v) => v.id === id);
    if (!vehicle) return { at, reach: 0 };
    const frame = vehicleFrame(vehicle, scale, detail);
    return { at: { x: frame.x, y: frame.y }, reach: reachPx(vehicle, scale, detail) / scale };
  };
  const from = end(line.agv, line.from),
    to = end(line.blocker, line.to);
  const dx = to.at.x - from.at.x,
    dy = to.at.y - from.at.y,
    length = Math.hypot(dx, dy),
    start = from.reach,
    stop = length - to.reach;
  if (stop <= start) return;
  const ux = dx / length,
    uy = dy / length,
    dash = DASH_PX / scale,
    gap = GAP_PX / scale;
  for (let d = start; d < stop; d += dash + gap) {
    const e = Math.min(stop, d + dash);
    g.moveTo(from.at.x + ux * d, from.at.y + uy * d).lineTo(from.at.x + ux * e, from.at.y + uy * e);
  }
  g.stroke({ color: PALETTE.waitLine, width: widthPx / scale, cap: "butt" });
}
