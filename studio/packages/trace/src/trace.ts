/**
 * Trace reader (spec §11.1, §11.3, §11.4): {@link openTrace} and {@link Trace}.
 *
 * It reads the same files as the Python reader (`simulatte.trace.reader`) and follows its rules: the header, the
 * initial state, the chunk index and the footer are read when the trace is opened (the index comes from the footer
 * when the file ends with a valid trailer, else from a scan of every record); a chunk is read only when
 * {@link Trace.prepare} asks for it. A short or CRC-failing record with no valid record anywhere after it is an
 * incomplete tail ({@link Trace.truncated}) and is ignored; a failing record followed by valid records raises
 * {@link TraceCorrupted}; a chunk is visible only once the `INDEX` record that commits it follows it.
 *
 * A *cursor* is `[t, seq]`. {@link Trace.stateAt} returns the replay state after every domain event whose cursor is
 * at or before the given one; the activation cursor `[tActivation, -1]` denotes the initial state. Reading a chunk
 * is asynchronous (decompression), so the API is split as in the viewer: `await prepare(cursor)` loads the chunk,
 * then the synchronous `stateAt(cursor)` replays it.
 *
 * Not ported from the Python reader: the event catalog and KPI scalars (not needed to replay states) and
 * `check()`/`verify()`.
 */

import {
  FORMAT_MAJOR,
  FRAME_SIZE,
  MAGIC,
  PREAMBLE_SIZE,
  REQUIRED_FEATURES,
  RecordType,
  TRAILER_MAGIC,
  TRAILER_SIZE,
  TraceCorrupted,
  UnsupportedTrace,
  byteSource,
  inflate,
  readFrame,
  readPayload,
  resolveLimits,
  validRecordFrom,
  type ByteSource,
  type Frame,
  type ReaderLimits,
} from "./container";
import { DeltaError, applyDeltas, type Op, type State } from "./deltas";
import { decodeWire, defineKey, newMap, type Wire } from "./wire";

/** A position in a trace: `[t, seq]`. */
export type Cursor = readonly [number, number];

/** `stateAt` was called for a chunk that `prepare` has not loaded. */
export class NotPreparedError extends Error {
  override readonly name = "NotPreparedError";
}

/** An entry of the chunk index: where the `CHUNK` record is and which events it holds. */
export interface ChunkInfo {
  offset: number;
  /** Size of the record including its framing. */
  length: number;
  first: Cursor;
  last: Cursor;
  tStart: number;
  tEnd: number;
  epoch: number;
}

/** A recorded domain event: `[seq, ordinal, type, t, payload, deltas]` as stored in a chunk. */
export interface TraceEvent {
  seq: number;
  ordinal: number | null;
  type: string;
  t: number;
  payload: Wire;
  deltas: readonly Op[];
}

export interface OpenOptions {
  /** Overrides of the default {@link ReaderLimits}. */
  limits?: Partial<ReaderLimits>;
}

type Dict = Record<string, unknown>;

interface Chunk {
  snapshot: State;
  events: readonly TraceEvent[];
  cursors: readonly Cursor[];
}

interface Scan {
  header: Dict | null;
  initial: Dict | null;
  exts: Map<number, unknown>;
  index: ChunkInfo[];
  footer: Dict | null;
  truncated: boolean;
  /** Offset where the pass stopped. */
  stop: number;
}

const CACHED_CHUNKS = 4;

/**
 * Open the trace in `source`, reading its header, initial state, chunk index and footer.
 *
 * Rejects with {@link TraceCorrupted} for damaged or inconsistent files and files above the limits, and with
 * {@link UnsupportedTrace} for a format version or required feature this reader does not support.
 */
export async function openTrace(source: Blob | ArrayBuffer, options: OpenOptions = {}): Promise<Trace> {
  const limits = resolveLimits(options.limits);
  const trace = new Trace(byteSource(source), limits);
  await trace.load();
  return trace;
}

export class Trace {
  /** The decoded `HEADER` record. */
  header: Readonly<Dict> = {};
  /** The recording level, `"full"` or `"kpi"`. */
  level = "";
  /** The visible chunks, in file order. */
  index: readonly ChunkInfo[] = [];
  /** Whether the file ends with an incomplete tail (or lacks its trailer) that was ignored. */
  truncated = false;
  /** How the run ended (`completed`, `cancelled`, `failed`); null without a footer. */
  outcome: string | null = null;

