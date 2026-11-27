import type { Graphics } from "pixi.js";
import type { Point } from "../project";
import { SYMBOL_PX, type Detail, type Shown, type VehicleItem } from "../map/model";
import { PALETTE } from "./palette";

/** Pixels across a vehicle symbol: the mockup's proportions, 16 by 29, at `SYMBOL_PX.vehicle` long. */
export const VEHICLE_SYMBOL_WIDTH = 12;
/** Characters of a label shown on the map; a longer one ends in an ellipsis (the detail shows it whole). */
export const MAP_LABEL_CHARS = 32;
/** A label for the map: at most `MAP_LABEL_CHARS` characters, the last an ellipsis when it is cut. */
export const clipLabel = (label: string): string => {
  const chars = [...label];
  return chars.length > MAP_LABEL_CHARS ? `${chars.slice(0, MAP_LABEL_CHARS - 1).join("")}…` : label;
};
/**
 * The text drawn beside a vehicle: its short label at symbol level, its one-line label at true size. Both are
 * clipped whole, as the line carries ids from the state (a goal, a blocker) as well as the label.
 */
export const vehicleText = (vehicle: VehicleItem, detail: Detail): string =>
  clipLabel(detail === "true" ? vehicle.line : vehicle.short);
/** Pixels of the light outline. */
const OUTLINE_PX = 1.5;

/** Where a vehicle is drawn and how large, in metres: its symbol (with the fan-out offset) or its footprint. */
export interface VehicleFrame extends Point {
  heading: number;
  /** Along the heading. */
  length: number;
  width: number;
}

export function vehicleFrame(vehicle: VehicleItem, scale: number, detail: Detail): VehicleFrame {
  if (detail === "true")
    return { x: vehicle.x, y: vehicle.y, heading: vehicle.heading, length: vehicle.length, width: vehicle.width };
  return {
    x: vehicle.x + vehicle.offset.x / scale,
    y: vehicle.y - vehicle.offset.y / scale,
    heading: vehicle.heading,
    length: SYMBOL_PX.vehicle / scale,
    width: VEHICLE_SYMBOL_WIDTH / scale,
  };
}

/** The point lies inside the rectangle the body and its nose fill. */
export function hitsVehicle(frame: VehicleFrame, point: Point): boolean {
  const dx = point.x - frame.x,
    dy = point.y - frame.y,
    c = Math.cos(frame.heading),
    s = Math.sin(frame.heading);
  return Math.abs(dx * c + dy * s) <= frame.length / 2 && Math.abs(-dx * s + dy * c) <= frame.width / 2;
}

/** The body colour of each shown state (spec section 5.4). */
export const BODY: Record<Shown, number> = {
  stranded: PALETTE.stranded,
  waiting: PALETTE.waiting,
  moving: PALETTE.moving,
  working: PALETTE.moving,
  idle: PALETTE.idle,
  charging: PALETTE.charging,
  resting: PALETTE.moving,
  unknown: PALETTE.idle,
};
/** The colour of the short label and the one-line label of each shown state. */
export const LABEL: Record<Shown, number> = {
  stranded: PALETTE.strandedText,
  waiting: PALETTE.waitingText,
  moving: PALETTE.movingText,
  working: PALETTE.movingText,
  idle: PALETTE.idleText,
  charging: PALETTE.chargingText,
  resting: PALETTE.movingText,
  unknown: PALETTE.idleText,
};

/** Pixels between the vehicle's centre and the outside of its dashed ring. */
export const ringPx = (frame: VehicleFrame, scale: number): number =>
  (Math.hypot(frame.length, frame.width) / 2) * scale + 4;

/**
 * A vehicle (spec section 5.4): a rounded body with a pointed nose toward its heading, its state colour and
 * mark, the load mark toward the tail and the fleet stripe at the tail.
 */
