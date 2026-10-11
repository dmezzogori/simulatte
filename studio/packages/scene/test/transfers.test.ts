import { expect, it } from "vitest";
import type { PreparedFrame } from "@simulatte/playback";
import type { State, TraceEvent } from "@simulatte/trace";
import { Projector, TransferTrails } from "../src/project";

const equipmentState: State = {
  a: { $kind: "psp" },
  b: { $kind: "server" },
  c: { $kind: "server" },
};
const frame = (
  seq: number,
  location: string | null,
  crossedEvents: readonly TraceEvent[] = [],
  crossingsComplete = true,
): PreparedFrame => ({
  time: 0,
  cursor: [0, seq],
  mode: "continuous",
  state: {
    ...equipmentState,
    ...(location === null ? {} : { job: { $kind: "job", location } }),
  },
  layout: null,
  catalog: {},
  kpis: {},
  diagnostics: [],
  crossedEvents,
  crossingsComplete,
});
const event = (seq: number, ...deltas: TraceEvent["deltas"]): TraceEvent => ({
  seq,
  ordinal: seq,
  t: 0,
  type: "test",
  payload: {},
  deltas,
});
const location = (seq: number, value: string) =>
  event(seq, ["set", "job", "location", value]);
const setup = () => {
  const projector = new Projector(equipmentState);
  const trails = new TransferTrails();
  return {
    trails,
    update: (f: PreparedFrame, clock = 0) =>
      trails.update(f, projector.project(f), clock, 6),
  };
};

it("records every crossed physical destination through transit and retirement", () => {
  const { update } = setup();
  update(frame(0, "psp:a"));
  const end = frame(8, null, [
    location(1, "transit"),
    location(3, "queue:b"),
    location(4, "server:b"),
    location(5, "transit"),
    location(7, "queue:c"),
    event(8, ["retire", "job"]),
  ]);
  expect(update(end, 1)).toEqual([
    { from: { x: 0, y: -0 }, to: { x: 5, y: -0 }, born: 1, lifetime: 6 },
    { from: { x: 5, y: -0 }, to: { x: 10, y: -0 }, born: 1, lifetime: 6 },
  ]);
  expect(update(end, 2)).toHaveLength(2);
});

it("seeds newly created jobs without inventing a source and honors retirement", () => {
  const { update } = setup();
  update(frame(0, null));
  expect(
    update(
      frame(5, null, [
        event(1, ["create", "job", "job", { location: "psp:a" }]),
        location(2, "queue:b"),
        event(3, ["retire", "job"]),
        event(4, ["create", "job", "job", { location: "queue:c" }]),
        event(5, ["retire", "job"]),
      ]),
    ),
  ).toHaveLength(1);
});

it("keeps trails for exactly six frames despite forward sequence gaps", () => {
  const { update } = setup();
  update(frame(0, "psp:a"));
  expect(update(frame(4, "queue:b", [location(4, "queue:b")]), 1)).toHaveLength(
    1,
  );
  for (let clock = 2; clock <= 6; clock++)
    expect(update(frame(clock * 10, "queue:b"), clock)).toHaveLength(1);
  expect(update(frame(70, "queue:b"), 7)).toHaveLength(0);
});

it("discards uncertain sources but preserves known trails and observes a bounded suffix", () => {
  const { update, trails } = setup();
  update(frame(0, "psp:a"));
  expect(update(frame(1, "queue:b", [location(1, "queue:b")]), 1)).toHaveLength(
    1,
  );
  const partial = frame(
    10,
    "queue:b",
    [location(8, "queue:c"), location(10, "queue:b")],
    false,
  );
  const observed = update(partial, 2);
  expect(observed).toHaveLength(2);
  expect(observed[1]).toMatchObject({
    from: { x: 10, y: -0 },
    to: { x: 5, y: -0 },
  });
  trails.reset();
  expect(update(frame(12, "psp:a", [location(12, "psp:a")]), 3)).toHaveLength(
    0,
  );
  expect(update(frame(0, "queue:c"), 4)).toHaveLength(0);
});

it("gives the first twenty queued jobs distinct positions and retains the full count", () => {
  const ids = Array.from({ length: 21 }, (_, i) => `job-${i}`);
  const state: State = {
    b: { $kind: "server", queue: ids },
    ...Object.fromEntries(
      ids.map((id) => [id, { $kind: "job", location: "queue:b" }]),
    ),
  };
  const projection = new Projector(state).project({ ...frame(0, null), state });
  const jobs = projection.glyphs.filter((glyph) => glyph.kind === "job");
  expect(jobs).toHaveLength(20);
  expect(new Set(jobs.map((glyph) => `${glyph.x},${glyph.y}`)).size).toBe(20);
  expect(projection.glyphs.find((glyph) => glyph.id === "b")?.queue).toBe(21);
});

it("retains a suffix's physical source when an incomplete transit frame is rendered again", () => {
  const { update } = setup();
  update(frame(0, "psp:a"));
  const transit = frame(
    10,
    "transit",
    [location(9, "queue:c"), location(10, "transit")],
    false,
  );
  expect(update(transit, 1)).toHaveLength(0);
  expect(update(transit, 2)).toHaveLength(0);
  expect(
    update(frame(11, "queue:b", [location(11, "queue:b")]), 3),
  ).toHaveLength(1);
});

it("bounds decorations even when one frame crosses hundreds of transfers", () => {
  const { update } = setup();
  update(frame(0, "psp:a"));
  const events = Array.from({ length: 300 }, (_, i) =>
    location(i + 1, i % 2 ? "psp:a" : "queue:b"),
  );
  expect(update(frame(300, "psp:a", events), 1)).toHaveLength(256);
  expect(
    update(
      { ...frame(301, "queue:b", [location(301, "queue:b")]), time: Infinity },
      2,
    ),
  ).toHaveLength(0);
});
