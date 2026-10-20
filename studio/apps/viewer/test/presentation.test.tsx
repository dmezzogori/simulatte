import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { validatePresentation, type PresentationManifest } from "@simulatte/playback";
import { parseRoute, routeCrumbs, routeHash, type WorkspaceRoute } from "../src/workspace/state";
import { recordingLabel } from "../src/workspace/presentations";
import { EvidencePanel, EvidenceQualifiers, canonicalJson, contentHash, evidenceFromOutput, excerptRows, labelsOf, qualifierText } from "../src/presentation/evidence-panel";
import { draftOf, manifestOf } from "../src/presentation/editor";
import type { ArtifactSource, OutputDescriptor, RowsPage } from "../src/artifacts/types";

const fixture = (name: string) => JSON.parse(readFileSync(new URL(`../../../../tests/fixtures/presentations/${name}`, import.meta.url), "utf8")) as unknown;
const output: OutputDescriptor = {
  id: "e".repeat(64),
  name: "Mean lateness",
  kind: "number",
  artifacts: [{ path: "total/number.json", length: 12, sha256: "a".repeat(64) }],
  metadata: { partial_snapshot: true, planned: 24, available: 8, inferential_convention: "descriptive", input_availability: {}, unit: "h", population: "All jobs" },
  renderer: null,
};
const origin = { execution_id: "6f1d2c3b-4a5e-4f60-8a71-9b8c7d6e5f40", analysis_name: "Weekly report", snapshot_hash: "c".repeat(64) };

