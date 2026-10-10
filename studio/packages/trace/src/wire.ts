/**
 * Wire values (spec §9.1): what trace records are made of, decoded from MessagePack.
 *
 * A wire value is `null`, a boolean, a number, a string, an array of wire values or a map of wire values keyed by
 * string. Map keys are escaped on the wire so that a decoder can rebuild maps safely: a key that is `__proto__`,
 * `constructor` or `prototype`, or that starts with `~`, is prefixed with `~`, and a decoder strips one leading `~`.
 *
 * Decoded maps are prototype-free objects whose keys are defined as own properties, so a key such as `__proto__`
 * is plain data. Decoded arrays and maps are frozen, so values can be shared between states.
 */

export type Wire = null | boolean | number | string | readonly Wire[] | { readonly [key: string]: Wire };

export interface WireLimits {
  /** Deepest nesting of arrays and maps. */
  maxDepth: number;
  /** Longest array or map. */
  maxLen: number;
}

/** A MessagePack value that is not a valid wire value, or a limit that was exceeded. */
export class WireError extends Error {
  override readonly name = "WireError";
}

const HOSTILE_KEYS = new Set(["__proto__", "constructor", "prototype"]);
const MAX_SAFE = BigInt(Number.MAX_SAFE_INTEGER);

/** Escape a map key for the wire (the writers do this; the reader only needs {@link unescapeKey}). */
export function escapeKey(key: string): string {
  return HOSTILE_KEYS.has(key) || key.startsWith("~") ? `~${key}` : key;
}

/** Invert {@link escapeKey}: strip one leading `~`. */
export function unescapeKey(key: string): string {
  return key.startsWith("~") ? key.slice(1) : key;
}

/** Define `key` as an own, enumerable, writable data property of `target` (safe for `__proto__`). */
export function defineKey(target: object, key: string, value: unknown): void {
  Object.defineProperty(target, key, { value, enumerable: true, writable: true, configurable: true });
}

/** A new prototype-free object. */
export function newMap(): Record<string, unknown> {
  return Object.create(null) as Record<string, unknown>;
}

/**
 * Float encodings (ruling R31). Canonical encoding tells an integer from a float (`1` from `1.0`), but both decode to
 * the same JavaScript number. The decoder therefore records, for each decoded array and map, the slots (indices or
 * keys) that hold a float-encoded number with an integral value in the safe integer range; every other number's
 * encoding follows from its value (a non-integral, non-finite, unsafe or `-0` number can only be a float). The
 * records are kept beside the values, which stay plain numbers.
 */
const FLOAT_SLOTS = new WeakMap<object, ReadonlySet<number | string>>();

/** Whether `container[slot]` was decoded from a float encoding (or set from one by the delta operations). */
export function isFloatAt(container: object, slot: number | string): boolean {
  return FLOAT_SLOTS.get(container)?.has(slot) ?? false;
}

/** Record which items of `items` (a new array) hold float-encoded integral numbers. */
export function markFloats(items: readonly unknown[], floats: readonly boolean[]): void {
  const slots = new Set<number>();
  floats.forEach((float, i) => {
    if (float) slots.add(i);
  });
  if (slots.size > 0) FLOAT_SLOTS.set(items, slots);
}

/** A float-encoded number whose value alone would read as an integer; the decoder unwraps it into a plain number. */
class FloatValue {
  constructor(readonly value: number) {}
}

function readFloat(value: number): number | FloatValue {
  return Number.isSafeInteger(value) && !Object.is(value, -0) ? new FloatValue(value) : value;
}

const UTF8 = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true });

/**
 * Decode exactly one MessagePack wire value. Binary, extensions (including timestamps), unsafe integers,
 * duplicate/unescaped key collisions, invalid UTF-8, trailing bytes and excessive nesting/length are rejected.
 * Float-encoded integral values keep their encoding in the enclosing container (see {@link isFloatAt}).
 */
export function decodeWire(data: Uint8Array, limits: WireLimits): Wire {
  try {
    const decoder = new WireDecoder(data, limits);
    const value = decoder.read(0);
    if (decoder.position !== data.byteLength) throw new WireError("trailing bytes after wire value");
    return value instanceof FloatValue ? value.value : value;
  } catch (error) {
    if (error instanceof WireError) throw error;
    throw new WireError(error instanceof Error ? error.message : String(error));
  }
}

/** The trace's small MessagePack subset; checks bounds before reading or allocating container contents. */
class WireDecoder {
  position = 0;
  private readonly view: DataView;

  constructor(private readonly data: Uint8Array, private readonly limits: WireLimits) {
    this.view = new DataView(data.buffer, data.byteOffset, data.byteLength);
  }

  private take(length: number): number {
    const start = this.position;
    if (length > this.data.byteLength - start) throw new WireError("truncated wire value");
    this.position += length;
    return start;
  }

  private uint(width: 1 | 2 | 4): number {
    const start = this.take(width);
    return width === 1 ? this.view.getUint8(start) : width === 2 ? this.view.getUint16(start) : this.view.getUint32(start);
  }

  private text(length: number): string {
    const start = this.take(length);
    return UTF8.decode(this.data.subarray(start, start + length));
  }

