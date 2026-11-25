// This extension replaces dynamic function generation; the player does not permit unsafe-eval.
import "pixi.js/unsafe-eval";
import { Application, Container, Graphics, Text } from "pixi.js";
import type { PreparedFrame } from "@simulatte/playback";
import {
  Projector,
  TransferTrails,
  type TransferTrail,
  type Glyph,
  type Projection,
  type Point,
} from "./project";
import { detailAt, type MapModel } from "./map/model";
import { drawFloor, drawGrid } from "./draw/floor";
import { drawNode, NODE_PX } from "./draw/node";
import { PALETTE } from "./draw/palette";
import { drawScaleBar } from "./draw/scale-bar";
import { BADGE_PX, drawBayLines, drawFacility, facilityBox, facilityLabels } from "./draw/facility";
import { LABEL, clipLabel, drawVehicle, ringPx, vehicleFrame, vehicleText } from "./draw/vehicle";
import { pickMap } from "./draw/pick";
import { drawHeld, drawWaitLine } from "./draw/traffic";
import {
  OUTLINE_REACH_PX,
  drawOverlay,
  drawRouteAhead,
  outlineFacility,
  outlinedBox,
  type OverlayContext,
} from "./draw/overlay";
import { expandHolds, focusPoint, overlayFor } from "./map/overlay";
export { Projector, equipment, type Projection, type Glyph } from "./project";
export { motionProgress, motionSpeed } from "./motion";
export {
  detailAt,
  SYMBOL_PX,
  type Detail,
  type FacilityItem,
  type MapModel,
  type OrderItem,
  type Route,
  type Shown,
  type TrafficSummary,
  type VehicleItem,
  type Wait,
  type WaitLine,
} from "./map/model";
export { overlayFor, type Overlay } from "./map/overlay";
export { scaleBar } from "./draw/scale-bar";
export { PALETTE } from "./draw/palette";
export interface Camera {
  x: number;
  y: number;
  scale: number;
}
export type TrustedRenderer = (
  graphics: Graphics,
  glyph: Glyph,
  scale: number,
) => void;
export interface EditorPointer {
  phase: 'start' | 'move' | 'end' | 'cancel'; point: Point; id: string | null; shift: boolean;
}
/** Kinds with no placement (M24): in the map model for the panel, not drawn on a map. */
const UNPLACEABLE = new Set(["order", "fleet"]);
const FACILITIES = new Set(["warehouse", "parking_area", "charging_station"]);
/** Pixels kept around the map when it is fitted: a facility symbol moved clear of its bay, with its name. */
const FIT_MARGIN_PX = 64;
// Viewer, editor and export scenes can coexist. Pixi's boolean destroy(true)
// also clears global pools; only the last scene may release shared resources.
let liveScenes = 0;
/** Scenes constructed and not yet disposed; disposal-accounting tests read it. */
export const liveSceneCount = (): number => liveScenes;
export class Scene {
  constructor() {liveScenes++;}
  readonly #renderers = new Map<string, TrustedRenderer>();
  /** Only application code can install a renderer; recording data never supplies executable code. */
  registerRenderer(kind: string, renderer: TrustedRenderer): () => void {
    this.#renderers.set(kind, renderer);
    return () => {
      if (this.#renderers.get(kind) === renderer) this.#renderers.delete(kind);
    };
  }
  readonly app = new Application();
  readonly #world = new Container();
  readonly #graphics = new Graphics();
  readonly #background = new Graphics();
  /** Screen space, above the world: the scale bar. */
  readonly #scaleBar = new Container();
  readonly #scaleBarGraphics = new Graphics();
  #scaleBarText: Text | null = null;
  /** World space, above the scale bar: the decorative trails of a map. */
  readonly #front = new Graphics();
  /** The projection comes from a frame with a layout: the scale bar is drawn. */
  #layout = false;
  /** False hides every text: labels, node ids and the length of the scale bar. */
  labels = true;
  readonly #transfers = new TransferTrails();
  #trails: readonly TransferTrail[] = [];
  #transferClock = 0;
  #activeWall = 0;
  #wall = 0;
  #playing = false;
  setPlaying(playing: boolean): void {
    const now = performance.now();
    if (this.#playing) this.#activeWall += Math.max(0, now - this.#wall);
    this.#wall = now;
    this.#playing = playing;
  }
  resetTransfers(): void {
    this.#transfers.reset();
    this.#trails = [];
  }
  #staticSignature = "";
  decorativeTransfers =
    typeof matchMedia !== "function" ||
    !matchMedia("(prefers-reduced-motion: reduce)").matches;
  readonly #labels = new Container();
  readonly #texts = new Map<string, Text>();
  #projection: Projection | null = null;
  #projector: Projector | null = null;
  #selected: string | null = null;
  #camera: Camera = { x: 0, y: 0, scale: 50 };
  #disposed = false;
  #lost = false;
  #resize: ResizeObserver | null = null;
  #cleanup: (() => void)[] = [];
  onSelect: ((id: string | null) => void) | null = null;
  onEditorPointer: ((event: EditorPointer) => boolean) | null = null;
  /** The map of the last projection; null without a layout or a network. */
  get map(): MapModel | null {
    return this.#projection?.map ?? null;
  }
  screenToWorld(x: number, y: number): Point {
    return {x: (x - this.#camera.x) / this.#camera.scale, y: -(y - this.#camera.y) / this.#camera.scale};
  }
  /** Static editor projections reuse rendering/camera controls without a playback clock. */
  renderProjection(projection: Projection): void {
    if (this.#disposed) return;
    const first = this.#projection === null;
    this.#projection = projection;
    this.#layout = false;
    if (first) this.fit(); else this.draw();
  }
  onError: ((message: string) => void) | null = null;
  get canvas(): HTMLCanvasElement {
    return this.app.canvas;
  }
  get camera(): Camera {
    return { ...this.#camera };
  }
  set camera(value: Camera) {
    if (
      ![value.x, value.y, value.scale].every(Number.isFinite) ||
      value.scale <= 0
    )
      throw new RangeError("Invalid camera");
    this.#camera = { ...value };
    this.draw();
  }
  async init(
    host: HTMLElement,
    width?: number,
    height?: number,
  ): Promise<void> {
    await this.app.init({
      width: width ?? host.clientWidth,
      height: height ?? host.clientHeight,
      background: 0xf4f5f0,
      antialias: true,
      autoStart: false,
      sharedTicker: false,
      preference: "webgl",
      resolution: 1,
      preserveDrawingBuffer: true,
    });
    if (this.#disposed) {
      this.app.destroy({removeView: true, releaseGlobalResources: liveScenes === 0}, { children: true, texture: true });
      return;
    }
    this.app.stage.addChild(this.#world, this.#scaleBar, this.#front);
    this.#scaleBar.addChild(this.#scaleBarGraphics);
    this.#world.addChild(this.#background, this.#graphics, this.#labels);
    host.append(this.canvas);
    this.canvas.setAttribute(
      "aria-label",
      "Simulation scene. Use Fit, zoom and focus controls to navigate.",
    );
    this.canvas.tabIndex = 0;
    const listen = <K extends keyof HTMLElementEventMap>(
      name: K,
      fn: (event: HTMLElementEventMap[K]) => void,
      options?: AddEventListenerOptions,
    ) => {
      this.canvas.addEventListener(name, fn, options);
      this.#cleanup.push(() => this.canvas.removeEventListener(name, fn));
    };
    let drag: Point | null = null,
      moved = false;
    let editing = false;
    const editorEvent = (phase: EditorPointer['phase'], e: PointerEvent): boolean => {
      const rect = this.canvas.getBoundingClientRect(), x = e.clientX - rect.left, y = e.clientY - rect.top;
      return this.onEditorPointer?.({phase, point: this.screenToWorld(x, y), id: phase === 'start' ? this.pick(x, y) : null, shift: e.shiftKey}) ?? false;
    };
    listen("pointerdown", (e) => {
      if (e.button !== 0 && this.onEditorPointer) return;
      editing = editorEvent('start', e);
      drag = editing ? null : { x: e.clientX, y: e.clientY };
      moved = false;
      this.canvas.setPointerCapture(e.pointerId);
    });
    listen("pointermove", (e) => {
      if (editing) {editorEvent('move', e); return;}
      if (!drag) return;
      const dx = e.clientX - drag.x,
        dy = e.clientY - drag.y;
      if (Math.abs(dx) + Math.abs(dy) > 2) moved = true;
      this.pan(dx, dy);
      drag = { x: e.clientX, y: e.clientY };
    });
    listen("pointerup", (e) => {
      if (editing) {editorEvent('end', e); editing = false; return;}
      if (drag && !moved) {
        const rect = this.canvas.getBoundingClientRect();
        this.onSelect?.(this.pick(e.clientX - rect.left, e.clientY - rect.top));
      }
      drag = null;
    });
    listen("pointercancel", (e) => {
      if (editing) editorEvent('cancel', e);
      editing = false;
      drag = null;
    });
    listen(
      "wheel",
      (e) => {
        e.preventDefault();
        const rect = this.canvas.getBoundingClientRect();
        this.zoom(
          Math.exp(-e.deltaY * 0.002),
          e.clientX - rect.left,
          e.clientY - rect.top,
        );
      },
      { passive: false },
    );
    listen("keydown", (e) => {
      if (e.key === "ArrowLeft") this.pan(30, 0);
      else if (e.key === "ArrowRight") this.pan(-30, 0);
      else if (e.key === "ArrowUp") this.pan(0, 30);
      else if (e.key === "ArrowDown") this.pan(0, -30);
      else if (e.key === "+") this.zoom(1.2);
      else if (e.key === "-") this.zoom(1 / 1.2);
      else if (e.key === "Escape") this.onSelect?.(null);
      else return;
      e.preventDefault();
    });
    const lost = (e: Event) => {
      e.preventDefault();
      this.#lost = true;
      this.onError?.("Graphics context lost. Waiting for restoration.");
    };
    const restored = () => {
      this.#lost = false;
      this.#staticSignature = "";
      this.draw();
    };
    this.canvas.addEventListener("webglcontextlost", lost);
    this.canvas.addEventListener("webglcontextrestored", restored);
    this.#cleanup.push(() => {
      this.canvas.removeEventListener("webglcontextlost", lost);
      this.canvas.removeEventListener("webglcontextrestored", restored);
    });
    if (width === undefined) {
      this.#resize = new ResizeObserver(() => {
        if (host.clientWidth && host.clientHeight) {
          this.app.renderer.resize(host.clientWidth, host.clientHeight);
          this.draw();
        }
      });
      this.#resize.observe(host);
    }
  }
  render(
    frame: PreparedFrame,
    options: {
      transferClock?: number;
      transferLifetime?: number;
      resetTransfers?: boolean;
    } = {},
  ): void {
    if (this.#disposed) return;
    const start = performance.now();
    if (!this.#projector)
      this.#projector = new Projector(frame.initialState ?? frame.state);
    const first = !this.#projection;
    this.#projection = this.#projector.project(frame);
    this.#layout = !this.#projection.fallback;
    if (options.transferClock === undefined) this.setPlaying(this.#playing);
    if (options.resetTransfers) this.resetTransfers();
    this.#transferClock = options.transferClock ?? this.#activeWall;
    this.#trails = this.#transfers.update(
      frame,
      this.#projection,
      this.#transferClock,
      options.transferLifetime ?? 200,
    );
    if (first) this.fit();
    else this.draw();
    performance.measure("simulatte.scene", {
      start,
      duration: performance.now() - start,
    });
    performance.clearMeasures("simulatte.scene");
  }
  select(id: string | null): void {
    this.#selected = id;
    this.draw();
  }
  fit(): void {
    const map = this.#projection?.map ?? null;
    const glyphs = this.#projection?.glyphs.filter((g) => !g.unplaced && (!map || this.#onMap(g)));
    if (!glyphs || (!glyphs.length && !map?.strips.length)) return;
    let minX = Infinity,
      maxX = -Infinity,
      minY = Infinity,
      maxY = -Infinity;
    for (const glyph of glyphs) {
      const radius = Math.hypot(glyph.width, glyph.depth) / 2;
      minX = Math.min(minX, glyph.x - radius);
      maxX = Math.max(maxX, glyph.x + radius);
      minY = Math.min(minY, glyph.y - radius);
      maxY = Math.max(maxY, glyph.y + radius);
    }
    if (map)
      for (const strip of map.strips)
        for (const end of [strip.a, strip.b]) {
          minX = Math.min(minX, end.x - strip.width / 2);
          maxX = Math.max(maxX, end.x + strip.width / 2);
          minY = Math.min(minY, end.y - strip.width / 2);
          maxY = Math.max(maxY, end.y + strip.width / 2);
        }
    // On a map, symbols and their texts are a fixed number of pixels: the margin is in pixels.
    const scale = map
      ? Math.min(
          Math.max(this.app.screen.width - 2 * FIT_MARGIN_PX, 1) / (maxX - minX + 1),
          Math.max(this.app.screen.height - 2 * FIT_MARGIN_PX, 1) / (maxY - minY + 1),
          120,
        )
      : Math.min(
          this.app.screen.width / (maxX - minX + 4),
          this.app.screen.height / (maxY - minY + 4),
          120,
        );
    this.#camera = {
      x: this.app.screen.width / 2 - ((minX + maxX) / 2) * scale,
      y: this.app.screen.height / 2 + ((minY + maxY) / 2) * scale,
      scale,
    };
    this.draw();
  }
  /** Centres on an element; on a map, an order is centred on its vehicle, else on its origin facility. */
  focus(id: string): void {
    const map = this.#projection?.map ?? null;
    const point = (map && focusPoint(map, id)) ?? this.#projection?.glyphs.find((g) => g.id === id);
    if (point) {
      this.#camera.x = this.app.screen.width / 2 - point.x * this.#camera.scale;
      this.#camera.y =
        this.app.screen.height / 2 + point.y * this.#camera.scale;
      this.draw();
    }
  }
  pan(dx: number, dy: number): void {
    this.#camera.x += dx;
    this.#camera.y += dy;
    this.draw();
  }
  zoom(
    factor: number,
    x = this.app.screen.width / 2,
    y = this.app.screen.height / 2,
  ): void {
    const scale = Math.max(2, Math.min(400, this.#camera.scale * factor)),
      ratio = scale / this.#camera.scale;
    this.#camera = {
      x: x - (x - this.#camera.x) * ratio,
      y: y - (y - this.#camera.y) * ratio,
      scale,
    };
    this.draw();
  }
  private pick(x: number, y: number): string | null {
    const point = this.screenToWorld(x, y);
    const map = this.#projection?.map;
    if (map)
      return pickMap(map, this.#projection!.glyphs.filter((g) => this.#onMap(g)), this.#camera, x, y, (kind) =>
        this.#renderers.has(kind),
      );
    for (const g of [...(this.#projection?.glyphs ?? [])].reverse()) {
      if (map && !this.#onMap(g)) continue;
      const dx = point.x - g.x,
        dy = point.y - g.y,
        c = Math.cos(g.rotation),
        s = Math.sin(g.rotation);
      if (
        Math.abs(dx * c + dy * s) <= Math.max(g.width / 2, 0.15) &&
        Math.abs(-dx * s + dy * c) <= Math.max(g.depth / 2, 0.15)
      )
        return g.id;
    }
    return null;
  }
  private draw(): void {
    if (this.#disposed || this.#lost || !this.#projection || !this.app.renderer)
      return;
    const { x, y, scale } = this.#camera;
    this.#world.position.set(x, y);
    this.#world.scale.set(scale, -scale);
    this.#labels.visible = this.labels;
    this.#front.clear();
    this.#drawScaleBar(scale);
    const map = this.#projection.map;
    if (map) {
      this.#drawMap(map);
      this.app.render();
      return;
    }
    const signature = JSON.stringify([
      this.#camera,
      this.app.screen.width,
      this.app.screen.height,
      this.#projection.links,
      this.#projection.ports,
    ]);
    const g = this.#background;
    if (signature !== this.#staticSignature) {
      this.#staticSignature = signature;
      g.clear();
      const spacing = 10 ** Math.ceil(Math.log10(35 / scale));
      const left = -x / scale,
        right = (this.app.screen.width - x) / scale,
        bottom = (y - this.app.screen.height) / scale,
        top = y / scale;
      for (
        let gx = Math.ceil(left / spacing) * spacing;
        gx <= right;
        gx += spacing
      )
        g.moveTo(gx, bottom).lineTo(gx, top);
      for (
        let gy = Math.ceil(bottom / spacing) * spacing;
        gy <= top;
        gy += spacing
      )
        g.moveTo(left, gy).lineTo(right, gy);
      g.stroke({ color: 0xe7eae3, width: 1 / scale });
      for (const [a, b] of this.#projection.links) {
        g.moveTo(a.x, a.y)
          .lineTo(b.x, b.y)
          .stroke({ color: 0xc4ccc3, width: 2 / scale });
        const angle = Math.atan2(b.y - a.y, b.x - a.x),
          mx = (a.x + b.x) / 2,
          my = (a.y + b.y) / 2,
          size = 6 / scale;
        g.moveTo(
          mx - size * Math.cos(angle - 0.5),
          my - size * Math.sin(angle - 0.5),
        )
          .lineTo(mx, my)
          .lineTo(
            mx - size * Math.cos(angle + 0.5),
            my - size * Math.sin(angle + 0.5),
          )
          .stroke({ color: 0x89958b, width: 1.5 / scale });
      }
      for (const port of this.#projection.ports)
        g.circle(port.x, port.y, 3 / scale).fill(0x52695c);
    }
    const dynamic = this.#graphics;
    dynamic.clear();
    if (this.decorativeTransfers)
      for (const trail of this.#trails) {
        const dx = trail.to.x - trail.from.x,
          dy = trail.to.y - trail.from.y,
          length = Math.hypot(dx, dy),
          dash = 6 / scale;
        for (let d = 0; d < length; d += dash * 2) {
          const stop = Math.min(length, d + dash);
          dynamic
            .moveTo(
              trail.from.x + (dx * d) / length,
              trail.from.y + (dy * d) / length,
            )
            .lineTo(
              trail.from.x + (dx * stop) / length,
              trail.from.y + (dy * stop) / length,
            );
        }
        dynamic.stroke({
          color: 0x668471,
          width: 2 / scale,
          alpha:
            0.35 *
            Math.max(
              0,
              1 - (this.#transferClock - trail.born) / trail.lifetime,
            ),
        });
      }
    const visible = new Set<string>();
    for (const item of this.#projection.glyphs) {
      const sx = x + item.x * scale,
        sy = y - item.y * scale;
      if (
        sx < -200 ||
        sy < -100 ||
        sx > this.app.screen.width + 200 ||
        sy > this.app.screen.height + 100
      )
        continue;
      this.glyph(dynamic, item, scale);
      if (
        scale > 20 &&
        item.kind !== "job" &&
        (item.kind !== "node" || item.id === this.#selected)
      ) {
        visible.add(item.id);
        let text = this.#texts.get(item.id);
        if (!text) {
          text = new Text({
            text: "",
            style: { fontFamily: "system-ui", fontSize: 12, fill: 0x303d36 },
          });
          text.anchor.set(0.5, 0);
          this.#texts.set(item.id, text);
          this.#labels.addChild(text);
        }
        text.text = `${item.label.slice(0, 160)}${item.queue ? " · " + item.queue + " queued" : ""}${item.badge ? "\n" + item.badge : ""}${item.diagnostic ? "\n" + item.diagnostic : ""}`;
        text.position.set(item.x, item.y - item.depth / 2 - 0.12);
        text.scale.set(1 / scale, -1 / scale);
        text.visible = true;
      }
    }
    for (const [id, text] of this.#texts)
      if (!visible.has(id)) {
        text.destroy();
        this.#texts.delete(id);
      }
    this.app.render();
  }
  /** A glyph is drawn and picked on a map unless its kind has no placement; a registered renderer still wins. */
  #onMap(glyph: Glyph): boolean {
    return !UNPLACEABLE.has(glyph.kind) || this.#renderers.has(glyph.kind);
  }
  #drawScaleBar(scale: number): void {
    this.#scaleBar.visible = this.#layout;
    if (!this.#layout) return;
    if (!this.#scaleBarText) {
      this.#scaleBarText = new Text({
        text: "",
        style: { fontFamily: "system-ui", fontSize: 12, fill: PALETTE.scaleBar },
      });
      this.#scaleBarText.anchor.set(0.5, 1);
      this.#scaleBar.addChild(this.#scaleBarText);
    }
    this.#scaleBarText.visible = this.labels;
    drawScaleBar(this.#scaleBarGraphics, this.#scaleBarText, scale, this.app.screen.height);
  }
  /** The layers of spec section 5.1, bottom to top. */
  #drawMap(map: MapModel): void {
    const projection = this.#projection!;
    const { x, y, scale } = this.#camera,
      width = this.app.screen.width,
      height = this.app.screen.height,
      detail = detailAt(map, scale);
    // Static: the grid and the floor. `held` changes with the frame and is not part of it.
    const signature = JSON.stringify([
      "map",
      this.#camera,
      width,
      height,
      map.strips.map((s) => [s.a.x, s.a.y, s.b.x, s.b.y, s.width, s.oneWay]),
    ]);
    if (signature !== this.#staticSignature) {
      this.#staticSignature = signature;
      this.#background.clear();
      drawGrid(this.#background, scale, {
        left: -x / scale,
        right: (width - x) / scale,
        bottom: (y - height) / scale,
        top: y / scale,
      });
      drawFloor(this.#background, map.strips, scale, detail);
    }
    const onScreen = (p: { x: number; y: number }): boolean => {
      const sx = x + p.x * scale,
        sy = y - p.y * scale;
      return sx >= -200 && sy >= -100 && sx <= width + 200 && sy <= height + 100;
    };
    // Vehicles and facilities of the map model are drawn as sprites unless a renderer is registered for their kind;
    // the others (a vehicle without a position, in the unplaced row) keep their glyph.
    const custom = (kind: string): boolean => this.#renderers.has(kind);
    const spriteVehicles = custom("agv") ? [] : map.vehicles,
      spriteFacilities = map.facilities.filter((f) => !custom(f.kind)),
      sprites = new Set([...spriteVehicles.map((v) => v.id), ...spriteFacilities.map((f) => f.id)]),
      nodesById = new Map(map.nodes.map((n) => [n.id, n])),
      boundToSprite = new Set(spriteFacilities.flatMap((f) => f.nodes));
    const glyphs = projection.glyphs.filter((g) => this.#onMap(g) && !sprites.has(g.id) && onScreen(g));
    const facilities = glyphs.filter((g) => FACILITIES.has(g.kind)),
      vehicles = glyphs.filter((g) => g.kind === "agv"),
      nodes = glyphs.filter((g) => g.kind === "node"),
      generic = glyphs.filter((g) => !FACILITIES.has(g.kind) && g.kind !== "agv" && g.kind !== "node");
    const dynamic = this.#graphics;
    dynamic.clear();
    // The held tint, under everything but the floor; what the selected vehicle holds in the stronger tint.
    const overlay = overlayFor(map, this.#selected);
    drawHeld(dynamic, map, scale, detail, expandHolds(map, overlay?.holds ?? []));
    // the selected element and the related ones carry a selection outline, their labels outside it
    const outlined = new Set(overlay ? [this.#selected!, ...overlay.related] : []);
    const visible = new Set<string>();
    const label = (item: Glyph): void => {
      if (scale > 20 && item.kind !== "job" && (item.kind !== "node" || item.id === this.#selected))
        this.#text(
          visible,
          item.id,
          `${item.label.slice(0, 160)}${item.queue ? " · " + item.queue + " queued" : ""}${item.badge ? "\n" + item.badge : ""}${item.diagnostic ? "\n" + item.diagnostic : ""}`,
          item.x,
          item.y - item.depth / 2 - 0.12,
          PALETTE.text,
        );
    };
    for (const item of facilities) {
      this.glyph(dynamic, item, scale);
      label(item);
    }
    const px = 1 / scale;
    const boxes = spriteFacilities.map((facility) => facilityBox(facility, map, nodesById, scale, detail));
    // bay lines can be long (a warehouse beside its first bay, spec M28): they are not culled
    spriteFacilities.forEach((facility, i) => drawBayLines(dynamic, facility, boxes[i]!, nodesById, scale));
    for (const [i, facility] of spriteFacilities.entries()) {
      const box = boxes[i]!;
      if (!onScreen(box)) continue;
      if (outlined.has(facility.id)) outlineFacility(dynamic, box, scale);
      drawFacility(dynamic, facility, box, nodesById, scale);
      const places = facilityLabels(facility, box, map, nodesById, scale),
        texts = outlined.has(facility.id) ? facilityLabels(facility, outlinedBox(box, scale), map, nodesById, scale) : places;
      this.#text(visible, `${facility.id}\u0000name`, clipLabel(facility.label), texts.name.x, texts.name.y, PALETTE.text, {
        anchorY: texts.name.anchorY,
        bold: true,
      });
      if (facility.count)
        this.#text(visible, `${facility.id}\u0000count`, facility.count, texts.count.x, texts.count.y, PALETTE.text, {
          anchorY: texts.count.anchorY,
        });
      if (facility.kind === "warehouse" && facility.badge > 0)
        this.#text(visible, `${facility.id}\u0000badge`, String(facility.badge), places.badge.x, places.badge.y, PALETTE.badgeText, {
          anchorY: 0.5,
          bold: true,
          size: BADGE_PX + 3,
        });
    }
    for (const port of projection.ports) dynamic.circle(port.x, port.y, 3 / scale).fill(PALETTE.port);
    if (this.#renderers.has("node"))
      for (const item of nodes) {
        this.glyph(dynamic, item, scale);
        label(item);
      }
    else
      for (const node of map.nodes) {
        const selected = node.id === this.#selected;
        if ((detail !== "true" && !selected) || !onScreen(node)) continue;
        // a node bound to a facility is drawn as its bay or inside it (spec section 5.4); its id stays
        if (selected || !boundToSprite.has(node.id)) drawNode(dynamic, node, scale, selected);
        this.#text(visible, node.id, node.id, node.x, node.y - (NODE_PX + 2) / scale, PALETTE.nodeText);
      }
    const context: OverlayContext = {
      map,
      scale,
      detail,
      boxes: new Map(spriteFacilities.map((facility, i) => [facility.id, boxes[i]!])),
    };
    if (overlay) drawRouteAhead(dynamic, overlay, this.#selected!, context);
    for (const item of [...generic, ...vehicles]) {
      this.glyph(dynamic, item, scale);
      label(item);
    }
    for (const vehicle of spriteVehicles) {
      const frame = vehicleFrame(vehicle, scale, detail);
      if (!onScreen(frame)) continue;
      drawVehicle(dynamic, vehicle, scale, detail);
      const fill = LABEL[vehicle.shown],
        reach =
          (vehicle.shown === "waiting" ? ringPx(frame, scale) : (Math.hypot(frame.length, frame.width) / 2) * scale) +
          (outlined.has(vehicle.id) ? OUTLINE_REACH_PX : 0);
      if (detail === "true")
        this.#text(visible, `${vehicle.id}\u0000line`, vehicleText(vehicle, detail), frame.x, frame.y + (reach + 2) * px, fill, {
          anchorY: 1,
          bold: true,
        });
      else if (vehicle.short)
        this.#text(visible, `${vehicle.id}\u0000short`, vehicleText(vehicle, detail), frame.x, frame.y - (reach + 1) * px, fill, {
          bold: true,
        });
    }
    // Wait lines; the selected vehicle's is drawn thicker with the overlay.
    for (const line of map.waitLines)
      if (!overlay?.waitLine || line.agv !== overlay.waitLine.agv) drawWaitLine(dynamic, line, map, scale, detail);
    if (overlay) drawOverlay(dynamic, overlay, this.#selected!, context);
    for (const [id, text] of this.#texts)
      if (!visible.has(id)) {
        text.destroy();
        this.#texts.delete(id);
      }
    this.#front.position.set(x, y);
    this.#front.scale.set(scale, -scale);
    if (this.decorativeTransfers) this.#drawTrails(this.#front, scale);
  }
  /**
   * A label centred on a point of the world, of fixed pixel size: below it, or above it with `anchorY` 1. Keys not
   * added to `visible` are removed.
   */
  #text(
    visible: Set<string>,
    id: string,
    content: string,
    x: number,
    y: number,
    fill: number,
    options: { anchorY?: number; bold?: boolean; size?: number } = {},
  ): void {
    visible.add(id);
    let text = this.#texts.get(id);
    if (!text) {
      text = new Text({
        text: "",
        style: {
          fontFamily: "system-ui",
          fontSize: options.size ?? 12,
          fontWeight: options.bold ? "600" : "normal",
          fill,
        },
      });
      this.#texts.set(id, text);
      this.#labels.addChild(text);
    }
    // the side of a facility's texts and the colour of a vehicle's label change with the level and the state;
    // a style is changed only when it differs
    text.anchor.set(0.5, options.anchorY ?? 0);
    if (text.style.fill !== fill) text.style.fill = fill;
    text.text = content;
    text.position.set(x, y);
    text.scale.set(1 / this.#camera.scale, -1 / this.#camera.scale);
    text.visible = true;
  }
  /** The decorative trails of a map, as the scene draws them without one. */
  #drawTrails(g: Graphics, scale: number): void {
    for (const trail of this.#trails) {
      const dx = trail.to.x - trail.from.x,
        dy = trail.to.y - trail.from.y,
        length = Math.hypot(dx, dy),
        dash = 6 / scale;
      for (let d = 0; d < length; d += dash * 2) {
        const stop = Math.min(length, d + dash);
        g.moveTo(trail.from.x + (dx * d) / length, trail.from.y + (dy * d) / length).lineTo(
          trail.from.x + (dx * stop) / length,
          trail.from.y + (dy * stop) / length,
        );
      }
      g.stroke({
        color: PALETTE.trail,
        width: 2 / scale,
        alpha: 0.35 * Math.max(0, 1 - (this.#transferClock - trail.born) / trail.lifetime),
      });
    }
  }
  private glyph(g: Graphics, item: Glyph, scale: number): void {
    const custom = this.#renderers.get(item.kind);
    if (custom) {
      custom(g, item, scale);
      return;
    }
    const c = Math.cos(item.rotation),
      s = Math.sin(item.rotation),
      corners = [
        [-1, -1],
        [1, -1],
        [1, 1],
        [-1, 1],
      ].map(([dx, dy]) => ({
        x: item.x + ((dx! * item.width) / 2) * c - ((dy! * item.depth) / 2) * s,
        y: item.y + ((dx! * item.width) / 2) * s + ((dy! * item.depth) / 2) * c,
      }));
    if (item.kind === "node" || item.kind === "job" || item.shape === "circle")
      g.circle(item.x, item.y, Math.max(item.width / 2, 2 / scale));
    else if (item.shape === "diamond")
      g.poly([
        item.x,
        item.y + item.depth / 2,
        item.x + item.width / 2,
        item.y,
        item.x,
        item.y - item.depth / 2,
        item.x - item.width / 2,
        item.y,
      ]);
    else g.poly(corners.flatMap((p) => [p.x, p.y]));
    g.fill(item.color).stroke({
      color: item.id === this.#selected ? 0x315b4b : 0x89958b,
      width: (item.id === this.#selected ? 3 : 1) / scale,
    });
    if (item.kind === "server" && item.status === "Processing")
      g.circle(
        item.x + item.width * 0.32,
        item.y + item.depth * 0.28,
        3 / scale,
      ).fill(0x456f4d);
  }
  dispose(): void {
    if (this.#disposed) return;
    this.#disposed = true;
    liveScenes--;
    this.#resize?.disconnect();
    this.#cleanup.forEach((fn) => fn());
    if (this.app.renderer)
      this.app.destroy({removeView: true, releaseGlobalResources: liveScenes === 0}, { children: true, texture: true });
    this.#texts.clear();
  }
}
