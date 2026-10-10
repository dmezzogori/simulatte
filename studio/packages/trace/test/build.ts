/**
 * Hand-made traces for reader tests: a MessagePack writer that can force float64 encodings and write raw maps
 * (duplicate or non-string keys), and a container writer (records, zlib chunks, INDEX, footer, trailer).
 */

import { deflateSync } from "node:zlib";

/** A number written as float64 even when it is integral (Python's `1.0`). */
export class F64 {
  constructor(readonly value: number) {}
}

/** A map written from its `[key, value]` pairs as given: keys may repeat or be numbers. */
export class RawMap {
  constructor(readonly pairs: readonly (readonly [unknown, unknown])[]) {}
}

/** MessagePack bytes inserted as they are. */
export class Raw {
  constructor(readonly bytes: Uint8Array) {}
}

export function f64(value: number): F64 {
  return new F64(value);
}

/** MessagePack encoding: integral numbers in the safe range as integers, other numbers as float64. */
export function pack(value: unknown): Uint8Array {
  const out: number[] = [];
  write(value, out);
  return new Uint8Array(out);
}

function write(value: unknown, out: number[]): void {
  if (value instanceof Raw) {
    out.push(...value.bytes);
  } else if (value === null) {
    out.push(0xc0);
  } else if (typeof value === "boolean") {
    out.push(value ? 0xc3 : 0xc2);
  } else if (value instanceof F64) {
    float64(value.value, out);
  } else if (typeof value === "number") {
    if (Number.isSafeInteger(value) && !Object.is(value, -0)) integer(value, out);
    else float64(value, out);
  } else if (typeof value === "string") {
    const bytes = new TextEncoder().encode(value);
    if (bytes.length < 32) out.push(0xa0 | bytes.length);
    else out.push(0xdb, ...u32(bytes.length));
    out.push(...bytes);
  } else if (Array.isArray(value)) {
    header(value.length, 0x90, 0xdd, out);
    for (const item of value) write(item, out);
  } else if (value instanceof RawMap) {
    header(value.pairs.length, 0x80, 0xdf, out);
    for (const [key, item] of value.pairs) {
      write(key, out);
      write(item, out);
    }
  } else if (typeof value === "object") {
    const keys = Object.keys(value as object);
    header(keys.length, 0x80, 0xdf, out);
    for (const key of keys) {
      write(key, out);
      write((value as Record<string, unknown>)[key], out);
    }
  } else {
    throw new TypeError(`cannot pack ${typeof value}`);
  }
}

function header(length: number, fix: number, wide: number, out: number[]): void {
  if (length < 16) out.push(fix | length);
  else out.push(wide, ...u32(length));
}

function integer(value: number, out: number[]): void {
  if (value >= 0 && value < 128) out.push(value);
  else if (value < 0 && value >= -32) out.push(0x100 + value);
  else {
    const view = new DataView(new ArrayBuffer(9));
    view.setUint8(0, 0xd3);
    view.setBigInt64(1, BigInt(value));
    out.push(...new Uint8Array(view.buffer));
  }
}

function float64(value: number, out: number[]): void {
  const view = new DataView(new ArrayBuffer(9));
  view.setUint8(0, 0xcb);
  view.setFloat64(1, value);
  out.push(...new Uint8Array(view.buffer));
}

function u32(value: number): number[] {
  return [(value >>> 24) & 0xff, (value >>> 16) & 0xff, (value >>> 8) & 0xff, value & 0xff];
}

function crc(data: Uint8Array): number {
  let c = 0xffffffff;
  for (const byte of data) {
    c ^= byte;
    for (let i = 0; i < 8; i++) c = c & 1 ? (c >>> 1) ^ 0xedb88320 : c >>> 1;
  }
  return (c ^ 0xffffffff) >>> 0;
}

function record(type: number, payload: Uint8Array): number[] {
  return [...u32(payload.length), type, ...u32(crc(payload)), ...payload];
}

export const HEADER = {
  features: { required: ["wire-v1", "deltas-v1", "chunks-zlib"], optional: [] },
  catalog: {},
  kinds: {},
  manifest: { seed: "1" },
  level: "full",
  chunk_limits: {},
  volatile: {},
};

export interface ChunkSpec {
  /** `[seq, ordinal, type, t, payload, deltas]` entries; deltas may hold {@link F64} values. */
  events: unknown[][];
  /** The chunk's start snapshot. */
  snapshot: unknown;
}

/** A complete trace (footer and trailer): header, the given initial record, one CHUNK and INDEX per chunk spec. */
export function buildTrace(initial: unknown, chunks: readonly ChunkSpec[] = []): ArrayBuffer {
  const out: number[] = [...new TextEncoder().encode("SIMTRACE"), 0, 1, 0, 0];
  out.push(...record(1, pack(HEADER)));
  out.push(...record(3, pack(initial)));
  const index: unknown[] = [];
  let last: unknown = [0, -1];
  for (const chunk of chunks) {
    const first = [chunk.events[0]![3], chunk.events[0]![0]];
    last = [chunk.events.at(-1)![3], chunk.events.at(-1)![0]];
    const body = { first, last, t_start: first[0], t_end: (last as unknown[])[0], epoch: 0, snapshot: chunk.snapshot };
    const raw = pack(new RawMap([...Object.entries(body), ["events", chunk.events]]));
    const offset = out.length;
    const framed = record(5, new Uint8Array(deflateSync(raw)));
    out.push(...framed);
    const entry = { offset, length: framed.length, first, last, t_start: first[0], t_end: (last as unknown[])[0], epoch: 0 };
    index.push(entry);
    out.push(...record(6, pack(entry)));
  }
  const footer = {
    outcome: "completed",
    cursor: last,
    manifest: null,
    fingerprint: { digest: null, kpis: {} },
    index,
    epochs: [12],
  };
  const footerOffset = out.length;
  out.push(...record(8, pack(footer)));
  const trailer = new DataView(new ArrayBuffer(8));
  trailer.setBigUint64(0, BigInt(footerOffset));
  out.push(...new Uint8Array(trailer.buffer), ...new TextEncoder().encode("SIMTEND\0"));
  return new Uint8Array(out).buffer;
}
