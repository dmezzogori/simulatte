import { readdirSync } from "node:fs";
import { expect, it } from "vitest";
import { loadFrame } from "./fixtures";

const NAMES = [
  "corridor-wait",
  "corridor-idle",
  "advanced-charging",
  "two-fleets",
  "bare-layout",
  "production",
  "no-layout",
  "sprites",
];

it("has exactly the documented fixtures", () => {
  const files = readdirSync(new URL("../../../fixtures/map/", import.meta.url))
    .filter((file) => file.endsWith(".json"))
    .map((file) => file.slice(0, -".json".length));
  expect(files.sort()).toEqual([...NAMES].sort());
});

it.each(NAMES)("loads %s", (name) => {
  const frame = loadFrame(name);
  expect(Object.keys(frame.state).length).toBeGreaterThan(0);
  expect(frame.initialState).not.toBeNull();
  expect(frame.cursor).toHaveLength(2);
  expect(frame.mode).toBe("continuous");
  expect(frame.crossingsComplete).toBe(true);
  if (name === "no-layout") expect(frame.layout).toBeNull();
  else expect(frame.layout).not.toBeNull();
});

it("records the wait of AGV-1 in corridor-wait", () => {
  const agv = loadFrame("corridor-wait").state["AGV-1"] as { wait: { blockers: string[] } };
  expect(agv.wait.blockers).toEqual(["AGV-2"]);
});

it("gives bare-layout no wait key", () => {
  expect(loadFrame("bare-layout").state["AGV-1"]).not.toHaveProperty("wait");
});
