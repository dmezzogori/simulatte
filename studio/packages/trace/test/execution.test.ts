import { describe, expect, it } from "vitest";
import { openTrace, TraceCorrupted } from "../src/index";
import { buildTrace, HEADER } from "./build";

const request = { schema: 1, model: "plant.py:build", source_hash: "a".repeat(64), input_hash: "b".repeat(64), layout_hash: "c".repeat(64), layout_revision: 1, parameters: { count: 1, rate: 1.5, enabled: true }, seed: "9223372036854775807", stopping: { type: "horizon", horizon: 1, warmup: 0 }, runtime: {}, level: "full", kpis: [] };
const requested = { schema: 1, request_id: "d".repeat(64), request };
const resolved = { schema: 1, runtime: {}, dependencies: {}, imports: {}, layout: {}, capabilities: {}, warnings: [] };
const initial = { cursor: [0, -1], state: {}, manifest: {}, execution: { schema: 1, resolved } };
const ended = { schema: 1, termination: "horizon", actual_end: 1 };
const header = { ...HEADER, features: { ...HEADER.features, optional: ["execution-v1"] }, execution: requested };

function recording(overrides: Record<string, unknown> = {}, initialOverride: unknown = initial, footerOverride: Record<string, unknown> = {}) {
  return buildTrace(initialOverride, [], { header: { ...header, ...overrides }, footer: { cursor: [1, -1], execution: ended, ...footerOverride } });
}

describe("optional execution provenance", () => {
  it("exposes immutable requested/resolved/final metadata", async () => {
    const trace = await openTrace(recording());
    expect(trace.execution).toEqual({ header: requested, initial: initial.execution, footer: ended });
    expect(Reflect.set(trace.execution!.header, "schema", 2)).toBe(false);
    expect(Reflect.set(trace.execution!, "footer", null)).toBe(false);
    expect((await openTrace(buildTrace({ cursor: [0, -1], state: {}, manifest: {} }))).execution).toBeNull();
  });
  it("retains unknown optional fields in known provenance records", async () => {
    const extra = { future: { information: 7 } };
    const trace = await openTrace(recording({ execution: { ...requested, ...extra } },
      { ...initial, execution: { ...initial.execution, ...extra, resolved: { ...resolved, ...extra } } },
      { execution: { ...ended, ...extra } }));
    expect(trace.execution!.header["future"]).toEqual(extra.future);
    expect(trace.execution!.initial!["future"]).toEqual(extra.future);
    expect(trace.execution!.footer!["future"]).toEqual(extra.future);
  });
  it("ignores an unknown optional execution feature", async () => {
    const trace = await openTrace(recording({ features: { ...HEADER.features, optional: ["execution-v9"] }, execution: { schema: 9, future: true } }));
    expect(trace.execution).toBeNull();
  });
  it.each([
    { schema: 2 }, { request_id: "bad" }, { request: { ...request, seed: "-1" } },
    { request: { ...request, layout_revision: -1 } }, { request: { ...request, source_hash: "bad" } },
    { request: { ...request, parameters: { bad: Infinity } } },
  ])("rejects malformed requested metadata %j", async (change) => {
    await expect(openTrace(recording({ execution: { ...requested, ...change } }))).rejects.toThrow(TraceCorrupted);
  });
  it("rejects missing INITIAL provenance and mismatched termination time", async () => {
    await expect(openTrace(recording({}, { cursor: [0, -1], state: {}, manifest: {} }))).rejects.toThrow(TraceCorrupted);
    await expect(openTrace(recording({}, initial, { execution: { ...ended, actual_end: 2 } }))).rejects.toThrow(TraceCorrupted);
  });
});
