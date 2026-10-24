/** Reading saved outputs: names for people and values at a readable precision. */
import { formatValue, type Plain } from "../analysis/codec";
import { createContext } from "react";
import { formatFinding } from "../findings";

/** Whether the reader asked for full digits on this page. */
export const FullDigits = createContext(false);

/** ``lateness_by_family`` reads as "Lateness by family"; the emitted name stays visible as the code name. */
export function outputTitle(name: string): string {
  const words = name.replace(/[_.-]+/g, " ").trim();
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : name;
}
/** A value for reading: floats to four significant digits unless full digits are asked for; int64 stays exact. */
export function readableValue(value: Plain | undefined, dtype?: unknown, full = false): string {
  if (typeof value === "number" && Number.isFinite(value) && !(dtype === "int64" && Number.isInteger(value)))
    return full ? formatValue(value, dtype).replace(/^-/, "\u2212") : formatFinding(value);
  if (typeof value === "bigint") return value.toString().replace(/^-/, "\u2212");
  return formatValue(value, dtype);
}
