import { expect, it, vi } from "vitest";
import {
  openRecording,
  type Cursor,
  type EventQuery,
  type Recording,
  type TraceEvent,
} from "@simulatte/trace";
import { buildTrace, type ChunkSpec } from "../../trace/test/build";
import { Session, type PreparedFrame } from "../src/index";

const position = (time: number) => ({
  mode: "continuous" as const,
  time,
  cursor: null,
});
function manyEvents(count = 50000) {
  const event = (seq: number): TraceEvent => ({
    seq,
    t: Math.floor(seq / 3),
    type: "tick",
    ordinal: seq,
    payload: {},
    deltas: [],
  });
  const lookup = (query: EventQuery): readonly TraceEvent[] => {
    const last = Math.min(
      count - 1,
      query.through
        ? Math.min(Math.floor(query.through[0]) * 3 + 2, query.through[1])
        : count - 1,
    );
    const first = query.after ? query.after[1] + 1 : 0;
    return Array.from(
      { length: Math.max(0, Math.min(query.limit ?? 200, last - first + 1)) },
      (_, i) => event(query.direction === "backward" ? last - i : first + i),
    );
  };
  const events = vi.fn(async (query: EventQuery = {}) => lookup(query));
  const recording = {
    trace: {
      activation: [0, -1],
      replay: () => ({
        prepare: async (at: Cursor) => ({ cell: { value: at[1] } }),
      }),
    },
    domainCursor: (at: Cursor) => at,
    seriesDisplay: {},
    catalog: {},
    events,
  } as unknown as Recording;
  return { recording, events, lookup };
}

it("keeps nearby playback cached and bounds distant seeks across 50,000 events", async () => {
  const { recording, events } = manyEvents(),
    session = new Session(recording);
  await session.prepare(position(0));
  events.mockClear();
  for (let time = 1; time <= 20; time++) {
    const frame = await session.prepare(position(time));
    expect(frame.cursor).toEqual([time, time * 3 + 2]);
    expect(frame.crossedEvents?.map((event) => event.seq)).toEqual([
      time * 3,
      time * 3 + 1,
      time * 3 + 2,
    ]);
    expect(frame.crossingsComplete).toBe(true);
  }
  expect(events).not.toHaveBeenCalled();
  const far = await session.prepare(position(16666));
  expect(far.cursor).toEqual([16666, 49999]);
  expect(far.state["cell"]?.["value"]).toBe(49999);
  expect(far.crossedEvents?.map((event) => event.seq)).toEqual(
    Array.from({ length: 256 }, (_, i) => 49744 + i),
  );
  expect(far.crossingsComplete).toBe(false);
  const calls = events.mock.calls.map(([query]) => query!);
  expect(
    calls.filter((q) => q.direction !== "backward").length,
  ).toBeLessThanOrEqual(2);
  expect(calls.filter((q) => q.direction === "backward")).toEqual([
    expect.objectContaining({
      through: [16666, Number.MAX_SAFE_INTEGER],
      after: [20, 62],
      limit: 257,
    }),
  ]);
  session.dispose();
});

it("matches indexed cursor and replay state across jumps and same-time chunk boundaries", async () => {
  const initial = {
    state: { cell: { $kind: "cell", value: -1 } },
    cursor: [0, -1],
    manifest: {},
  };
  const chunks: ChunkSpec[] = Array.from({ length: 40 }, (_, chunk) => {
    const first = chunk * 100;
    return {
      snapshot: { cell: { $kind: "cell", value: first - 1 } },
      events: Array.from({ length: 100 }, (_, i) => {
        const seq = first + i;
        return [
          seq,
          seq,
          "tick",
          Math.floor(seq / 3),
          {},
          [["set", "cell", "value", seq]],
        ];
      }),
    };
  });
  const r = await openRecording(buildTrace(initial, chunks)),
    session = new Session(r);
  let previous: PreparedFrame | null = null;
  for (const time of [0, 900, 1333, 10, 1300, 1300.25]) {
    const frame = await session.prepare(position(time)),
      cursor = (await r.cursorAtTime(time))!;
    await r.trace.prepare(cursor);
    expect(frame.cursor).toEqual(cursor);
    expect(frame.state).toEqual(r.trace.stateAt(cursor));
    if (previous?.cursor && time >= previous.time) {
      const crossed = await r.events({
        after: previous.cursor,
        through: cursor,
        direction: "backward",
        limit: 257,
      });
      expect(frame.crossedEvents).toEqual(crossed.slice(0, 256).reverse());
      expect(frame.crossingsComplete).toBe(crossed.length <= 256);
    } else {
      expect(frame.crossedEvents).toEqual([]);
      expect(frame.crossingsComplete).toBe(false);
    }
    previous = frame;
  }
  session.dispose();
});

