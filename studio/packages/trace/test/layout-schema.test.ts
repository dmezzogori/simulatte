import { readFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { build } from "vite";
import { expect, it } from "vitest";
import { assertSupportedSchema, validateLayoutDefinition, validateLayoutPatch } from "../src/layout-schema";

it("rejects unsupported schema keywords, references and types eagerly, including nested rules", () => {
  for (const schema of [{ not: {} }, { $ref: "https://example.org/schema" }, { type: "null" }, { properties: { position: { multipleOf: 2 } } }]) {
    expect(() => assertSupportedSchema(schema)).toThrow(/unsupported layout schema/);
  }
});

it("uses the shared schema for geometry, atomic addresses, typed tombstones and hostile keys", () => {
  validateLayoutDefinition({ kind: "explicit", id: "😀" }, "address");
  validateLayoutPatch({ schema_version: "1.0", placements: { machine: { kind: "server", position: { $unset: true } } } });
  for (const value of [
    { kind: "lattice", i: 0 }, { kind: "lattice", i: 0.5, j: 0 },
    { kind: "explicit", id: "" }, { kind: "explicit", id: "x", extra: true },
    { kind: "edge", source: { kind: "lattice", i: 0, j: 0 }, target: { kind: "explicit" } },
  ]) expect(() => validateLayoutDefinition(value, "address")).toThrow();
  for (const value of [
    { schema_version: "1.0", placements: { machine: { kind: "server", position: [0, 0, 0, 0] } } },
    { schema_version: "1.0", placements: { machine: { kind: "server", visual: { color: 42 } } } },
    { schema_version: "1.0", grid: { dx: -1 } },
    JSON.parse('{"schema_version":"1.0","__proto__":{}}'),
  ]) expect(() => validateLayoutPatch(value)).toThrow();
});

it("accepts the schema 1.1 traffic fields and still rejects unknown keys", () => {
  validateLayoutPatch({ schema_version: "1.1", network: { type: "lattice", bounds: [0, 0, 2, 1], passing: true, block_length: 0.5, zones: { row: { members: [{ kind: "row", j: 0 }] } } } });
  validateLayoutPatch({ schema_version: "1.1", overrides: [{ id: "p", target: { kind: "column", i: 1 }, properties: { passing: false } }] });
  expect(() => validateLayoutPatch({ schema_version: "1.1", network: { type: "lattice", bounds: [0, 0, 2, 1], block_length: 0 } })).toThrow();
  expect(() => validateLayoutPatch({ schema_version: "1.1", network: { type: "lattice", bounds: [0, 0, 2, 1], unknown_key: 1 } })).toThrow();
  expect(() => validateLayoutPatch({ schema_version: "1.1", unknown_key: 1 })).toThrow();
});

it("accepts a length on explicit arcs of schema 1.1 and rejects a bad one or an unknown sibling", () => {
  const arcs = (arc: object) => ({ schema_version: "1.1", network: { type: "explicit", nodes: [{ id: "A", position: [0, 0, 0] }, { id: "B", position: [10, 0, 0] }], arcs: [{ source: "A", target: "B", ...arc }] } });
  validateLayoutPatch(arcs({ length: 25 }));
  validateLayoutPatch(arcs({ length: { $unset: true } }));
  for (const arc of [{ length: 0 }, { length: -1 }, { length: "long" }, { extent: 25 }]) expect(() => validateLayoutPatch(arcs(arc))).toThrow();
});

it("bundles the canonical schema and opens layout metadata without runtime filesystem or network access", async () => {
  const result = await build({ configFile: false, logLevel: "silent", ssr: {noExternal: ["@noble/hashes"]}, build: {
    ssr: fileURLToPath(new URL("../src/index.ts", import.meta.url)), write: false, minify: false,
  } });
  if (Array.isArray(result) || !("output" in result)) throw new Error("unexpected build result");
  const chunk = result.output.find((item) => item.type === "chunk");
  if (!chunk || chunk.type !== "chunk") throw new Error("missing bundle");
  const fixture = readFileSync(new URL("../../../../tests/fixtures/traces/generated/layout_full.simtrace", import.meta.url)).toString("base64");
  const output = execFileSync(process.execPath, ["--input-type=module", "-e", `
    import fs from 'node:fs';
    const input = JSON.parse(fs.readFileSync(0, 'utf8'));
    fs.readFileSync = fs.readFile = () => { throw new Error('runtime filesystem access'); };
    globalThis.fetch = () => { throw new Error('runtime network access'); };
    const { openTrace } = await import('data:text/javascript;base64,' + Buffer.from(input.code).toString('base64'));
    const bytes = Uint8Array.from(Buffer.from(input.fixture, 'base64'));
    const trace = await openTrace(bytes.buffer);
    if (trace.layout?.schema_version !== '1.0') throw new Error('missing layout');
    console.log(trace.layout.physical_hash);
  `], { input: JSON.stringify({ code: chunk.code, fixture }), encoding: "utf8" });
  expect(output.trim()).toMatch(/^[0-9a-f]{64}$/);
});
