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
 * Decode one MessagePack value into a wire value.
 *
 * Throws {@link WireError} for malformed or truncated input, trailing data, limit violations, values outside the
 * wire model (binary, extension types, timestamps, integers beyond +/-(2^53 - 1)) and maps whose keys repeat after
 * unescaping (for example `"a"` and `"~a"`).
 *
 * Two checks of the Python reader cannot be made on top of `@msgpack/msgpack`: a map with the same raw key twice
 * keeps the last value, and a map keyed by integers is read as if the keys were their decimal strings.
 */
export function decodeWire(data: Uint8Array, limits: WireLimits): Wire {
  const decoder = new Decoder({
    extensionCodec: new ExtensionCodec<undefined>(), // no extension types, not even the timestamp
    useBigInt64: true, // 64-bit integers arrive as bigint, so the safe range can be checked
    maxStrLength: data.length,
    maxBinLength: data.length,
    maxExtLength: 0,
    maxArrayLength: limits.maxLen,
    maxMapLength: limits.maxLen,
  });
  let raw: unknown;
  try {
    raw = decoder.decode(data);
  } catch (error) {
    throw new WireError(error instanceof Error ? error.message : String(error));
  }
  return convert(raw, 0, limits);
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
    return Object.freeze(value.map((item) => convert(item, depth + 1, limits)));
  }
  if (Object.getPrototypeOf(value) !== Object.prototype) {
    throw new WireError("unsupported wire type: binary or extension value");
  }
  const source = value as Record<string, unknown>;
  const map = newMap();
  for (const rawKey of Object.keys(source)) {
    const key = unescapeKey(rawKey);
    if (Object.hasOwn(map, key)) throw new WireError(`duplicate map key after unescaping: ${JSON.stringify(key)}`);
    defineKey(map, key, convert(source[rawKey], depth + 1, limits));
  }
  return Object.freeze(map) as Wire;
}

/** Whether two wire values are equal; numbers compare by value (so `0` equals `-0`, as in the Python reader). */
export function wireEquals(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (typeof a !== "object" || typeof b !== "object" || a === null || b === null) return false;
  if (Array.isArray(a) || Array.isArray(b)) {
    return Array.isArray(a) && Array.isArray(b) && a.length === b.length && a.every((item, i) => wireEquals(item, b[i]));
  }
  const keys = Object.keys(a);
  return (
    keys.length === Object.keys(b).length &&
    keys.every((key) => Object.hasOwn(b, key) && wireEquals(descriptorValue(a, key), descriptorValue(b, key)))
  );
}

function descriptorValue(target: object, key: string): unknown {
  return Object.getOwnPropertyDescriptor(target, key)?.value;
}