export function drawVehicle(g: Graphics, vehicle: VehicleItem, scale: number, detail: Detail): void {
  const frame = vehicleFrame(vehicle, scale, detail),
    { length: L, width: W } = frame,
    c = Math.cos(frame.heading),
    s = Math.sin(frame.heading),
    px = 1 / scale;
  /** A point of the vehicle's frame: `a` metres along the heading, `b` metres to its left. */
  const at = (a: number, b: number): Point => ({ x: frame.x + a * c - b * s, y: frame.y + a * s + b * c });
  const polygon = (points: readonly (readonly [number, number])[]): number[] =>
    points.flatMap(([a, b]) => {
      const p = at(a, b);
      return [p.x, p.y];
    });
  const tail = -L / 2,
    shoulder = L / 2 - 0.31 * L,
    radius = Math.min(0.2 * W, 0.15 * L);
  const body: [number, number][] = [
    [shoulder, W / 2],
    [L / 2, 0],
    [shoulder, -W / 2],
  ];
  // the two rounded corners of the tail, three points each
  for (const [cornerA, cornerB, from] of [
    [tail + radius, -W / 2 + radius, -Math.PI / 2],
    [tail + radius, W / 2 - radius, Math.PI],
  ] as const)
    for (let i = 0; i <= 3; i++) {
      const angle = from - (i * Math.PI) / 6;
      body.push([cornerA + radius * Math.cos(angle), cornerB + radius * Math.sin(angle)]);
    }
  g.poly(polygon(body)).fill(BODY[vehicle.shown]).stroke({ color: PALETTE.outline, width: OUTLINE_PX * px, join: "round" });

  // With a fleet stripe (about a quarter of the body) the load and the state mark move toward the nose.
  const striped = vehicle.fleetIndex !== null,
    unit = Math.min(W, 0.55 * L),
    stripe = 0.22 * L;
  if (vehicle.fleetIndex !== null) {
    const colour = PALETTE.fleets[vehicle.fleetIndex % PALETTE.fleets.length]!;
    const inset = OUTLINE_PX * px * 0.5;
    g.poly(
      polygon([
        [tail + inset, -W / 2 + inset],
        [tail + stripe, -W / 2 + inset],
        [tail + stripe, W / 2 - inset],
        [tail + inset, W / 2 - inset],
      ]),
    ).fill(colour);
    // a white seam keeps the stripe apart from a body of a similar colour
    const a = at(tail + stripe, -W / 2 + inset),
      b = at(tail + stripe, W / 2 - inset);
    g.moveTo(a.x, a.y).lineTo(b.x, b.y).stroke({ color: PALETTE.outline, width: px });
  }
  if (vehicle.loaded) {
    const side = 0.5 * unit,
      centre = striped ? -0.12 * L : -0.19 * L;
    g.poly(
      polygon([
        [centre - side / 2, -side / 2],
        [centre + side / 2, -side / 2],
        [centre + side / 2, side / 2],
        [centre - side / 2, side / 2],
      ]),
    )
      .fill(PALETTE.load)
      .stroke({ color: PALETTE.outline, width: px });
  }
  // The state mark sits on the body, ahead of the load. The pause bars turn with the body; the bolt and the cross
  // are drawn upright so that they read the same way at any heading.
  const along = vehicle.loaded ? (striped ? 0.17 * L : 0.15 * L) : striped ? 0.05 * L : -0.06 * L,
    mark = at(along, 0);
  switch (vehicle.shown) {
    case "waiting": {
      // two bars across the body, a bar's width apart, as in the mockup
      const bar = Math.max(2 * px, 0.17 * unit),
        height = 0.6 * unit;
      for (const from of [along - bar * 1.5, along + bar * 0.5])
        g.poly(
          polygon([
            [from, -height / 2],
            [from + bar, -height / 2],
            [from + bar, height / 2],
            [from, height / 2],
          ]),
        );
      g.fill(PALETTE.outline);
      dashedCircle(g, { x: frame.x, y: frame.y }, (ringPx(frame, scale) - 1) * px, 4 * px, 3 * px);
      g.stroke({ color: PALETTE.waiting, width: 2 * px });
      break;
    }
    case "charging":
      g.poly(bolt(mark, 0.8 * unit)).fill(PALETTE.outline);
      break;
    case "stranded": {
      const h = 0.3 * unit;
      g.moveTo(mark.x - h, mark.y - h)
        .lineTo(mark.x + h, mark.y + h)
        .moveTo(mark.x - h, mark.y + h)
        .lineTo(mark.x + h, mark.y - h)
        .stroke({ color: PALETTE.outline, width: Math.max(2 * px, 0.13 * unit), cap: "round" });
      break;
    }
    case "working": {
      // a small square just ahead of the nose: the side of the bay the vehicle works at is not recorded
      const side = 0.42 * unit,
        nose = L / 2 + side / 2 + px;
      g.poly(
        polygon([
          [nose - side / 2, -side / 2],
          [nose + side / 2, -side / 2],
          [nose + side / 2, side / 2],
          [nose - side / 2, side / 2],
        ]),
      )
        .fill(PALETTE.outline)
        .stroke({ color: BODY.working, width: 1.5 * px });
      break;
    }
    default:
      break;
  }
}

/** A lightning bolt `height` metres tall centred on `at`, upright, as in the mockup. */
export function bolt(at: Point, height: number): number[] {
  const k = height / 12;
  return [
    [1, 6],
    [-4, -1],
    [0, -1],
    [-1, -6],
    [4, 1],
    [0, 1],
  ].flatMap(([x, y]) => [at.x + x! * k, at.y + y! * k]);
}

/** Dashes along a circle: `dash` long, `gap` apart, in metres; the caller strokes them. */
export function dashedCircle(g: Graphics, centre: Point, radius: number, dash: number, gap: number): void {
  const count = Math.max(4, Math.round((2 * Math.PI * radius) / (dash + gap))),
    step = (2 * Math.PI) / count,
    on = (step * dash) / (dash + gap);
  for (let i = 0; i < count; i++) {
    const from = i * step;
    g.moveTo(centre.x + radius * Math.cos(from), centre.y + radius * Math.sin(from)).arc(
      centre.x,
      centre.y,
      radius,
      from,
      from + on,
    );
  }
}
