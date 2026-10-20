/** Canonical MessagePack and domain-separated hashes, byte-identical to Python's ``simulatte._wire.canonical_pack``
 * and ``simulatte.execution.hashing.canonical_hash``: map keys escaped (``~`` before ``__proto__``, ``constructor``,
 * ``prototype`` and keys starting with ``~``) and sorted by their UTF-8 bytes, integers in their smallest form,
 * every non-integer number as float64, NaN normalized, strings as str types. JSON numbers that are integers are
 * integers (as Python's ``json`` reads them); anything else is a float. */
export const MAX_SAFE_INT = 2 ** 53 - 1;
const HOSTILE = new Set(["__proto__", "constructor", "prototype"]);
const encoder = new TextEncoder();
export function escapeKey(key: string): string {
  return HOSTILE.has(key) || key.startsWith("~") ? "~" + key : key;
}
function compareBytes(a: Uint8Array, b: Uint8Array): number {
  const n = Math.min(a.length, b.length);
  for (let i = 0; i < n; i++) if (a[i] !== b[i]) return a[i]! - b[i]!;
  return a.length - b.length;
}
class Writer {
  #chunks: Uint8Array[] = [];
  #length = 0;
  push(bytes: Uint8Array): void {
    this.#chunks.push(bytes);
    this.#length += bytes.length;
  }
  byte(...values: number[]): void {
    this.push(Uint8Array.from(values));
  }
  uint(value: number, width: 2 | 4 | 8): void {
    const view = new DataView(new ArrayBuffer(width));
    if (width === 2) view.setUint16(0, value);
    else if (width === 4) view.setUint32(0, value);
    else view.setBigUint64(0, BigInt(value));
    this.push(new Uint8Array(view.buffer));
  }
  int(value: number, width: 1 | 2 | 4 | 8): void {
    const view = new DataView(new ArrayBuffer(width));
    if (width === 1) view.setInt8(0, value);
    else if (width === 2) view.setInt16(0, value);
    else if (width === 4) view.setInt32(0, value);
    else view.setBigInt64(0, BigInt(value));
    this.push(new Uint8Array(view.buffer));
  }
  float(value: number): void {
    const view = new DataView(new ArrayBuffer(8));
    view.setFloat64(0, Number.isNaN(value) ? NaN : value);
    this.push(new Uint8Array(view.buffer));
  }
  bytes(): Uint8Array {
    const out = new Uint8Array(this.#length);
    let offset = 0;
    for (const chunk of this.#chunks) {
      out.set(chunk, offset);
      offset += chunk.length;
    }
    return out;
  }
}
function length(writer: Writer, count: number, fix: number, fixMask: number, code16: number, code32: number): void {
  if (count <= fix) writer.byte(fixMask | count);
  else if (count <= 0xffff) {
    writer.byte(code16);
    writer.uint(count, 2);
  } else {
    writer.byte(code32);
    writer.uint(count, 4);
  }
}
function string(writer: Writer, text: string): void {
  const bytes = encoder.encode(text);
  if (bytes.length <= 31) writer.byte(0xa0 | bytes.length);
  else if (bytes.length <= 0xff) writer.byte(0xd9, bytes.length);
  else if (bytes.length <= 0xffff) {
    writer.byte(0xda);
    writer.uint(bytes.length, 2);
  } else {
    writer.byte(0xdb);
    writer.uint(bytes.length, 4);
  }
  writer.push(bytes);
}
function integer(writer: Writer, value: number): void {
  if (value < -MAX_SAFE_INT || value > MAX_SAFE_INT) throw new RangeError(`integer outside +/-(2**53 - 1): ${value}`);
  if (value >= 0) {
    if (value <= 0x7f) writer.byte(value);
    else if (value <= 0xff) writer.byte(0xcc, value);
    else if (value <= 0xffff) {
      writer.byte(0xcd);
      writer.uint(value, 2);
    } else if (value <= 0xffffffff) {
      writer.byte(0xce);
      writer.uint(value, 4);
    } else {
      writer.byte(0xcf);
      writer.uint(value, 8);
    }
  } else if (value >= -32) writer.int(value, 1);
  else if (value >= -0x80) {
    writer.byte(0xd0);
    writer.int(value, 1);
  } else if (value >= -0x8000) {
    writer.byte(0xd1);
    writer.int(value, 2);
  } else if (value >= -0x80000000) {
    writer.byte(0xd2);
    writer.int(value, 4);
  } else {
    writer.byte(0xd3);
    writer.int(value, 8);
  }
}
function pack(writer: Writer, value: unknown, depth: number): void {
  if (depth > 64) throw new RangeError("canonical value nests deeper than 64");
  if (value === null || value === undefined) writer.byte(0xc0);
  else if (value === true) writer.byte(0xc3);
  else if (value === false) writer.byte(0xc2);
  else if (typeof value === "number") {
    // JSON integers within the safe range read as Python ints; anything else (1e300, -0, fractions) is a float.
    if (Number.isInteger(value) && !Object.is(value, -0) && Math.abs(value) <= MAX_SAFE_INT) integer(writer, value);
    else {
      writer.byte(0xcb);
      writer.float(value);
    }
  } else if (typeof value === "bigint") integer(writer, Number(value));
  else if (typeof value === "string") string(writer, value);
  else if (Array.isArray(value)) {
    length(writer, value.length, 15, 0x90, 0xdc, 0xdd);
    for (const item of value) pack(writer, item, depth + 1);
  } else if (typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>)
      .filter(([, item]) => item !== undefined)
      .map(([key, item]) => {
        const escaped = escapeKey(key);
        return { escaped, bytes: encoder.encode(escaped), item };
      })
      .sort((a, b) => compareBytes(a.bytes, b.bytes));
    length(writer, entries.length, 15, 0x80, 0xde, 0xdf);
    for (const entry of entries) {
      string(writer, entry.escaped);
      pack(writer, entry.item, depth + 1);
    }
  } else throw new TypeError(`not a wire value: ${typeof value}`);
}
/** ``simulatte._wire.canonical_pack(freeze(value))``. */
export function canonicalPack(value: unknown): Uint8Array {
  const writer = new Writer();
  pack(writer, value, 0);
  return writer.bytes();
}
export async function sha256Hex(bytes: BufferSource): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}
/** ``simulatte.execution.hashing.canonical_hash(domain, value)``. */
export async function canonicalHash(domain: string, value: unknown): Promise<string> {
  const prefix = encoder.encode(`simulatte\0${domain}\0`);
  const body = canonicalPack(value);
  const all = new Uint8Array(prefix.length + body.length);
  all.set(prefix, 0);
  all.set(body, prefix.length);
  return sha256Hex(all);
}

