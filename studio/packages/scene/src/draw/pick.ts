import type { Glyph } from "../project";
import { detailAt, type MapModel, type MapNode } from "../map/model";
import { facilityBox, hitsFacility } from "./facility";
import { NODE_PX } from "./node";
import { hitsVehicle, vehicleFrame } from "./vehicle";

/** Kinds with no placement (M24): in the map model for the panel, not drawn, labelled or picked on a map. */
const UNPLACEABLE = new Set(["order", "fleet"]);

/** A glyph is drawn and picked on a map unless its kind has no placement; a registered renderer (`custom`) still wins. */
export const onMap = (glyph: Glyph, custom: (kind: string) => boolean = () => false): boolean =>
  !UNPLACEABLE.has(glyph.kind) || custom(glyph.kind);

/** The world's offset and scale on the canvas: a world point (x, y) is at (camera.x + x s, camera.y - y s). */
export interface MapCamera {
  x: number;
  y: number;
  scale: number;
}

/** A glyph is hit inside its rotated rectangle, at least 0.15 m around its centre, as the scene picks without a map. */
const hitsGlyph = (g: Glyph, point: { x: number; y: number }): boolean => {
  const dx = point.x - g.x,
    dy = point.y - g.y,
    c = Math.cos(g.rotation),
    s = Math.sin(g.rotation);
  return (
    Math.abs(dx * c + dy * s) <= Math.max(g.width / 2, 0.15) && Math.abs(-dx * s + dy * c) <= Math.max(g.depth / 2, 0.15)
  );
};

/**
 * The id of the element drawn at canvas pixel (x, y) on a map, or null. Vehicles are tested before facilities,
 * both as drawn (the symbol with its offset at symbol level, the footprint at true size); then the other glyphs,
 * the last drawn first, without nodes at symbol level and without the kinds a map does not draw (`onMap`). A node is hit
 * within its drawn radius. A kind for which `custom` is true has a registered renderer: it is tested as its glyph.
 */
export function pickMap(
  map: MapModel,
  glyphs: readonly Glyph[],
  camera: MapCamera,
  x: number,
  y: number,
  custom: (kind: string) => boolean = () => false,
): string | null {
  const { scale } = camera,
    point = { x: (x - camera.x) / scale, y: -(y - camera.y) / scale },
    detail = detailAt(map, scale);
  if (!custom("agv"))
    for (const vehicle of [...map.vehicles].reverse())
      if (hitsVehicle(vehicleFrame(vehicle, scale, detail), point)) return vehicle.id;
  const nodes = new Map<string, MapNode>(map.nodes.map((n) => [n.id, n]));
  for (const facility of [...map.facilities].reverse())
    if (!custom(facility.kind) && hitsFacility(facilityBox(facility, map, nodes, scale, detail), point))
      return facility.id;
  const drawn = new Set<string>([
    ...(custom("agv") ? [] : map.vehicles.map((v) => v.id)),
    ...map.facilities.filter((f) => !custom(f.kind)).map((f) => f.id),
  ]);
  for (const glyph of [...glyphs].reverse()) {
    if (drawn.has(glyph.id) || !onMap(glyph, custom)) continue;
    if (glyph.kind === "node" && !custom("node")) {
      // drawn as a circle of NODE_PX pixels, at true-size level only
      if (detail === "true" && Math.hypot(point.x - glyph.x, point.y - glyph.y) <= NODE_PX / scale) return glyph.id;
      continue;
    }
    if (glyph.kind === "node" && detail === "symbol") continue;
    if (hitsGlyph(glyph, point)) return glyph.id;
  }
  return null;
}
