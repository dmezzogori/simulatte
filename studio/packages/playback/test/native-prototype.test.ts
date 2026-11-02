import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { expect, it } from "vitest";
import { openRecording, type Cursor, type State } from "@simulatte/trace";
import { Session } from "../src/index";
import { Projector } from "../../scene/src/project";

const root = fileURLToPath(new URL("../../../../prototypes/native_engine_dynamic/fixtures/", import.meta.url));
type Expected = {
  cursor_range: [Cursor, Cursor]; outcome: string; scalars: Record<string, number>;
  events: [number, number, string][];
  checks: {cursor: Cursor; state: State; kpis: Record<string, number | null>}[];
};

it.each(["native", "lifecycle", "direct", "semantics", "observed"])("replays and seeks the %s trace with production Python parity", async (fixture) => {
  const expected = JSON.parse(readFileSync(`${root}${fixture}.expected.json`, "utf8")) as Expected;
  const bytes = new Uint8Array(readFileSync(`${root}${fixture}.simtrace`));
  const recording = await openRecording(bytes.buffer);
  expect(recording.trace.cursorRange).toEqual(expected.cursor_range);
  expect(recording.trace.outcome).toBe(expected.outcome);
  expect(recording.scalars).toEqual(expected.scalars);
  const events = await recording.events({limit: 1000});
  expect(events.map(e => [e.t, e.seq, e.type])).toEqual(expected.events);
  const session = new Session(recording);
  session.setSeries(["shopfloor/system_wip", "shopfloor/floor_wip"]);
  const projector = new Projector(recording.trace.initialState ?? {});
  for (const check of [...expected.checks, ...[...expected.checks].reverse()]) {
    await recording.trace.prepare(check.cursor);
    expect(recording.trace.stateAt(check.cursor)).toEqual(check.state);
    const frame = await session.prepare({mode: "exact", time: check.cursor[0], cursor: check.cursor});
    expect(frame.state).toEqual(check.state);
    for (const [key, value] of Object.entries(check.kpis)) expect(frame.kpis[key]?.value).toBe(value);
    const scene = projector.project(frame);
    expect(scene.fallback).toBe(true);
    expect(scene.glyphs.filter(g => g.kind === "server")).toHaveLength(2);
    const users = check.state["server-0"]?.["users"];
    if (Array.isArray(users) && users.includes("job-0")) {
      expect(scene.glyphs.some(g => g.id === "job-0" && !g.unplaced)).toBe(true);
    }
    if (!check.state["job-0"]) expect(scene.glyphs.some(g => g.id === "job-0")).toBe(false);
  }
  session.dispose();
});
