import { expect, it } from "vitest";
import { motionProgress } from "../src/motion";
import { equipment, Projector } from "../src/project";
import type { PreparedFrame } from "@simulatte/playback";
it("evaluates triangular/trapezoidal profiles and portable keyframes", () => {
  const triangle = {
    t_start: 0,
    t_end: 2,
    description: {
      curve: "trapezoidal",
      v_max: 10,
      accel: 1,
      decel: 1,
      distance: 1,
    },
  };
  expect(motionProgress(triangle, 0.5).fraction).toBeCloseTo(0.125);
  expect(motionProgress(triangle, 1.5).fraction).toBeCloseTo(0.875);
  const keyframes = {
    t_start: 1000,
    t_end: 1004,
    description: {
      curve: "keyframes",
      keyframes: [
        [0, 0],
        [1, 0.5],
        [4, 1],
      ],
      accuracy: 0.01,
    },
  };
  expect(motionProgress(keyframes, 1002).fraction).toBeCloseTo(2 / 3);
  expect(motionProgress(keyframes, 1004).approximate).toBe(false);
  expect(motionProgress({ ...keyframes, t_end: 1005 }, 1002).approximate).toBe(
    true,
  );
  expect(motionProgress({ t_start: 0, t_end: Infinity }, 10)).toMatchObject({
    fraction: 0,
    diagnostic: "Stalled",
  });
});
it("keeps transit unplaced until the recorded destination cursor", () => {
  expect(equipment("queue:server:with:colons")).toBe("server:with:colons");
  expect(equipment("transit")).toBeNull();
  const state = {
    machine: { $kind: "server" },
    job: { $kind: "job", location: "transit" },
  };
  const projector = new Projector(state);
  const frame = {
    state,
    time: 0,
    cursor: [0, 0],
    mode: "exact",
    layout: null,
    catalog: {},
    kpis: {},
    diagnostics: [],
  } as PreparedFrame;
  expect(
    projector.project(frame).glyphs.find((g) => g.id === "job")?.unplaced,
  ).toBe(true);
  expect(
    projector
      .project({
        ...frame,
        state: { ...state, job: { $kind: "job", location: "queue:machine" } },
      })
      .glyphs.find((g) => g.id === "job")?.unplaced,
  ).toBe(false);
});

it("decorates adjacent fallback transitions without erasing live trails at forward gaps", async () => {
  const { TransferTrails } = await import("../src/project");
  const projector = new Projector({
      a: { $kind: "psp" },
      b: { $kind: "server" },
    }),
    trails = new TransferTrails();
  const frame = (seq: number, location: string): PreparedFrame => ({
    time: 0,
    cursor: [0, seq],
    mode: "exact",
    state: {
      a: { $kind: "psp", jobs: ["job"] },
      b: { $kind: "server", queue: ["job"] },
      job: { $kind: "job", location },
    },
    layout: null,
    catalog: {},
    kpis: {},
    diagnostics: [],
  });
  const update = (seq: number, location: string) => {
    const f = frame(seq, location);
    return trails.update(f, projector.project(f));
  };
  expect(update(1, "psp:a")).toHaveLength(0);
  expect(update(2, "transit")).toHaveLength(0);
  expect(update(3, "queue:b")).toHaveLength(1);
  expect(update(4, "server:b")).toHaveLength(1);
  expect(update(10, "psp:a")).toHaveLength(1);
  expect(update(1, "queue:b")).toHaveLength(0);
});

it.each([
  [{ t_start: 0, t_end: 10, description: { curve: "constant" } }, -1, 0],
  [{ t_start: 0, t_end: 10, description: { curve: "constant" } }, 20, 1],
  [{ t_start: 2, t_end: 2 }, 2, 1],
  [{ t_start: 3, t_end: 2 }, 3, 0],
  [{ t_start: 0, t_end: Infinity }, 30, 0],
] as const)(
  "clamps endpoints and refuses invalid/stalled motion",
  (motion, time, fraction) => {
    expect(motionProgress(motion, time).fraction).toBe(fraction);
  },
);

it("expires decoration by active milliseconds or six export frames, independently of simulation rate", async () => {
  const { TransferTrails } = await import("../src/project");
  const initial = { a: { $kind: "psp" }, b: { $kind: "server" } };
  const projector = new Projector(initial);
  for (const lifetime of [200, 6])
    for (const rate of [0.1, 1, 10]) {
      const trails = new TransferTrails();
      const frame = (
        seq: number,
        location: string,
        time: number,
      ): PreparedFrame => ({
        time,
        cursor: [time, seq],
        mode: "continuous",
        state: { ...initial, job: { $kind: "job", location } },
        layout: null,
        catalog: {},
        kpis: {},
        diagnostics: [],
      });
      const from = frame(1, "psp:a", 0),
        to = frame(2, "queue:b", rate);
      trails.update(from, projector.project(from), 0, lifetime);
      expect(
        trails.update(to, projector.project(to), 1, lifetime),
      ).toHaveLength(1);
      expect(
        trails.update(to, projector.project(to), lifetime, lifetime),
      ).toHaveLength(1);
      expect(
        trails.update(to, projector.project(to), lifetime + 1, lifetime),
      ).toHaveLength(0);
      trails.reset();
      expect(
        trails.update(from, projector.project(from), lifetime + 2, lifetime),
      ).toHaveLength(0);
    }
});
