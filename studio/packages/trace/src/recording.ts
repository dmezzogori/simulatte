/** Viewer queries over domain chunks and the separately committed observer/KPI streams. */
import {
  byteSource,
  FRAME_SIZE,
  PREAMBLE_SIZE,
  readFrame,
  readPayload,
  RecordType,
  resolveLimits,
  TraceCorrupted,
  type ByteSource,
  type ReaderLimits,
} from "./container";
import {
  compare,
  openTrace,
  traceEvent,
  type Cursor,
  type Trace,
  type TraceEvent,
  type OpenOptions,
} from "./trace";
import { decodeWire, type Wire } from "./wire";

type MapValue = Readonly<Record<string, Wire>>;
export interface SeriesDisplay {
  readonly interpolation: "previous" | "linear" | "none";
  readonly missing: "gap" | "hold";
}
export interface Sample {
  readonly cursor: Cursor;
  readonly key: string;
  readonly value: number;
}
export interface EventQuery {
  after?: Cursor;
  before?: Cursor;
  through?: Cursor;
  direction?: "forward" | "backward";
  limit?: number;
  phase?: "prelude" | "simulation";
  type?: string;
  entity?: string;
  text?: string;
  signal?: AbortSignal;
}
interface RecordIndex {
  offset: number;
  type: number;
  first: Cursor;
  last: Cursor;
  phase: string;
  chunk?: number;
}
interface KpiIndex {
  offset: number;
  first: Cursor;
  last: Cursor;
  keys: ReadonlySet<string>;
}
const map = (value: unknown): MapValue => {
  if (value === null || typeof value !== "object" || Array.isArray(value))
    throw new TraceCorrupted("Expected metadata map");
  return value as MapValue;
};
const array = (value: unknown): readonly Wire[] => {
  if (!Array.isArray(value))
    throw new TraceCorrupted("Expected metadata array");
  return value as readonly Wire[];
};
const cursor = (value: unknown): Cursor => {
  const a = array(value);
  if (
    a.length !== 2 ||
    typeof a[0] !== "number" ||
    Number.isNaN(a[0]) ||
    !Number.isSafeInteger(a[1])
  ) {
    throw new TraceCorrupted("Invalid metadata cursor");
  }
  return Object.freeze([a[0], a[1] as number]);
};
const sample = (value: unknown): Sample => {
  const a = array(value);
  if (a.length !== 4 || typeof a[2] !== "string" || typeof a[3] !== "number")
    throw new TraceCorrupted("Invalid KPI sample");
  return Object.freeze({
    cursor: cursor([a[1], a[0]]),
    key: a[2],
    value: a[3],
  });
};

export async function openRecording(
  source: Blob | ArrayBuffer | ByteSource,
  options: OpenOptions = {},
  signal?: AbortSignal,
): Promise<Recording> {
  const base = byteSource(source);
  const bytes: ByteSource = {
    size: base.size,
    read: async (offset, length, requestSignal) => {
      const active = requestSignal ?? signal;
      active?.throwIfAborted();
      const result = await base.read(offset, length, active);
      active?.throwIfAborted();
      return result;
    },
  };
  const trace = await openTrace(bytes, options);
  const recording = new Recording(trace, bytes, resolveLimits(options.limits));
  await recording.load(signal);
  return recording;
}

