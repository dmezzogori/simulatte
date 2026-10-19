import { describe, it, expect } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import {
  parseRoute,
  routeHash,
  WorkspaceDrafts,
  validateProjectState,
} from "../src/workspace/state";
import { Sidebar } from "../src/workspace/shell";

describe("project navigation and independent drafts", () => {
  it("round trips stable IDs and safely handles malformed routes", () => {
    for (const route of [
      { page: "overview" },
      { page: "model", id: "id a" },
      { page: "run", attempt: "123" },
      { page: "experiments" },
    ] as const)
      expect(parseRoute(routeHash(route))).toEqual(route);
    expect(parseRoute("#/model/%ZZ")).toEqual({ page: "overview" });
  });
  it("selection and resynchronization never submit and preserve dirty drafts", () => {
    const drafts = new WorkspaceDrafts();
    const revision = { number: 1, digest: "a".repeat(64) };
    drafts.observe("a", revision, { seed: "7" });
    drafts.edit("a", { seed: "42" });
    drafts.observe("b", { number: 2, digest: "b".repeat(64) }, { seed: "3" });
    drafts.observe("a", revision, { seed: "7" });
    expect(drafts.get("a")?.options.seed).toBe("42");
    drafts.observe("a", { number: 3, digest: "c".repeat(64) }, { seed: "8" });
    expect(drafts.get("a")?.conflict).toBe(true);
    expect(drafts.get("a")?.options.seed).toBe("42");
    drafts.reload("a");
    expect(drafts.get("a")?.options.seed).toBe("8");
  });
  it("has one accessible sidebar with no duplicate top-level tabs", () => {
    const html = renderToStaticMarkup(
      <Sidebar name="Plant" route={{ page: "overview" }} />,
    );
    expect(html.match(/aria-label="Project navigation"/g)).toHaveLength(1);
    for (const name of [
      "Overview",
      "Models",
      "Runs",
      "Experiments",
      "Analyses",
      "Presentations",
      "Settings",
    ])
      expect(html).toContain(`>${name}</a>`);
    expect(html).not.toContain('role="tab"');
  });
  it("rejects malformed ownership or models in project state", () => {
    expect(() => validateProjectState({ models: [], history: [] })).toThrow();
  });
});
