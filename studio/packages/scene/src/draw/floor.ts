import type { Graphics } from "pixi.js";
import type { Point } from "../project";
import { SYMBOL_PX, type Detail, type Strip } from "../map/model";
import { PALETTE } from "./palette";

/** The part of the world the canvas shows, in metres. */
export interface View {
  left: number;
  right: number;
  bottom: number;
  top: number;
}

/** Pixels between the chevrons of a one-way strip. */
const CHEVRON_SPACING = 48;

/** The background grid, as the scene draws it without a map. */
export function drawGrid(g: Graphics, scale: number, view: View): void {
  const spacing = 10 ** Math.ceil(Math.log10(35 / scale));
  for (let gx = Math.ceil(view.left / spacing) * spacing; gx <= view.right; gx += spacing)
    g.moveTo(gx, view.bottom).lineTo(gx, view.top);
  for (let gy = Math.ceil(view.bottom / spacing) * spacing; gy <= view.top; gy += spacing)
    g.moveTo(view.left, gy).lineTo(view.right, gy);
  g.stroke({ color: PALETTE.grid, width: 1 / scale });
}

/** The drawn width of a strip in pixels: never thinner than `SYMBOL_PX.strip` at symbol level (M26). */
export const stripPx = (strip: Strip, scale: number, detail: Detail): number =>
  detail === "symbol" ? Math.max(strip.width * scale, SYMBOL_PX.strip) : strip.width * scale;

/** The strips of the floor and their chevrons (spec section 5.3). */
export function drawFloor(g: Graphics, strips: readonly Strip[], scale: number, detail: Detail): void {
  for (const strip of strips)
    g.moveTo(strip.a.x, strip.a.y)
      .lineTo(strip.b.x, strip.b.y)
      .stroke({ color: PALETTE.strip, width: stripPx(strip, scale, detail) / scale, cap: "round" });
  for (const strip of strips) {
    const dx = strip.b.x - strip.a.x,
      dy = strip.b.y - strip.a.y,
      length = Math.hypot(dx, dy);
    if (!length) continue;
    const along = { x: dx / length, y: dy / length },
      px = stripPx(strip, scale, detail),
      size = Math.min(Math.max(px * 0.5, 5), 8) / scale,
      lengthPx = length * scale;
    if (strip.oneWay) {
      // About CHEVRON_SPACING apart, evenly spread, the outer ones half an interval from the ends.
      const count = Math.max(1, Math.floor(lengthPx / CHEVRON_SPACING));
      for (let i = 0; i < count; i++) {
        const t = ((i + 0.5) / count) * length;
        chevron(g, { x: strip.a.x + along.x * t, y: strip.a.y + along.y * t }, along, size);
      }
    } else if (detail === "true") {
      // One chevron at each end, pointing out of the strip: it is travelled both ways.
      const inset = Math.min(lengthPx / 4, Math.max(px, 16)) / scale;
      chevron(g, { x: strip.a.x + along.x * inset, y: strip.a.y + along.y * inset }, { x: -along.x, y: -along.y }, size);
      chevron(g, { x: strip.b.x - along.x * inset, y: strip.b.y - along.y * inset }, along, size);
    }
  }
  g.stroke({ color: PALETTE.chevron, width: 1.5 / scale, cap: "round", join: "round" });
}

/** An open arrowhead centred on `at`, pointing along the unit vector `to`, `size` metres long. */
function chevron(g: Graphics, at: Point, to: Point, size: number): void {
  const back = size / 2,
    wing = size * 0.6;
  g.moveTo(at.x - to.x * back - to.y * wing, at.y - to.y * back + to.x * wing)
    .lineTo(at.x + to.x * back, at.y + to.y * back)
    .lineTo(at.x - to.x * back + to.y * wing, at.y - to.y * back - to.x * wing);
}
