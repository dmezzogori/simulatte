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
export { Projector, equipment, type Projection, type Glyph } from "./project";
export { motionProgress } from "./motion";
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
export class Scene {
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
  screenToWorld(x: number, y: number): Point {
    return {x: (x - this.#camera.x) / this.#camera.scale, y: -(y - this.#camera.y) / this.#camera.scale};
  }
  /** Static editor projections reuse rendering/camera controls without a playback clock. */
  renderProjection(projection: Projection): void {
    if (this.#disposed) return;
    const first = this.#projection === null;
    this.#projection = projection;
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
      this.app.destroy(true, { children: true, texture: true });
      return;
    }
    this.app.stage.addChild(this.#world);
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
    const glyphs = this.#projection?.glyphs.filter((g) => !g.unplaced);
    if (!glyphs?.length) return;
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
    const scale = Math.min(
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
  focus(id: string): void {
    const glyph = this.#projection?.glyphs.find((g) => g.id === id);
    if (glyph) {
      this.#camera.x = this.app.screen.width / 2 - glyph.x * this.#camera.scale;
      this.#camera.y =
        this.app.screen.height / 2 + glyph.y * this.#camera.scale;
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
    for (const g of [...(this.#projection?.glyphs ?? [])].reverse()) {
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
    this.#resize?.disconnect();
    this.#cleanup.forEach((fn) => fn());
    if (this.app.renderer)
      this.app.destroy(true, { children: true, texture: true });
    this.#texts.clear();
  }
}
