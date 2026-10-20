import { describe, it, expect } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { ArtifactView, orderOutputs, ProvenanceNotes } from "../src/artifacts/view";
import { TableRows, clampLimit, tableQuery, PAGE_LIMITS } from "../src/artifacts/table";
import {
  validateFigure,
  rendererState,
  acceptsFrameMessage,
  frameUrl,
  PACKAGED_PLOTLY_VERSION,
} from "../src/artifacts/figure-frame";
import type { ArtifactSource, OutputDescriptor, OutputManifest } from "../src/artifacts/types";
import { annotationDisabled } from "../src/workspace/analyses";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const metadata = (extra: Record<string, unknown> = {}) => ({
  partial_snapshot: true,
  planned: 24,
  available: 8,
  inferential_convention: "descriptive",
  input_availability: {
    jobs: {
      valid: 8,
      missing: [{ run: "r", configuration: 0, index: 9, status: "pending", missing_fields: [], reason: "not finished" }],
    },
  },
  ...extra,
});
const output = (name: string, kind: OutputDescriptor["kind"], extra: Record<string, unknown> = {}): OutputDescriptor => ({
  id: name.padEnd(64, "0"),
  name,
  kind,
  artifacts: [{ path: `${name}/x`, length: 1, sha256: "a".repeat(64) }],
  metadata: metadata(extra),
  renderer: kind === "figure" ? { name: "plotly.js", version: PACKAGED_PLOTLY_VERSION } : null,
});
const manifest: OutputManifest = {
  schema: "analysis-output-v1",
  execution_id: "e",
  provenance: {},
  outputs: [
    output("figure", "figure"),
    output("picture", "image", { media_type: "image/png" }),
    output("values", "table", {
      schema: { name: "values", fields: [{ name: "big", dtype: "int64", unit: null, nullable: false }] },
      row_count: 10000,
    }),
    output("spread", "distribution"),
    output("total", "number", { unit: "h" }),
  ],
};
const source: ArtifactSource = {
  page: () => new Promise(() => {}),
  blob: () => new Promise(() => {}),
  csv: () => new Promise(() => {}),
};

describe("artifact view", () => {
  it("orders output cards numeric-first: numbers, tables, distributions, images, figures", () => {
    expect(orderOutputs(manifest.outputs).map((o) => o.kind)).toEqual([
      "number",
      "table",
      "distribution",
      "image",
      "figure",
    ]);
    const html = renderToStaticMarkup(<ArtifactView manifest={manifest} source={source} />);
    const kinds = [...html.matchAll(/data-kind="(\w+)"/g)].map((m) => m[1]);
    expect(kinds).toEqual(["number", "table", "distribution", "image", "figure"]);
    expect(html).toContain("total");
    expect(html).toContain("Partial snapshot");
    expect(html).toContain("8 of 24");
    expect(html).not.toContain("<svg");
  });
  it("labels provenance with counts, convention and missing inputs", () => {
    const html = renderToStaticMarkup(<ProvenanceNotes metadata={metadata()} />);
    expect(html).toContain("8 of 24");
    expect(html).toContain("descriptive");
    expect(html).toContain("not finished");
    expect(html).toContain("replication 9");
  });
});

describe("artifact table", () => {
  const schema = {
    name: "values",
    fields: [
      { name: "big", dtype: "int64", unit: null, nullable: false },
      { name: "value", dtype: "float64", unit: "h", nullable: true },
      { name: "label", dtype: "string", unit: null, nullable: true },
    ],
  };
  it("renders exact int64 text and explicit non-finite labels from untagged rows", () => {
    const html = renderToStaticMarkup(
      <TableRows
        schema={schema}
        rows={[
          { big: 9223372036854775807n, value: NaN, label: "a" },
          { big: 4611686018427387904n, value: Infinity, label: null },
          { big: 1n, value: -Infinity, label: "c" },
        ]}
        offset={0}
      />,
    );
    expect(html).toContain("9223372036854775807");
    expect(html).toContain("4611686018427387904");
    expect(html).toContain(">NaN<");
    expect(html).toContain(">+∞<");
    expect(html).toContain(">−∞<");
    expect(html).toContain(">—<");
    expect(html).toContain("value<small> · h</small>");
    expect(html).not.toContain("9223372036854776000");
  });
  it("caps page sizes at 500 and builds server query strings", () => {
    expect(PAGE_LIMITS[PAGE_LIMITS.length - 1]).toBe(500);
    expect(clampLimit(9999)).toBe(500);
    expect(clampLimit(0)).toBe(1);
    expect(clampLimit(250)).toBe(250);
    expect(tableQuery(null, [])).toEqual({ sort: "", filter: "" });
    expect(
      tableQuery({ field: "value", direction: "desc" }, [
        { field: "big", op: "ge", value: JSON.stringify({ $int64: "4611686018427387911" }) },
        { field: "label", op: "in", value: '["a","b"]' },
      ]),
    ).toEqual({
      sort: "value:desc",
      filter: 'big:ge:{"$int64":"4611686018427387911"},label:in:["a","b"]',
    });
  });
});

