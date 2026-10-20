import { describe, it, expect } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import {
  DetailsLoader,
  HistoryPaging,
  PAGE_SIZE,
  RunsTable,
  filterRuns,
  groupRuns,
  summarizeProvenance,
  type HistoryRow,
} from "../src/workspace/runs";
import { ComparisonReport, parseSelection } from "../src/workspace/comparison";
import { experimentActions, staleModels } from "../src/workspace/experiments";
import { runBody, plansOf } from "../src/workspace/models";
import type { RunDetails } from "../src/workspace/state";
import type { ExperimentReport } from "../src/studio-client";

const row = (i: number, extra: Partial<HistoryRow> = {}): HistoryRow => ({
  id: "a" + String(i).padStart(5, "0"),
  model_id: "model-a",
  name: "",
  status: "succeeded",
  created: 1_000_000 - i,
  has_result: true,
  important: false,
  text: "",
  details: null,
  slot: "interactive",
  ...extra,
});
const models = [{ id: "model-a", name: "Plant" }];

describe("runs page", () => {
  it("boundedHistoryAndTablePages", async () => {
    const TOTAL = 10_000;
    const all = Array.from({ length: TOTAL }, (_, i) => row(i));
    const fetched: (string | null)[] = [];
    const paging = new HistoryPaging(async (after) => {
      fetched.push(after);
      const start = after ? Number(after) : 0;
      return {
        items: all.slice(start, start + PAGE_SIZE),
        next_cursor: start + PAGE_SIZE < TOTAL ? String(start + PAGE_SIZE) : null,
      };
    });
    await paging.loadFirst();
    await paging.loadMore();
    await paging.loadMore();
    expect(fetched).toEqual([null, String(PAGE_SIZE), String(2 * PAGE_SIZE)]);
    expect(paging.pages).toBe(3);
    expect(paging.hasMore).toBe(true);
    const rows = paging.rows();
    expect(rows).toHaveLength(3 * PAGE_SIZE);
    expect(rows.map((r) => r.id)).toEqual(all.slice(0, 3 * PAGE_SIZE).map((r) => r.id));
    // Rendering is bounded by the loaded rows, not by the history size.
    const html = renderToStaticMarkup(
      <RunsTable rows={rows} models={models} details={new Map()} replications={new Map()} editing={null} onEdit={() => {}} />,
    );
    expect(html.match(/<tr/g)).toHaveLength(3 * PAGE_SIZE + 1);
    expect(html).not.toContain("a09999");
    // Provenance prefetch is bounded by the rendered rows and the concurrency cap.
    let inFlight = 0,
      peak = 0;
    const calls: string[] = [];
    const loader = new DetailsLoader(async (id) => {
      calls.push(id);
      inFlight++;
      peak = Math.max(peak, inFlight);
      await new Promise((r) => setTimeout(r, 1));
      inFlight--;
      return { id, request: { seed: 1, level: "kpi" } } as unknown as RunDetails;
    }, 8);
    loader.request(rows.map((r) => r.id));
    loader.request(rows.map((r) => r.id));
    await loader.idle();
    expect(calls).toHaveLength(3 * PAGE_SIZE);
    expect(peak).toBeLessThanOrEqual(8);
    expect(loader.get(rows[0]!.id)).toMatchObject({ id: rows[0]!.id });
  });
  it("prepends newer runs from the state without reordering loaded pages", async () => {
    const all = Array.from({ length: 5 }, (_, i) => row(i + 10));
    const paging = new HistoryPaging(async () => ({ items: all, next_cursor: "c" }));
    await paging.loadFirst();
    const newer = row(1, { status: "running", has_result: false });
    paging.merge([newer, { ...all[0]!, status: "failed" }, all[1]!]);
    const rows = paging.rows();
    expect(rows.map((r) => r.id)).toEqual([newer.id, ...all.map((r) => r.id)]);
    expect(rows[1]!.status).toBe("failed");
    paging.merge([newer]);
    expect(paging.rows()[0]).toBe(paging.rows()[0]);
    paging.update(all[2]!.id, { name: "Renamed", important: true });
    expect(paging.rows().find((r) => r.id === all[2]!.id)).toMatchObject({ name: "Renamed", important: true });
    expect(paging.cursor).toBe("c");
  });
  it("filters loaded rows by text and groups replications and legacy history", () => {
    const rows = [
      row(1, { name: "Keep this study", important: true }),
      row(2, { status: "failed" }),
      row(3, { slot: "local-1", worker: null }),
      row(4, { model_id: null, slot: "interactive" }),
    ];
    expect(filterRuns(rows, "keep", models).map((r) => r.id)).toEqual([rows[0]!.id]);
    expect(filterRuns(rows, "FAILED", models).map((r) => r.id)).toEqual([rows[1]!.id]);
    expect(filterRuns(rows, "plant", models)).toHaveLength(3);
    expect(filterRuns(rows, "a00003", models).map((r) => r.id)).toEqual([rows[2]!.id]);
    expect(filterRuns(rows, "", models)).toBe(rows);
    const groups = groupRuns(rows);
    expect(groups.interactive.map((r) => r.id)).toEqual([rows[0]!.id, rows[1]!.id]);
    expect(groups.replications.map((r) => r.id)).toEqual([rows[2]!.id]);
    expect(groups.unassigned.map((r) => r.id)).toEqual([rows[3]!.id]);
  });
  it("summarizes provenance in words and keeps raw identifiers for diagnostics", () => {
    const run = {
      id: "f".repeat(32),
      request_id: "e".repeat(64),
      generation: 3,
      status: "succeeded",
      revision: 7,
      request: {
        model: "plant.py:build",
        seed: 42,
        level: "full",
        stopping: { type: "horizon", horizon: 100, warmup: 10 },
        parameters: { servers: 3, rule: "FIFO" },
        runtime: { python: "3.14.0", lock_hash: "a".repeat(64) },
        source_hash: "b".repeat(64),
        measurements: { schema: 1, plan: "numbers", selection: {}, definition_hash: "c".repeat(64) },
      },
      details: null,
      trace: null,
      layout: null,
      artifacts: [],
      name: "",
      text: "",
      important: false,
    } as unknown as RunDetails;
    const summary = summarizeProvenance(run);
    expect(summary.seed).toBe("42");
    expect(summary.level).toBe("Full recording");
    expect(summary.stopping).toBe("horizon · horizon 100 · warmup 10");
    expect(summary.parameters).toEqual([["servers", "3"], ["rule", "FIFO"]]);
    expect(summary.measurements).toBe("numbers");
    expect(summarizeProvenance({ ...run, request: { ...run.request, measurements: undefined } }).measurements).toBe("KPIs only");
    expect(summary.runtime).toContain("python 3.14.0");
    expect(summary.runtime).not.toContain("a".repeat(64));
    expect(summary.diagnostic.join("\n")).toContain("a".repeat(64));
    expect(summary.diagnostic.join("\n")).toContain("e".repeat(64));
  });
});

