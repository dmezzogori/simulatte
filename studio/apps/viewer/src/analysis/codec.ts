/** Tagged JSON (``simulatte.analysis.codec``) in the viewer: exact int64 as ``bigint``, non-finite float64 as IEEE values. */
export type Plain =
  | null
  | boolean
  | number
  | bigint
  | string
  | Plain[]
  | { [key: string]: Plain };
export const INT64_MAX = 9223372036854775807n;
export const INT64_MIN = -9223372036854775808n;
const INT_TEXT = /^-?(?:0|[1-9][0-9]*)$/;
const isObject = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value);
function record(value: Record<string, unknown>): { [key: string]: Plain } {
  const out: { [key: string]: Plain } = {};
  for (const [key, item] of Object.entries(value)) out[key] = untag(item);
  return out;
}
/** ``{$record}`` → object, ``{$int64}`` → bigint, ``{$float64}`` → NaN/±Infinity; other JSON passes through. */
export function untag(value: unknown): Plain {
  if (value === null || value === undefined) return null;
  if (Array.isArray(value)) return value.map(untag);
  switch (typeof value) {
    case "string":
    case "boolean":
    case "bigint":
      return value;
    case "number":
      if (!Number.isFinite(value)) throw new Error("Bare non-finite number in tagged JSON");
      return value;
  }
  if (!isObject(value)) throw new Error("Unsupported tagged value");
  const keys = Object.keys(value);
  if (keys.length === 1) {
    const inner = value[keys[0]!];
    switch (keys[0]) {
      case "$record":
        if (!isObject(inner)) throw new Error("Invalid $record");
        return record(inner);
      case "$int64": {
        if (typeof inner !== "string" || !INT_TEXT.test(inner)) throw new Error("Invalid $int64");
        const big = BigInt(inner);
        if (big > INT64_MAX || big < INT64_MIN) throw new Error("$int64 out of range");
        return big;
      }
      case "$float64":
        if (inner === "nan") return NaN;
        if (inner === "inf") return Infinity;
        if (inner === "-inf") return -Infinity;
        throw new Error("Invalid $float64");
    }
  }
  return record(value);
}
function jsonText(value: Plain): string {
  if (value === null) return "null";
  if (typeof value === "bigint") return value.toString();
  if (typeof value === "number") return Number.isFinite(value) ? JSON.stringify(value) : JSON.stringify(formatValue(value));
  if (typeof value === "string" || typeof value === "boolean") return JSON.stringify(value);
  if (Array.isArray(value)) return "[" + value.map(jsonText).join(",") + "]";
  return "{" + Object.entries(value).map(([key, item]) => JSON.stringify(key) + ":" + jsonText(item)).join(",") + "}";
}
/** Display text: exact int64 digits, ``NaN``/``+∞``/``−∞`` for non-finite values and ``—`` for missing ones. */
export function formatValue(value: unknown, dtype?: unknown): string {
  if (value === null || value === undefined) return "—";
  switch (typeof value) {
    case "bigint":
      return value.toString();
    case "number":
      if (Number.isNaN(value)) return "NaN";
      if (value === Infinity) return "+∞";
      if (value === -Infinity) return "−∞";
      return dtype === "int64" && Number.isInteger(value) ? value.toFixed(0) : String(value);
    case "boolean":
      return value ? "true" : "false";
    case "string":
      return value;
    default:
      return jsonText(value as Plain);
  }
}
const INF = /^\+?(?:inf|infinity|∞)$/i;
const NEG_INF = /^[-−](?:inf|infinity|∞)$/i;
function scalarJson(text: string, dtype: string): string {
  const raw = text.trim();
  if (raw.toLowerCase() === "null") return "null";
  switch (dtype) {
    case "int64": {
      if (!INT_TEXT.test(raw)) throw new Error("Enter an integer");
      const big = BigInt(raw);
      if (big > INT64_MAX || big < INT64_MIN) throw new Error("Value is outside the int64 range");
      return Number.isSafeInteger(Number(big)) ? raw : JSON.stringify({ $int64: raw });
    }
    case "float64": {
      if (raw.toLowerCase() === "nan") return JSON.stringify({ $float64: "nan" });
      if (INF.test(raw)) return JSON.stringify({ $float64: "inf" });
      if (NEG_INF.test(raw)) return JSON.stringify({ $float64: "-inf" });
      const number = Number(raw);
      if (raw === "" || !Number.isFinite(number)) throw new Error("Enter a number, NaN, inf or -inf");
      return JSON.stringify(number);
    }
    case "bool":
      if (raw !== "true" && raw !== "false") throw new Error("Enter true or false");
      return raw;
    default:
      return JSON.stringify(text);
  }
}
/** A filter operand as tagged JSON text for the rows route; ``list`` splits comma-separated values for ``in``. */
export function tagJson(text: string, dtype: string, list = false): string {
  if (!list) return scalarJson(text, dtype);
  const items = dtype === "string" ? text.split(",") : text.split(",").map((item) => item.trim());
  return "[" + items.map((item) => scalarJson(item, dtype)).join(",") + "]";
}
