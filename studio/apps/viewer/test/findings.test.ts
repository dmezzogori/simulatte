import { describe, expect, it } from "vitest";
import {
  differenceEvidence,
  differenceNote,
  estimateNote,
  estimateText,
  factorsOf,
  formatFinding,
  kpiName,
  niceAxis,
  replicationMarks,
  supportedDecimals,
  tickLabels,
} from "../src/findings";
import { defaultBaseline, progressSentence } from "../src/workspace/study";
import { studiesOf } from "../src/workspace/shell";
import { studyExecutions } from "../src/workspace/analyses";
import { outputTitle, readableValue } from "../src/artifacts/format";

const estimate = (mean: number | null, halfWidth: number | null, state = "ok", n = 4) => ({
  n,
  mean,
  sd: halfWidth === null ? null : 1,
  half_width: halfWidth,
  lower: mean === null || halfWidth === null ? null : mean - halfWidth,
  upper: mean === null || halfWidth === null ? null : mean + halfWidth,
  state,
  level: 0.95,
});

describe("finding precision", () => {
  it("keeps two significant digits of the half-width", () => {
    expect(supportedDecimals(0.17448)).toBe(2);
    expect(supportedDecimals(10.7037)).toBe(0);
    expect(supportedDecimals(6.02603)).toBe(1);
    expect(supportedDecimals(0)).toBeNull();
    expect(supportedDecimals(null)).toBeNull();
  });
  it("prints an estimate with its interval at the supported precision", () => {
    expect(estimateText(estimate(1.11333, 0.17448))).toEqual({ value: "1.11", interval: "[0.94, 1.29]" });
    expect(estimateText(estimate(7.11494, 10.7037))).toEqual({ value: "7", interval: "[−4, 18]" });
    expect(estimateText(estimate(-32.3712, 7.58867))).toEqual({ value: "−32.4", interval: "[−40.0, −24.8]" });
    expect(estimateText(estimate(0.41, 0.19), { signed: true }).value).toBe("+0.41");
  });
  it("shows full digits only when asked", () => {
    expect(estimateText(estimate(7.11494, 10.7037), { full: true }).value).toBe("7.11494");
    expect(formatFinding(0.2747553319573443)).toBe("0.2748");
    expect(formatFinding(0.2747553319573443, { full: true })).toBe("0.2747553319573443");
  });
  it("names missing and non-finite values and never signs zero", () => {
    expect(formatFinding(null)).toBe("—");
    expect(formatFinding(NaN)).toBe("—");
    expect(formatFinding(Infinity)).toBe("+∞");
    expect(formatFinding(-Infinity)).toBe("−∞");
    expect(formatFinding(-0.0001, { decimals: 1, signed: true })).toBe("0.0");
  });
  it("prints no interval for degenerate and insufficient estimates, and says why", () => {
    expect(estimateText(estimate(0, 0, "degenerate", 3))).toEqual({ value: "0", interval: null });
    expect(estimateNote(estimate(0, 0, "degenerate", 3))).toBe("All 3 replications equal: no interval");
    expect(estimateText(estimate(4, null, "insufficient", 1)).interval).toBeNull();
    expect(estimateNote(estimate(4, null, "insufficient", 1))).toBe("One replication: no interval");
    expect(estimateNote(estimate(7.1, 10.7))).toBe("Interval wider than the estimate");
    expect(estimateNote(estimate(1.11, 0.17))).toBeNull();
  });
});

describe("differences", () => {
  it("encode only what the interval says about zero", () => {
    expect(differenceEvidence(estimate(0.41, 0.19))).toBe("excludes");
    expect(differenceEvidence(estimate(-0.18, 0.21))).toBe("includes");
    expect(differenceEvidence(estimate(0, 0, "degenerate"))).toBe("degenerate");
    expect(differenceEvidence(estimate(2, null, "insufficient", 1))).toBe("insufficient");
    expect(differenceNote(estimate(0.41, 0.19))).toBe("Interval excludes 0");
    expect(differenceNote(estimate(-0.18, 0.21))).toBe("Interval includes 0");
    expect(differenceNote(estimate(0, 0, "degenerate"))).toBe("Every pair is equal");
  });
});