describe("model page run request", () => {
  it("sends the selected measurement plan, or null for KPIs only, on run.start and run.stop_and_run", () => {
    expect(runBody("model-a", "", undefined, "auto-1")).toEqual({ model_id: "model-a", auto_session: "auto-1", measurements: null });
    expect(runBody("model-a", "numbers", undefined, null)).toEqual({ model_id: "model-a", auto_session: null, measurements: { plan: "numbers" } });
    const owner = { request_id: "r", attempt_id: "a", generation: 1 };
    expect(runBody("model-a", "numbers", owner, null)).toEqual({ model_id: "model-a", owner, measurements: { plan: "numbers" } });
    expect(plansOf({ name: "m", parameters: { version: 1, fields: [] }, stopping: {} })).toEqual([]);
    expect(
      plansOf({
        name: "m",
        parameters: { version: 1, fields: [] },
        stopping: {},
        measurement_plans: [{ name: "numbers", datasets: [{ name: "numbers", fields: [{ name: "x", type: "int64" }] }] }],
      }).map((p) => [p.name, p.summary]),
    ).toEqual([["numbers", "numbers (x)"]]);
  });
});

describe("experiments page", () => {
  it("exposes the exact commands behind pause, cancel, resume and restart", () => {
    const run = { run_id: "r".repeat(64), experiment_id: "x", name: "Sweep", status: "running", created: 0, counts: {} };
    expect(experimentActions(run).map((a) => [a.label, a.command, a.body])).toEqual([
      ["Cancel", "experiment.cancel", { run_id: run.run_id }],
    ]);
    expect(experimentActions({ ...run, status: "paused" }).map((a) => [a.label, a.command])).toEqual([
      ["Resume", "experiment.resume"],
      ["Cancel", "experiment.cancel"],
      ["Delete", "experiment.delete"],
    ]);
    expect(experimentActions({ ...run, status: "completed" }).map((a) => [a.label, a.command])).toEqual([
      ["Start again", "experiment.start"],
      ["Delete", "experiment.delete"],
    ]);
    expect(experimentActions({ ...run, status: "completed" })[0]!.body).toEqual({ run_id: run.run_id });
    expect(experimentActions({ ...run, status: "completed" })[1]!.confirm).toMatch(/cannot be undone/);
  });
  it("marks definitions stale when a model's source revision moves past the baseline", () => {
    const baseline = new Map([["model-a", 3], ["model-b", 1]]);
    const current = [
      { id: "model-a", revision: { number: 4, digest: "a".repeat(64) } },
      { id: "model-b", revision: { number: 1, digest: "b".repeat(64) } },
      { id: "model-c", revision: null },
    ];
    expect([...staleModels(baseline, current)]).toEqual(["model-a"]);
  });
});