  #initial: State | null = null;
  #activation: Cursor | null = null;
  #footer: Dict | null = null;
  #firsts: readonly Cursor[] = [];
  readonly #cache = new Map<number, Chunk>();
  readonly #loading = new Map<number, Promise<Chunk>>();

  /** @internal Use {@link openTrace}. */
  constructor(
    private readonly source: ByteSource,
    private readonly limits: ReaderLimits,
  ) {}

  /** @internal */
  async load(): Promise<void> {
    const { source } = this;
    if (source.size < PREAMBLE_SIZE) throw new TraceCorrupted("not a simulatte trace: the file is shorter than the preamble");
    const preamble = await source.read(0, PREAMBLE_SIZE);
    if (new TextDecoder().decode(preamble.subarray(0, 8)) !== MAGIC) {
      throw new TraceCorrupted("not a simulatte trace: bad magic bytes");
    }
    const view = new DataView(preamble.buffer, preamble.byteOffset, preamble.byteLength);
    const major = view.getUint16(8);
    const minor = view.getUint16(10);
    if (major !== FORMAT_MAJOR) {
      throw new UnsupportedTrace(`unsupported trace format ${major}.${minor}; this reader reads format ${FORMAT_MAJOR}.x`);
    }

    const located = await this.locateFooter();
    if (located === null) {
      const scan = await this.scan(PREAMBLE_SIZE, source.size, false, true);
      this.takeHead(scan);
      checkIndex(scan.index, PREAMBLE_SIZE, scan.stop);
      this.index = scan.index;
      this.#footer = scan.footer;
      // A footer without its trailer: the trailer is missing or cut, an incomplete tail.
      this.truncated = scan.truncated || scan.footer !== null;
    } else {
      const [footerOffset, footer] = located;
      this.#footer = footer;
      const head = await this.scan(PREAMBLE_SIZE, footerOffset, true, false);
      this.takeHead(head);
      const index = malformed("footer", () => arrayOf(field(footer, "index")).map(chunkInfo));
      malformed("footer", () => arrayOf(field(footer, "epochs")).map((offset) => integer(offset, "epoch offset")));
      checkIndex(index, head.stop, footerOffset);
      this.index = index;
      // The tail holds the last chunk, its index and late catalog extensions.
      const last = index[index.length - 1];
      const tail = await this.scan(last?.offset ?? head.stop, footerOffset, false, false);
      if (!sameInfos(tail.index, last === undefined ? [] : [last])) {
        throw new TraceCorrupted("the footer index disagrees with the INDEX records at the end of the file");
      }
      this.truncated = false;
    }
    const footer = this.#footer;
    if (footer !== null) {
      malformed("footer", () => {
        this.outcome = string(field(footer, "outcome"), "outcome");
        asCursor(field(footer, "cursor"));
        field(footer, "manifest");
        const fingerprint = mapOf(field(footer, "fingerprint"), "fingerprint");
        mapOf(field(fingerprint, "kpis"), "kpis");
        field(fingerprint, "digest");
      });
    }
    this.#firsts = this.index.map((info) => info.first);
  }

