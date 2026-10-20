import { describe, it, expect } from "vitest";
import { untag, formatValue, tagJson, INT64_MAX, INT64_MIN } from "../src/analysis/codec";

describe("tagged JSON codec", () => {
  it("untags records, exact int64 and non-finite float64 values recursively", () => {
    const value = untag({
      $record: {
        big: { $int64: "9223372036854775807" },
        small: 7,
        nan: { $float64: "nan" },
        up: { $float64: "inf" },
        down: { $float64: "-inf" },
        text: "x",
        nested: [{ $record: { n: { $int64: "-9223372036854775808" } } }, null, true],
      },
    }) as Record<string, unknown>;
    expect(value.big).toBe(9223372036854775807n);
    expect(value.small).toBe(7);
    expect(Number.isNaN(value.nan)).toBe(true);
    expect(value.up).toBe(Infinity);
    expect(value.down).toBe(-Infinity);
    expect(value.text).toBe("x");
    expect((value.nested as unknown[])[0]).toEqual({ n: INT64_MIN });
    expect((value.nested as unknown[]).slice(1)).toEqual([null, true]);
    expect(value.big).toBe(INT64_MAX);
  });
  it("refuses malformed tags and out-of-range int64 text", () => {
    expect(() => untag({ $int64: "1.5" })).toThrow();
    expect(() => untag({ $int64: "9223372036854775808" })).toThrow();
    expect(() => untag({ $float64: "infinity" })).toThrow();
    expect(() => untag({ $record: [] })).toThrow();
  });
  it("round trips 2^63-1 as an exact decimal string", () => {
    const text = "9223372036854775807";
    const value = untag({ $int64: text });
    expect(typeof value).toBe("bigint");
    expect(formatValue(value)).toBe(text);
    expect(formatValue(value, "int64")).toBe(text);
    expect(Number(text).toString()).not.toBe(text); // the float path would lose the value
    expect(tagJson(text, "int64")).toBe(JSON.stringify({ $int64: text }));
  });
  it("formats nulls, non-finite values, booleans and nested values explicitly", () => {
    expect(formatValue(null)).toBe("—");
    expect(formatValue(undefined)).toBe("—");
    expect(formatValue(NaN)).toBe("NaN");
    expect(formatValue(Infinity)).toBe("+∞");
    expect(formatValue(-Infinity)).toBe("−∞");
    expect(formatValue(0.5, "float64")).toBe("0.5");
    expect(formatValue(12, "float64")).toBe("12");
    expect(formatValue(true)).toBe("true");
    expect(formatValue("text")).toBe("text");
    expect(formatValue([1n, NaN, null])).toBe('[1,"NaN",null]');
    expect(formatValue({ a: 2n ** 60n })).toBe('{"a":1152921504606846976}');
  });
  it("tags filter operands by field type, keeping safe integers plain", () => {
    expect(tagJson("42", "int64")).toBe("42");
    expect(tagJson("-9007199254740992", "int64")).toBe(JSON.stringify({ $int64: "-9007199254740992" }));
    expect(tagJson("nan", "float64")).toBe(JSON.stringify({ $float64: "nan" }));
    expect(tagJson("+∞", "float64")).toBe(JSON.stringify({ $float64: "inf" }));
    expect(tagJson("-inf", "float64")).toBe(JSON.stringify({ $float64: "-inf" }));
    expect(tagJson("2.5", "float64")).toBe("2.5");
    expect(tagJson("true", "bool")).toBe("true");
    expect(tagJson("a,b", "string")).toBe('"a,b"');
    expect(tagJson("null", "int64")).toBe("null");
    expect(tagJson("1, 2", "int64", true)).toBe("[1,2]");
    expect(() => tagJson("1.5", "int64")).toThrow(/integer/);
    expect(() => tagJson("9223372036854775808", "int64")).toThrow(/int64/);
    expect(() => tagJson("abc", "float64")).toThrow(/number/);
    expect(() => tagJson("yes", "bool")).toThrow(/true or false/);
  });
});
