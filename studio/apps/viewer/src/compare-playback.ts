/** Side-by-side playback of two committed full recordings on one clock (design 7, D100).
 * Frames never enter React: listeners receive each side's frames and update the DOM themselves. */
import {HttpSource, openRecording, type RecordingView} from '@simulatte/trace';
import {Playback, Session, type Clock, type PlaybackStatus, type PreparedFrame} from '@simulatte/playback';
import {Scene} from '@simulatte/scene';
import type {Prefix} from './studio-client';

export type Side = 0 | 1;
/** Why a committed result cannot join side-by-side playback. */
export type CompareRefusal = {readonly code: 'kpi_only'; readonly level: string};
export class CompareRefused extends Error {
  constructor(readonly reason: CompareRefusal) {
    super(`A ${reason.level}-level recording has no entity events; replay it at full level to compare`);
    this.name = 'CompareRefused';
  }
}
const browserFrames: Clock = {request: callback => requestAnimationFrame(callback), cancel: id => cancelAnimationFrame(id)};

/** One animation-frame request serves every callback registered for the next frame, with one timestamp.
 * There is deliberately no `now()`: Playback then anchors each tick on that shared frame timestamp, so
 * two playbacks started in one turn derive their time from the same wall-clock interval. */
export class FrameClock implements Clock {
  #due = new Map<number, (now: number) => void>();
  #id = 0;
  #request: number | null = null;
  constructor(private readonly frames: Clock = browserFrames) {}
  request(callback: (now: number) => void): number {
    const id = ++this.#id;
    this.#due.set(id, callback);
    this.#request ??= this.frames.request(now => this.fire(now));
    return id;
  }
  cancel(id: number): void {
    if (!this.#due.delete(id) || this.#due.size > 0 || this.#request === null) return;
    this.frames.cancel(this.#request);
    this.#request = null;
  }
  private fire(now: number): void {
    this.#request = null;
    const due = [...this.#due.values()];
    this.#due.clear();
    for (const callback of due) callback(now);
  }
  dispose(): void {
    if (this.#request !== null) this.frames.cancel(this.#request);
    this.#request = null;
    this.#due.clear();
  }
}

/** The part of `Scene` side-by-side playback drives; tests substitute a scene that never draws. */
export interface CompareScene {
  render(frame: PreparedFrame): void;
  setPlaying(playing: boolean): void;
  resetTransfers(): void;
  dispose(): void;
}
export interface CompareSide {
  readonly attempt: string;
  readonly recording: RecordingView;
  readonly playback: Playback;
  readonly scene: CompareScene | null;
}
export type CompareListener = (side: Side, frame: PreparedFrame | null, status: PlaybackStatus, error?: unknown) => void;
interface Options {
  /** The animation-frame source shared by both sides (browser frames by default). */
  clock?: Clock;
  /** Creates a side's scene in its host; a scene counts toward Scene's live-scene accounting until disposed. */
  scene?: (host: HTMLElement) => Promise<CompareScene>;
}
interface Slot {epoch: number; abort: AbortController | null; side: CompareSide | null; unsubscribe: (() => void) | null; shown: PreparedFrame | null;}
const superseded = () => new DOMException('Compare selection superseded', 'AbortError');
async function createScene(host: HTMLElement): Promise<CompareScene> {
  const scene = new Scene();
  try {await scene.init(host);} catch (error) {scene.dispose(); throw error;}
  return scene;
}
const clamp = (side: CompareSide, time: number): number | null => {
  const range = side.recording.finiteRange;
  return range && Number.isFinite(time) ? Math.max(range[0], Math.min(range[1], time)) : null;
};

/** Two sessions, two scenes, one FrameClock. Every transport command acts on both sides; a seek or
 * step issued directly on either side's Playback is followed by the other. Disposal releases only
 * the two compare scenes, so another live scene keeps Pixi's shared resources (D90). */
export class ComparePlayback {
  readonly clock: FrameClock;
  readonly #slots: [Slot, Slot] = [0, 1].map(() => ({epoch: 0, abort: null, side: null, unsubscribe: null, shown: null})) as [Slot, Slot];
  readonly #listeners = new Set<CompareListener>();
  readonly #scene: (host: HTMLElement) => Promise<CompareScene>;
  #epoch = 0;
  #commands = 0;
  #rate = 1;
  #resume = false;
  #disposed = false;
  constructor(private readonly headers: Readonly<Record<string, string>>, options: Options = {}) {
    this.clock = new FrameClock(options.clock);
    this.#scene = options.scene ?? createScene;
  }
  side(index: Side): CompareSide | null {return this.#slots[index].side;}
  private sides(): CompareSide[] {return this.#slots.flatMap(slot => slot.side ? [slot.side] : []);}
  /** Play intent of either side; a side that reached its end stops while the other continues. */
  get playing(): boolean {return this.sides().some(side => side.playback.playRequested);}
  /** The shared position: the later of both sides' times (a shorter recording holds its end). */
  get time(): number | null {
    const times = this.sides().map(side => side.playback.frame?.time).filter((time): time is number => time !== undefined && Number.isFinite(time));
    return times.length ? Math.max(...times) : null;
  }
  /** The union of both finite time ranges. */
  get range(): readonly [number, number] | null {
    const ranges = this.sides().flatMap(side => side.recording.finiteRange ? [side.recording.finiteRange] : []);
    return ranges.length ? [Math.min(...ranges.map(range => range[0])), Math.max(...ranges.map(range => range[1]))] : null;
  }
  get rate(): number {return this.#rate;}
  set rate(value: number) {
    if (!Number.isFinite(value) || value <= 0 || value > 1024) throw new RangeError('Playback rate must be in (0, 1024]');
    const playing = this.playing;
    this.#rate = value;
    for (const side of this.sides()) side.playback.rate = value;
    // Restart both in one turn so they re-anchor on the same frame.
    if (playing) void this.play();
  }
  subscribe(listener: CompareListener): () => void {
    this.#listeners.add(listener);
    return () => {this.#listeners.delete(listener);};
  }
  private usable(): void {if (this.#disposed) throw new Error('Compare playback is disposed');}
  /** Opens a committed result through the authenticated prefix endpoint; refuses a KPI-only recording. */
  async load(index: Side, prefix: Prefix, host: HTMLElement | null): Promise<CompareSide> {
    this.usable();
    this.close(index);
    const slot = this.#slots[index], epoch = slot.epoch, abort = new AbortController();
    slot.abort = abort;
    this.halt();
    const current = () => epoch === slot.epoch && !this.#disposed;
    try {
      const source = await HttpSource.open(prefix.url, this.headers, abort.signal);
      if (!current()) throw superseded();
      const recording = await openRecording(source, {}, abort.signal);
      if (!current()) throw superseded();
      if (recording.trace.level !== 'full') throw new CompareRefused({code: 'kpi_only', level: recording.trace.level});
      const session = new Session(recording);
      session.setSeries(recording.seriesKeys.slice(0, 8));
      const playback = new Playback(session, this.clock);
      playback.rate = this.#rate;
      let scene: CompareScene | null = null;
      try {
        scene = host ? await this.#scene(host) : null;
      } catch (error) {playback.dispose(); throw error;}
      if (!current()) {scene?.dispose(); playback.dispose(); throw superseded();}
      const side: CompareSide = {attempt: prefix.attempt, recording, playback, scene};
      slot.side = side;
      slot.unsubscribe = playback.subscribe((frame, status, error) => this.present(index, side, frame, status, error));
      // Join the other side's position; a fresh pair starts at the first recording's start.
      const time = this.time ?? recording.finiteRange?.[0] ?? 0;
      await this.command(() => this.seekSide(side, time));
      return side;
    } finally {
      if (slot.abort === abort) slot.abort = null;
    }
  }
  /** Disposes one side's session and scene. */
  close(index: Side): void {
    const slot = this.#slots[index];
    slot.epoch++;
    slot.abort?.abort(superseded());
    slot.abort = null;
    slot.unsubscribe?.();
    slot.unsubscribe = null;
    slot.side?.playback.dispose();
    slot.side?.scene?.dispose();
    slot.side = null;
    slot.shown = null;
  }
  private present(index: Side, side: CompareSide, frame: PreparedFrame | null, status: PlaybackStatus, error?: unknown): void {
    const slot = this.#slots[index];
    if (slot.side !== side) return;
    side.scene?.setPlaying(status === 'playing');
    const changed = frame !== null && frame !== slot.shown;
    if (changed) {slot.shown = frame; side.scene?.render(frame);}
    for (const listener of [...this.#listeners]) listener(index, frame, status, error);
    // A seek or step issued on this side's own Playback: the other side follows.
    if (changed && status === 'paused' && this.#commands === 0 && Number.isFinite(frame.time)) {
      const other = this.#slots[index === 0 ? 1 : 0].side;
      if (other && other.playback.frame?.time !== clamp(other, frame.time)) void this.seekTime(frame.time);
    }
  }
  private async command<T>(run: () => Promise<T>): Promise<T> {
    this.#commands++;
    try {return await run();} finally {this.#commands--;}
  }
  private halt(): void {for (const side of this.sides()) side.playback.pause();}
  private async seekSide(side: CompareSide, time: number): Promise<void> {
    const target = clamp(side, time);
    const frame = side.playback.frame;
    if (target === null || frame?.mode === 'continuous' && frame.time === target) return;
    await side.playback.seek({mode: 'continuous', time: target, cursor: null});
  }
  private async align(time: number | null): Promise<void> {
    if (time !== null) await Promise.all(this.sides().map(side => this.seekSide(side, time)));
  }
  /** Brings both sides to one time, then starts both in one turn so their first tick shares a frame. */
  async play(): Promise<void> {
    this.usable();
    const epoch = ++this.#epoch;
    this.halt();
    await this.command(() => this.align(this.time));
    if (epoch !== this.#epoch || this.#disposed) return;
    for (const side of this.sides()) side.playback.play();
  }
  /** Stops both and aligns them on the later time, so a side that lagged a frame catches up. */
  async pause(): Promise<void> {
    if (this.#disposed) return;
    ++this.#epoch;
    this.#resume = false;
    this.halt();
    await this.command(() => this.align(this.time));
  }
  async seekTime(time: number): Promise<void> {
    this.usable();
    ++this.#epoch;
    this.#resume = false;
    this.halt();
    for (const side of this.sides()) side.scene?.resetTransfers();
    await this.command(() => this.align(time));
  }
  /** Moves both sides to the nearest event time of either recording in that direction. */
  async step(direction: 1 | -1): Promise<void> {
    this.usable();
    const epoch = ++this.#epoch;
    this.#resume = false;
    this.halt();
    const time = this.time, sides = this.sides();
    if (time === null) return;
    await this.command(async () => {
      const found = await Promise.all(sides.map(async side => (await side.recording.events(direction === 1
        ? {after: [time, Number.MAX_SAFE_INTEGER], limit: 1}
        : {before: [time, Number.MIN_SAFE_INTEGER], direction: 'backward', limit: 1}))[0]?.t ?? null));
      if (epoch !== this.#epoch || this.#disposed) return;
      const times = found.filter((value): value is number => value !== null);
      const start = Math.min(...sides.map(side => side.recording.finiteRange?.[0] ?? time));
      const target = times.length ? (direction === 1 ? Math.min(...times) : Math.max(...times)) : direction === -1 ? start : null;
      if (target !== null) await this.align(target);
    });
  }
  /** A hidden page pauses both sides; becoming visible resumes both together. */
  setHidden(hidden: boolean): void {
    if (this.#disposed) return;
    if (hidden) {
      if (!this.playing) return;
      void this.pause();
      this.#resume = true;
    } else if (this.#resume) {
      this.#resume = false;
      void this.play();
    }
  }
  dispose(): void {
    if (this.#disposed) return;
    this.#disposed = true;
    ++this.#epoch;
    this.close(0);
    this.close(1);
    this.clock.dispose();
    this.#listeners.clear();
  }
}
