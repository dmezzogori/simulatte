import type { Graphics } from "pixi.js";
import type { Point } from "../project";
import { SYMBOL_PX, type Detail, type FacilityItem, type MapModel, type MapNode } from "../map/model";
import { PALETTE } from "./palette";
import { bolt } from "./vehicle";

/** Pixels of the white square drawn on each node a warehouse is bound to. */
export const BAY_PX = 10;
/** Pixels kept between a warehouse symbol and the bay squares of its own nodes. */
const CLEAR_PX = 4;
/** Pixels of a warehouse symbol across the direction to its first bay (the long side is `SYMBOL_PX.facility`). */
const WAREHOUSE_SHORT_PX = 28;
/** Pixels of the height of an empty parking or charging symbol. */
const HOLDER_PX = 30;
/** Pixels between the vehicles standing on a parking or charging symbol and its outline, along the row. */
const HOLDER_PAD_PX = 4;
/** Pixels from the node to the bottom of such a symbol with vehicles on it, and of the row of its mark. */
const HOLDER_REACH_PX = 14;
const MARK_ROW_PX = 18;
/** Pixels between a box and its texts, and of a line of text. */
const TEXT_GAP_PX = 2;
const LINE_PX = 14;
/** Pixels between the shelf lines of a warehouse. */
const SHELF_PX = 6;
/** Pixels of the radius of the badge of waiting orders. */
export const BADGE_PX = 7;

/** Where a facility is drawn, in metres: a rectangle `2 hw` by `2 hh` centred on (x, y), turned by `rotation`. */
export interface FacilityBox extends Point {
  hw: number;
  hh: number;
  rotation: number;
  /** The centre of the "P" or the bolt, and its height. */
  mark: Point;
  markSize: number;
  /** Symbol level: vehicles fanned out on the facility's position stand in the symbol, below its mark. */
  holds: boolean;
}

const key = (p: Point): string => `${p.x.toFixed(6)}|${p.y.toFixed(6)}`;

/** The nodes of the map a facility is bound to, in binding order. */
export const boundNodes = (facility: FacilityItem, nodes: ReadonlyMap<string, MapNode>): MapNode[] =>
  facility.nodes.flatMap((id) => {
    const node = nodes.get(id);
    return node ? [node] : [];
  });

/**
 * The rectangle a facility is drawn in. True-size level: its footprint. Symbol level: a symbol of fixed pixel
 * size at its position plus its fan-out offset; a warehouse symbol that would cover one of its bay squares is
 * moved along the direction from that node to its centre until it is `CLEAR_PX` clear; a parking area or
 * charging station that is not fanned out grows to hold the vehicles fanned out on its position, its mark in a
 * row above them.
 */
export function facilityBox(
  facility: FacilityItem,
  map: MapModel,
  nodes: ReadonlyMap<string, MapNode>,
  scale: number,
  detail: Detail,
): FacilityBox {
  if (detail === "true") {
    const size = Math.min(facility.width, facility.depth) * 0.25,
      c = Math.cos(facility.rotation),
      s = Math.sin(facility.rotation),
      a = -facility.width / 2 + size * 0.7,
      b = -facility.depth / 2 + size * 0.7;
    return {
      x: facility.x,
      y: facility.y,
      hw: facility.width / 2,
      hh: facility.depth / 2,
      rotation: facility.rotation,
      mark: { x: facility.x + a * c - b * s, y: facility.y + a * s + b * c },
      markSize: size,
      holds: false,
    };
  }
  const px = 1 / scale;
  // the centre in pixels, x right and y up, relative to the true position
  let cx = facility.offset.x,
    cy = -facility.offset.y,
    hw: number,
    hh: number,
    markY = 0,
    markSize = 16,
    holds = false;
  if (facility.kind === "warehouse") {
    const bays = boundNodes(facility, nodes);
    const first = bays[0];
    const across = first ? Math.abs(facility.x - first.x) >= Math.abs(facility.y - first.y) : false;
    hw = (across ? WAREHOUSE_SHORT_PX : SYMBOL_PX.facility) / 2;
    hh = (across ? SYMBOL_PX.facility : WAREHOUSE_SHORT_PX) / 2;
    const reachX = hw + BAY_PX / 2 + CLEAR_PX,
      reachY = hh + BAY_PX / 2 + CLEAR_PX;
    for (const bay of bays) {
      const rx = (facility.x - bay.x) * scale + cx,
        ry = (facility.y - bay.y) * scale + cy;
      if (Math.abs(rx) >= reachX || Math.abs(ry) >= reachY) continue;
      const length = Math.hypot(rx, ry),
        dx = length > 0 ? rx / length : 0,
        dy = length > 0 ? ry / length : 1;
      const distance = Math.min(dx ? reachX / Math.abs(dx) : Infinity, dy ? reachY / Math.abs(dy) : Infinity);
      cx = (bay.x - facility.x) * scale + dx * distance;
      cy = (bay.y - facility.y) * scale + dy * distance;
    }
  } else {
    const here = facility.offset.x === 0 && facility.offset.y === 0 ? key(facility) : null;
    const spread = map.vehicles
      .filter((v) => here !== null && key(v) === here)
      .reduce((m, v) => Math.max(m, Math.abs(v.offset.x) + SYMBOL_PX.vehicle / 2 + HOLDER_PAD_PX), 0);
    if (spread > 0) {
      // the vehicles in a row across the node, the mark in a row above them
      holds = true;
      hw = Math.max(spread, SYMBOL_PX.facility / 2);
      hh = HOLDER_REACH_PX + MARK_ROW_PX / 2;
      cy += MARK_ROW_PX / 2;
      markY = HOLDER_REACH_PX + MARK_ROW_PX / 2;
      markSize = MARK_ROW_PX - 4;
    } else {
      hw = SYMBOL_PX.facility / 2;
      hh = HOLDER_PX / 2;
    }
  }
  return {
    x: facility.x + cx * px,
    y: facility.y + cy * px,
    hw: hw * px,
    hh: hh * px,
    rotation: 0,
    mark: { x: facility.x + facility.offset.x * px, y: facility.y + (markY - facility.offset.y) * px },
    markSize: markSize * px,
    holds,
  };
}