describe("comparison page", () => {
  const estimate = (n: number, mean: number | null, half_width: number | null, state: string) =>
    ({ n, mean, sd: half_width === null ? null : 1, half_width, lower: null, upper: null, state, level: 0.95 });
  const report: ExperimentReport = {
    run_id: "a".repeat(64), name: "Policy study", status: "completed", level: 0.95, effective_level: 0.95, pointwise: true, m: 2,
    configurations: [
      { index: 0, label: "conwip/base", parameters: { policy: "conwip" }, counts: { succeeded: 2 }, mixed_runtime: false },
      { index: 1, label: "immediate/base", parameters: { policy: "immediate" }, counts: { succeeded: 1, failed: 1 }, mixed_runtime: true },
    ],
    rows: [
      { configuration: 0, label: "conwip/base", kpi: "lateness", n_succeeded: 2, estimate: estimate(2, -8.4, 1.1, "ok") },
      { configuration: 1, label: "immediate/base", kpi: "lateness", n_succeeded: 1, estimate: estimate(1, -3.8, null, "insufficient") },
    ],
    missing: [{ configuration: 1, index: 1, status: "failed", attempt: "b".repeat(32) }], missing_total: 1,
    replications: [], replications_total: 4,
  };
  it("comparisonRetainsMissingPairs", () => {
    const html = renderToStaticMarkup(
      <ComparisonReport report={report} options={{ level: 0.95, bonferroni: false, baseline: null }} onOptions={() => {}} />,
    );
    expect(html).toContain("Policy study");
    expect(html).toMatch(/succeeded 2/);
    expect(html).toMatch(/succeeded 1/);
    expect(html).toMatch(/failed 1/);
    expect(html).toContain("Missing pairs (1)");
    expect(html).toMatch(/immediate\/base[^<]*<\/td><td>1<\/td><td>failed/);
    expect(html).toContain("pointwise 95 % t intervals, no multiple-comparison correction");
    expect(html).toContain("mixed runtime");
    expect(html).toContain("state-insufficient");
    const start = html.indexOf('immediate/base<span class="badge mixed"');
    expect(start).toBeGreaterThan(0);
    const insufficient = html.slice(start, html.indexOf("state-insufficient", start));
    expect(insufficient).not.toContain("±");
    expect(insufficient).toContain(">—<");
    expect(html.slice(html.indexOf("conwip/base</td><td>lateness"), start)).toContain("± 1.1");
    expect(html).toMatch(/within each experiment run/i);
    expect(html).not.toContain("b".repeat(32));
    const adjusted = renderToStaticMarkup(
      <ComparisonReport report={{ ...report, pointwise: false, effective_level: 0.975 }} options={{ level: 0.95, bonferroni: true, baseline: null }} onOptions={() => {}} />,
    );
    expect(adjusted).toContain("Bonferroni-adjusted (m = 2, effective level 0.975)");
  });
  it("parses run and configuration selections from the route", () => {
    const a = "a".repeat(64), b = "b".repeat(64);
    const parsed = parseSelection([a, b + ":1", b + ":0", "bad", a, "c".repeat(64) + ":x"]);
    expect([...parsed.runs.keys()]).toEqual([a, b]);
    expect(parsed.runs.get(a)).toBeNull();
    expect([...parsed.runs.get(b)!]).toEqual([1, 0]);
    expect(parsed.invalid).toEqual(["bad", "c".repeat(64) + ":x"]);
  });
});