  /**
   * The first and last cursors {@link stateAt} accepts; null if the run was never activated.
   *
   * The first is the activation cursor. The last is the footer cursor of a complete `full` trace, else the last
   * cursor of the last visible chunk (the activation cursor without chunks).
   */
  get cursorRange(): readonly [Cursor, Cursor] | null {
    const start = this.#activation;
    if (start === null) return null;
    let end: Cursor = start;
    if (this.#footer !== null && this.level === "full") end = asCursor(this.#footer["cursor"]);
    else if (this.index.length > 0) end = this.index[this.index.length - 1]!.last;
    return [start, compare(end, start) > 0 ? end : start];
  }

  /**
   * Load and decompress the chunk that {@link stateAt} needs for `cursor` (nothing for the activation state).
   * Rejects with `RangeError` for a cursor outside {@link cursorRange} and with {@link TraceCorrupted} if the chunk
   * is damaged. The last few prepared chunks stay available.
   */
  async prepare(cursor: Cursor): Promise<void> {
    const i = this.chunkFor(this.checkedCursor(cursor));
    if (i < 0) return;
    const cached = this.#cache.get(i);
    if (cached !== undefined) {
      this.#cache.delete(i); // most recently used last
      this.#cache.set(i, cached);
      return;
    }
    let loading = this.#loading.get(i);
    if (loading === undefined) {
      loading = this.readChunk(this.index[i]!).finally(() => this.#loading.delete(i));
      this.#loading.set(i, loading);
    }
    this.#cache.set(i, await loading);
    while (this.#cache.size > CACHED_CHUNKS) this.#cache.delete(this.#cache.keys().next().value!);
  }

  /**
   * The replay state after every domain event at or before `cursor`: entity id to field values, each with the
   * entity kind under `"$kind"`. Maps are prototype-free objects; the result is a fresh object that the caller may
   * change, while the values inside it are frozen and shared.
   *
   * Throws {@link NotPreparedError} if the chunk was not loaded by {@link prepare}, `RangeError` for a cursor
   * outside {@link cursorRange} and {@link TraceCorrupted} if the chunk's deltas do not apply.
   */
  stateAt(cursor: Cursor): State {
    const at = this.checkedCursor(cursor);
    const i = this.chunkFor(at);
    if (i < 0) return copyState(this.#initial!);
    const chunk = this.#cache.get(i);
    if (chunk === undefined) {
      throw new NotPreparedError(`the chunk for cursor [${at[0]}, ${at[1]}] is not prepared: await prepare(cursor) first`);
    }
    const state = copyState(chunk.snapshot);
    const events = chunk.events.slice(0, upperBound(chunk.cursors, at));
    for (const event of events) {
      try {
        applyDeltas(state, event.deltas);
      } catch (error) {
        if (error instanceof DeltaError) {
          throw new TraceCorrupted(`the deltas of event seq ${event.seq} do not apply to the replay state: ${error.message}`);
        }
        throw error;
      }
    }
    return state;
  }

  private checkedCursor(cursor: Cursor): Cursor {
    const at = asCursor(cursor, TypeError);
    const bounds = this.cursorRange;
    if (bounds === null || this.#initial === null) {
      throw new RangeError("the trace has no initial state: the run was never activated");
    }
    if (compare(at, bounds[0]) < 0 || compare(at, bounds[1]) > 0) {
      throw new RangeError(
        `cursor [${at[0]}, ${at[1]}] is outside the cursor range [${bounds[0]}] .. [${bounds[1]}] of the trace`,
      );
    }
    return at;
  }

  /** Index of the chunk whose events start at or before `at`; -1 if `at` precedes every chunk. */
  private chunkFor(at: Cursor): number {
    return upperBound(this.#firsts, at) - 1;
  }

  // -----------------------------------------------------------------------------------------------------------
  // Opening
  // -----------------------------------------------------------------------------------------------------------

  /** The offset and decoded payload of the footer named by a valid trailer; null if the file has no trailer. */
  private async locateFooter(): Promise<[number, Dict] | null> {
    const { source } = this;
    const size = source.size;
    if (size < PREAMBLE_SIZE + TRAILER_SIZE) return null;
    const trailer = await source.read(size - TRAILER_SIZE, TRAILER_SIZE);
    if (new TextDecoder().decode(trailer.subarray(8)) !== TRAILER_MAGIC) return null;
    const view = new DataView(trailer.buffer, trailer.byteOffset, trailer.byteLength);
    const offsetBig = view.getBigUint64(0);
    const end = size - TRAILER_SIZE;
    const offset = offsetBig <= BigInt(Number.MAX_SAFE_INTEGER) ? Number(offsetBig) : -1;
    const frame = offset >= PREAMBLE_SIZE && offset < end ? await readFrame(source, offset, end, this.limits) : null;
    if (frame === null || frame.type !== RecordType.FOOTER || offset + FRAME_SIZE + frame.length !== end) {
      throw new TraceCorrupted("the trailer does not point at a footer record");
    }
    const data = await readPayload(source, offset, frame);
    if (data === null) throw new TraceCorrupted("the footer record fails its CRC check");
    return [offset, malformed("footer", () => mapOf(this.decode(data, "the footer"), "footer"))];
  }

  /**
   * Read records from `start` to `end`, checking CRCs; `headOnly` stops at the first chunk-era record.
   *
   * With `damageIsTail`, a short record, or a CRC-failing record with no CRC-valid record anywhere after it
   * (walking the frames that follow, damaged or not, until one is cut short or exceeds `maxRecord`), ends the pass
   * as an incomplete tail; otherwise any damage raises.
   */
  private async scan(start: number, end: number, headOnly: boolean, damageIsTail: boolean): Promise<Scan> {
    const { source, limits } = this;
    const scan: Scan = { header: null, initial: null, exts: new Map(), index: [], footer: null, truncated: false, stop: 0 };
    let pending: readonly [number, number] | null = null; // [offset, record size] of a chunk awaiting its INDEX
    let pos = start;
    while (pos < end) {
      const frame: Frame | null = await readFrame(source, pos, end, limits);
      if (frame === null) {
        if (!damageIsTail) throw new TraceCorrupted(`record at offset ${pos} is cut short`);
        scan.truncated = true;
        break;
      }
      const { type, length } = frame;
      if (
        headOnly &&
        (type === RecordType.CHUNK || type === RecordType.INDEX || type === RecordType.KPI || type === RecordType.FOOTER)
      ) {
        break;
      }
      const recordEnd = pos + FRAME_SIZE + length;
      const data = await readPayload(source, pos, frame);
      if (data === null) {
        if (!damageIsTail || (await validRecordFrom(source, recordEnd, end, limits))) {
          throw new TraceCorrupted(`record at offset ${pos} fails its CRC check and valid records follow it`);
        }
        scan.truncated = true;
        break;
      }
      if (pos === PREAMBLE_SIZE && type !== RecordType.HEADER) throw new TraceCorrupted("the first record is not a HEADER");
      if (type === RecordType.CHUNK) {
        pending = [pos, FRAME_SIZE + length];
      } else if (type === RecordType.INDEX) {
        const info = malformed("INDEX record", () => chunkInfo(this.decode(data, "an INDEX record")));
        if (pending === null || info.offset !== pending[0] || info.length !== pending[1]) {
          throw new TraceCorrupted(`INDEX record at offset ${pos} does not commit the chunk before it`);
        }
        scan.index.push(info);
        pending = null;
      } else if (type === RecordType.HEADER) {
        scan.header = malformed("header", () => mapOf(this.decode(data, "the header"), "header"));
      } else if (type === RecordType.INITIAL) {
        scan.initial = malformed("initial record", () => mapOf(this.decode(data, "the initial record"), "initial record"));
      } else if (type === RecordType.CATALOG_EXT) {
        scan.exts.set(pos, this.decode(data, "a CATALOG_EXT record"));
      } else if (type === RecordType.KPI) {
        this.decode(data, "a KPI record");
      } else if (type === RecordType.PRELUDE) {
        this.decode(data, "a PRELUDE record");
      } else if (type === RecordType.FOOTER) {
        scan.footer = malformed("footer", () => mapOf(this.decode(data, "the footer"), "footer"));
        pos = recordEnd;
        break;
      }
      pos = recordEnd; // record types of later minor versions are skipped
    }
    scan.stop = pos;
    return scan;
  }

  private takeHead(scan: Scan): void {
    const header = scan.header;
    if (header === null) throw new TraceCorrupted("the trace has no complete header record");
    const { initial } = scan;
    malformed("header or initial record", () => {
      const features = mapOf(field(header, "features"), "features");
      const required = arrayOf(field(features, "required")).map((name) => string(name, "feature"));
      this.level = string(field(header, "level"), "level");
      mapOf(field(header, "manifest"), "manifest");
      if (initial !== null) {
        this.#initial = stateOf(field(initial, "state"));
        this.#activation = asCursor(field(initial, "cursor"));
        mapOf(field(initial, "manifest"), "manifest");
      }
      const unknown = required.filter((name) => !REQUIRED_FEATURES.includes(name));
      if (unknown.length > 0) {
        throw new UnsupportedTrace(`the trace requires features this reader does not support: ${unknown.sort().join(", ")}`);
      }
    });
    this.header = header;
  }

  private decode(data: Uint8Array, what: string): Wire {
    try {
      return decodeWire(data, this.limits);
    } catch (error) {
      throw new TraceCorrupted(`cannot decode ${what}: ${error instanceof Error ? error.message : String(error)}`);
    }
  }

  // -----------------------------------------------------------------------------------------------------------
  // Chunks
  // -----------------------------------------------------------------------------------------------------------

  private async readChunk(info: ChunkInfo): Promise<Chunk> {
    const { source, limits } = this;
    const { offset } = info;
    const frame = await readFrame(source, offset, source.size, limits);
    if (frame === null || frame.type !== RecordType.CHUNK) {
      throw new TraceCorrupted(`the chunk index names offset ${offset} as a CHUNK record, which it is not`);
    }
    if (FRAME_SIZE + frame.length !== info.length) {
      throw new TraceCorrupted(`CHUNK record at offset ${offset} has length ${FRAME_SIZE + frame.length}, not ${info.length}`);
    }
    const data = await readPayload(source, offset, frame);
    if (data === null) throw new TraceCorrupted(`CHUNK record at offset ${offset} fails its CRC check`);
    const raw = await inflate(data, limits.maxChunk, `CHUNK record at offset ${offset}`);
    const body = this.decode(raw, `the chunk at offset ${offset}`);
    return malformed(`chunk at offset ${offset}`, () => {
      const map = mapOf(body, "chunk");
      const events = arrayOf(field(map, "events")).map(traceEvent);
      const cursors = events.map((event): Cursor => [event.t, event.seq]);
      const first = cursors[0];
      const last = cursors[cursors.length - 1];
      const consistent =
        first !== undefined &&
        last !== undefined &&
        compare(first, info.first) === 0 &&
        compare(first, asCursor(field(map, "first"))) === 0 &&
        compare(last, info.last) === 0 &&
        compare(last, asCursor(field(map, "last"))) === 0 &&
        cursors.every((cursor, i) => i === 0 || compare(cursors[i - 1]!, cursor) < 0);
      if (!consistent) throw new TraceCorrupted(`the events of the chunk at offset ${offset} disagree with its index entry`);
      return { snapshot: stateOf(field(map, "snapshot")), events, cursors };
    });
  }
}

// -------------------------------------------------------------------------------------------------------------
// Helpers
// -------------------------------------------------------------------------------------------------------------

/** Run `build`, turning structural errors in decoded data into {@link TraceCorrupted}. */
function malformed<T>(what: string, build: () => T): T {
  try {
    return build();
  } catch (error) {
    if (error instanceof TraceCorrupted || error instanceof UnsupportedTrace || !(error instanceof Error)) throw error;
    throw new TraceCorrupted(`malformed ${what}: ${error.message}`);
  }
}

function field(map: Dict, key: string): unknown {
  if (!Object.hasOwn(map, key)) throw new TypeError(`missing ${JSON.stringify(key)}`);
  return Object.getOwnPropertyDescriptor(map, key)?.value;
}

function mapOf(value: unknown, what: string): Dict {
  if (value === null || typeof value !== "object" || Array.isArray(value)) throw new TypeError(`${what} is not a map`);
  return value as Dict;
}

function arrayOf(value: unknown): readonly unknown[] {
  if (!Array.isArray(value)) throw new TypeError("not an array");
  return value;
}

function string(value: unknown, what: string): string {
  if (typeof value !== "string") throw new TypeError(`${what} is not a string`);
  return value;
}

function integer(value: unknown, what: string): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value)) throw new TypeError(`${what} is not an integer`);
  return value;
}

function real(value: unknown, what: string): number {
  if (typeof value !== "number") throw new TypeError(`${what} is not a number`);
  return value;
}

function asCursor(value: unknown, error: new (message: string) => Error = TypeError): Cursor {
  if (!Array.isArray(value) || value.length !== 2 || typeof value[0] !== "number" || !Number.isSafeInteger(value[1])) {
    throw new error(`a cursor is a [t, seq] pair of numbers, got ${JSON.stringify(value)}`);
  }
  return [value[0], value[1] as number];
}

function chunkInfo(entry: unknown): ChunkInfo {
  const map = mapOf(entry, "index entry");
  return {
    offset: integer(field(map, "offset"), "offset"),
    length: integer(field(map, "length"), "length"),
    first: asCursor(field(map, "first")),
    last: asCursor(field(map, "last")),
    tStart: real(field(map, "t_start"), "t_start"),
    tEnd: real(field(map, "t_end"), "t_end"),
    epoch: integer(field(map, "epoch"), "epoch"),
  };
}

function traceEvent(entry: unknown): TraceEvent {
  const items = arrayOf(entry);
  if (items.length !== 6) throw new TypeError("an event has six items");
  const [seq, ordinal, type, t, payload, deltas] = items;
  return {
    seq: integer(seq, "seq"),
    ordinal: ordinal === null ? null : integer(ordinal, "ordinal"),
    type: string(type, "type"),
    t: real(t, "t"),
    payload: payload as Wire,
    deltas: arrayOf(deltas).map((op) => arrayOf(op)),
  };
}

/**
 * A replay state from a decoded map of entity maps, each holding a string `"$kind"` (as the Python reader requires); a
 * shallow copy: the field values are frozen and shared.
 */
function stateOf(value: unknown): State {
  const source = mapOf(value, "state");
  const state = newMap();
  for (const id of Object.keys(source)) {
    const fields = mapOf(Object.getOwnPropertyDescriptor(source, id)?.value, "entity");
    if (typeof Object.getOwnPropertyDescriptor(fields, "$kind")?.value !== "string") {
      throw new TypeError(`entity ${JSON.stringify(id)} is not a map with a string "$kind"`);
    }
    defineKey(state, id, copyMap(fields));
  }
  return state as State;
}

function copyMap(source: Dict): Dict {
  const copy = newMap();
  for (const key of Object.keys(source)) defineKey(copy, key, Object.getOwnPropertyDescriptor(source, key)?.value);
  return copy;
}

function copyState(state: State): State {
  const copy = newMap();
  for (const id of Object.keys(state)) defineKey(copy, id, copyMap(Object.getOwnPropertyDescriptor(state, id)?.value as Dict));
  return copy as State;
}

function compare(a: Cursor, b: Cursor): number {
  if (a[0] !== b[0]) return a[0] < b[0] ? -1 : 1;
  if (a[1] !== b[1]) return a[1] < b[1] ? -1 : 1;
  return 0;
}

/** Number of items of the sorted `items` that are at or before `at` (Python's `bisect_right`). */
function upperBound(items: readonly Cursor[], at: Cursor): number {
  let low = 0;
  let high = items.length;
  while (low < high) {
    const mid = (low + high) >>> 1;
    if (compare(items[mid]!, at) <= 0) low = mid + 1;
    else high = mid;
  }
  return low;
}

function sameInfos(a: readonly ChunkInfo[], b: readonly ChunkInfo[]): boolean {
  return (
    a.length === b.length &&
    a.every(
      (x, i) =>
        x.offset === b[i]!.offset &&
        x.length === b[i]!.length &&
        compare(x.first, b[i]!.first) === 0 &&
        compare(x.last, b[i]!.last) === 0 &&
        x.tStart === b[i]!.tStart &&
        x.tEnd === b[i]!.tEnd &&
        x.epoch === b[i]!.epoch,
    )
  );
}

/** Index entries must fit the file between the head records and the footer (or end of scan), in order. */
function checkIndex(index: readonly ChunkInfo[], start: number, end: number): void {
  if (index.length > Math.floor((end - start) / (2 * FRAME_SIZE))) {
    throw new TraceCorrupted(`the chunk index has ${index.length} entries, more than the file can hold`);
  }
  let previousEnd = start;
  let previousLast: Cursor | null = null;
  for (const info of index) {
    if (info.offset < previousEnd || info.length < FRAME_SIZE || info.offset + info.length + FRAME_SIZE > end) {
      throw new TraceCorrupted(`chunk index entry at offset ${info.offset} lies outside the records or overlaps`);
    }
    if (compare(info.first, info.last) > 0 || (previousLast !== null && compare(info.first, previousLast) <= 0)) {
      throw new TraceCorrupted(`chunk index entry at offset ${info.offset} has cursors out of order`);
    }
    previousEnd = info.offset + info.length + FRAME_SIZE;
    previousLast = info.last;
  }
}
