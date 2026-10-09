/**
 * Trace container (spec §11.1): framing, CRC-32, reader limits and zlib chunk decompression.
 *
 * A trace file is the magic `SIMTRACE`, the format version (major, minor) as two big-endian u16, a sequence of
 * records and, when the trace is complete, a trailer. Each record is
 * `length (u32) | type (u8) | crc32 of the stored payload (u32) | payload`, big-endian; `length` counts the payload
 * only. The trailer is the offset of the `FOOTER` record (u64) followed by `SIMTEND\0`.
 */

export const MAGIC = "SIMTRACE";
export const FORMAT_MAJOR = 1;
export const PREAMBLE_SIZE = 12;
export const FRAME_SIZE = 9;
export const TRAILER_SIZE = 16;
export const TRAILER_MAGIC = "SIMTEND\0";
export const REQUIRED_FEATURES: readonly string[] = ["wire-v1", "deltas-v1", "chunks-zlib"];

export const RecordType = {
  HEADER: 1,
  PRELUDE: 2,
  INITIAL: 3,
  CATALOG_EXT: 4,
  CHUNK: 5,
  INDEX: 6,
  KPI: 7,
  FOOTER: 8,
} as const;

/** The trace file is damaged beyond an incomplete tail, inconsistent, or exceeds the reader limits. */
export class TraceCorrupted extends Error {
  override readonly name = "TraceCorrupted";
}

/** The trace uses a format version or a required feature this reader does not support. */
export class UnsupportedTrace extends Error {
  override readonly name = "UnsupportedTrace";
}

/** Bounds enforced while reading a trace (spec §11.1); raise them for trusted local files. */
export interface ReaderLimits {
  /** Largest payload of any record, in bytes. */
  maxRecord: number;
  /** Largest decompressed chunk, in bytes. */
  maxChunk: number;
  /** Deepest nesting of decoded arrays and maps. */
  maxDepth: number;
  /** Longest decoded array or map. */
  maxLen: number;
}

export const DEFAULT_LIMITS: Readonly<ReaderLimits> = Object.freeze({
  maxRecord: 64 << 20,
  maxChunk: 256 << 20,
  maxDepth: 64,
  maxLen: 10_000_000,
});

export function resolveLimits(limits: Partial<ReaderLimits> | undefined): ReaderLimits {
  const resolved = { ...DEFAULT_LIMITS, ...limits };
  for (const [name, value] of Object.entries(resolved)) {
    if (!Number.isInteger(value) || value < 1) throw new RangeError(`ReaderLimits.${name} must be an integer >= 1`);
  }
  return resolved;
}

/** Random access to the bytes of a trace file. */
export interface ByteSource {
  readonly size: number;
  /** Up to `length` bytes at `offset` (fewer only at the end of the file). */
  read(offset: number, length: number): Promise<Uint8Array>;
}

export function byteSource(source: Blob | ArrayBuffer): ByteSource {
  if (source instanceof ArrayBuffer) {
    const bytes = new Uint8Array(source);
    return { size: bytes.length, read: async (offset, length) => bytes.subarray(offset, offset + length) };
  }
  if (typeof Blob !== "undefined" && source instanceof Blob) {
    return {
      size: source.size,
      read: async (offset, length) => new Uint8Array(await source.slice(offset, offset + length).arrayBuffer()),
    };
  }
  throw new TypeError("a trace source is a Blob or an ArrayBuffer");
}

export interface Frame {
  type: number;
  /** Payload length. */
  length: number;
  crc: number;
}

/**
 * The frame of the record at `offset`, or null if `end` cuts it short.
 * Throws {@link TraceCorrupted} if the record claims a payload above `maxRecord`.
 */
export async function readFrame(
  source: ByteSource,
  offset: number,
  end: number,
  limits: ReaderLimits,
): Promise<Frame | null> {
  if (offset + FRAME_SIZE > end) return null;
  const head = await source.read(offset, FRAME_SIZE);
  if (head.length < FRAME_SIZE) return null;
  const view = new DataView(head.buffer, head.byteOffset, head.byteLength);
  const length = view.getUint32(0);
  if (length > limits.maxRecord) {
    throw new TraceCorrupted(
      `record at offset ${offset} has a ${length}-byte payload, above maxRecord=${limits.maxRecord}`,
    );
  }
  if (offset + FRAME_SIZE + length > end) return null;
  return { type: view.getUint8(4), length, crc: view.getUint32(5) };
}

/** The payload of the record at `offset`, or null if it fails its CRC check. */
export async function readPayload(source: ByteSource, offset: number, frame: Frame): Promise<Uint8Array | null> {
  const data = await source.read(offset + FRAME_SIZE, frame.length);
  return data.length === frame.length && crc32(data) === frame.crc ? data : null;
}

/**
 * Whether a CRC-valid record follows at `offset` or after further damaged but well-framed records.
 * The walk stops at the first frame that is cut short by `end` or exceeds `maxRecord`.
 */
export async function validRecordFrom(
  source: ByteSource,
  offset: number,
  end: number,
  limits: ReaderLimits,
): Promise<boolean> {
  let at = offset;
  for (;;) {
    let frame: Frame | null;
    try {
      frame = await readFrame(source, at, end, limits);
    } catch (error) {
      if (error instanceof TraceCorrupted) return false;
      throw error;
    }
    if (frame === null) return false;
    if ((await readPayload(source, at, frame)) !== null) return true;
    at += FRAME_SIZE + frame.length;
  }
}

/**
 * Decompress the zlib stream `data` (one complete stream, nothing after it) into at most `maxBytes` bytes.
 * Throws {@link TraceCorrupted} otherwise.
 */
export async function inflate(data: Uint8Array, maxBytes: number, what: string): Promise<Uint8Array> {
  const parts: Uint8Array[] = [];
  let total = 0;
  try {
    const stream = new Blob([data as BlobPart]).stream().pipeThrough(new DecompressionStream("deflate"));
    const reader = stream.getReader();
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      total += value.length;
      if (total > maxBytes) {
        await reader.cancel();
        throw new TraceCorrupted(`${what} decompresses to more than maxChunk=${maxBytes} bytes`);
      }
      parts.push(value);
    }
  } catch (error) {
    if (error instanceof TraceCorrupted) throw error;
    const reason = error instanceof Error && error.message !== "" ? `: ${error.message}` : "";
    throw new TraceCorrupted(`${what} is not one complete valid zlib stream${reason}`);
  }
  const out = new Uint8Array(total);
  let at = 0;
  for (const part of parts) {
    out.set(part, at);
    at += part.length;
  }
  return out;
}

const CRC_TABLE = (() => {
  const table = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    table[n] = c >>> 0;
  }
  return table;
})();

/** CRC-32 (IEEE, as zlib's `crc32`) of `data`. */
export function crc32(data: Uint8Array): number {
  let c = 0xffffffff;
  for (let i = 0; i < data.length; i++) c = (CRC_TABLE[(c ^ data[i]!) & 0xff]!) ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}
