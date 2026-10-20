import { describe, it, expect } from "vitest";
import {
  EXPERIMENT_PAGE,
  HISTORY_PAGE,
  appendExperiments,
  appendRuns,
  initialExperimentPaging,
  initialRunPaging,
  mergeExperiments,
} from "../src/workspace/analyses";
import type { ExperimentRun } from "../src/studio-client";
import type { HistoryItem } from "../src/workspace/state";

const run = (n: number, generation = n): HistoryItem & { generation: number } => ({
  id: "attempt-" + n,
  model_id: null,
  name: "",
  status: "succeeded",
  created: n,
  has_result: true,
  important: false,
  text: "",
  details: null,
  generation,
});
const page = (first: number, count: number) => Array.from({ length: count }, (_, i) => run(first - i));

describe("completed runs in the new-analysis form", () => {
  it("starts after the state's newest page and never repeats a run", () => {
    const history = page(120, HISTORY_PAGE); // generations 120…71, the newest 50 the state carries
    const paging = initialRunPaging(history);
    expect(paging.items.map((r) => r.id)).toEqual(history.map((r) => r.id));
    expect(paging.hasMore).toBe(true);
    expect(paging.cursor).toBe("120:71");
    // The server answers the page after generation 71, possibly overlapping with runs seen meanwhile.
    const older = appendRuns(paging, { items: [run(71), ...page(70, 49)], next_cursor: "120:22" });
    expect(older.items.length).toBe(HISTORY_PAGE + 49);
    expect(new Set(older.items.map((r) => r.id)).size).toBe(older.items.length);
    expect(older.items[HISTORY_PAGE]?.id).toBe("attempt-70");
    expect(older.cursor).toBe("120:22");
    expect(older.hasMore).toBe(true);
    const last = appendRuns(older, { items: page(21, 21), next_cursor: null });
    expect(last.items.length).toBe(HISTORY_PAGE + 49 + 21);
    expect(last.hasMore).toBe(false);
  });
  it("offers no paging for a short history and falls back to the first server page without generations", () => {
    const short = initialRunPaging(page(3, 3));
    expect(short.hasMore).toBe(false);
    expect(short.cursor).toBeNull();
    const plain = page(60, HISTORY_PAGE).map(({ generation: _generation, ...item }) => item as HistoryItem);
    const paging = initialRunPaging(plain);
    expect(paging.hasMore).toBe(true);
    expect(paging.cursor).toBeNull(); // the first server page is fetched and deduplicated instead
    const merged = appendRuns(paging, { items: [...page(60, 50)], next_cursor: "60:11" });
    expect(merged.items.length).toBe(HISTORY_PAGE);
    expect(merged.cursor).toBe("60:11");
  });
});

const experiment = (n: number, status = "completed"): ExperimentRun => ({
  run_id: String(n).padStart(64, "0"),
  experiment_id: "e".repeat(64),
  name: "run " + n,
  status,
  created: n,
  counts: { succeeded: n },
});
const newest = (first: number, count: number) => Array.from({ length: count }, (_, i) => experiment(first - i));

describe("experiment runs in the new-analysis form", () => {
  it("pages older runs after the state's newest page without repeating any", () => {
    const paging = initialExperimentPaging(newest(45, EXPERIMENT_PAGE));
    expect(paging.items.length).toBe(EXPERIMENT_PAGE);
    expect(paging.hasMore).toBe(true);
    expect(paging.cursor).toBeNull(); // the first server page is fetched and deduplicated
    const second = appendExperiments(paging, { items: newest(45, EXPERIMENT_PAGE), next_cursor: "c1" });
    expect(second.items.length).toBe(EXPERIMENT_PAGE);
    expect(second.cursor).toBe("c1");
    const third = appendExperiments(second, { items: newest(25, EXPERIMENT_PAGE), next_cursor: "c2" });
    expect(third.items.map((r) => r.name).slice(EXPERIMENT_PAGE, EXPERIMENT_PAGE + 2)).toEqual(["run 25", "run 24"]);
    const last = appendExperiments(third, { items: newest(5, 5), next_cursor: null });
    expect(last.items.length).toBe(45);
    expect(new Set(last.items.map((r) => r.run_id)).size).toBe(45);
    expect(last.hasMore).toBe(false);
    expect(last.items[last.items.length - 1]?.name).toBe("run 1");
  });
  it("takes fresh status and counts from the state without dropping loaded older runs", () => {
    const paging = appendExperiments(initialExperimentPaging(newest(30, EXPERIMENT_PAGE)), { items: newest(10, 10), next_cursor: null });
    const merged = mergeExperiments(paging, [experiment(31, "running"), ...newest(30, 19), { ...experiment(12), status: "cancelled" }]);
    expect(merged.items[0]?.name).toBe("run 31"); // a newer run goes on top
    expect(merged.items.find((r) => r.name === "run 12")?.status).toBe("cancelled");
    expect(merged.items.length).toBe(31);
    expect(merged.items[merged.items.length - 1]?.name).toBe("run 1");
    expect(merged.hasMore).toBe(false);
    expect(initialExperimentPaging(newest(3, 3)).hasMore).toBe(false);
  });
});