describe("figure isolation", () => {
  const good = {
    data: [{ type: "bar", y: [1, 2], marker: { colorscale: "Viridis", color: [1, 2] } }],
    layout: { title: { text: "Lateness" }, template: { layout: { font: { family: "sans" } } } },
  };
  it("accepts a self-contained spec and keeps only data, layout and frames", () => {
    const verdict = validateFigure({ ...good, frames: [] });
    expect(verdict.ok).toBe(true);
    if (verdict.ok) expect(Object.keys(verdict.figure)).toEqual(["data", "layout", "frames"]);
  });
  it("rejects URL-bearing sources, src references, hrefs, template references, config and map traces", () => {
    const refused = (spec: unknown) => {
      const verdict = validateFigure(spec);
      expect(verdict.ok).toBe(false);
      return verdict.ok ? "" : verdict.reason;
    };
    expect(refused({ ...good, layout: { images: [{ source: "https://example.com/a.png" }] } })).toMatch(/layout\.images\[0\]\.source/);
    expect(refused({ ...good, layout: { images: [{ source: "data:image/png;base64,AAAA" }] } })).toMatch(/source/);
    expect(refused({ data: [{ type: "scatter", ysrc: "user:1:abc" }], layout: {} })).toMatch(/ysrc/);
    expect(refused({ data: [{ type: "scatter", customdatasrc: "x" }], layout: {} })).toMatch(/customdatasrc/);
    expect(refused({ ...good, layout: { annotations: [{ text: '<a href="https://x">x</a>' }] } })).toMatch(/href/);
    expect(refused({ ...good, layout: { title: { text: "javascript:alert(1)" } } })).toMatch(/script/);
    expect(refused({ ...good, layout: { template: "plotly_dark" } })).toMatch(/template/);
    expect(refused({ ...good, layout: { template: "https://example.com/t.json" } })).toMatch(/template/);
    expect(refused({ ...good, config: { plotlyServerURL: "https://plot.ly" } })).toMatch(/config/);
    expect(refused({ ...good, layout: { config: {} } })).toMatch(/config/);
    expect(refused({ data: [{ type: "scattermapbox" }], layout: {} })).toMatch(/scattermapbox/);
    expect(refused({ data: [{ type: "choropleth" }], layout: {} })).toMatch(/choropleth/);
    expect(refused({ ...good, layout: { geo: {} } })).toMatch(/geo/);
    expect(refused({ ...good, layout: JSON.parse('{"__proto__": {"polluted": true}}') })).toMatch(/__proto__/);
    expect(refused([])).toMatch(/object/);
    expect(refused({ data: {}, layout: {} })).toMatch(/data/);
    expect(refused({ data: [], layout: [] })).toMatch(/layout/);
  });
  it("labels renderer mismatches instead of rendering", () => {
    expect(rendererState({ name: "plotly.js", version: PACKAGED_PLOTLY_VERSION })).toEqual({ state: "ready" });
    expect(rendererState({ name: "plotly.js", version: "3.0.0" })).toMatchObject({ state: "mismatch" });
    expect(rendererState({ name: "plotly.js", version: "3.0.0" })).toMatchObject({
      label: expect.stringContaining(PACKAGED_PLOTLY_VERSION),
    });
    expect(rendererState({ name: "vega", version: PACKAGED_PLOTLY_VERSION })).toMatchObject({ state: "mismatch" });
    expect(rendererState(null)).toMatchObject({ state: "unknown" });
    expect(PACKAGED_PLOTLY_VERSION).toBe(
      JSON.parse(readFileSync(fileURLToPath(new URL("../node_modules/plotly.js-dist-min/package.json", import.meta.url)), "utf8")).version,
    );
  });
  it("binds frame messages to the nonce and the frame window", () => {
    const frame = {};
    const nonce = "n-1";
    expect(frameUrl(nonce)).toBe("plotly-frame.html#n-1");
    expect(acceptsFrameMessage({ source: frame, data: { v: 1, nonce, type: "ready" } }, nonce, frame)).toEqual({
      type: "ready",
      data: { v: 1, nonce, type: "ready" },
    });
    expect(acceptsFrameMessage({ source: {}, data: { v: 1, nonce, type: "ready" } }, nonce, frame)).toBeNull();
    expect(acceptsFrameMessage({ source: frame, data: { v: 1, nonce: "other", type: "ready" } }, nonce, frame)).toBeNull();
    expect(acceptsFrameMessage({ source: frame, data: { v: 2, nonce, type: "ready" } }, nonce, frame)).toBeNull();
    expect(acceptsFrameMessage({ source: frame, data: "ready" }, nonce, frame)).toBeNull();
    expect(acceptsFrameMessage({ source: frame, data: { v: 1, nonce, type: "ready" } }, nonce, null)).toBeNull();
  });
});

describe("analysis annotations", () => {
  it("never disables Add annotation without a reason", () => {
    expect(annotationDisabled("", "", "")).toEqual({ disabled: true, title: "Type an annotation first" });
    expect(annotationDisabled("", "", "   ")).toEqual({ disabled: true, title: "Type an annotation first" });
    expect(annotationDisabled("", "", "first look")).toEqual({});
    expect(annotationDisabled("Saving note…", "", "first look")).toMatchObject({ disabled: true, title: expect.stringContaining("Working") });
    expect(annotationDisabled("", "This project is open read-only", "x")).toEqual({ disabled: true, title: "This project is open read-only" });
  });
});