// --- Streaming SHA-256 for export files of any size (crypto.subtle digests only whole buffers).
const K = new Uint32Array([
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3,
  0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
  0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13,
  0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
  0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
  0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
]);
export class Sha256 {
  #h = new Uint32Array([0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19]);
  #w = new Uint32Array(64);
  #block = new Uint8Array(64);
  #filled = 0;
  #length = 0;
  #done = false;
  update(bytes: Uint8Array): this {
    if (this.#done) throw new Error("digest already taken");
    this.#length += bytes.length;
    let offset = 0;
    while (offset < bytes.length) {
      const take = Math.min(64 - this.#filled, bytes.length - offset);
      this.#block.set(bytes.subarray(offset, offset + take), this.#filled);
      this.#filled += take;
      offset += take;
      if (this.#filled === 64) {
        this.#compress(this.#block);
        this.#filled = 0;
      }
    }
    return this;
  }
  #compress(block: Uint8Array): void {
    const w = this.#w, h = this.#h;
    const view = new DataView(block.buffer, block.byteOffset, 64);
    for (let i = 0; i < 16; i++) w[i] = view.getUint32(i * 4);
    for (let i = 16; i < 64; i++) {
      const a = w[i - 15]!, b = w[i - 2]!;
      const s0 = ((a >>> 7) | (a << 25)) ^ ((a >>> 18) | (a << 14)) ^ (a >>> 3);
      const s1 = ((b >>> 17) | (b << 15)) ^ ((b >>> 19) | (b << 13)) ^ (b >>> 10);
      w[i] = (w[i - 16]! + s0 + w[i - 7]! + s1) >>> 0;
    }
    let [a, b, c, d, e, f, g, hh] = h as unknown as [number, number, number, number, number, number, number, number];
    for (let i = 0; i < 64; i++) {
      const S1 = ((e >>> 6) | (e << 26)) ^ ((e >>> 11) | (e << 21)) ^ ((e >>> 25) | (e << 7));
      const ch = (e & f) ^ (~e & g);
      const t1 = (hh + S1 + ch + K[i]! + w[i]!) >>> 0;
      const S0 = ((a >>> 2) | (a << 30)) ^ ((a >>> 13) | (a << 19)) ^ ((a >>> 22) | (a << 10));
      const maj = (a & b) ^ (a & c) ^ (b & c);
      const t2 = (S0 + maj) >>> 0;
      hh = g; g = f; f = e; e = (d + t1) >>> 0; d = c; c = b; b = a; a = (t1 + t2) >>> 0;
    }
    h[0] = (h[0]! + a) >>> 0; h[1] = (h[1]! + b) >>> 0; h[2] = (h[2]! + c) >>> 0; h[3] = (h[3]! + d) >>> 0;
    h[4] = (h[4]! + e) >>> 0; h[5] = (h[5]! + f) >>> 0; h[6] = (h[6]! + g) >>> 0; h[7] = (h[7]! + hh) >>> 0;
  }
  hex(): string {
    if (!this.#done) {
      this.#done = true;
      const bits = BigInt(this.#length) * 8n;
      const tail = new Uint8Array(this.#filled < 56 ? 64 : 128);
      tail.set(this.#block.subarray(0, this.#filled));
      tail[this.#filled] = 0x80;
      new DataView(tail.buffer).setBigUint64(tail.length - 8, bits);
      this.#compress(tail.subarray(0, 64));
      if (tail.length === 128) this.#compress(tail.subarray(64));
    }
    return Array.from(this.#h, (word) => word.toString(16).padStart(8, "0")).join("");
  }
}
/** SHA-256 of a Blob read in pieces; small blobs go through crypto.subtle. */
export async function sha256Blob(blob: Blob, piece = 8 * 1024 * 1024): Promise<string> {
  if (blob.size <= piece) return sha256Hex(await blob.arrayBuffer());
  const hash = new Sha256();
  for (let offset = 0; offset < blob.size; offset += piece) hash.update(new Uint8Array(await blob.slice(offset, offset + piece).arrayBuffer()));
  return hash.hex();
}
