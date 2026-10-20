/** Pure camera framing and visibility rules shared by the live scene and headless export. */
import type {Camera} from './index';
import type {Glyph, Point} from './project';
export type Bounds = readonly [number, number, number, number];
export interface Visibility {
  /** `null` shows every entity; a set shows only the listed ids. */
  readonly entities: ReadonlySet<string> | null;
  /** `null` keeps the default label rule; a set labels exactly the listed (visible) ids at any zoom. */
  readonly labels: ReadonlySet<string> | null;
}
export const EVERYTHING: Visibility = Object.freeze({entities: null, labels: null});
/** The camera that contains the world rectangle inside a width×height viewport, letterboxed and centred. */
export function cameraForBounds(bounds: Bounds, width: number, height: number): Camera {
  const [xmin, ymin, xmax, ymax] = bounds;
  if (!bounds.every(Number.isFinite) || !(xmin < xmax) || !(ymin < ymax)) throw new RangeError('Invalid world bounds');
  if (!(width > 0) || !(height > 0)) throw new RangeError('Invalid viewport');
  const scale = Math.min(width / (xmax - xmin), height / (ymax - ymin));
  return {x: width / 2 - ((xmin + xmax) / 2) * scale, y: height / 2 + ((ymin + ymax) / 2) * scale, scale};
}
/** The world rectangle a camera shows in a width×height viewport (the inverse of `cameraForBounds` up to letterboxing). */
export function boundsOfCamera(camera: Camera, width: number, height: number): [number, number, number, number] {
  return [-camera.x / camera.scale, (camera.y - height) / camera.scale, (width - camera.x) / camera.scale, camera.y / camera.scale];
}
export function worldToScreen(camera: Camera, x: number, y: number): Point {
  return {x: camera.x + x * camera.scale, y: camera.y - y * camera.scale};
}
export function glyphVisible(glyph: Pick<Glyph, 'id'>, visibility: Visibility): boolean {
  return visibility.entities === null || visibility.entities.has(glyph.id);
}
/** Default rule: readable zoom, never jobs, nodes only when selected; an explicit list overrides it. */
export function labelVisible(glyph: Pick<Glyph, 'id' | 'kind'>, visibility: Visibility, selected: string | null, scale: number): boolean {
  if (!glyphVisible(glyph, visibility)) return false;
  if (visibility.labels !== null) return visibility.labels.has(glyph.id);
  return scale > 20 && glyph.kind !== 'job' && (glyph.kind !== 'node' || glyph.id === selected);
}
