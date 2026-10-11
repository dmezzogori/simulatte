import { expect, it, vi } from "vitest";
import { HttpSource, openTrace, type ByteSource } from "../src/index";
import { buildTrace } from "./build";

async function deferredSource() {
  const initial = { state: { cell: { $kind: "cell", value: -1 } }, cursor: [0, -1], manifest: {} };
  const bytes = new Uint8Array(buildTrace(initial, Array.from({ length: 8 }, (_, i) => ({
    snapshot: { cell: { $kind: "cell", value: i - 1 } },
    events: [[i, i, "tick", i, {}, [["set", "cell", "value", i]]]],
  }))));
  const gates = new Map<number, {
    ignoreAbort: boolean;
    entered: (signal: AbortSignal | undefined) => void;
    release: (() => void) | null;
  }>();
  const calls: number[] = [];
  let active = 0, peak = 0;
  const source: ByteSource = {
    size: bytes.length,
    async read(offset, length, signal) {
      calls.push(offset);
      active++;
      peak = Math.max(peak, active);
      try {
        const gate = gates.get(offset);
        if (gate) {
          gates.delete(offset);
          await new Promise<void>((resolve, reject) => {
            const cancel = () => {
              if (!gate.ignoreAbort) reject(signal!.reason);
            };
            gate.release = () => { signal?.removeEventListener("abort", cancel); resolve(); };
            signal?.addEventListener("abort", cancel, { once: true });
            gate.entered(signal);
          });
        }
        return bytes.slice(offset, offset + length);
      } finally { active--; }
    },
  };
  const trace = await openTrace(source);
  calls.length = 0;
  peak = 0;
  return {
    trace, calls, bytes,
    peak: () => peak,
    block(index: number, ignoreAbort = false) {
      let entered!: (signal: AbortSignal | undefined) => void;
      const ready = new Promise<AbortSignal | undefined>((resolve) => { entered = resolve; });
      const gate = { entered, ignoreAbort, release: null as (() => void) | null };
      gates.set(trace.index[index]!.offset, gate);
      return { ready, release: () => gate.release!() };
    },
  };
}

it("rejects a canceled stalled replay promptly and runs its replacement without waiting for abandoned I/O", async () => {
  const source = await deferredSource(), replay = source.trace.replay();
  const gate = source.block(0, true), controller = new AbortController();
  const canceled = expect(replay.prepare([0, 0], controller.signal)).rejects.toMatchObject({ name: "AbortError" });
  const underlying = await gate.ready;
  const replacement = replay.prepare([1, 1]);
  controller.abort();
  await canceled;
  expect(underlying?.aborted).toBe(true);
  expect((await replacement)["cell"]?.["value"]).toBe(1);
  expect(source.trace.cacheStats.activeReads).toBe(1);
  expect(source.trace.cacheStats.chunks).toBe(1);
  gate.release();
  await vi.waitFor(() => expect(source.trace.cacheStats.activeReads).toBe(0));
  expect(source.trace.cacheStats.chunks).toBe(1);
  expect(source.peak()).toBeLessThanOrEqual(2);
});

it("releases both occupied HTTP read slots when stalled payload fetches are canceled", async () => {
  const { bytes } = await deferredSource();
  const blocked = new Map<number, (signal: AbortSignal) => void>();
  vi.stubGlobal("fetch", vi.fn(async (_url: string, init: RequestInit) => {
    if (init.method === "HEAD")
      return new Response(null, { headers: { "content-length": String(bytes.length), etag: '"trace"' } });
    const range = new Headers(init.headers).get("Range")!;
    const [, first, last] = /^bytes=(\d+)-(\d+)$/.exec(range)!;
    const start = Number(first), end = Number(last);
    const enter = blocked.get(start);
    if (enter) {
      return new Promise<Response>((_resolve, reject) => {
        const signal = init.signal!;
        signal.addEventListener("abort", () => reject(signal.reason), { once: true });
        enter(signal);
      });
    }
    return new Response(bytes.slice(start, end + 1), {
      status: 206,
      headers: { etag: '"trace"', "content-range": `bytes ${start}-${end}/${bytes.length}` },
    });
  }));
  try {
    const trace = await openTrace(await HttpSource.open("/trace"));
    const ready = [0, 1].map((index) => new Promise<AbortSignal>((resolve) => {
      blocked.set(trace.index[index]!.offset + 9, resolve);
    }));
    const controllers = [new AbortController(), new AbortController()];
    const canceled = controllers.map((controller, index) =>
      expect(trace.prepare([index, index], controller.signal)).rejects.toMatchObject({ name: "AbortError" }),
    );
    const signals = await Promise.all(ready);
    expect(trace.cacheStats.activeReads).toBe(2);
    const replacement = trace.preparedState([2, 2]);
    controllers.forEach((controller) => controller.abort());
    await Promise.all(canceled);
    expect(signals.every((signal) => signal.aborted)).toBe(true);
    expect((await replacement)["cell"]?.["value"]).toBe(2);
    expect(trace.cacheStats.activeReads).toBe(0);
  } finally { vi.unstubAllGlobals(); }
});

