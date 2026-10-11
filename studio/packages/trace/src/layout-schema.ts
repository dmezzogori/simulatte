/** The bounded JSON-Schema subset used by the canonical Python/TypeScript layout schema.
 * Vite embeds this static JSON import; decoding never loads files or fetches a schema.
 */
import schema from "../../../../src/simulatte/layout/schema/layout-v1.schema.json";

type Rule = boolean | { readonly [key: string]: any };
const KEYWORDS = new Set("$defs $id $ref $schema additionalProperties anyOf const default enum exclusiveMinimum items maxItems maximum minItems minLength minimum oneOf pattern prefixItems properties propertyNames required title type uniqueItems".split(" "));

/** Fail eagerly when the shared schema grows beyond this supported subset. */
export function assertSupportedSchema(rule: Rule): void {
  if (typeof rule === "boolean") return;
  for (const key of Object.keys(rule)) if (!KEYWORDS.has(key)) throw new TypeError(`unsupported layout schema keyword: ${key}`);
  if (rule["$ref"] !== undefined && !/^#\/\$defs\/[A-Za-z]+$/.test(rule["$ref"])) throw new TypeError("unsupported layout schema reference");
  if (rule["type"] !== undefined && !["object", "array", "string", "number", "integer", "boolean"].includes(rule["type"])) throw new TypeError("unsupported layout schema type");
  for (const key of ["$defs", "properties"]) for (const child of Object.values(rule[key] ?? {})) assertSupportedSchema(child as Rule);
  for (const key of ["oneOf", "anyOf", "prefixItems"]) for (const child of rule[key] ?? []) assertSupportedSchema(child);
  for (const key of ["additionalProperties", "items", "propertyNames"]) if (rule[key] !== undefined) assertSupportedSchema(rule[key]);
}
assertSupportedSchema(schema);

function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value !== null && typeof value === "object") return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonical((value as Record<string, unknown>)[key])}`).join(",")}}`;
  return JSON.stringify(value);
}

function valid(value: unknown, rule: Rule): boolean {
  try { validate(value, rule); return true; } catch (error) { if (error instanceof TypeError) return false; throw error; }
}
function validate(value: unknown, rule: Rule): void {
  const fail = (): never => { throw new TypeError("invalid layout schema value"); };
  if (rule === false) fail();
  if (typeof rule === "boolean") return;
  if (rule["$ref"] !== undefined) validate(value, (schema.$defs as Record<string, Rule>)[rule["$ref"].slice(8)]!);
  for (const key of ["oneOf", "anyOf"]) if (rule[key] !== undefined) {
    const count = (rule[key] as Rule[]).filter((child) => valid(value, child)).length;
    if (count === 0 || (key === "oneOf" && count !== 1)) fail();
  }
  const object = value !== null && typeof value === "object" && !Array.isArray(value);
  const numeric = typeof value === "number" && Number.isFinite(value);
  const types: Record<string, boolean> = { object, array: Array.isArray(value), string: typeof value === "string", number: numeric, integer: numeric && Number.isInteger(value), boolean: typeof value === "boolean" };
  if (rule["type"] !== undefined && !types[rule["type"]]) fail();
  if ("const" in rule && canonical(value) !== canonical(rule["const"])) fail();
  if (rule["enum"] !== undefined && !(rule["enum"] as unknown[]).some((item) => canonical(item) === canonical(value))) fail();
  if (object) {
    const record = value as Record<string, unknown>;
    if ((rule["required"] ?? []).some((key: string) => !Object.hasOwn(record, key))) fail();
    const properties = rule["properties"] ?? {};
    for (const [key, item] of Object.entries(record)) {
      if (rule["propertyNames"] !== undefined) validate(key, rule["propertyNames"]);
      validate(item, Object.hasOwn(properties, key) ? properties[key] : rule["additionalProperties"] ?? {});
    }
  }
  if (Array.isArray(value)) {
    if (value.length < (rule["minItems"] ?? 0) || value.length > (rule["maxItems"] ?? Infinity)) fail();
    if (rule["uniqueItems"] && new Set(value.map(canonical)).size !== value.length) fail();
    const prefix: Rule[] = rule["prefixItems"] ?? [];
    value.forEach((item, index) => validate(item, index < prefix.length ? prefix[index]! : rule["items"] ?? {}));
  }
  if (typeof value === "string" && (Array.from(value).length < (rule["minLength"] ?? 0) || (rule["pattern"] !== undefined && !new RegExp(rule["pattern"], "u").test(value)))) fail();
  if (numeric && (value < (rule["minimum"] ?? -Infinity) || value > (rule["maximum"] ?? Infinity) || value <= (rule["exclusiveMinimum"] ?? -Infinity))) fail();
}

export function validateLayoutPatch(value: unknown): void { validate(value, schema); }
export function validateLayoutDefinition(value: unknown, name: keyof typeof schema.$defs): void { validate(value, schema.$defs[name]); }