  private integer64(signed: boolean): number {
    const start = this.take(8);
    const value = signed ? this.view.getBigInt64(start) : this.view.getBigUint64(start);
    if (value < -MAX_SAFE || value > MAX_SAFE) throw new WireError(`integer out of range: ${value}`);
    return Number(value);
  }

  private container(length: number, depth: number, map: boolean): Wire {
    if (depth >= this.limits.maxDepth) throw new WireError(`nesting depth exceeds ${this.limits.maxDepth}`);
    if (length > this.limits.maxLen) throw new WireError(`container length exceeds ${this.limits.maxLen}`);
    // Every array item needs at least one byte; every map entry needs at least a key and a value.
    if (length > Math.floor((this.data.byteLength - this.position) / (map ? 2 : 1))) {
      throw new WireError("truncated wire container");
    }
    const result = map ? newMap() : [] as Wire[];
    const floats = new Set<number | string>();
    for (let i = 0; i < length; i++) {
      let key: number | string = i;
      if (map) {
        const raw = this.read(depth + 1);
        if (typeof raw !== "string") throw new WireError("map keys must be str");
        key = unescapeKey(raw);
        if (Object.hasOwn(result, key)) throw new WireError(`duplicate map key after unescaping: ${JSON.stringify(key)}`);
      }
      const value = this.read(depth + 1);
      if (value instanceof FloatValue) floats.add(key);
      const item = value instanceof FloatValue ? value.value : value;
      if (Array.isArray(result)) result.push(item);
      else defineKey(result, key as string, item);
    }
    if (floats.size > 0) FLOAT_SLOTS.set(result, floats);
    return Object.freeze(result) as Wire;
  }

  read(depth: number): Wire | FloatValue {
    const code = this.uint(1);
    if (code < 0x80) return code;
    if (code >= 0xe0) return code - 0x100;
    if (code >= 0xa0 && code < 0xc0) return this.text(code & 0x1f);
    if (code >= 0x90 && code < 0xa0) return this.container(code & 0x0f, depth, false);
    if (code >= 0x80 && code < 0x90) return this.container(code & 0x0f, depth, true);
    switch (code) {
      case 0xc0: return null;
      case 0xc2: return false;
      case 0xc3: return true;
      case 0xca: return readFloat(this.view.getFloat32(this.take(4)));
      case 0xcb: return readFloat(this.view.getFloat64(this.take(8)));
      case 0xcc: return this.uint(1);
      case 0xcd: return this.uint(2);
      case 0xce: return this.uint(4);
      case 0xcf: return this.integer64(false);
      case 0xd0: return this.view.getInt8(this.take(1));
      case 0xd1: return this.view.getInt16(this.take(2));
      case 0xd2: return this.view.getInt32(this.take(4));
      case 0xd3: return this.integer64(true);
      case 0xd9: return this.text(this.uint(1));
      case 0xda: return this.text(this.uint(2));
      case 0xdb: return this.text(this.uint(4));
      case 0xdc: return this.container(this.uint(2), depth, false);
      case 0xdd: return this.container(this.uint(4), depth, false);
      case 0xde: return this.container(this.uint(2), depth, true);
      case 0xdf: return this.container(this.uint(4), depth, true);
      default: throw new WireError(`unsupported MessagePack wire tag: 0x${code.toString(16)}`);
    }
  }
}

/**
 * Whether two wire values have the same canonical encoding (ruling R31, spec §6.2, §9.1), the value equality of
 * replay: booleans differ from numbers, an integer from a float (`1` from `1.0`), `-0` from `0`, and NaN equals NaN.
 * `aFloat` and `bFloat` say whether `a` and `b`, when they are integral numbers, were float-encoded (see
 * {@link isFloatAt}); items of decoded arrays and maps carry their own records.
 */
export function wireEquals(a: unknown, b: unknown, aFloat = false, bFloat = false): boolean {
  if (typeof a === "number" || typeof b === "number") {
    return (
      typeof a === "number" &&
      typeof b === "number" &&
      Object.is(a, b) &&
      isFloatNumber(a, aFloat) === isFloatNumber(b, bFloat)
    );
  }
  if (a === b) return true;
  if (typeof a !== "object" || typeof b !== "object" || a === null || b === null) return false;
  if (Array.isArray(a) || Array.isArray(b)) {
    return (
      Array.isArray(a) &&
      Array.isArray(b) &&
      a.length === b.length &&
      a.every((item, i) => wireEquals(item, b[i], isFloatAt(a, i), isFloatAt(b, i)))
    );
  }
  const keys = Object.keys(a);
  return (
    keys.length === Object.keys(b).length &&
    keys.every(
      (key) =>
        Object.hasOwn(b, key) &&
        wireEquals(descriptorValue(a, key), descriptorValue(b, key), isFloatAt(a, key), isFloatAt(b, key)),
    )
  );
}

/** Whether number `value` has a float encoding: recorded as one, or a value no integer encoding can hold. */
function isFloatNumber(value: number, recorded: boolean): boolean {
  return recorded || !Number.isSafeInteger(value) || Object.is(value, -0);
}

function descriptorValue(target: object, key: string): unknown {
  return Object.getOwnPropertyDescriptor(target, key)?.value;
}