it("keeps a shared read alive for another caller and detaches completed request signals", async () => {
  const source = await deferredSource(), gate = source.block(0);
  const first = new AbortController(), second = new AbortController();
  const canceled = expect(source.trace.prepare([0, 0], first.signal)).rejects.toMatchObject({ name: "AbortError" });
  const underlying = await gate.ready;
  const events = source.trace.chunkEvents(0, second.signal);
  first.abort();
  await canceled;
  expect(underlying?.aborted).toBe(false);
  gate.release();
  expect((await events).map((event) => event.seq)).toEqual([0]);
  second.abort();
  expect(underlying?.aborted).toBe(false);
  expect(source.calls.filter((offset) => offset === source.trace.index[0]!.offset)).toHaveLength(1);
  await source.trace.prepare([0, 0]);
});

it("aborts only the last departing consumer and does not delete a replacement read for the same chunk", async () => {
  const source = await deferredSource(), gate = source.block(0, true);
  const first = new AbortController(), second = new AbortController();
  const a = expect(source.trace.prepare([0, 0], first.signal)).rejects.toMatchObject({ name: "AbortError" });
  const underlying = await gate.ready;
  const b = expect(source.trace.preparedState([0, 0], second.signal)).rejects.toMatchObject({ name: "AbortError" });
  first.abort();
  await a;
  expect(underlying?.aborted).toBe(false);
  second.abort();
  await b;
  expect(underlying?.aborted).toBe(true);
  const renewed = source.block(0);
  const replacement = source.trace.preparedState([0, 0]);
  await renewed.ready;
  gate.release();
  await vi.waitFor(() => expect(source.trace.cacheStats.activeReads).toBe(1));
  const joined = source.trace.chunkEvents(0);
  renewed.release();
  expect((await replacement)["cell"]?.["value"]).toBe(0);
  expect(await joined).toHaveLength(1);
  expect(source.calls.filter((offset) => offset === source.trace.index[0]!.offset)).toHaveLength(2);
});

it("removes canceled queued chunk reads and retains the two-read and four-chunk bounds", async () => {
  const source = await deferredSource(), a = source.block(0), b = source.block(1);
  const first = source.trace.prepare([0, 0]), second = source.trace.prepare([1, 1]);
  await Promise.all([a.ready, b.ready]);
  for (let i = 0; i < 100; i++) {
    const controller = new AbortController();
    const canceled = expect(source.trace.prepare([2, 2], controller.signal)).rejects.toMatchObject({ name: "AbortError" });
    controller.abort();
    await canceled;
  }
  const live = source.trace.prepare([3, 3]);
  expect(source.trace.cacheStats.activeReads).toBe(2);
  a.release(); b.release();
  await Promise.all([first, second, live]);
  expect(source.calls).not.toContain(source.trace.index[2]!.offset);
  await Promise.all([4, 5, 6, 7].map((i) => source.trace.prepare([i, i])));
  expect(source.peak()).toBeLessThanOrEqual(2);
  expect(source.trace.cacheStats.activeReads).toBe(0);
  expect(source.trace.cacheStats.chunks).toBe(4);
});

it("removes canceled queued replay requests before they can read or mutate state", async () => {
  const source = await deferredSource(), replay = source.trace.replay(), gate = source.block(0);
  const first = replay.prepare([0, 0]);
  await gate.ready;
  for (let i = 0; i < 100; i++) {
    const controller = new AbortController();
    const canceled = expect(replay.prepare([2, 2], controller.signal)).rejects.toMatchObject({ name: "AbortError" });
    controller.abort();
    await canceled;
  }
  const live = replay.prepare([1, 1]);
  gate.release();
  await first;
  expect((await live)["cell"]?.["value"]).toBe(1);
  expect(source.calls).not.toContain(source.trace.index[2]!.offset);
});

it("does not commit reconstructed state when canceled between awaited replay stages", async () => {
  const source = await deferredSource(), replay = source.trace.replay();
  const baseline = await replay.prepare([0, 0]);
  const controller = new AbortController();
  const original = source.trace.preparedState.bind(source.trace);
  vi.spyOn(source.trace, "preparedState").mockImplementation(async (cursor, signal) => {
    const state = await original(cursor, signal);
    if (cursor[1] === 1) controller.abort();
    return state;
  });
  await expect(replay.prepare([1, 1], controller.signal)).rejects.toMatchObject({ name: "AbortError" });
  const replacement = await replay.prepare([0, 0]);
  expect(replacement).toEqual(baseline);
  expect(replacement["cell"]?.["value"]).toBe(0);
  expect((await replay.prepare([2, 2]))["cell"]?.["value"]).toBe(2);
});
