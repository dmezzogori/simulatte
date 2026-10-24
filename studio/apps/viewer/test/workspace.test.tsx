import { describe, it, expect } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import {
  parseRoute,
  routeHash,
  WorkspaceDrafts,
  validateProjectState,
} from "../src/workspace/state";
import { Sidebar } from "../src/workspace/shell";
import { CheckField } from "../src/workspace/forms";

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
      "All experiments",
      "Analyses",
      "Presentations",
      "Settings",
    ])
      expect(html).toContain(`<span>${name}</span>`);
    expect(html).not.toContain('role="tab"');
    expect(html).toContain("No experiment has run yet.");
    expect(html.match(/<a /g)!.length).toBe(8);
  });
  it("rejects malformed ownership or models in project state", () => {
    expect(() => validateProjectState({ models: [], history: [] })).toThrow();
  });
});

describe("form primitives", () => {
  it("names a checkbox by its label alone and describes it with the hint", () => {
    const html = renderToStaticMarkup(
      <CheckField label="Show every entity" hint="Untick to choose entities">
        <input type="checkbox" />
      </CheckField>,
    );
    const input = /<input[^>]*>/.exec(html)![0];
    const labelId = /aria-labelledby="([^"]+)"/.exec(input)![1];
    const hintId = /aria-describedby="([^"]+)"/.exec(input)![1];
    expect(html).toContain(`<span id="${labelId}">Show every entity</span>`);
    expect(html).toContain(`<small id="${hintId}">Untick to choose entities</small>`);
    const plain = renderToStaticMarkup(
      <CheckField label="Plain">
        <input type="checkbox" />
      </CheckField>,
    );
    expect(plain).not.toContain("aria-labelledby");
  });
});