it("cancels a distant-seek indexed fallback without poisoning the next request", async () => {
  const { recording, events, lookup } = manyEvents(),
    session = new Session(recording);
  await session.prepare(position(0));
  let enter!: () => void,
    block = true;
  const ready = new Promise<void>((resolve) => {
    enter = resolve;
  });
  events.mockImplementation(async (query = {}) => {
    if (query.direction === "backward" && block) {
      block = false;
      enter();
      if (!query.signal) throw new Error("Missing cancellation signal");
      query.signal.throwIfAborted();
      return new Promise((_resolve, reject) =>
        query.signal!.addEventListener(
          "abort",
          () => reject(query.signal!.reason),
          { once: true },
        ),
      );
    }
    return lookup(query);
  });
  const abort = new AbortController();
  const cancelled = expect(
    session.prepare(position(16666), abort.signal),
  ).rejects.toMatchObject({ name: "AbortError" });
  await ready;
  abort.abort();
  const replacement = session.prepare(position(10));
  await cancelled;
  const frame = await replacement;
  expect(frame.cursor).toEqual([10, 32]);
  expect(frame.crossedEvents?.map((event) => event.seq)).toEqual(
    Array.from({ length: 30 }, (_, i) => i + 3),
  );
  expect(frame.crossingsComplete).toBe(true);
  session.dispose();
});

it("marks initial, activation and backward frames incomplete and repeated cursors complete", async () => {
  const { recording } = manyEvents(),
    session = new Session(recording);
  for (const requested of [
    position(10),
    position(5),
    { mode: "activation" as const, time: 0, cursor: null },
  ]) {
    const frame = await session.prepare(requested);
    expect(frame.crossedEvents).toEqual([]);
    expect(frame.crossingsComplete).toBe(false);
  }
  const same = await session.prepare({
    mode: "exact",
    time: 0,
    cursor: [0, -1],
  });
  expect(same.crossedEvents).toEqual([]);
  expect(same.crossingsComplete).toBe(true);
  const first = await session.prepare(position(0));
  expect(first.crossedEvents?.map((event) => event.seq)).toEqual([0, 1, 2]);
  expect(first.crossingsComplete).toBe(true);
  const repeated = await session.prepare(position(0.1));
  expect(repeated.crossedEvents).toEqual([]);
  expect(repeated.crossingsComplete).toBe(true);
  const backwardsInGap = await session.prepare(position(0));
  expect(backwardsInGap.crossedEvents).toEqual([]);
  expect(backwardsInGap.crossingsComplete).toBe(false);
  session.dispose();
});

it("retains only the last 256 crossings on a bounded forward scan", async () => {
  const { recording, events } = manyEvents(),
    session = new Session(recording);
  await session.prepare(position(0));
  events.mockClear();
  const frame = await session.prepare(position(100));
  expect(frame.crossedEvents?.map((event) => event.seq)).toEqual(
    Array.from({ length: 256 }, (_, i) => 47 + i),
  );
  expect(frame.crossingsComplete).toBe(false);
  expect(
    events.mock.calls.every(([query]) => query?.direction !== "backward"),
  ).toBe(true);
  expect(Object.isFrozen(frame.crossedEvents)).toBe(true);
  session.dispose();
});

it("collects exact forward crossings including same-time events with bounded indexed queries", async () => {
  const { recording, events } = manyEvents(),
    session = new Session(recording);
  await session.prepare({ mode: "activation", time: 0, cursor: null });
  const exact = (seq: number) => ({
    mode: "exact" as const,
    time: Math.floor(seq / 3),
    cursor: [Math.floor(seq / 3), seq] as Cursor,
  });
  const first = await session.prepare(exact(0));
  expect(first.crossedEvents?.map((event) => event.seq)).toEqual([0]);
  const second = await session.prepare(exact(2));
  expect(second.crossedEvents?.map((event) => event.seq)).toEqual([1, 2]);
  expect(second.crossingsComplete).toBe(true);
  const boundary = await session.prepare(exact(258));
  expect(boundary.crossedEvents).toHaveLength(256);
  expect(boundary.crossingsComplete).toBe(true);
  const far = await session.prepare(exact(10000));
  expect(far.crossedEvents?.map((event) => event.seq)).toEqual(
    Array.from({ length: 256 }, (_, i) => 9745 + i),
  );
  expect(far.crossingsComplete).toBe(false);
  expect(events.mock.calls.every(([query]) => query?.limit === 257)).toBe(true);
  session.dispose();
});
