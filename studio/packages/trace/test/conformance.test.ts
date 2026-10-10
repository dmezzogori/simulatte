import { readFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { encode } from "@msgpack/msgpack";
import {
  NotPreparedError,
  TraceCorrupted,
  UnsupportedTrace,
  applyDeltas,
  openTrace,
  unescapeKey,
  type Cursor,
  type Trace,
} from "../src/index";

const TRACES = fileURLToPath(new URL("../../../../tests/fixtures/traces/", import.meta.url));

interface Check {
  label: string;
  cursor: [number, number];
  state: unknown;
}

interface Expected {
  trace: string;
  level: string;
  outcome: string | null;
  truncated: boolean;
  seed: string;
  cursorRange: [Cursor, Cursor] | null;
  chunks: [Cursor, Cursor][];
  checks: Check[];
}

function load(dir: string, name: string): { bytes: Uint8Array; expected: Expected } {
  const bytes = new Uint8Array(readFileSync(`${TRACES}${dir}/${name}.simtrace`));
  const expected = JSON.parse(readFileSync(`${TRACES}${dir}/${name}.expected.json`, "utf8")) as Expected;
  return { bytes, expected };
}

function names(dir: string): string[] {
  return readdirSync(`${TRACES}${dir}`)
    .filter((file) => file.endsWith(".simtrace"))
    .map((file) => file.replace(/\.simtrace$/, ""))
    .sort();
}

function copyOf(bytes: Uint8Array): ArrayBuffer {
  return bytes.slice().buffer;
}

/**
 * A canonical, order-independent text form of a decoded value or of parsed expected JSON.
 *
 * Maps become sorted `[key, value]` pairs (so a key such as `__proto__` is data, never a setter), non-finite floats
 * become the strings of the expected JSON and -0 is marked, since JSON.stringify would print it as 0. Both sides
 * go through this function: the actual value from the reader and the parsed expected file.
 */
function canon(value: unknown): string {
  return JSON.stringify(plain(value));
}

function plain(value: unknown): unknown {
  if (typeof value === "number") {
    if (Number.isNaN(value)) return "nan";
    if (value === Infinity) return "+inf";
    if (value === -Infinity) return "-inf";
    if (Object.is(value, -0)) return { negativeZero: true };
    return value;
  }
  if (Array.isArray(value)) return ["a", value.map(plain)];
  if (value !== null && typeof value === "object") {
    const keys = Object.keys(value).sort();
    return ["m", keys.map((key) => [key, plain(Object.getOwnPropertyDescriptor(value, key)?.value)])];
  }
  return value;
}

async function opened(bytes: Uint8Array): Promise<Trace> {
  return openTrace(copyOf(bytes));
}

describe.each([["generated"], ["frozen"]])("%s fixtures", (dir) => {
  const all = names(dir);

  it("has fixtures", () => {
    expect(all.length).toBeGreaterThan(0);
  });

  describe.each(all)("%s", (name) => {
    const { bytes, expected } = load(dir, name);

    it("opens with the metadata of the reference reader", async () => {
      for (const source of [copyOf(bytes), new Blob([bytes as BlobPart])]) {
        const trace = await openTrace(source);
        expect(trace.level).toBe(expected.level);
        expect(trace.outcome).toBe(expected.outcome);
        expect(trace.truncated).toBe(expected.truncated);
        expect(trace.cursorRange).toEqual(expected.cursorRange);
        expect(trace.index.map((chunk) => [chunk.first, chunk.last])).toEqual(expected.chunks);
        const manifest = (trace.header as { manifest: { seed: unknown } }).manifest;
        expect(manifest.seed).toBe(expected.seed); // a decimal string, never a number
      }
    });

    it("answers stateAt after prepare like the reference reader", async () => {
      const trace = await opened(bytes);
      expect(expected.checks.length).toBeGreaterThan(0);
      for (const check of expected.checks) {
        await trace.prepare(check.cursor);
        expect(canon(trace.stateAt(check.cursor)), `${check.label} ${check.cursor}`).toBe(canon(check.state));
      }
    });

    it("refuses stateAt on a chunk that was not prepared", async () => {
      const trace = await opened(bytes);
      const inChunk = expected.checks.find((check) => check.label === "chunk-0-first");
      if (inChunk === undefined) return; // no chunk: the activation state needs none
      expect(() => trace.stateAt(inChunk.cursor)).toThrow(NotPreparedError);
      await trace.prepare(inChunk.cursor);
      expect(() => trace.stateAt(inChunk.cursor)).not.toThrow();
    });

    it("returns the activation state without a prepared chunk", async () => {
      const trace = await opened(bytes);
      const activation = expected.checks.find((check) => check.label === "activation");
      expect(activation).toBeDefined();
      expect(canon(trace.stateAt(activation!.cursor))).toBe(canon(activation!.state));
    });

    it("rejects cursors outside the cursor range", async () => {
      const trace = await opened(bytes);
      const [start, end] = trace.cursorRange!;
      await expect(trace.prepare([start[0] - 1, 0])).rejects.toThrow(RangeError);
      expect(() => trace.stateAt([end[0] + 1, 0])).toThrow(RangeError);
    });
  });
});

describe("hostile keys", () => {
  it("decode into prototype-free objects without touching Object.prototype", async () => {
    const { bytes, expected } = load("frozen", "hostile_keys");
    const trace = await opened(bytes);
    const end = expected.cursorRange![1];
    await trace.prepare(end);
    const state = trace.stateAt(end);

    expect(Object.getPrototypeOf(state)).toBeNull();
    for (const id of ["__proto__", "constructor", "prototype", "~x", "~__proto__", "host"]) {
      expect(Object.hasOwn(state, id), id).toBe(true);
    }
    const entity = state["__proto__"]!;
    expect(Object.getPrototypeOf(entity)).toBeNull();
    expect(Object.hasOwn(entity, "__proto__")).toBe(true);
    expect(entity["__proto__"]).toBe("set-0");
    const nested = entity["nested"] as Record<string, unknown>;
    expect(Object.getPrototypeOf(nested)).toBeNull();
    expect(Object.hasOwn(nested, "__proto__")).toBe(true);
    expect(Object.hasOwn(nested, "~__proto__")).toBe(true);
    expect(Object.hasOwn(nested, "~~y")).toBe(true); // a key that starts with "~" survives the unescaping

    expect(Object.getPrototypeOf({})).toBe(Object.prototype);
    expect(({} as Record<string, unknown>)["polluted"]).toBeUndefined();
    expect(Object.keys(Object.prototype)).toEqual([]);
  });

  it("unescapes one leading tilde", () => {
    expect(unescapeKey("~__proto__")).toBe("__proto__");
    expect(unescapeKey("~~x")).toBe("~x");
    expect(unescapeKey("plain")).toBe("plain");
  });
});

describe("numbers", () => {
  it("keeps -0, infinities, NaN and the largest safe integers", async () => {
    const { bytes, expected } = load("frozen", "nonfinite");
    const trace = await opened(bytes);
    const first = expected.cursorRange![0];
    const state = trace.stateAt(first);
    const numbers = state["numbers"]!;
    expect(numbers["scalar"]).toBe(Infinity);
    const series = numbers["series"] as number[];
    expect(series[0]).toBe(-Infinity);
    expect(Object.is(series[1], -0)).toBe(true);
    expect(Object.is(series[2], 0)).toBe(true);
    expect(series[3]).toBe(Number.MAX_SAFE_INTEGER);
    expect(series[4]).toBe(Number.MIN_SAFE_INTEGER);
    const table = numbers["table"] as Record<string, number>;
    expect(Number.isNaN(table["nan"])).toBe(true);
    expect(Object.is(table["negzero"], -0)).toBe(true);
  });
});

describe("truncated traces", () => {
  it("shows the committed chunks and flags the tail", async () => {
    const { bytes, expected } = load("frozen", "truncated");
    const trace = await opened(bytes);
    expect(trace.truncated).toBe(true);
    expect(trace.outcome).toBeNull();
    expect(trace.index.length).toBe(expected.chunks.length);
  });
});

describe("damage and limits", () => {
  const { bytes } = load("generated", "shop_small");

  it("rejects bytes that are not a trace", async () => {
    await expect(openTrace(new ArrayBuffer(4))).rejects.toThrow(TraceCorrupted);
    await expect(openTrace(copyOf(new TextEncoder().encode("SIMTRACX\0\x01\0\0 and more bytes")))).rejects.toThrow(
      TraceCorrupted,
    );
  });

  it("rejects an unsupported major version", async () => {
    const edited = bytes.slice();
    edited[9] = 2; // format_major low byte
    await expect(openTrace(copyOf(edited))).rejects.toThrow(UnsupportedTrace);
  });

  async function chunkInfo(index: number) {
    const trace = await opened(bytes);
    return { trace, info: trace.index[index]! };
  }

  it("raises TraceCorrupted when a chunk the footer index names fails its CRC check", async () => {
    const { trace, info } = await chunkInfo(3);
    const edited = bytes.slice();
    edited[info.offset + 9 + 5]! ^= 0xff;
    const damaged = await opened(edited); // the footer index is intact, so opening does not read this chunk
    await expect(damaged.prepare(trace.index[3]!.first)).rejects.toThrow(TraceCorrupted);
    await expect(damaged.prepare(trace.index[2]!.first)).resolves.toBeUndefined();
  });

  it("raises TraceCorrupted for a damaged record followed by valid ones when scanning", async () => {
    const { info } = await chunkInfo(3);
    const edited = bytes.slice(0, bytes.length - 16); // no trailer: every record is scanned
    edited[info.offset + 9 + 5]! ^= 0xff;
    await expect(openTrace(copyOf(edited))).rejects.toThrow(TraceCorrupted);
  });

  it("treats a damaged last record as an incomplete tail when scanning", async () => {
    const trace = await opened(bytes);
    const last = trace.index[trace.index.length - 1]!;
    const edited = bytes.slice(0, last.offset + last.length); // the last chunk, its INDEX record not yet written
    edited[last.offset + 9 + 5]! ^= 0xff;
    const damaged = await opened(edited);
    expect(damaged.truncated).toBe(true);
    expect(damaged.index.length).toBe(trace.index.length - 1);
  });

  it("raises TraceCorrupted for INDEX records with cursors out of order when scanning", async () => {
    const trace = await opened(bytes);
    const last = trace.index[trace.index.length - 1]!;
    const entry = {
      offset: last.offset,
      length: last.length,
      first: trace.index[0]!.first, // commits its chunk, but starts before the chunks that precede it
      last: last.last,
      t_start: last.tStart,
      t_end: last.tEnd,
      epoch: last.epoch,
    };
    const payload = encode(entry);
    const frame = new DataView(new ArrayBuffer(9));
    frame.setUint32(0, payload.length);
    frame.setUint8(4, 6); // INDEX
    frame.setUint32(5, crc(payload));
    const edited = new Uint8Array([
      ...bytes.subarray(0, last.offset + last.length),
      ...new Uint8Array(frame.buffer),
      ...payload,
    ]);
    await expect(openTrace(copyOf(edited))).rejects.toThrow(TraceCorrupted);
  });

  it("enforces the reader limits", async () => {
    await expect(openTrace(copyOf(bytes), { limits: { maxRecord: 64 } })).rejects.toThrow(TraceCorrupted);
    const trace = await openTrace(copyOf(bytes), { limits: { maxChunk: 64 } });
    await expect(trace.prepare(trace.cursorRange![1])).rejects.toThrow(TraceCorrupted);
    const shallow = await openTrace(copyOf(bytes), { limits: { maxDepth: 2 } }).catch((error: unknown) => error);
    expect(shallow).toBeInstanceOf(TraceCorrupted);
  });

  it("rejects maps that repeat a key after unescaping", async () => {
    await expect(buildMinimal()).resolves.toBeDefined();
    await expect(buildMinimal({ a: 1, "~a": 2 })).rejects.toThrow(TraceCorrupted);
  });
});

describe("applyDeltas", () => {
  const state = () => ({ e: Object.assign(Object.create(null), { $kind: "k", list: [1, 2], map: { a: 1 } }) });

  it("creates an absent field with set (ruling R12)", () => {
    const s = state();
    applyDeltas(s, [["set", "e", "fresh", -0]]);
    expect(Object.is((s.e as Record<string, unknown>)["fresh"], -0)).toBe(true);
  });

  it("does not mutate shared values", () => {
    const s = state();
    const list = s.e["list"] as number[];
    const map = s.e["map"] as Record<string, number>;
    applyDeltas(s, [
      ["insert", "e", "list", 1, 9],
      ["move", "e", "list", 2, 0],
      ["put", "e", "map", "b", 2],
      ["delete", "e", "map", "a"],
    ]);
    expect(list).toEqual([1, 2]);
    expect(map).toEqual({ a: 1 });
    expect(s.e["list"]).toEqual([2, 1, 9]);
  });

  it("creates and retires entities", () => {
    const s = state() as Record<string, Record<string, unknown>>;
    applyDeltas(s, [["create", "n", "kind", { x: 1 }], ["retire", "e"]]);
    expect(Object.keys(s)).toEqual(["n"]);
    expect(s["n"]!["$kind"]).toBe("kind");
    expect(() => applyDeltas(s, [["create", "n", "kind", {}]])).toThrow();
  });

  it("rejects operations on missing entities, fields and values", () => {
    const s = state();
    expect(() => applyDeltas(s, [["set", "nope", "f", 1]])).toThrow();
    expect(() => applyDeltas(s, [["insert", "e", "nope", 0, 1]])).toThrow();
    expect(() => applyDeltas(s, [["remove", "e", "list", 99]])).toThrow();
    expect(() => applyDeltas(s, [["delete", "e", "map", "zzz"]])).toThrow();
    expect(() => applyDeltas(s, [["frobnicate", "e"]])).toThrow();
  });
});

/** A trace made of a hand-made header only, for decoder edge cases. */
async function buildMinimal(manifest: Record<string, unknown> = { seed: "1" }) {
  const header = {
    features: { required: ["wire-v1", "deltas-v1", "chunks-zlib"], optional: [] },
    catalog: {},
    kinds: {},
    manifest,
    level: "full",
    chunk_limits: {},
    volatile: {},
  };
  const out: number[] = [...new TextEncoder().encode("SIMTRACE"), 0, 1, 0, 0];
  const payload = encode(header);
  const view = new DataView(new ArrayBuffer(9));
  view.setUint32(0, payload.length);
  view.setUint8(4, 1);
  view.setUint32(5, crc(payload));
  out.push(...new Uint8Array(view.buffer), ...payload);
  return openTrace(new Uint8Array(out).buffer);
}

function crc(data: Uint8Array): number {
  let c = 0xffffffff;
  for (const byte of data) {
    c ^= byte;
    for (let i = 0; i < 8; i++) c = c & 1 ? (c >>> 1) ^ 0xedb88320 : c >>> 1;
  }
  return (c ^ 0xffffffff) >>> 0;
}
