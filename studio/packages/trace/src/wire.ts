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

import { Decoder, ExtensionCodec } from "@msgpack/msgpack";

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

/**
 * `@msgpack/msgpack` has no hook that tells a float encoding from an integer one, so the decoder's float readers are
 * wrapped. They are internal methods of the decoder (version 3.x); {@link checkFloatHook} fails loudly at the first
 * decode if they change.
 */
class WireDecoder extends Decoder<undefined> {}
type FloatReaders = { readF32(): number; readF64(): number };
const baseReaders = Decoder.prototype as unknown as FloatReaders;
const wireReaders = WireDecoder.prototype as unknown as { readF32(): unknown; readF64(): unknown };
wireReaders.readF32 = function (this: FloatReaders) {
  return readFloat(baseReaders.readF32.call(this));
};
wireReaders.readF64 = function (this: FloatReaders) {
  return readFloat(baseReaders.readF64.call(this));
};
let floatHookChecked = false;

function checkFloatHook(): void {
  if (floatHookChecked) return;
  const probe = new WireDecoder().decode(new Uint8Array([0x92, 0xcb, 0x3f, 0xf0, 0, 0, 0, 0, 0, 0, 0xca, 0x40, 0, 0, 0]));
  const [f64, f32] = probe as unknown[];
  if (!(f64 instanceof FloatValue && f64.value === 1 && f32 instanceof FloatValue && f32.value === 2)) {
    throw new Error("@msgpack/msgpack no longer reads floats through readF64/readF32: update wire.ts");
  }
  floatHookChecked = true;
}

/**
 * Decode one MessagePack value into a wire value.
 *
 * Throws {@link WireError} for malformed or truncated input, trailing data, limit violations, values outside the
 * wire model (binary, extension types, timestamps, integers beyond +/-(2^53 - 1), map keys that are not strings) and
 * maps that repeat a key, as written or after unescaping (for example `"a"` and `"~a"`), like the Python reader.
 * Float-encoded integral numbers are recorded (see {@link isFloatAt}).
 */
export function decodeWire(data: Uint8Array, limits: WireLimits): Wire {
  checkFloatHook();
  let keys = 0;
  const decoder = new WireDecoder({
    extensionCodec: new ExtensionCodec<undefined>(), // no extension types, not even the timestamp
    useBigInt64: true, // 64-bit integers arrive as bigint, so the safe range can be checked
    maxStrLength: data.length,
    maxBinLength: data.length,
    maxExtLength: 0,
    maxArrayLength: limits.maxLen,
    maxMapLength: limits.maxLen,
    mapKeyConverter: (key: unknown) => {
      // The decoder would turn integer keys into strings and keep the last of two equal keys: reject the first and
      // make every key unique (a counter prefix that convert() strips), so convert() sees each written key.
      if (typeof key !== "string") throw new WireError(`map keys must be str, got ${keyType(key)}`);
      return `${(keys++).toString(36)}\u0000${key}`;
    },
  });
  let raw: unknown;
  try {
    raw = decoder.decode(data);
  } catch (error) {
    throw new WireError(error instanceof Error ? error.message : String(error));
  }
  return raw instanceof FloatValue ? raw.value : convert(raw, 0, limits);
}

function convert(value: unknown, depth: number, limits: WireLimits): Wire {
  switch (typeof value) {
    case "string":
    case "boolean":
    case "number":
      return value;
    case "bigint":
      if (value < -MAX_SAFE || value > MAX_SAFE) throw new WireError(`integer out of range: ${value}`);
      return Number(value);
    case "object":
      break;
    default:
      throw new WireError(`unsupported wire type: ${typeof value}`);
  }
  if (value === null) return null;
  if (depth + 1 > limits.maxDepth) throw new WireError(`nesting depth exceeds ${limits.maxDepth}`);
  if (Array.isArray(value)) {
    const items: Wire[] = [];
    const floats: boolean[] = [];
    for (const item of value) {
      const float = item instanceof FloatValue;
      floats.push(float);
      items.push(float ? item.value : convert(item, depth + 1, limits));
    }
    markFloats(items, floats);
    return Object.freeze(items);
  }
  if (Object.getPrototypeOf(value) !== Object.prototype) {
    throw new WireError("unsupported wire type: binary or extension value");
  }
  const source = value as Record<string, unknown>;
  const map = newMap();
  const slots = new Set<string>();
  for (const rawKey of Object.keys(source)) {
    const key = unescapeKey(rawKey.slice(rawKey.indexOf("\u0000") + 1)); // strip the prefix of mapKeyConverter
    if (Object.hasOwn(map, key)) throw new WireError(`duplicate map key after unescaping: ${JSON.stringify(key)}`);
    const item = source[rawKey];
    if (item instanceof FloatValue) {
      slots.add(key);
      defineKey(map, key, item.value);
    } else {
      defineKey(map, key, convert(item, depth + 1, limits));
    }
  }
  if (slots.size > 0) FLOAT_SLOTS.set(map, slots);
  return Object.freeze(map) as Wire;
}

function keyType(key: unknown): string {
  if (key instanceof FloatValue || typeof key === "number") return "number";
  return key === null ? "null" : typeof key;
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