export class Recording {
  readonly kinds: Readonly<Record<string, Wire>> = Object.create(null) as Record<
    string,
    Wire
  >;
  readonly catalog: Record<string, Wire> = Object.create(null) as Record<
    string,
    Wire
  >;
  readonly scalars: Record<string, number> = Object.create(null) as Record<
    string,
    number
  >;
  readonly seriesDisplay: Record<string, SeriesDisplay> = Object.create(
    null,
  ) as Record<string, SeriesDisplay>;
  readonly #seriesKeys = new Set<string>();
  get seriesKeys(): readonly string[] {
    return Object.freeze([...this.#seriesKeys]);
  }
  #records: RecordIndex[] = [];
  #kpis: KpiIndex[] = [];
  #finite: [number, number] | null = null;
  #history: Cursor | null = null;
  #initialSeen = false;
  #epochs = 0;
  get finiteRange(): readonly [number, number] | null {
    return this.#finite && Object.freeze([...this.#finite] as [number, number]);
  }
  get historyEnd(): Cursor | null {
    return this.#history;
  }
  constructor(
    readonly trace: Trace,
    private readonly source: ByteSource,
    private readonly limits: ReaderLimits,
  ) {}

  private time(t: number): void {
    if (!Number.isFinite(t)) return;
    if (!this.#finite) this.#finite = [t, t];
    else {
      this.#finite[0] = Math.min(this.#finite[0], t);
      this.#finite[1] = Math.max(this.#finite[1], t);
    }
  }
  private async body(
    offset: number,
    signal?: AbortSignal,
  ): Promise<MapValue | null> {
    signal?.throwIfAborted();
    const frame = await readFrame(
      this.source,
      offset,
      this.source.size,
      this.limits,
      signal,
    );
    if (!frame) return null;
    const bytes = await readPayload(this.source, offset, frame, signal);
    if (!bytes) return null;
    return map(
      decodeWire(bytes, {
        maxDepth: this.limits.maxDepth,
        maxLen: this.limits.maxLen,
      }),
    );
  }
  /** Index one record at a time, yielding to the browser between records. */
  async load(signal?: AbortSignal): Promise<void> {
    Object.assign(this.catalog, map(this.trace.header["catalog"]));
    Object.assign(this.kinds, map(this.trace.header["kinds"]));
    let previousObserver: Cursor | null = null;
    let pos = PREAMBLE_SIZE;
    const chunks = new Map(this.trace.index.map((info, i) => [info.offset, i]));
    while (pos < this.source.size) {
      signal?.throwIfAborted();
      const frame = await readFrame(
        this.source,
        pos,
        this.source.size,
        this.limits,
        signal,
      );
      if (!frame || frame.type === RecordType.FOOTER) break;
      if (frame.type === RecordType.CHUNK) {
        const chunk = chunks.get(pos);
        if (chunk !== undefined) {
          const info = this.trace.index[chunk]!;
          this.#records.push({
            offset: pos,
            type: frame.type,
            first: info.first,
            last: info.last,
            phase: "simulation",
            chunk,
          });
          this.time(info.first[0]);
          this.time(info.last[0]);
          if (
            !Number.isFinite(info.first[0]) ||
            !Number.isFinite(info.last[0])
          ) {
            for (const event of await this.trace.chunkEvents(chunk, signal))
              this.time(event.t);
          }
        }
      } else if (
        (
          [
            RecordType.INITIAL,
            RecordType.CATALOG_EXT,
            RecordType.KPI,
            RecordType.PRELUDE,
            RecordType.OBSERVERS,
          ] as readonly number[]
        ).includes(frame.type)
      ) {
        const body = await this.body(pos, signal);
        if (!body) {
          if (!this.trace.truncated)
            throw new TraceCorrupted("Corrupted viewer metadata");
          break;
        }
        if (frame.type === RecordType.INITIAL) {
          this.#initialSeen = true;
          this.time(cursor(body["cursor"])[0]);
        } else if (frame.type === RecordType.CATALOG_EXT) {
          if (
            !Number.isSafeInteger(body["epoch"]) ||
            (body["epoch"] as number) <= this.#epochs
          )
            throw new TraceCorrupted("Invalid catalog epoch");
          this.#epochs = body["epoch"] as number;
          Object.assign(this.catalog, map(body["types"]));
          Object.assign(this.kinds, map(body["kinds"]));
        } else if (frame.type === RecordType.KPI) {
          for (const [key, value] of Object.entries(
            map(body["scalars"] ?? {}),
          )) {
            if (typeof value !== "number")
              throw new TraceCorrupted("Invalid KPI scalar");
            this.scalars[key] = value;
          }
          for (const [key, raw] of Object.entries(
            map(body["series_display"] ?? {}),
          )) {
            const rule = map(raw);
            if (
              Object.hasOwn(this.seriesDisplay, key) ||
              !this.trace.kpiDeclarations[key] ||
              Object.keys(rule).length !== 2 ||
              typeof rule["interpolation"] !== "string" ||
              !["previous", "linear", "none"].includes(rule["interpolation"]) ||
              typeof rule["missing"] !== "string" ||
              !["gap", "hold"].includes(rule["missing"])
            )
              throw new TraceCorrupted("Invalid KPI display declaration");
            this.seriesDisplay[key] = Object.freeze({
              interpolation: rule["interpolation"],
              missing: rule["missing"],
            }) as unknown as SeriesDisplay;
          }
          const samples = array(body["samples"] ?? []).map(sample);
          if (samples.length) {
            const keys = new Set(samples.map((s) => s.key));
            keys.forEach((key) => this.#seriesKeys.add(key));
            samples.forEach((s) => this.time(s.cursor[0]));
            this.#kpis.push({
              offset: pos,
              first: samples[0]!.cursor,
              last: samples[samples.length - 1]!.cursor,
              keys,
            });
          }
        } else {
          const events = array(body["events"]).map(traceEvent);
          const first = cursor(body["first"]),
            last = cursor(body["last"]);
          if (
            !events.length ||
            compare(first, [events[0]!.t, events[0]!.seq]) ||
            compare(last, [
              events[events.length - 1]!.t,
              events[events.length - 1]!.seq,
            ])
          )
            throw new TraceCorrupted("Event bounds mismatch");
          const phase =
            frame.type === RecordType.PRELUDE
              ? "prelude"
              : String(body["phase"]);
          if (frame.type === RecordType.OBSERVERS) {
            if (
              Object.keys(body).length !== 5 ||
              phase !== (this.#initialSeen ? "simulation" : "prelude") ||
              body["epoch"] !== this.#epochs
            )
              throw new TraceCorrupted("Invalid observer metadata");
            for (const e of events) {
              if (
                e.ordinal !== null ||
                e.deltas.length ||
                map(this.catalog[e.type])["category"] !== "observer" ||
                (previousObserver &&
                  compare(previousObserver, [e.t, e.seq]) >= 0)
              )
                throw new TraceCorrupted("Invalid observer event");
              previousObserver = [e.t, e.seq];
            }
          }
          this.#records.push({
            offset: pos,
            type: frame.type,
            first,
            last,
            phase,
          });
          if (phase === "simulation") events.forEach((e) => this.time(e.t));
        }
      }
      pos += FRAME_SIZE + frame.length;
      if (this.#records.length + this.#kpis.length > 1_000_000)
        throw new TraceCorrupted("Viewer metadata index limit exceeded");
      if ((this.#records.length + this.#kpis.length) % 64 === 0)
        await new Promise((resolve) => setTimeout(resolve, 0));
    }
    if (this.trace.endTime !== null) this.time(this.trace.endTime);
    for (const record of this.#records)
      if (
        record.phase === "simulation" &&
        (!this.#history || compare(record.last, this.#history) > 0)
      )
        this.#history = record.last;
    Object.freeze(this.catalog);
    Object.freeze(this.kinds);
    Object.freeze(this.scalars);
    Object.freeze(this.seriesDisplay);
  }

  async events(query: EventQuery = {}): Promise<readonly TraceEvent[]> {
    const limit = query.limit ?? 200;
    if (!Number.isInteger(limit) || limit < 1 || limit > 1000)
      throw new RangeError("Event page limit must be 1–1000");
    const reverse = query.direction === "backward";
    const records = this.#records.filter(
      (r) =>
        r.phase === (query.phase ?? "simulation") &&
        (!query.after || compare(r.last, query.after) > 0) &&
        (!query.before || compare(r.first, query.before) < 0) &&
        (!query.through || compare(r.first, query.through) <= 0),
    );
    records.sort((a, b) =>
      reverse ? compare(b.last, a.last) : compare(a.first, b.first),
    );
    let result: TraceEvent[] = [];
    for (const record of records) {
      query.signal?.throwIfAborted();
      if (result.length === limit) {
        const edge = result[result.length - 1]!;
        if (
          reverse
            ? compare(record.last, [edge.t, edge.seq]) < 0
            : compare(record.first, [edge.t, edge.seq]) > 0
        )
          break;
      }
      const events =
        record.chunk !== undefined
          ? await this.trace.chunkEvents(record.chunk, query.signal)
          : array(
              (await this.body(record.offset, query.signal))?.["events"],
            ).map(traceEvent);
      for (const event of events) {
        const at: Cursor = [event.t, event.seq];
        if (
          (query.after && compare(at, query.after) <= 0) ||
          (query.before && compare(at, query.before) >= 0) ||
          (query.through && compare(at, query.through) > 0)
        )
          continue;
        if (query.type && query.type !== event.type) continue;
        if (query.entity && !event.deltas.some((op) => op[1] === query.entity))
          continue;
        if (
          query.text &&
          !JSON.stringify(event.payload)
            .toLowerCase()
            .includes(query.text.toLowerCase())
        )
          continue;
        // Retain at most one page, including while combining overlapping streams.
        const order = (a: TraceEvent, b: TraceEvent) =>
          (reverse ? -1 : 1) * compare([a.t, a.seq], [b.t, b.seq]);
        let lo = 0,
          hi = result.length;
        while (lo < hi) {
          const mid = (lo + hi) >>> 1;
          if (order(result[mid]!, event) < 0) lo = mid + 1;
          else hi = mid;
        }
        if (lo < limit) {
          result.splice(lo, 0, event);
          if (result.length > limit) result.pop();
        }
      }
      result.sort(
        (a, b) => (reverse ? -1 : 1) * compare([a.t, a.seq], [b.t, b.seq]),
      );
      result = result.slice(0, limit);
    }
    return Object.freeze(result);
  }

  /** Replay query position clamped to the legacy domain bounds. */
  domainCursor(at: Cursor): Cursor | null {
    const bounds = this.trace.cursorRange;
    if (!bounds) return null;
    return compare(at, bounds[0]) < 0
      ? bounds[0]
      : compare(at, bounds[1]) > 0
        ? bounds[1]
        : at;
  }
  async cursorAtTime(time: number): Promise<Cursor | null> {
    const events = await this.events({
      through: [time, Number.MAX_SAFE_INTEGER],
      direction: "backward",
      limit: 1,
    });
    const event = events[0];
    return event
      ? Object.freeze([event.t, event.seq] as Cursor)
      : this.trace.activation;
  }
  /** Samples streamed one bounded wire record at a time. Consumers must not retain an unbounded history. */
  async *samples(key: string, signal?: AbortSignal): AsyncGenerator<Sample> {
    let previous: Cursor | null = null;
    for (const index of this.#kpis)
      if (index.keys.has(key)) {
        signal?.throwIfAborted();
        const body = await this.body(index.offset, signal);
        for (const raw of array(body?.["samples"])) {
          const s = sample(raw);
          if (s.key !== key) continue;
          if (previous && compare(previous, s.cursor) >= 0)
            throw new TraceCorrupted("Nonmonotone KPI samples");
          previous = s.cursor;
          yield s;
        }
      }
  }
  /** Neighbours use recorded samples, never chart decimation. The finite predecessor supports missing=hold. */
  async sampleBracket(
    key: string,
    at: Cursor,
    signal?: AbortSignal,
  ): Promise<
    Readonly<{
      left: Sample | null;
      right: Sample | null;
      finite: Sample | null;
    }>
  > {
    let left: Sample | null = null,
      right: Sample | null = null,
      finite: Sample | null = null;
    for await (const s of this.samples(key, signal)) {
      if (compare(s.cursor, at) <= 0) {
        left = s;
        if (Number.isFinite(s.value)) finite = s;
      } else if (!right || s.cursor[0] === right.cursor[0]) right = s;
      else break;
    }
    return Object.freeze({ left, right, finite });
  }
  /** Display-only buckets preserve first/last, finite extrema and missing-value boundaries. */
  async chart(
    key: string,
    range: readonly [number, number],
    maxPoints = 4000,
    signal?: AbortSignal,
  ): Promise<readonly Sample[]> {
    if (!Number.isInteger(maxPoints) || maxPoints < 10 || maxPoints > 4000)
      throw new RangeError("Chart limit must be 10–4000");
    const buckets = Math.floor(maxPoints / 9), span = range[1] - range[0];
    const result: Sample[] = [];
    const display = this.seriesDisplay[key] ?? {interpolation: "previous", missing: "gap"};
    let bucket = -1, group: Sample[] = [], gap = false, held: number | null = null;
    const flush = () => {
      const unique = [...new Set(group)].sort((a, b) => compare(a.cursor, b.cursor));
      // A bucket with omitted gaps must never invent a continuous line between extrema.
      // Conservatively separate representatives, retaining the bucket's full finite envelope.
      for (const sample of unique) {
        if (gap) result.push({...sample, value: NaN});
        result.push(sample);
      }
      if (gap && unique.length) result.push({...unique[unique.length - 1]!, value: NaN});
    };
    const accept = (raw: Sample) => {
      if (Number.isFinite(raw.value)) held = raw.value;
      const s = !Number.isFinite(raw.value) && display.missing === "hold" && held !== null ? {...raw, value: held} : raw;
      if (!Number.isFinite(s.cursor[0]) || s.cursor[0] < range[0] || s.cursor[0] > range[1]) return;
      const next = span > 0 ? Math.min(buckets - 1, Math.floor(((s.cursor[0] - range[0]) / span) * buckets)) : 0;
      if (next !== bucket) {flush(); group = [s, s, s, s]; gap = !Number.isFinite(s.value); bucket = next;}
      else {
        group[1] = s;
        if (Number.isFinite(s.value)) {
          if (!Number.isFinite(group[2]!.value) || s.value < group[2]!.value) group[2] = s;
          if (!Number.isFinite(group[3]!.value) || s.value > group[3]!.value) group[3] = s;
        } else gap = true;
      }
    };
    let pending: Sample | null = null;
    for await (const sample of this.samples(key, signal)) {
      if (pending && pending.cursor[0] !== sample.cursor[0]) accept(pending);
      // Missing=hold can use an earlier finite sample at this same time.
      if (pending && pending.cursor[0] === sample.cursor[0] && Number.isFinite(pending.value)) held = pending.value;
      pending = sample;
    }
    if (pending) accept(pending);
    flush();
    return Object.freeze(result);
  }
}
