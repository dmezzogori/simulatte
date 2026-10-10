import { performance } from "node:perf_hooks";
import { openTrace, type Cursor } from "../src/index";
import { byteSource, DEFAULT_LIMITS, PREAMBLE_SIZE, inflate, readFrame, readPayload } from "../src/container";
import { decodeWire } from "../src/wire";

export interface Timing {
  samples: number;
  medianMs: number;
  p95Ms: number;
  minMs: number;
  maxMs: number;
}

function summarize(samples: number[]): Timing {
  const sorted = [...samples].sort((a, b) => a - b);
  return {
    samples: sorted.length,
    medianMs: sorted[Math.floor(sorted.length / 2)]!,
    p95Ms: sorted[Math.ceil(sorted.length * 0.95) - 1]!,
    minMs: sorted[0]!,
    maxMs: sorted.at(-1)!,
  };
}

/** Benchmark an in-memory trace. File I/O and payload inflation are excluded from wire-decode timings. */
export async function benchmarkTrace(data: ArrayBuffer, iterations = 10, maxCursors = 8) {
  if (!Number.isSafeInteger(iterations) || iterations < 1) throw new RangeError("iterations must be positive");
  if (!Number.isSafeInteger(maxCursors) || maxCursors < 1) throw new RangeError("maxCursors must be positive");
  const opened: number[] = [];
  let trace = await openTrace(data); // untimed warm-up of module/runtime paths
  for (let i = 0; i < iterations; i++) {
    const start = performance.now();
    trace = await openTrace(data);
    opened.push(performance.now() - start);
  }

  // Decode the first chunk independently of decompression and record framing; a chunkless trace uses HEADER.
  const source = byteSource(data);
  const info = trace.index[0];
  const offset = info?.offset ?? PREAMBLE_SIZE;
  const frame = await readFrame(source, offset, source.size, DEFAULT_LIMITS);
  if (frame === null) throw new Error("benchmark trace has no complete payload");
  const stored = await readPayload(source, offset, frame);
  if (stored === null) throw new Error("benchmark payload has invalid CRC");
  const payload = info ? await inflate(stored, DEFAULT_LIMITS.maxChunk, "benchmark chunk") : stored;
  decodeWire(payload, DEFAULT_LIMITS);
  const decoded: number[] = [];
  for (let i = 0; i < iterations; i++) {
    const start = performance.now();
    decodeWire(payload, DEFAULT_LIMITS);
    decoded.push(performance.now() - start);
  }

  // Spread cursors across the file. The cold measurement uses a new reader with no prepared chunk;
  // the immediately following warm measurement reuses that reader and cursor (including full state replay).
  const candidates = trace.index.map((entry) => entry.last);
  if (!candidates.length && trace.cursorRange) candidates.push(trace.cursorRange[1]);
  const count = Math.min(maxCursors, candidates.length);
  const cursors: Cursor[] = Array.from({ length: count }, (_, i) =>
    candidates[count === 1 ? 0 : Math.floor(i * (candidates.length - 1) / (count - 1))]!,
  );
  const cold: number[] = [];
  const warm: number[] = [];
  for (let i = 0; i < iterations; i++) {
    for (const cursor of cursors) {
      const reader = await openTrace(data);
      let start = performance.now();
      await reader.prepare(cursor);
      reader.stateAt(cursor);
      cold.push(performance.now() - start);
      start = performance.now();
      await reader.prepare(cursor);
      reader.stateAt(cursor);
      warm.push(performance.now() - start);
    }
  }
  return {
    bytes: data.byteLength,
    chunks: trace.index.length,
    iterations,
    cursors,
    open: summarize(opened),
    decode: { payloadBytes: payload.byteLength, record: info ? "CHUNK" : "HEADER", ...summarize(decoded) },
    coldSeek: cold.length ? summarize(cold) : null,
    warmSeek: warm.length ? summarize(warm) : null,
  };
}
