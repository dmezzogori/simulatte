import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { validatePresentation, type PresentationManifest } from "@simulatte/playback";
import { parseRoute, routeCrumbs, routeHash, type WorkspaceRoute } from "../src/workspace/state";
import { PresentationPages, focusWhenShown, notificationReloads, originalRunLabel, originalOptions, PresentationOriginalAction, recordingLabel } from "../src/workspace/presentations";
import { EvidencePanel, EvidenceQualifiers, analysisEvidenceSource, contentHash, documentField, evidenceFile, evidenceFromOutput, excerptRows, labelsOf, qualifierText } from "../src/presentation/evidence-panel";
import { currentView, draftOf, manifestOf, pickerOutputs } from "../src/presentation/editor";
import { hideOutside, type Hideable } from "../src/presentation/hide-outside";
import { DetailsLoader } from "../src/workspace/runs";
import { ReplicationActions } from "../src/experiments";
import type { ReplicationEntry } from "../src/studio-client";
import type { RunDetails } from "../src/workspace/state";
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
      { page: "presentations", original: "old-study-attempt" },
    ] as WorkspaceRoute[])
      expect(parseRoute(routeHash(route))).toStrictEqual(route);
    expect(routeHash({ page: "presentations", id: "p", present: true })).toBe("#/presentations/p/present");
    expect(routeHash({ page: "presentations", attempt: "r" })).toBe("#/presentations/new/r");
    expect(routeHash({ page: "presentations", original: "old" })).toBe("#/presentations/new?original=old");
    expect(parseRoute("#/presentations/new?original=" )).toEqual({ page: "overview" });
    expect(routeCrumbs({ page: "presentations", original: "old" }, [])).toEqual([{ label: "Presentations", hash: "#/presentations" }, { label: "New presentation" }]);
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
describe("original run picker", () => {
  it("identifies experiment replications by experiment, configuration and replication", () => {
    const row = { id: "a".repeat(32), name: "", created: 1_700_000_000 };
    expect(originalRunLabel(row, { run_id: "r1", name: "Release study", configuration: "servers=3", index: 4 })).toBe(
      "Release study · servers=3 · replication 4 · Run aaaaaaaa",
    );
    expect(originalRunLabel({ ...row, name: "Baseline" }, undefined)).toMatch(/^Baseline · /);
  });
});
describe("presentation list paging", () => {
  it("follows next_cursor on request and keeps loaded older pages across refreshes", async () => {
    const all = Array.from({ length: 120 }, (_, i) => ({ id: "p" + i, name: "P" + i, revision: 1, created: 1000 - i, recording: { attempt_id: "a" }, exports: { active: 0, total: 0 } }));
    const asked: (string | null)[] = [];
    const pages = new PresentationPages(async (cursor) => {
      asked.push(cursor);
      const start = cursor === null ? 0 : Number(cursor);
      const end = Math.min(start + 50, all.length);
      return { items: all.slice(start, end), next_cursor: end < all.length ? String(end) : null };
    });
    await pages.refresh();
    expect(pages.items).toHaveLength(50);
    expect(pages.cursor).toBe("50");
    await pages.more();
    expect(pages.items.map((p) => p.id).slice(48, 52)).toEqual(["p48", "p49", "p50", "p51"]);
    asked.length = 0;
    await pages.refresh();
    expect(asked).toEqual([null, "50"]);
    expect(pages.items).toHaveLength(100);
    await pages.more();
    expect(pages.items).toHaveLength(120);
    expect(pages.cursor).toBeNull();
    asked.length = 0;
    await pages.more();
    expect(asked).toEqual([]);
  });
});
describe("presentation page notifications", () => {
  it("refreshes only the list on export state, so an open editor and its running export stay mounted", () => {
    expect(notificationReloads({ type: "export_state", body: { presentation_id: "p1", state: "running" } }, "p1")).toEqual({ list: true, detail: false });
    expect(notificationReloads({ type: "presentation_changed", body: { id: "p1", revision: 2 } }, "p1")).toEqual({ list: true, detail: true });
    expect(notificationReloads({ type: "presentation_changed", body: { id: "p2", revision: 2 } }, "p1")).toEqual({ list: true, detail: false });
    expect(notificationReloads({ type: "recording_state", body: {} }, undefined)).toEqual({ list: true, detail: false });
    expect(notificationReloads({ type: "analysis_state", body: {} }, "p1")).toEqual({ list: false, detail: false });
  });
});
describe("leaving presentation mode", () => {
  it("returns focus to the Present control once the editor shows it again", () => {
    const focused: string[] = [];
    const present = { focus: () => focused.push("present") };
    let shown = false;
    const doc = { getElementById: (id: string) => (shown && id === "pv-present-button" ? present : null) };
    expect(focusWhenShown(doc, "pv-present-button")).toBe(false);
    shown = true;
    expect(focusWhenShown(doc, "pv-present-button")).toBe(true);
    expect(focused).toEqual(["present"]);
  });
});
describe("presentation mode isolation", () => {
  it("hides everything around the mode, at every level up to the body, from focus and assistive technology", () => {
    type Fake = Hideable & { name: string; parentElement: Fake | null; children: Fake[]; attrs: Map<string, string> };
    const node = (name: string, children: Fake[] = [], ariaHidden: string | null = null): Fake => {
      const attrs = new Map<string, string>(ariaHidden === null ? [] : [["aria-hidden", ariaHidden]]);
      const n: Fake = { name, inert: false, parentElement: null, children, attrs, getAttribute: (k) => attrs.get(k) ?? null, setAttribute: (k, v) => void attrs.set(k, v), removeAttribute: (k) => void attrs.delete(k) };
      for (const c of children) c.parentElement = n;
      return n;
    };
    const mode = node("mode"), other = node("other"), sidebar = node("sidebar"), decor = node("decor", [], "true");
    const main = node("main", [mode, other]), app = node("app", [sidebar, main]), body = node("body", [app, decor]);
    const restore = hideOutside(mode, body);
    const hidden = (n: Fake) => n.inert && n.getAttribute("aria-hidden") === "true";
    expect([sidebar, other, decor].map(hidden)).toEqual([true, true, true]);
    expect([app, main, mode].map((n) => n.inert || n.attrs.size > 0)).toEqual([false, false, false]);
    restore();
    expect([sidebar, other, decor].map((n) => n.inert)).toEqual([false, false, false]);
    expect([sidebar.getAttribute("aria-hidden"), decor.getAttribute("aria-hidden")]).toEqual([null, "true"]);
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
    // The shared fixture pins the exact hash the Python store computes for the same descriptor.
    const shared = fixture("output-content-hash.json") as { input: OutputDescriptor; hash: string };
    expect(await contentHash(shared.input)).toBe(shared.hash);
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
describe("row excerpts far into a table", () => {
  it("pages past 10,000 rows to reach the offset, and stops at the end of the table", async () => {
    const total = 12_345;
    let calls = 0;
    const source: ArtifactSource = {
      async page(_id, cursor, limit): Promise<RowsPage> {
        calls++;
        const start = cursor ? Number(cursor) : 0;
        const count = Math.min(limit, 500, total - start);
        const page = Array.from({ length: count }, (_, i) => ({ index: start + i }));
        return { rows: page, next_cursor: start + count < total ? String(start + count) : null, row_count: total, indexed: false };
      },
      blob: () => Promise.reject(new Error("unused")),
      csv: () => Promise.reject(new Error("unused")),
    };
    expect(await excerptRows(source, "o", { offset: 12_000, limit: 3, columns: ["index"] })).toEqual([{ index: 12_000 }, { index: 12_001 }, { index: 12_002 }]);
    calls = 0;
    await expect(excerptRows(source, "o", { offset: 20_000, limit: 3, columns: [] })).rejects.toThrow("offset 20000 exceeds 12345 rows");
    expect(calls).toBe(1);
  });
});
describe("evidence files", () => {
  it("reads an item's bytes at the path its output descriptor lists, not one derived from the label name", async () => {
    // A sanitized directory: the label is "Mean lateness", the listed file is mean_lateness/number.json.
    const number = { ...output, id: "1".repeat(64), name: "Mean lateness", kind: "number" as const, artifacts: [{ path: "mean_lateness/number.json", length: 2, sha256: "a".repeat(64) }] };
    const drawing = { ...output, id: "2".repeat(64), name: "drawing", kind: "image" as const, artifacts: [{ path: "out_2/image.svg", length: 6, sha256: "b".repeat(64) }] };
    const files: Record<string, string> = { "mean_lateness/number.json": "{}", "out_2/image.svg": "<svg/>" };
    const asked: string[] = [];
    const replies: Record<string, unknown>[] = [];
    const source = analysisEvidenceSource(
      async (_type, body) => {
        replies.push(body);
        return { ok: true, output: { outputs: [number, drawing] } };
      },
      async (path) => {
        asked.push(path);
        const file = decodeURIComponent(path.split("/artifacts/")[1]!);
        return file in files ? new Response(files[file]!) : new Response("{}", { status: 404 });
      },
    )(origin.execution_id);
    const item = await evidenceFromOutput(number, origin, {});
    expect(await (await evidenceFile(source, item, "number.json")).text()).toBe("{}");
    const image = await evidenceFromOutput(drawing, origin, {});
    expect(await (await evidenceFile(source, image, "image.png", "image.svg")).text()).toBe("<svg/>");
    expect(asked.map((p) => decodeURIComponent(p.split("/artifacts/")[1]!))).toEqual(["mean_lateness/number.json", "out_2/image.svg"]);
    expect(replies).toHaveLength(1);
    await expect(evidenceFile(source, image, "figure.json")).rejects.toThrow("lists no figure.json");
  });
});
describe("evidence picker", () => {
  it("offers outputs only from the analysis currently chosen, never from the one still on screen", () => {
    const loaded = { origin, outputs: [output] };
    expect(pickerOutputs(loaded, origin.execution_id)).toBe(loaded);
    expect(pickerOutputs(loaded, "another-execution")).toBeNull();
    expect(pickerOutputs(null, origin.execution_id)).toBeNull();
  });
});
describe("evidence documents", () => {
  it("reads a field of a tagged analysis document such as number.json", () => {
    const number = { $record: { value: -1.5, metadata: { $record: { unit: "h" } } } };
    expect(documentField(number, "value")).toBe(-1.5);
    expect(documentField({ $record: { value: { $int64: "7" } } }, "value")).toBe(7n);
    expect(documentField({ $record: { summary: { $record: { count: 3 } } } }, "summary")).toEqual({ count: 3 });
    expect(documentField({ $record: {} }, "value")).toBeNull();
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
  it("takes only the framing from the live view, keeping the Rate field over the transport's playback rate", () => {
    const manifest = validatePresentation(fixture("valid-full.json"));
    const draft = { ...draftOf(manifest), rate: 2 };
    const scene = { fitBounds() {}, setVisibility() {}, worldBounds: () => [1, 2, 3, 4] as const };
    const playback = { rate: 1, seekTime: async () => {} };
    const patch = currentView(draft, { recording: manifest.recording, layout: manifest.layout }, scene, { setSeries() {} }, playback);
    expect(patch).toEqual({ camera: [1, 2, 3, 4] });
    const saved = manifestOf({ ...draft, ...patch }, manifest.recording, manifest.layout, patch.camera, manifest);
    expect(saved.rate).toBe(2);
  });
});

describe("presentation refresh interleaving", () => {
  it("discards an older-page continuation from before a refresh", async () => {
    let all = Array.from({ length: 150 }, (_, i) => ({ id: "p" + i, name: "P", revision: 1, created: 1, recording: { attempt_id: "a" }, exports: { active: 0, total: 0 } }));
    let block = false;
    let release!: () => void;
    const pages = new PresentationPages(async cursor => {
      const start = cursor === null ? 0 : all.findIndex(x => x.id === cursor) + 1;
      const items = all.slice(start, start + 50);
      const response = { items, next_cursor: start + 50 < all.length ? items.at(-1)!.id : null };
      if (block) { block = false; await new Promise<void>(resolve => { release = resolve; }); }
      return response;
    });
    await pages.refresh(); await pages.more();
    block = true;
    const pending = pages.more();
    all = [{ ...all[0]!, id: "new" }, ...all];
    await pages.refresh();
    release(); await pending;
    expect(pages.cursor).not.toBeNull();
    while (pages.cursor !== null) await pages.more();
    expect(new Set(pages.items.map(x => x.id))).toEqual(new Set(all.map(x => x.id)));
  });
});

describe("row excerpt cancellation", () => {
  it("passes the owner's signal to the page reader and stops after the first page", async () => {
    const controller = new AbortController();
    let calls = 0;
    let received: AbortSignal | undefined;
    const source: ArtifactSource = {
      async page(_id, _cursor, _limit, _query, signal) {
        calls++; received = signal; controller.abort();
        return { rows: [{ index: 0 }], next_cursor: "1", row_count: 12500, indexed: false };
      },
      blob: async () => new Blob(), csv: async () => new Blob(),
    };
    await expect(excerptRows(source, "o", { offset: 12000, limit: 1, columns: [] }, controller.signal)).rejects.toMatchObject({ name: "AbortError" });
    expect(calls).toBe(1);
    expect(received).toBe(controller.signal);
  });
});

describe("export row excerpt cancellation", () => {
  it("propagates export cancellation through the HTTP reader after the first page", async () => {
    const { evidenceSource } = await import("../src/artifacts/static-capture");
    const { prepareExport } = await import("@simulatte/export");
    const controller = new AbortController();
    const table: OutputDescriptor = { ...output, kind: "table", metadata: { ...output.metadata, schema: { name: "jobs", fields: [{ name: "index", dtype: "int64", unit: null, nullable: false }] }, row_count: 12500 } };
    const item = await evidenceFromOutput(table, origin, { rows: { offset: 12000, limit: 1, columns: ["index"] } });
    const manifest = { ...validatePresentation(fixture("valid-full.json")), evidence: [item] };
    let calls = 0;
    let received: AbortSignal | null | undefined;
    const source = evidenceSource(async () => ({ ok: true, execution: { state: "succeeded" }, output: { outputs: [table] } }), async (_path, init) => {
      calls++; received = init?.signal;
      controller.abort();
      return new Response(JSON.stringify({ rows: [{ index: 0 }], next_cursor: "1", row_count: 12500, indexed: false }));
    });
    await expect(prepareExport(manifest, "html", { panel: true, traceSize: 1 }, source, controller.signal)).rejects.toMatchObject({ name: "AbortError" });
    expect(calls).toBe(1);
    expect(received).toBe(controller.signal);
  });
});


describe("original selection outside recent history", () => {
  it("links directly to the selected original and resolves its complete label with one bounded attempt lookup", async () => {
    const attempt = "a".repeat(32);
    const calls: string[] = [];
    const details = new DetailsLoader(async (id) => {
      calls.push(id);
      return { name: "", text: "", important: false, request_id: "r", generation: 1, status: "succeeded", revision: 1, details: null, trace: null, layout: null, artifacts: [], id, request: { seed: 73 }, replication: { run_id: "old-study", name: "Study 21", configuration: "servers=4", index: 29 } } as RunDetails;
    });
    const visible = [{ id: attempt, name: "", created: 0 }];
    details.request([attempt]);
    await details.idle();
    expect(originalOptions(visible, details)).toEqual([{ id: attempt, label: "Study 21 · servers=4 · replication 29 · Run aaaaaaaa · seed 73" }]);
    expect(calls).toEqual([attempt]);
    const html = renderToStaticMarkup(<PresentationOriginalAction attempt={attempt} />);
    expect(html).toContain(`href="#/presentations/new?original=${attempt}"`);
    expect(html).toContain("Create presentation from this run");
    details.dispose();
  });
  it("indexes newly visible completed attempts even while their study's status remains running", async () => {
    const calls: string[] = [];
    const details = new DetailsLoader(async (id) => {
      calls.push(id);
      return { name: "", text: "", important: false, request_id: "r", generation: 1, status: "succeeded", revision: 1, details: null, trace: null, layout: null, artifacts: [], id, request: { seed: id === "first" ? 1 : 2 }, replication: { run_id: "study", name: "Still running", configuration: "baseline", index: id === "first" ? 0 : 1 } } as RunDetails;
    });
    details.request(["first"]); await details.idle();
    const visible = [{ id: "second", name: "", created: 0 }, { id: "first", name: "", created: 0 }];
    details.request(visible.map((r) => r.id)); await details.idle();
    expect(originalOptions(visible, details).map((r) => r.label)).toEqual([
      "Still running · baseline · replication 1 · Run second · seed 2",
      "Still running · baseline · replication 0 · Run first · seed 1",
    ]);
    expect(calls).toEqual(["first", "second"]);
    details.dispose();
  });
});


describe("replication presentation actions", () => {
  const attempt = "b".repeat(32);
  const item: ReplicationEntry = { configuration: 0, index: 29, status: "succeeded", attempt, trace: null };
  const href = (id: string) => routeHash({ page: "presentations", original: id });
  it("offers presentation creation for successful/reused numerical runs without a full recording", () => {
    for (const status of ["succeeded", "reused"]) {
      const html = renderToStaticMarkup(<ReplicationActions item={{ ...item, status }} onOpen={() => {}} presentationHref={href} />);
      expect(html).toContain(`href="#/presentations/new?original=${attempt}"`);
      expect(html).toContain("Create presentation from this run");
    }
  });
  it("keeps unavailable runs and the legacy experiment inspector free of project actions", () => {
    const legacy = renderToStaticMarkup(<ReplicationActions item={item} onOpen={() => {}} />);
    expect(legacy).not.toContain("Create presentation");
    for (const unavailable of [{ ...item, status: "failed" }, { ...item, attempt: null }]) {
      expect(renderToStaticMarkup(<ReplicationActions item={unavailable} onOpen={() => {}} presentationHref={href} />)).not.toContain("Create presentation");
    }
  });
});