/** Where a facility's name, count and badge go, in metres; `anchorY` 1 puts a text above its point, 0 below. */
export interface FacilityLabels {
  name: Point & { anchorY: number };
  count: Point & { anchorY: number };
  badge: Point;
}

/**
 * The name above the box and the count below it; both on the side away from the first bay when the bay is
 * above or below the box, and both above a symbol that holds vehicles (their short labels are below). The name clears the badge; of facilities sharing a position, every second one moves
 * its texts out by a line, so that the names of a fan-out do not overlap.
 */
export function facilityLabels(
  facility: FacilityItem,
  box: FacilityBox,
  map: MapModel,
  nodes: ReadonlyMap<string, MapNode>,
  scale: number,
): FacilityLabels {
  const px = 1 / scale,
    { top, bottom, corner } = boxExtent(box);
  const bay = facility.kind === "warehouse" ? boundNodes(facility, nodes)[0] : undefined;
  const side = box.holds
    ? "above"
    : bay && Math.abs(box.y - bay.y) > Math.abs(box.x - bay.x)
      ? box.y > bay.y
        ? "above"
        : "below"
      : "split";
  const group = map.facilities
    .filter((f) => key(f) === key(facility))
    .sort((a, b) => a.offset.x - b.offset.x);
  const out = (group.findIndex((f) => f.id === facility.id) % 2) * LINE_PX;
  const badge = facility.kind === "warehouse" && facility.badge > 0 ? BADGE_PX : 0;
  const above = top + (TEXT_GAP_PX + badge) * px,
    below = bottom - TEXT_GAP_PX * px;
  const place = (y: number, anchorY: number) => ({ x: box.x, y, anchorY });
  if (side === "above")
    return {
      count: place(above + out * px, 1),
      name: place(above + (out + LINE_PX) * px, 1),
      badge: corner,
    };
  if (side === "below")
    return {
      name: place(below - out * px, 0),
      count: place(below - (out + LINE_PX) * px, 0),
      badge: corner,
    };
  return { name: place(above + out * px, 1), count: place(below - out * px, 0), badge: corner };
}

/** The point lies inside the drawn rectangle. */
export function hitsFacility(box: FacilityBox, point: Point): boolean {
  const dx = point.x - box.x,
    dy = point.y - box.y,
    c = Math.cos(box.rotation),
    s = Math.sin(box.rotation);
  return Math.abs(dx * c + dy * s) <= box.hw && Math.abs(-dx * s + dy * c) <= box.hh;
}

/** A point of the box's frame, `a` along its width and `b` along its depth from its centre. */
const local = (box: FacilityBox, a: number, b: number): Point => {
  const c = Math.cos(box.rotation),
    s = Math.sin(box.rotation);
  return { x: box.x + a * c - b * s, y: box.y + a * s + b * c };
};
const corners = (box: FacilityBox): number[] =>
  [
    [-1, -1],
    [1, -1],
    [1, 1],
    [-1, 1],
  ].flatMap(([a, b]) => {
    const p = local(box, a! * box.hw, b! * box.hh);
    return [p.x, p.y];
  });

/** The highest and lowest y of the box and its top-right corner, for labels and the badge. */
export function boxExtent(box: FacilityBox): { top: number; bottom: number; corner: Point } {
  const ys = corners(box).filter((_, i) => i % 2 === 1);
  return { top: Math.max(...ys), bottom: Math.min(...ys), corner: local(box, box.hw, box.hh) };
}

/**
 * A facility (spec section 5.4): a warehouse with its shelf pattern, bays and badge circle; a parking area as a
 * dashed outline with a "P"; a charging station as a filled outline with a bolt. Text is the caller's.
 */