describe("names", () => {
  it("reads KPI keys and output names as words and keeps the key", () => {
    expect(kpiName("shopfloor/jobs_in_system")).toEqual({ key: "shopfloor/jobs_in_system", title: "Jobs in system", scope: "shopfloor" });
    expect(kpiName("throughput")).toEqual({ key: "throughput", title: "Throughput", scope: null });
    expect(outputTitle("lateness_by_family")).toBe("Lateness by family");
  });
  it("derives studies from experiment run names, newest first", () => {
    const studies = studiesOf([
      { run_id: "a", name: "capacity-study", created: 10 },
      { run_id: "b", name: "fleet", created: 30 },
      { run_id: "c", name: "capacity-study", created: 20 },
    ]);
    expect(studies).toEqual([
      { name: "fleet", latest: "b", runs: ["b"] },
      { name: "capacity-study", latest: "c", runs: ["c", "a"] },
    ]);
  });
  it("lists the analyses whose inputs include a study's runs", () => {
    const items = [
      { id: "1", experiment_run_ids: ["a"] },
      { id: "2", experiment_run_ids: [] },
      { id: "3" },
    ];
    expect(studyExecutions(items, new Set(["a"])).map((i) => i.id)).toEqual(["1"]);
    expect(studyExecutions(items, null)).toHaveLength(3);
  });
  it("says a run's progress in one sentence", () => {
    expect(progressSentence({ succeeded: 7, failed: 1 })).toBe("7 of 8 replications succeeded, 1 failed");
    expect(progressSentence({ succeeded: 1 })).toBe("1 of 1 replication succeeded");
  });
});

describe("figure geometry", () => {
  it("widens the axis to nice ticks and includes zero for differences", () => {
    const axis = niceAxis([0.94, 1.29, 1.43, 1.61]);
    expect(axis.min).toBeLessThanOrEqual(0.94);
    expect(axis.max).toBeGreaterThanOrEqual(1.61);
    expect(axis.at(axis.min)).toBe(0);
    expect(axis.at(axis.max)).toBe(100);
    const diff = niceAxis([0.22, 0.6], { zero: true });
    expect(diff.min).toBeLessThanOrEqual(0);
    expect(diff.ticks).toContain(0);
    expect(tickLabels([0, 0.5, 1])).toEqual(["0.0", "0.5", "1.0"]);
    expect(niceAxis([5, 5]).max).toBeGreaterThan(5);
  });
  it("draws one mark per replication with failures last and a spoken summary", () => {
    const { marks, summary } = replicationMarks({ failed: 1, succeeded: 3 });
    expect(marks).toEqual(["done", "done", "done", "failed"]);
    expect(summary).toBe("4 replications: 3 succeeded, 1 failed");
    expect(replicationMarks({ succeeded: 50 }, 40)).toMatchObject({ more: 10 });
  });
  it("finds the factors of a grid", () => {
    const configurations = [
      { parameters: { servers: 2, rule: "fifo" } },
      { parameters: { servers: 3, rule: "fifo" } },
      { parameters: { servers: 2, rule: "edd" } },
    ];
    expect(factorsOf(configurations)).toEqual([
      { name: "servers", values: ["2", "3"] },
      { name: "rule", values: ["fifo", "edd"] },
    ]);
  });
  it("makes the first configuration the baseline until the reader chooses", () => {
    const report = { configurations: [{ label: "two servers" }, { label: "three servers" }] } as never;
    const options = { level: 0.95, bonferroni: false, baseline: null };
    expect(defaultBaseline(report, options, false)).toEqual({ ...options, baseline: "two servers" });
    expect(defaultBaseline(report, options, true)).toBeNull();
  });
  it("reads saved values at four significant digits and keeps int64 exact", () => {
    expect(readableValue(0.2747553319573443)).toBe("0.2748");
    expect(readableValue(0.2747553319573443, "float64", true)).toBe("0.2747553319573443");
    expect(readableValue(9223372036854775807n, "int64")).toBe("9223372036854775807");
    expect(readableValue(416, "int64")).toBe("416");
    expect(readableValue(NaN)).toBe("NaN");
  });
});
