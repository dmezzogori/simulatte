// Readers of recorded values shared by the map model: each takes what it can use of an unknown value, else a
// neutral one. A trace may lack any field, or hold one of another type.

export type Fields = Record<string, unknown>;

/** A plain object, else an empty one. */
export const object = (v: unknown): Fields =>
  v !== null && typeof v === "object" && !Array.isArray(v) ? (v as Fields) : {};
/** A finite number, else null. */
export const finite = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
/** A string, else null. */
export const text = (v: unknown): string | null => (typeof v === "string" ? v : null);
/** A non-empty string, else null. */
export const named = (v: unknown): string | null => (typeof v === "string" && v !== "" ? v : null);
/** The strings of an array, else none. */
export const strings = (v: unknown): string[] =>
  Array.isArray(v) ? v.filter((id): id is string => typeof id === "string") : [];
/** The finite quantities of a record by key, in a map without a prototype: an SKU may be named `__proto__`. */
export function quantitiesOf(v: unknown): Record<string, number> {
  const quantities: Record<string, number> = Object.create(null);
  for (const [key, quantity] of Object.entries(object(v))) {
    const n = finite(quantity);
    if (n !== null) quantities[key] = n;
  }
  return quantities;
}
