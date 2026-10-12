import {
  compare,
  type Cursor,
  type LayoutBundle,
  type RecordingView,
  type Sample,
  type SeriesDisplay,
  type State,
  type Wire,
  type TraceEvent,
} from "@simulatte/trace";
export type Position = {
  readonly mode: "activation" | "exact" | "continuous";
  readonly time: number;
  readonly cursor: Cursor | null;
};
export interface KpiValue {
  readonly value: number | null;
  readonly sourceTime: number | null;
  readonly interpolated: boolean;
}
export interface PreparedFrame extends Position {
  /** Latest 256 events crossed since the last successful frame, in cursor order. */
  readonly crossedEvents?: readonly TraceEvent[];
  /** False for initial/reset/backward frames or a truncated crossing interval. */
  readonly crossingsComplete?: boolean;
  readonly state: State;
  readonly initialState?: State | null;
  readonly layout: LayoutBundle | null;
  readonly catalog: Readonly<Record<string, Wire>>;
  readonly kpis: Readonly<Record<string, KpiValue>>;
  readonly diagnostics: readonly string[];
}
const DEFAULT_DISPLAY: SeriesDisplay = {
  interpolation: "previous",
  missing: "gap",
};
// Keep normal playback on cached pages, but never enumerate a whole recording
// just to resolve a distant seek. At most two event pages are crossed per frame.
const FORWARD_CURSOR_SCAN_LIMIT = 400;
const CROSSED_EVENTS_LIMIT = 256;
interface CursorFrame {
  readonly cursor: Cursor | null;
  readonly crossedEvents: readonly TraceEvent[];
  readonly crossingsComplete: boolean;
}
/** The same eligibility rules serve interactive playback and manual export. */
export function kpiValue(
  position: Position,
  display: SeriesDisplay,
  left: Sample | null,
  right: Sample | null,
  finite: Sample | null,
): KpiValue {
  const empty = { value: null, sourceTime: null, interpolated: false };
  if (position.mode === "activation" || !left) return empty;
  const a = Number.isFinite(left.value)
    ? left.value
    : display.missing === "hold"
      ? (finite?.value ?? null)
      : null;
  if (a === null) return empty;
  const recorded = {
    value: a,
    sourceTime: left.cursor[0],
    interpolated: false,
  };
  if (display.interpolation === "none")
    return left.cursor[0] === position.time ? recorded : empty;
  if (
    position.mode === "exact" ||
    display.interpolation === "previous" ||
    left.cursor[0] === position.time
  )
    return recorded;
  if (
    !right ||
    !Number.isFinite(position.time) ||
    !Number.isFinite(left.cursor[0]) ||
    !Number.isFinite(right.cursor[0])
  )
    return empty;
  const b = Number.isFinite(right.value)
    ? right.value
    : display.missing === "hold"
      ? a
      : null;
  if (b === null || right.cursor[0] <= left.cursor[0]) return empty;
  const fraction =
    (position.time - left.cursor[0]) / (right.cursor[0] - left.cursor[0]);
  return {
    value: a + (b - a) * fraction,
    sourceTime: position.time,
    interpolated: true,
  };
}

