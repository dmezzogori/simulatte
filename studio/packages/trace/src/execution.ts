/** Optional execution-v1 data. Structural validation only, like the existing TS reader:
 * request hashes and semantic fingerprints are verified by the Python coordinator. */
import { TraceCorrupted } from "./container";
import type { Wire } from "./wire";

type Map = Readonly<Record<string, Wire>>;
export interface ExecutionProvenance {
  readonly header: Map;
  readonly initial: Map | null;
  readonly footer: Map | null;
}
function fail(): never { throw new TraceCorrupted("Invalid execution provenance"); }
function map(value: unknown): Map {
  if (value === null || typeof value !== "object" || Array.isArray(value)) fail();
  return value as Map;
}
function version(value: unknown): Map {
  const data = map(value);
  if (data["schema"] !== 1) fail();
  return data;
}
function hash(value: unknown): void {
  if (typeof value !== "string" || !/^[0-9a-f]{64}$/.test(value)) fail();
}
function finite(value: unknown): value is number { return typeof value === "number" && Number.isFinite(value); }
function natural(value: unknown): value is number { return finite(value) && Number.isSafeInteger(value) && value >= 0; }
const identifier = (value: string) => /^[\p{ID_Start}_][\p{ID_Continue}]*$/u.test(value);

function request(value: unknown): void {
  const data = version(value);
  for (const key of ["source_hash", "input_hash", "layout_hash"]) hash(data[key]);
  const model = data["model"];
  if (typeof model !== "string") fail();
  const parts = model.split(":");
  if (parts.length !== 2 || !identifier(parts[1]!) || !parts[0]!.endsWith(".py") || parts[0]!.includes("\\") || parts[0]!.includes("\0") || parts[0]!.split("/").some((part) => !part || part === "." || part === "..")) fail();
  if (!natural(data["layout_revision"]) || !["full", "kpi"].includes(data["level"] as string)) fail();
  const seed = data["seed"];
  if (typeof seed !== "string" || !/^(0|[1-9][0-9]{0,18})$/.test(seed) || BigInt(seed) >= 2n ** 63n) fail();
  const parameters = map(data["parameters"]);
  for (const [key, item] of Object.entries(parameters)) {
    if (!identifier(key) || !(typeof item === "string" || typeof item === "boolean" || finite(item))) fail();
  }
  map(data["runtime"]);
  const kpis = data["kpis"];
  if (!Array.isArray(kpis) || kpis.some((item) => typeof item !== "string" || !item) || new Set(kpis).size !== kpis.length) fail();
  const stop = map(data["stopping"]);
  if (stop["type"] === "horizon") {
    const horizon = stop["horizon"], warmup = stop["warmup"];
    if (Object.keys(stop).length !== 3 || !finite(horizon) || !finite(warmup) || horizon <= 0 || warmup < 0 || warmup >= horizon) fail();
  } else if (stop["type"] === "finite_population") {
    const arrivals = stop["arrivals"], cutoff = stop["cutoff"], safety = stop["safety_limit"];
    if (Object.keys(stop).length !== 4 || !finite(safety) || safety <= 0 || (arrivals === null) === (cutoff === null)) fail();
    if (arrivals === null ? !finite(cutoff) || cutoff < 0 || cutoff >= safety : !natural(arrivals) || cutoff !== null) fail();
  } else fail();
}

export function executionOf(headerRecord: Map, initialExecution: unknown, footerRecord: Map | null, hasInitial: boolean): ExecutionProvenance | null {
  const optional = map(headerRecord["features"])["optional"];
  if (!Array.isArray(optional) || !optional.includes("execution-v1")) return null;
  const header = version(headerRecord["execution"]);
  hash(header["request_id"]);
  request(header["request"]);
  let initial: Map | null = null, footer: Map | null = null;
  if (hasInitial) {
    initial = version(initialExecution);
    const resolved = version(initial["resolved"]);
    for (const name of ["runtime", "dependencies", "imports", "layout", "capabilities"]) map(resolved[name]);
    const warnings = resolved["warnings"];
    if (!Array.isArray(warnings) || warnings.some((item) => typeof item !== "string")) fail();
  }
  if (footerRecord !== null) {
    footer = version(footerRecord["execution"]);
    const reason = footer["termination"], end = footer["actual_end"], cursor = footerRecord["cursor"];
    if (typeof reason !== "string" || !reason || new TextEncoder().encode(reason).length > 4096 || !finite(end) || end < 0 || !Array.isArray(cursor) || end !== cursor[0]) fail();
  }
  return Object.freeze({ header, initial, footer });
}
