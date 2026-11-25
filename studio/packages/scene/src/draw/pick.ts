import type { Glyph } from "../project";
import { detailAt, type MapModel, type MapNode } from "../map/model";
import { facilityBox, hitsFacility } from "./facility";
import { hitsVehicle, vehicleFrame } from "./vehicle";

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
 * the last drawn first, without nodes at symbol level. A kind for which `custom` is true has a registered
 * renderer: it is tested as its glyph. `glyphs` are those the map draws.
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
    if (drawn.has(glyph.id) || (glyph.kind === "node" && detail === "symbol")) continue;
    if (hitsGlyph(glyph, point)) return glyph.id;
  }
  return null;
}