describe("presentation routes", () => {
  it("round trips the list, editor, presentation mode and new-from-recording routes", () => {
    for (const route of [
      { page: "presentations" },
      { page: "presentations", id: "0f6a9e2c-7d3b-4c1e-9b5a-2d8f4e6c1a7b" },
      { page: "presentations", id: "0f6a9e2c-7d3b-4c1e-9b5a-2d8f4e6c1a7b", present: true },
      { page: "presentations", attempt: "a".repeat(32) },
    ] as WorkspaceRoute[])
      expect(parseRoute(routeHash(route))).toStrictEqual(route);
    expect(routeHash({ page: "presentations", id: "p", present: true })).toBe("#/presentations/p/present");
    expect(routeHash({ page: "presentations", attempt: "r" })).toBe("#/presentations/new/r");
    for (const bad of ["#/presentations/new", "#/presentations/p/edit", "#/presentations/new/x/y", "#/presentations//present"])
      expect(parseRoute(bad), bad).toStrictEqual({ page: "overview" });
    expect(routeCrumbs({ page: "presentations", id: "p", present: true }, [])).toEqual([{ label: "Presentations", hash: "#/presentations" }, { label: "Presenting" }]);
    expect(routeCrumbs({ page: "presentations", attempt: "r" }, [])).toEqual([{ label: "Presentations", hash: "#/presentations" }, { label: "New presentation" }]);
  });
});
describe("recording provenance labels", () => {
  it("never calls a new seed or an edited run a verified reproduction", () => {
    expect(recordingLabel({ mode: "replay", verified: true, status: "succeeded" })).toBe("verified replay");
    expect(recordingLabel({ mode: "replay", verified: false, status: "succeeded" })).toBe("replay, not verified");
    expect(recordingLabel({ mode: "new_seed", verified: null, status: "succeeded" })).toBe("new recording, not a verified reproduction");
    expect(recordingLabel({ mode: "edited", verified: null, status: "succeeded" })).toBe("edited run, distinct from the original");
    expect(recordingLabel({ mode: "replay", verified: null, status: "running" })).toBe("replay · running");
  });
});
describe("evidence selection", () => {
  it("copies the P3 labels immutably and binds the selection to the output's content hash", async () => {
    const labels = labelsOf(output, origin);
    expect(labels).toEqual({ name: "Mean lateness", unit: "h", population: "All jobs", sample: { planned: 24, available: 8, partial: true }, convention: "descriptive", source: origin });
    expect(Object.isFrozen(labels) && Object.isFrozen(labels.sample) && Object.isFrozen(labels.source)).toBe(true);
    const item = await evidenceFromOutput(output, origin, {});
    expect(item.content_hash).toMatch(/^[0-9a-f]{64}$/);
    expect(item.content_hash).toBe(await contentHash(output));
    expect(await contentHash({ ...output, name: "Renamed" })).not.toBe(item.content_hash);
    expect(canonicalJson({ b: 1, a: [true, null, "é"], c: { z: 2.5, y: 3 } })).toBe('{"a":[true,null,"\\u00e9"],"b":1,"c":{"y":3,"z":2.5}}');
    expect(() => canonicalJson({ a: Number.NaN })).toThrow(TypeError);
    expect(qualifierText(labels)).toEqual(["unit h", "population All jobs", "8 of 24 replications · partial snapshot", "descriptive"]);
    const html = renderToStaticMarkup(<EvidenceQualifiers labels={labels} />);
    expect(html).toContain("partial snapshot");
    expect(html).toContain("Weekly report · snapshot cccccccccccc");
  });
  it("renders the panel with every selected item, its qualifiers and the annotations without bytes", () => {
    const manifest = validatePresentation(fixture("valid-full.json"));
    const html = renderToStaticMarkup(<EvidencePanel manifest={manifest} source={null} />);
    expect(html).toContain('aria-label="Supporting evidence"');
    for (const name of ["Mean lateness", "Lateness by family", "Lateness distribution"]) expect(html).toContain(name);
    expect(html.match(/data-kind="/g)).toHaveLength(3);
    expect(html).toContain("population Expedited jobs");
    expect(html).toContain("24 of 24 replications");
    expect(html).not.toContain("24 of 24 replications · partial");
    expect(html).toContain("Queue builds at the bottleneck");
    expect(html).toContain('href="#/analyses/6f1d2c3b-4a5e-4f60-8a71-9b8c7d6e5f40"');
  });
  it("reads a bounded row excerpt across server pages and keeps only the selected columns", async () => {
    const calls: number[] = [];
    const rows = Array.from({ length: 120 }, (_, i) => ({ index: i, family: "f" + (i % 3), lateness: i / 2 }));
    const source: ArtifactSource = {
      async page(_id, cursor, limit): Promise<RowsPage> {
        const start = cursor ? Number(cursor) : 0;
        calls.push(limit);
        const page = rows.slice(start, start + Math.min(limit, 50));
        return { rows: page, next_cursor: start + page.length < rows.length ? String(start + page.length) : null, row_count: rows.length, indexed: false };
      },
      blob: () => Promise.reject(new Error("unused")),
      csv: () => Promise.reject(new Error("unused")),
    };
    const excerpt = await excerptRows(source, "o", { offset: 95, limit: 10, columns: ["family", "lateness"] });
    expect(excerpt).toHaveLength(10);
    expect(excerpt[0]).toEqual({ family: "f2", lateness: 47.5 });
    expect(Object.keys(excerpt[9]!)).toEqual(["family", "lateness"]);
    expect(Math.max(...calls)).toBeLessThanOrEqual(500);
  });
});
describe("editor drafts", () => {
  it("round trips a manifest through the draft and back, taking the camera from the stage when unsaved", () => {
    const manifest = validatePresentation(fixture("valid-full.json"));
    const draft = draftOf(manifest);
    expect(draft.time).toEqual({ start: "0", end: "0.75" });
    expect(draft.camera).toEqual([-2, -3, 12.5, 6]);
    const saved = manifestOf(draft, manifest.recording, manifest.layout, draft.camera!, manifest);
    expect(saved).toEqual(manifest);
    const fresh = draftOf(null);
    expect(fresh.camera).toBeNull();
    const created = manifestOf({ ...fresh, name: "First look", time: { start: "0", end: "1" } }, manifest.recording, manifest.layout, [0, 0, 10, 5], null);
    expect(created.id).toBeUndefined();
    expect(created.camera.bounds).toEqual([0, 0, 10, 5]);
    expect(() => manifestOf({ ...fresh, time: { start: "1", end: "1" } }, manifest.recording, manifest.layout, [0, 0, 1, 1], null)).toThrow(/name|time/);
    const stale: PresentationManifest = { ...manifest, revision: 7 };
    expect(manifestOf(draft, manifest.recording, manifest.layout, draft.camera!, stale).revision).toBe(7);
  });
});
