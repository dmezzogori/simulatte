import { describe, expect, it } from "vitest";
import { decodeWire, isFloatAt, WireError } from "../src/wire";
import { pack, RawMap } from "./build";

const LIMITS = { maxDepth: 64, maxLen: 1000 };
const decode = (bytes: number[]) => decodeWire(new Uint8Array(bytes), LIMITS);

describe("wire subset decoder", () => {
  it.each([
    [[0xcc, 0xff], 255], [[0xcd, 0xff, 0xff], 65535], [[0xce, 0xff, 0xff, 0xff, 0xff], 4294967295],
    [[0xcf, 0, 0x1f, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff], Number.MAX_SAFE_INTEGER],
    [[0xd0, 0x80], -128], [[0xd1, 0x80, 0], -32768], [[0xd2, 0x80, 0, 0, 0], -2147483648],
    [[0xd3, 0xff, 0xe0, 0, 0, 0, 0, 0, 1], -Number.MAX_SAFE_INTEGER],
    [[0xd9, 1, 120], "x"], [[0xda, 0, 1, 120], "x"], [[0xdb, 0, 0, 0, 1, 120], "x"],
    [[0xdc, 0, 1, 1], [1]], [[0xdd, 0, 0, 0, 1, 1], [1]],
    [[0xde, 0, 1, 0xa1, 120, 1], { x: 1 }], [[0xdf, 0, 0, 0, 1, 0xa1, 120, 1], { x: 1 }],
  ] as const)("reads tag %j", (bytes, expected) => {
    expect(decode([...bytes])).toEqual(expected);
    for (let length = 0; length < bytes.length; length++) {
      expect(() => decode([...bytes.slice(0, length)])).toThrow(WireError);
    }
  });

  it("tracks integral f32 and f64, preserves negative zero and nonfinite floats", () => {
    const values = decode([0x96, 1, 0xca, 0x3f, 0x80, 0, 0, 0xcb, 0x3f, 0xf0, 0, 0, 0, 0, 0, 0,
      0xca, 0x80, 0, 0, 0, 0xca, 0x7f, 0x80, 0, 0, 0xca, 0x7f, 0xc0, 0, 0]) as number[];
    expect(values).toEqual([1, 1, 1, -0, Infinity, NaN]);
    expect(values.map((_, i) => isFloatAt(values, i))).toEqual([false, true, true, false, false, false]);
    expect(Object.is(values[3], -0)).toBe(true);
  });

  it.each([0xc1, 0xc4, 0xc5, 0xc6, 0xc7, 0xc8, 0xc9, 0xd4, 0xd5, 0xd6, 0xd7, 0xd8])(
    "rejects unsupported binary, extension and reserved tag %i", (tag) => {
      expect(() => decode([tag, ...new Array<number>(32).fill(0)])).toThrow(WireError);
    },
  );

  it("rejects unsafe integers and trailing bytes", () => {
    expect(() => decode([0xcf, 0, 0x20, 0, 0, 0, 0, 0, 0])).toThrow(/integer out of range/);
    expect(() => decode([0xd3, 0xff, 0xdf, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff])).toThrow(/integer out of range/);
    expect(() => decode([0xc0, 0xc0])).toThrow(/trailing/);
  });

  it("checks depth before recursion and declared size before allocation", () => {
    const nested = new Uint8Array([...new Array<number>(10000).fill(0x91), 0]);
    expect(() => decodeWire(nested, LIMITS)).toThrow(/nesting depth/);
    expect(() => decode([0xdd, 0xff, 0xff, 0xff, 0xff])).toThrow(/length/);
    expect(() => decode([0xdf, 0xff, 0xff, 0xff, 0xff])).toThrow(/length/);
    expect(() => decode([0xdd, 0, 0, 1, 0])).toThrow(/truncated/);
    expect(decodeWire(pack([[1]]), { maxDepth: 2, maxLen: 1 })).toEqual([[1]]);
    expect(() => decodeWire(pack([[1]]), { maxDepth: 1, maxLen: 1 })).toThrow(/nesting depth/);
  });

  it("rejects collisions after key unescaping and handles byte-array offsets", () => {
    expect(() => decodeWire(pack(new RawMap([["a", 1], ["~a", 2]])), LIMITS)).toThrow(/duplicate map key/);
    const data = new Uint8Array([99, 0xcd, 1, 0, 99]);
    expect(decodeWire(data.subarray(1, 4), LIMITS)).toBe(256);
  });
});
