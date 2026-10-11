import { expect, it, vi } from "vitest";
import {
  HttpSource,
  openRecording,
  openTrace,
  TraceCorrupted,
} from "../src/index";
import { buildTrace } from "./build";
const initial = {
  state: { cell: { $kind: "cell", value: 0 } },
  cursor: [0, -1],
  manifest: { seed: "7" },
};
const declaration = {
  name: "value",
  unit: "jobs",
  kind: ["series"],
  observation: "job",
  cohort: "completed_in_window",
  aggregation: "mean",
  clip: "none",
  censoring: "exclude",
  ema_reset: false,
  empty: null,
  description: "",
};
const data = () =>
  buildTrace(
    initial,
    [
      {
        snapshot: initial.state,
        events: [
          [0, 0, "tick", 0, {}, [["set", "cell", "value", 1]]],
          [2, 1, "tick", 1, {}, [["set", "cell", "value", 2]]],
        ],
      },
    ],
    {
      records: [
        [4, { epoch: 1, types: { log: { category: "observer" } }, kinds: {} }],
        [
          9,
          {
            phase: "simulation",
            first: [0, 1],
            last: [2, 4],
            epoch: 1,
            events: [
              [1, null, "log", 0, { message: "one" }, []],
              [4, null, "log", 2, { message: "last" }, []],
            ],
          },
        ],
        [
          7,
          {
            declarations: { "cell/value": declaration },
            series_display: {
              "cell/value": { interpolation: "linear", missing: "hold" },
            },
            samples: [
              [1, 0, "cell/value", 3],
              [3, 1, "cell/value", 5],
              [5, 2, "cell/value", NaN],
            ],
            scalars: {},
          },
        ],
      ],
    },
  );
it("merges domain and observer pages without altering legacy replay bounds", async () => {
  const r = await openRecording(data());
  expect((await r.events()).map((e) => e.seq)).toEqual([0, 1, 2, 4]);
  expect(
    (await r.events({ direction: "backward", limit: 2 })).map((e) => e.seq),
  ).toEqual([4, 2]);
  expect(
    (await r.events({ after: [0, 1], before: [2, 4] })).map((e) => e.seq),
  ).toEqual([2]);
  expect((await r.events({ entity: "cell" })).map((e) => e.seq)).toEqual([
    0, 2,
  ]);
  expect(r.historyEnd).toEqual([2, 4]);
  expect(r.trace.cursorRange?.[1]).toEqual([1, 2]);
  expect(r.finiteRange).toEqual([0, 2]);
  expect(r.trace.manifest["seed"]).toBe("7");
  const b = await r.sampleBracket("cell/value", [1, 2]);
  expect(b.left?.value).toBe(3);
  expect(b.right?.value).toBe(5);
  expect((await r.sampleBracket("cell/value", [2, 5])).finite?.value).toBe(5);
  expect(await r.chart("cell/value", [0, 2])).toHaveLength(3);
});
it("sequential replay preserves earlier returned snapshots", async () => {
  const t = await openTrace(data());
  const replay = t.replay();
  const a = await replay.prepare([0, 0]);
  const b = await replay.prepare([1, 2]);
  expect(a["cell"]?.["value"]).toBe(1);
  expect(b["cell"]?.["value"]).toBe(2);
  expect(await replay.prepare([0, -1])).toEqual(initial.state);
});
it("rejects malformed observer metadata and display rules", async () => {
  await expect(
    openRecording(
      buildTrace(initial, [], {
        records: [
          [
            9,
            {
              phase: "simulation",
              first: [0, 0],
              last: [0, 0],
              epoch: 0,
              events: [[0, 0, "log", 0, {}, []]],
            },
          ],
        ],
      }),
    ),
  ).rejects.toThrow(TraceCorrupted);
  await expect(
    openRecording(
      buildTrace(initial, [], {
        records: [
          [
            7,
            {
              declarations: { "cell/value": declaration },
              series_display: {
                "cell/value": { interpolation: ["linear"], missing: "hold" },
              },
            },
          ],
        ],
      }),
    ),
  ).rejects.toThrow(TraceCorrupted);
});
it("validates HTTP identity, exact ranges and cancellation", async () => {
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(
      new Response(null, { headers: { "content-length": "3", etag: '"a"' } }),
    )
    .mockResolvedValueOnce(
      new Response(new Uint8Array([1, 2]), {
        status: 206,
        headers: { etag: '"a"', "content-range": "bytes 1-2/3" },
      }),
    )
    .mockResolvedValueOnce(
      new Response(new Uint8Array([0]), {
        status: 206,
        headers: { etag: '"b"', "content-range": "bytes 0-0/3" },
      }),
    );
  vi.stubGlobal("fetch", fetcher);
  try {
    const s = await HttpSource.open("http://localhost/trace");
    expect(await s.read(1, 2)).toEqual(new Uint8Array([1, 2]));
    await expect(s.read(0, 1)).rejects.toThrow("changed");
  } finally {
    vi.unstubAllGlobals();
  }
});

it("matches Python states and observer history from the format 1.2 reference fixture", async () => {
  const { readFileSync } = await import("node:fs");
  const root = new URL(
    "../../../../tests/fixtures/traces/viewer/",
    import.meta.url,
  );
  const bytes = readFileSync(new URL("viewer.simtrace", root));
  const expected = JSON.parse(
    readFileSync(new URL("viewer.expected.json", root), "utf8"),
  ) as {
    events: unknown;
    prelude: unknown;
    display: unknown;
    states: { cursor: readonly [number, number]; state: unknown }[];
  };
  const recording = await openRecording(Uint8Array.from(bytes).buffer);
  const headerKinds = recording.trace.header["kinds"] as Record<string, unknown>;
  expect(headerKinds["fixture_cell"]).toBeDefined();
  expect(headerKinds["fixture_late"]).toBeUndefined();
  expect(recording.kinds["fixture_cell"]).toEqual(headerKinds["fixture_cell"]);
  expect(recording.kinds["fixture_late"]).toMatchObject({
    size: { type: "int", nullable: false, collection: null, presentation: false },
  });
  expect(Object.isFrozen(recording.kinds)).toBe(true);
  expect(Object.isFrozen(recording.kinds["fixture_cell"])).toBe(true);
  expect(Object.isFrozen(recording.kinds["fixture_late"])).toBe(true);
  expect(Reflect.set(recording.kinds, "replacement", {})).toBe(false);
  expect((await recording.events()).map((e) => [e.t, e.seq, e.type])).toEqual(
    expected.events,
  );
  const allPrelude = await recording.events({ phase: "prelude" });
  expect(
    allPrelude
      .filter((e) => e.ordinal === null && e.type !== "entity.created")
      .map((e) => [e.t, e.seq, e.type]),
  ).toEqual(expected.prelude);
  expect(recording.seriesDisplay).toEqual(expected.display);
  const replay = recording.trace.replay();
  for (const check of expected.states)
    expect(await replay.prepare(recording.domainCursor(check.cursor)!)).toEqual(
      check.state,
    );
});

it("rejects an oversized HTTP body without retaining unbounded response data", async () => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValueOnce(
        new Response(null, { headers: { "content-length": "2", etag: '"a"' } }),
      )
      .mockResolvedValueOnce(
        new Response(new Uint8Array([1, 2, 3]), {
          status: 206,
          headers: { etag: '"a"', "content-range": "bytes 0-1/2" },
        }),
      ),
  );
  try {
    const source = await HttpSource.open("/trace");
    await expect(source.read(0, 2)).rejects.toThrow("Oversized");
  } finally {
    vi.unstubAllGlobals();
  }
});
