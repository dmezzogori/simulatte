import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { ResultsPanel } from "../src/workspace/results-figure";
import type { ExperimentReport } from "../src/studio-client";
import type { ResultsOptions } from "../src/results-table";

const estimate = (mean: number, halfWidth: number | null, state = "ok", n = 4) => ({
  n,
  mean,
  sd: halfWidth === null ? null : 1,
  half_width: halfWidth,
  lower: halfWidth === null ? null : mean - halfWidth,
  upper: halfWidth === null ? null : mean + halfWidth,
  state,
  level: 0.95,
});
const options: ResultsOptions = { level: 0.95, bonferroni: false, baseline: "two servers" };
const report: ExperimentReport = {
  run_id: "a".repeat(64),
  name: "capacity-study",
  status: "completed",
  level: 0.95,
  effective_level: 0.95,
  pointwise: true,
  m: 4,
  configurations: [
    { index: 0, label: "two servers", parameters: { servers: 2 }, counts: { succeeded: 3, failed: 1 }, mixed_runtime: false },
    { index: 1, label: "three servers", parameters: { servers: 3 }, counts: { succeeded: 4 }, mixed_runtime: false },
  ],
  rows: [
    { configuration: 0, label: "two servers", kpi: "shopfloor/throughput", n_succeeded: 3, estimate: estimate(1.11333, 0.17448, "ok", 3) },
    { configuration: 1, label: "three servers", kpi: "shopfloor/throughput", n_succeeded: 4, estimate: estimate(1.52, 0.09) },
    { configuration: 0, label: "two servers", kpi: "shopfloor/tardiness", n_succeeded: 3, estimate: estimate(0, 0, "degenerate", 3) },
    { configuration: 1, label: "three servers", kpi: "shopfloor/tardiness", n_succeeded: 4, estimate: estimate(0, 0, "degenerate") },
  ],
  missing: [{ configuration: 0, index: 2, status: "failed", attempt: null }],
  missing_total: 1,
  replications: [],
  replications_total: 8,
  baseline: "two servers",
  paired_m: 2,
  paired_effective_level: 0.95,
  paired: [
    { configuration: 1, label: "three servers", kpi: "shopfloor/throughput", n: 3, estimate: estimate(0.41, 0.19, "ok", 3), missing: [2] },
    { configuration: 1, label: "three servers", kpi: "shopfloor/tardiness", n: 3, estimate: estimate(0, 0, "degenerate", 3), missing: [2] },
  ],
};
const html = (value: ExperimentReport, opts = options) => renderToStaticMarkup(<ResultsPanel sources={[{ report: value }]} options={opts} onOptions={() => {}} />);

describe("results figure", () => {
  it("draws one block per KPI with configurations as rows and the baseline marked", () => {
    const markup = html(report);
    expect(markup).toContain('data-kpi="shopfloor/throughput"');
    expect(markup).toContain(">Throughput<");
    expect(markup).toContain("<code>shopfloor/throughput</code>");
    expect(markup).toContain('class="fp-row is-baseline"');
    expect(markup).toContain(">Baseline<");
    expect(markup).toContain("1.11");
    expect(markup).toContain("[0.94, 1.29]");
    expect(markup).toContain('aria-label="4 replications: 3 succeeded, 1 failed"');
  });
  it("draws the difference against zero and fills it only when the interval excludes zero", () => {
    const markup = html(report);
    expect(markup).toContain("Difference from two servers, paired by replication");
    expect(markup).toContain('<span class="fp-subtrahend">− two servers</span>');
    expect(markup).toContain(
      "three servers minus two servers: throughput differs by +0.41, interval 0.22 to 0.60 excludes 0, 3 paired replications (replication 2 unpaired).",
    );
    expect(markup).toContain("+0.41");
    expect(markup).toContain('class="fp-diamond excludes"');
    expect(markup).toContain("Interval excludes 0");
    expect(markup).toContain("1 unpaired: replication 2");
    expect(markup).toContain('class="fp-zero"');
  });
  it("reads KPIs without any interval as one sentence instead of a figure", () => {
    const markup = html(report);
    expect(markup).not.toContain('data-kpi="shopfloor/tardiness"><header');
    expect(markup).toContain("Every replication of every configuration is 0; no interval can be formed.");
  });
  it("labels the interval convention exactly, Bonferroni included", () => {
    expect(html(report)).toContain("pointwise 95 % t intervals, no multiple-comparison correction");
    expect(html({ ...report, pointwise: false, effective_level: 0.975 }, { ...options, bonferroni: true })).toContain(
      "Bonferroni-adjusted (m = 4, effective level 0.975)",
    );
  });
  it("focuses a slice of a large grid and keeps the other configurations as context", () => {
    const configurations = [2, 3, 4].flatMap((servers) =>
      ["fifo", "edd", "spt", "slack"].map((rule) => ({ servers, rule })),
    ).map((parameters, index) => ({ index, label: `${parameters.servers}/${parameters.rule}`, parameters, counts: { succeeded: 4 }, mixed_runtime: false }));
    const { baseline: _b, paired: _p, ...plain } = report;
    const grid: ExperimentReport = {
      ...plain,
      configurations,
      rows: configurations.map((c) => ({ configuration: c.index, label: c.label, kpi: "shopfloor/throughput", n_succeeded: 4, estimate: estimate(1 + c.index / 10, 0.1) })),
      missing: [],
      missing_total: 0,
    };
    const markup = html(grid, { ...options, baseline: null });
    expect(markup).toContain("Showing 3 of 12 configurations");
    expect(markup).toContain("Other configurations");
    expect(markup.match(/class="fp-context-tick"/g)).toHaveLength(9);
  });
});