export function drawFacility(
  g: Graphics,
  facility: FacilityItem,
  box: FacilityBox,
  nodes: ReadonlyMap<string, MapNode>,
  scale: number,
): void {
  const px = 1 / scale;
  if (facility.kind === "warehouse") {
    const bays = boundNodes(facility, nodes);
    g.poly(corners(box)).fill(PALETTE.warehouse);
    const spacing = SHELF_PX * px;
    for (let b = -box.hh + spacing; b < box.hh - spacing / 2; b += spacing) {
      const from = local(box, -box.hw, b),
        to = local(box, box.hw, b);
      g.moveTo(from.x, from.y).lineTo(to.x, to.y);
    }
    g.stroke({ color: PALETTE.warehouseShelf, width: 2 * px });
    outlined(g, corners(box), PALETTE.warehouseOutline, px);
    for (const bay of bays)
      g.rect(bay.x - (BAY_PX / 2) * px, bay.y - (BAY_PX / 2) * px, BAY_PX * px, BAY_PX * px)
        .fill(PALETTE.bay)
        .stroke({ color: PALETTE.warehouseOutline, width: 2 * px });
    if (facility.badge > 0) {
      const { corner } = boxExtent(box);
      g.circle(corner.x, corner.y, BADGE_PX * px)
        .fill(PALETTE.badge)
        .stroke({ color: PALETTE.outline, width: 1.5 * px });
    }
    return;
  }
  if (facility.kind === "parking_area") {
    g.poly(corners(box))
      .fill({ color: PALETTE.bay, alpha: 0.6 })
      .stroke({ color: PALETTE.outline, width: 3.5 * px });
    dashedPolygon(g, corners(box), 4 * px, 3 * px);
    g.stroke({ color: PALETTE.parking, width: 1.5 * px });
    drawP(g, box.mark, box.markSize, Math.max(2.5 * px, box.markSize * 0.17));
    return;
  }
  g.poly(corners(box)).fill(PALETTE.chargingStation);
  outlined(g, corners(box), PALETTE.chargingOutline, px);
  g.poly(bolt(box.mark, box.markSize)).fill(PALETTE.chargingOutline);
}

/**
 * A thin line from each bay of a warehouse to its centre, drawn below every facility: the building and the bay
 * squares cover its ends, so only the part where they do not touch shows.
 */
export function drawBayLines(
  g: Graphics,
  facility: FacilityItem,
  box: FacilityBox,
  nodes: ReadonlyMap<string, MapNode>,
  scale: number,
): void {
  if (facility.kind !== "warehouse") return;
  const bays = boundNodes(facility, nodes);
  for (const bay of bays) g.moveTo(bay.x, bay.y).lineTo(box.x, box.y);
  if (bays.length) g.stroke({ color: PALETTE.warehouseOutline, width: 1.5 / scale });
}

/** A coloured outline over a wider white one: the light outline every sprite carries. */
function outlined(g: Graphics, flat: number[], color: number, px: number): void {
  g.poly(flat)
    .stroke({ color: PALETTE.outline, width: 3.5 * px })
    .poly(flat)
    .stroke({ color, width: 1.5 * px });
}

/** A bold "P" drawn with lines, `height` metres tall, centred on `at`. */
function drawP(g: Graphics, at: Point, height: number, stroke: number): void {
  const h = height / 2,
    left = at.x - h * 0.45,
    bowl = h / 2;
  g.moveTo(left, at.y - h)
    .lineTo(left, at.y + h)
    .lineTo(at.x + h * 0.05, at.y + h)
    .arc(at.x + h * 0.05, at.y + h - bowl, bowl, Math.PI / 2, -Math.PI / 2, true)
    .lineTo(left, at.y)
    .stroke({ color: PALETTE.parking, width: stroke, cap: "butt", join: "round" });
}

/** Dashes along a closed polygon given as flat coordinates, `dash` long and `gap` apart; the caller strokes them. */
function dashedPolygon(g: Graphics, flat: readonly number[], dash: number, gap: number): void {
  const n = flat.length / 2,
    period = dash + gap;
  // where the walk is within the current dash and gap; dashes continue across corners
  let phase = 0;
  for (let i = 0; i < n; i++) {
    const ax = flat[2 * i]!,
      ay = flat[2 * i + 1]!,
      bx = flat[(2 * i + 2) % flat.length]!,
      by = flat[(2 * i + 3) % flat.length]!,
      length = Math.hypot(bx - ax, by - ay);
    if (!(length > 0) || !(period > 0)) continue;
    let d = 0;
    // at most two steps per period, plus one: a bound that rounding cannot break
    for (let steps = Math.ceil((2 * length) / period) + 2; steps > 0 && d < length; steps--) {
      const on = phase < dash,
        run = Math.min((on ? dash : period) - phase, length - d);
      if (on)
        g.moveTo(ax + ((bx - ax) * d) / length, ay + ((by - ay) * d) / length).lineTo(
          ax + ((bx - ax) * (d + run)) / length,
          ay + ((by - ay) * (d + run)) / length,
        );
      d += run;
      phase += run;
      if (phase >= period - 1e-9 * period) phase = 0;
    }
  }
}