/** A forward sample cursor retains one wire record through its generator, not a whole series. */
class SeriesCursor {
  #iterator: AsyncGenerator<Sample> | null = null;
  #lifetime: AbortController | null = null;
  #at: Cursor | null = null;
  #left: Sample | null = null;
  #right: Sample | null = null;
  #next: Sample | null = null;
  #finite: Sample | null = null;
  #rightFinite: Sample | null = null;
  constructor(
    private recording: RecordingView,
    private key: string,
  ) {}
  private async nextGroup(): Promise<void> {
    const signal = this.#lifetime!.signal;
    this.#right = this.#next;
    this.#rightFinite = null;
    const time = this.#next?.cursor[0];
    while (this.#next && this.#next.cursor[0] === time) {
      signal.throwIfAborted();
      this.#right = this.#next;
      if (Number.isFinite(this.#next.value)) this.#rightFinite = this.#next;
      this.#next = (await this.#iterator!.next()).value ?? null;
      signal.throwIfAborted();
    }
  }
  async at(
    cursor: Cursor,
    hold: boolean,
    signal?: AbortSignal,
  ): Promise<{
    left: Sample | null;
    right: Sample | null;
    finite: Sample | null;
  }> {
    signal?.throwIfAborted();
    const restart =
      !this.#iterator || (this.#at && compare(cursor, this.#at) < 0);
    if (restart) {
      this.dispose();
      this.#lifetime = new AbortController();
      this.#iterator = this.recording.samples(this.key, this.#lifetime.signal);
    }
    // The iterator outlives individual frames. Forward cancellation only while this
    // request owns it, so aborting an older completed seek cannot poison later reads.
    const lifetime = this.#lifetime!;
    const cancel = () => lifetime.abort(signal?.reason);
    signal?.addEventListener("abort", cancel, { once: true });
    try {
      if (restart) {
        this.#next = (await this.#iterator!.next()).value ?? null;
        lifetime.signal.throwIfAborted();
        await this.nextGroup();
      }
      while (this.#right && compare(this.#right.cursor, cursor) <= 0) {
        this.#left = this.#right;
        if (this.#rightFinite) this.#finite = this.#rightFinite;
        await this.nextGroup();
      }
      this.#at = cursor;
      const right =
        hold &&
        this.#right &&
        !Number.isFinite(this.#right.value) &&
        this.#rightFinite
          ? { ...this.#right, value: this.#rightFinite.value }
          : this.#right;
      lifetime.signal.throwIfAborted();
      return { left: this.#left, right, finite: this.#finite };
    } catch (error) {
      this.dispose();
      throw error;
    } finally {
      signal?.removeEventListener("abort", cancel);
    }
  }
  dispose(): void {
    this.#lifetime?.abort();
    void this.#iterator?.return(undefined).catch(() => {});
    this.#iterator = null;
    this.#lifetime = null;
    this.#at =
      this.#left =
      this.#right =
      this.#next =
      this.#finite =
      this.#rightFinite =
        null;
  }
}

export class Session {
  readonly #replay;
  readonly #series = new Map<string, SeriesCursor>();
  #busy = false;
  #disposed = false;
  #active: AbortController | null = null;
  #queued: {
    position: Position;
    signal: AbortSignal | undefined;
    resolve: (frame: PreparedFrame) => void;
    reject: (error: unknown) => void;
  } | null = null;
  #events: readonly TraceEvent[] = [];
  #eventOffset = 0;
  #eventAt: Cursor | null = null;
  #time: number | null = null;
  #prepared: Position | null = null;
  constructor(readonly recording: RecordingView) {
    this.#replay = recording.trace.replay();
  }
  setSeries(keys: readonly string[]): void {
    if (keys.length > 8) throw new RangeError("At most eight active series");
    for (const [key, series] of this.#series)
      if (!keys.includes(key)) {
        series.dispose();
        this.#series.delete(key);
      }
    for (const key of keys)
      if (!this.#series.has(key))
        this.#series.set(key, new SeriesCursor(this.recording, key));
  }
  prepare(position: Position, signal?: AbortSignal): Promise<PreparedFrame> {
    if (this.#disposed)
      return Promise.reject(new Error("Playback session is disposed"));
    return new Promise((resolve, reject) => {
      this.#queued?.reject(
        new DOMException(
          "Preparation replaced by a newer request",
          "AbortError",
        ),
      );
      this.#queued = { position, signal, resolve, reject };
      if (!this.#busy) void this.drain();
    });
  }
  private async drain(): Promise<void> {
    this.#busy = true;
    while (this.#queued) {
      const next = this.#queued;
      this.#queued = null;
      const active = new AbortController();
      this.#active = active;
      const cancel = () => active.abort(next.signal?.reason);
      if (next.signal?.aborted) cancel();
      else next.signal?.addEventListener("abort", cancel, { once: true });
      try {
        const frame = await this.prepareOne(next.position, active.signal);
        if (this.#disposed)
          throw new DOMException("Session disposed", "AbortError");
        active.signal.throwIfAborted();
        this.#prepared = {
          mode: frame.mode,
          time: frame.time,
          cursor: frame.cursor,
        };
        next.resolve(frame);
      } catch (error) {
        this.#time = null;
        next.reject(error);
      } finally {
        next.signal?.removeEventListener("abort", cancel);
        this.#active = null;
      }
    }
    this.#busy = false;
  }
  private async prepareOne(
    position: Position,
    signal?: AbortSignal,
  ): Promise<PreparedFrame> {
    signal?.throwIfAborted();
    const r = this.recording;
    let at = position.cursor;
    let crossings: CursorFrame = {
      cursor: at,
      crossedEvents: [],
      crossingsComplete: false,
    };
    if (position.mode === "continuous") {
      crossings = await this.cursorAtTime(position.time, signal);
      at = crossings.cursor;
    } else {
      this.#time = null;
      const previous = this.#prepared;
      if (
        position.mode === "exact" &&
        at &&
        previous?.cursor &&
        position.time >= previous.time &&
        compare(at, previous.cursor) >= 0
      ) {
        crossings =
          compare(at, previous.cursor) === 0
            ? { cursor: at, crossedEvents: [], crossingsComplete: true }
            : await this.indexedCrossings(at, previous.cursor, signal);
      }
    }
    if (position.mode === "activation") at = r.trace.activation;
    if (
      position.mode !== "activation" &&
      this.#prepared &&
      position.time >= this.#prepared.time &&
      (at === this.#prepared.cursor ||
        (at &&
          this.#prepared.cursor &&
          compare(at, this.#prepared.cursor) === 0))
    )
      crossings = { cursor: at, crossedEvents: [], crossingsComplete: true };
    const cursor = at && r.domainCursor(at);
    signal?.throwIfAborted();
    // Replay serializes state commits; each request independently owns its
    // shared chunk-read subscription and can cancel without poisoning peers.
    const state = cursor
      ? await this.#replay.prepare(cursor, signal)
      : (Object.freeze(Object.create(null)) as State);
    signal?.throwIfAborted();
    const resolved = { ...position, cursor: at };
    const kpis: Record<string, KpiValue> = Object.create(null) as Record<
      string,
      KpiValue
    >;
    for (const [key, series] of this.#series) {
      const eligibility: Cursor =
        position.mode === "continuous"
          ? [position.time, Number.MAX_SAFE_INTEGER]
          : (at ?? [-Infinity, -1]);
      const display = r.seriesDisplay[key] ?? DEFAULT_DISPLAY;
      const bracket =
        position.mode === "continuous"
          ? await series.at(eligibility, display.missing === "hold", signal)
          : await r.sampleBracket(key, eligibility, signal);
      kpis[key] = Object.freeze(
        kpiValue(
          resolved,
          display,
          bracket.left,
          bracket.right,
          bracket.finite,
        ),
      );
    }
    signal?.throwIfAborted();
    return Object.freeze({
      ...resolved,
      crossedEvents: Object.freeze(crossings.crossedEvents),
      crossingsComplete: crossings.crossingsComplete,
      state,
      initialState: r.trace.initialState,
      layout: r.trace.layout,
      catalog: r.catalog,
      kpis: Object.freeze(kpis),
      diagnostics: Object.freeze([
        ...(r.trace.truncated
          ? ["Incomplete trace: showing committed records"]
          : []),
        ...(r.trace.level === "kpi"
          ? [
              "KPI-only recording: entity animation and event stepping are unavailable",
            ]
          : []),
        ...(r.trace.layout && r.trace.layout.schema_version !== "1.0"
          ? ["Unknown layout version: using viewer fallback geometry"]
          : []),
        ...(r.trace.endTime !== null && !Number.isFinite(r.trace.endTime)
          ? ["Unbounded trace time: playback uses the finite recorded range"]
          : []),
        ...(!Number.isFinite(position.time)
          ? [
              "Static inspection at a non-finite time; select a finite time to play",
            ]
          : []),
        ...(r.trace.oversizedWorkingSet
          ? ["A decoded chunk exceeds the estimated 128 MiB cache budget"]
          : []),
      ]),
    });
  }
  private async indexedCursorAtTime(
    time: number,
    after: Cursor | null,
    signal?: AbortSignal,
  ): Promise<CursorFrame> {
    const result = await this.indexedCrossings(
      [time, Number.MAX_SAFE_INTEGER],
      after,
      signal,
    );
    this.#eventAt = result.cursor;
    this.#events = [];
    this.#eventOffset = 0;
    return result;
  }
  private async indexedCrossings(
    through: Cursor,
    after: Cursor | null,
    signal?: AbortSignal,
  ): Promise<CursorFrame> {
    signal?.throwIfAborted();
    const events = await this.recording.events({
      through,
      ...(after ? { after } : {}),
      direction: "backward",
      limit: after ? CROSSED_EVENTS_LIMIT + 1 : 1,
      ...(signal ? { signal } : {}),
    });
    signal?.throwIfAborted();
    const event = events[0];
    return {
      cursor: event
        ? [event.t, event.seq]
        : (after ?? this.recording.trace.activation),
      crossedEvents: after
        ? events.slice(0, CROSSED_EVENTS_LIMIT).reverse()
        : [],
      crossingsComplete:
        after !== null && events.length <= CROSSED_EVENTS_LIMIT,
    };
  }
  private async cursorAtTime(
    time: number,
    signal?: AbortSignal,
  ): Promise<CursorFrame> {
    const previous = this.#prepared;
    const after = previous && time >= previous.time ? previous.cursor : null;
    let indexed: CursorFrame | null = null;
    if (this.#time === null || time < this.#time)
      indexed = await this.indexedCursorAtTime(time, after, signal);
    this.#time = time;
    let advanced = 0;
    const crossedEvents: TraceEvent[] = [];
    for (;;) {
      signal?.throwIfAborted();
      if (advanced === FORWARD_CURSOR_SCAN_LIMIT)
        return this.indexedCursorAtTime(time, after, signal);
      if (this.#eventOffset >= this.#events.length) {
        this.#events = await this.recording.events({
          ...(this.#eventAt ? { after: this.#eventAt } : {}),
          limit: 200,
          ...(signal ? { signal } : {}),
        });
        this.#eventOffset = 0;
        if (!this.#events.length) break;
      }
      const event = this.#events[this.#eventOffset]!;
      if (event.t > time) break;
      this.#eventAt = [event.t, event.seq];
      this.#eventOffset++;
      advanced++;
      crossedEvents.push(event);
      if (crossedEvents.length > CROSSED_EVENTS_LIMIT) crossedEvents.shift();
    }
    return (
      indexed ?? {
        cursor: this.#eventAt,
        crossedEvents,
        crossingsComplete: after !== null && advanced <= CROSSED_EVENTS_LIMIT,
      }
    );
  }
  dispose(): void {
    this.#disposed = true;
    this.#active?.abort(new DOMException("Session disposed", "AbortError"));
    this.#queued?.reject(new DOMException("Session disposed", "AbortError"));
    this.#queued = null;
    for (const series of this.#series.values()) series.dispose();
    this.#series.clear();
  }
}

export type PlaybackStatus =
  | "paused"
  | "playing"
  | "buffering"
  | "ended"
  | "error"
  | "disposed";
export interface Clock {
  request(callback: (now: number) => void): number;
  cancel(id: number): void;
  now?(): number;
}
const browserClock: Clock = {
  request: (cb) => requestAnimationFrame(cb),
  cancel: (id) => cancelAnimationFrame(id),
  now: () => performance.now(),
};
/** One clock owns advancement; consumers receive atomic, immutable frames. Manual sessions have no clock. */
export class Playback {
  #frame: PreparedFrame | null = null;
  #status: PlaybackStatus = "paused";
  #generation = 0;
  #request = 0;
  #last: number | null = null;
  #rate = 1;
  #wanted = false;
  #hidden = false;
  #pending: AbortController | null = null;
  #listeners = new Set<
    (
      frame: PreparedFrame | null,
      status: PlaybackStatus,
      error?: unknown,
    ) => void
  >();
  get frame(): PreparedFrame | null {
    return this.#frame;
  }
  get status(): PlaybackStatus {
    return this.#status;
  }
  get rate(): number {
    return this.#rate;
  }
  set rate(value: number) {
    if (!Number.isFinite(value) || value <= 0 || value > 1024)
      throw new RangeError("Playback rate must be in (0, 1024]");
    this.#rate = value;
    this.#last = null;
  }
  constructor(
    readonly session: Session,
    private clock: Clock = browserClock,
  ) {}
  subscribe(
    fn: (
      frame: PreparedFrame | null,
      status: PlaybackStatus,
      error?: unknown,
    ) => void,
  ): () => void {
    this.#listeners.add(fn);
    return () => this.#listeners.delete(fn);
  }
  private emit(error?: unknown): void {
    for (const fn of this.#listeners) fn(this.#frame, this.#status, error);
  }
  private invalidate(): number {
    this.clock.cancel(this.#request);
    this.#last = null;
    this.#pending?.abort();
    return ++this.#generation;
  }
  async seek(position: Position): Promise<void> {
    if (this.#status === "disposed") throw new Error("Playback is disposed");
    this.#wanted = false;
    const generation = this.invalidate();
    this.#status = "buffering";
    this.emit();
    const pending = new AbortController();
    this.#pending = pending;
    try {
      const frame = await this.session.prepare(position, pending.signal);
      if (generation !== this.#generation) return;
      this.#frame = frame;
      this.#status = "paused";
      this.emit();
    } catch (e) {
      if (generation === this.#generation) {
        this.#status = "error";
        this.emit(e);
      }
    }
  }
  async seekTime(time: number): Promise<void> {
    const range = this.session.recording.finiteRange;
    if (!Number.isFinite(time) || !range)
      throw new RangeError("No finite playback time");
    await this.seek({
      mode: "continuous",
      time: Math.max(range[0], Math.min(range[1], time)),
      cursor: null,
    });
  }
  async step(direction: 1 | -1): Promise<void> {
    this.pause();
    const generation = this.#generation;
    const at = this.#frame?.cursor;
    const pending = new AbortController();
    this.#pending = pending;
    let rows: readonly TraceEvent[];
    try {
      rows = await this.session.recording.events({
        ...(at ? (direction === 1 ? { after: at } : { before: at }) : {}),
        direction: direction === 1 ? "forward" : "backward",
        limit: 1,
        signal: pending.signal,
      });
    } catch (error) {
      if (generation === this.#generation) {
        this.#status = "error";
        this.emit(error);
      }
      return;
    }
    if (generation !== this.#generation) return;
    const event = rows[0];
    if (event)
      await this.seek({
        mode: "exact",
        time: event.t,
        cursor: [event.t, event.seq],
      });
    else if (direction === -1)
      await this.seek({
        mode: "activation",
        time: this.session.recording.trace.activation?.[0] ?? 0,
        cursor: null,
      });
  }
  play(): void {
    if (
      this.#status === "disposed" ||
      !this.#frame ||
      !Number.isFinite(this.#frame.time) ||
      !this.session.recording.finiteRange
    )
      return;
    this.invalidate();
    this.#wanted = true;
    this.#status = "playing";
    this.emit();
    this.schedule();
  }
  pause(): void {
    if (this.#status === "disposed") return;
    this.#wanted = false;
    this.invalidate();
    this.#status = "paused";
    this.emit();
  }
  setHidden(hidden: boolean): void {
    this.#hidden = hidden;
    if (hidden) {
      this.invalidate();
    } else if (this.#wanted) this.schedule();
  }
  private schedule(): void {
    if (this.#wanted && !this.#hidden)
      this.#request = this.clock.request((now) => {
        void this.tick(now);
      });
  }
  private async tick(now: number): Promise<void> {
    if (!this.#wanted || !this.#frame) return;
    const end = this.session.recording.finiteRange?.[1];
    if (end === undefined) return;
    if (this.#last === null) {
      this.#last = now;
      this.schedule();
      return;
    }
    const target = Math.min(
      end,
      this.#frame.time + (Math.max(0, now - this.#last) / 1000) * this.#rate,
    );
    const generation = this.#generation;
    this.#status = "buffering";
    this.emit();
    const pending = new AbortController();
    this.#pending = pending;
    try {
      let position: Position = {
        mode: "continuous",
        time: target,
        cursor: null,
      };
      if (this.#frame.mode !== "continuous" && this.#frame.cursor) {
        const next = await this.session.recording.events({
          after: this.#frame.cursor,
          through: [this.#frame.time, Number.MAX_SAFE_INTEGER],
          limit: 1,
          signal: pending.signal,
        });
        if (generation !== this.#generation) return;
        if (next[0])
          position = {
            mode: "exact",
            time: next[0].t,
            cursor: [next[0].t, next[0].seq],
          };
      }
      const frame = await this.session.prepare(position, pending.signal);
      if (generation !== this.#generation) return;
      this.#frame = frame;
      this.#last = this.clock.now?.() ?? now;
      if (position.mode === "continuous" && target >= end) {
        this.#wanted = false;
        this.#status = "ended";
      } else this.#status = "playing";
      this.emit();
      this.schedule();
    } catch (e) {
      if (generation === this.#generation) {
        this.#wanted = false;
        this.#status = "error";
        this.emit(e);
      }
    }
  }
  dispose(): void {
    this.#wanted = false;
    this.invalidate();
    this.#status = "disposed";
    this.session.dispose();
    this.emit();
    this.#listeners.clear();
  }
}
