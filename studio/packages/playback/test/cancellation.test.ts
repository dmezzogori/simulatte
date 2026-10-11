import { expect, it, vi } from "vitest";
import { openRecording, type Sample } from "@simulatte/trace";
import { buildTrace } from "../../trace/test/build";
import { Playback, Session } from "../src/index";

const position = (time: number) => ({
  mode: "continuous" as const,
  time,
  cursor: null,
});
const sample = (time: number, value: number): Sample => ({
  cursor: [time, time + 1],
  key: "v",
  value,
});
const recording = () =>
  openRecording(buildTrace({ state: {}, cursor: [0, -1], manifest: {} }, []));
function untilAbort(signal?: AbortSignal): Promise<never> {
  if (!signal) return Promise.reject(new Error("Missing cancellation signal"));
  signal.throwIfAborted();
  return new Promise((_resolve, reject) =>
    signal.addEventListener("abort", () => reject(signal.reason), {
      once: true,
    }),
  );
}

it("cancels a suspended sample read and reconstructs the series for the queued request", async () => {
  const r = await recording(),
    session = new Session(r),
    abort = new AbortController();
  let entered!: () => void,
    attempts = 0;
  const ready = new Promise<void>((resolve) => {
    entered = resolve;
  });
  const signals: (AbortSignal | undefined)[] = [];
  vi.spyOn(r, "samples").mockImplementation(async function* (_key, signal) {
    signals.push(signal);
    const attempt = ++attempts;
    yield sample(0, 4);
    if (attempt === 1) {
      entered();
      await untilAbort(signal);
    }
    yield sample(5, 8);
    yield sample(10, 12);
  });
  session.setSeries(["v"]);
  const cancelled = expect(
    session.prepare(position(7), abort.signal),
  ).rejects.toMatchObject({ name: "AbortError" });
  await ready;
  abort.abort();
  const replacement = session.prepare(position(7));
  await cancelled;
  expect((await replacement).kpis["v"]?.value).toBe(8);
  expect(signals).toHaveLength(2);
  expect(signals[0]?.aborted).toBe(true);
  expect(signals[1]?.aborted).toBe(false);
  session.dispose();
});

it("does not retain a completed frame's abort signal in the forward iterator", async () => {
  const r = await recording(),
    session = new Session(r),
    abort = new AbortController();
  const samples = vi
    .spyOn(r, "samples")
    .mockImplementation(async function* (_key, signal) {
      for (const [time, value] of [
        [0, 4],
        [5, 8],
        [10, 12],
        [20, 16],
      ]) {
        signal?.throwIfAborted();
        yield sample(time!, value!);
      }
    });
  session.setSeries(["v"]);
  expect(
    (await session.prepare(position(1), abort.signal)).kpis["v"]?.value,
  ).toBe(4);
  abort.abort();
  expect((await session.prepare(position(8))).kpis["v"]?.value).toBe(8);
  expect(samples).toHaveBeenCalledOnce();
  session.dispose();
});

it.each(["initial", "forward"])(
  "cancels the %s event query and allows a replacement seek",
  async (phase) => {
    const r = await recording(),
      session = new Session(r),
      abort = new AbortController();
    let entered!: () => void,
      blocked = false;
    const ready = new Promise<void>((resolve) => {
      entered = resolve;
    });
    vi.spyOn(r, "events").mockImplementation(async (query) => {
      const shouldBlock =
        phase === "initial"
          ? query?.direction === "backward"
          : query?.direction !== "backward";
      if (shouldBlock && !blocked) {
        blocked = true;
        entered();
        return untilAbort(query?.signal);
      }
      return [];
    });
    const cancelled = expect(
      session.prepare(position(1), abort.signal),
    ).rejects.toMatchObject({ name: "AbortError" });
    await ready;
    abort.abort();
    const replacement = session.prepare(position(2));
    await cancelled;
    expect((await replacement).time).toBe(2);
    session.dispose();
  },
);

it("disposal aborts an active sample query even without a caller signal", async () => {
  const r = await recording(),
    session = new Session(r);
  let entered!: () => void;
  const ready = new Promise<void>((resolve) => {
    entered = resolve;
  });
  vi.spyOn(r, "samples").mockImplementation(async function* (_key, signal) {
    yield sample(0, 4);
    entered();
    await untilAbort(signal);
  });
  session.setSeries(["v"]);
  const cancelled = expect(session.prepare(position(1))).rejects.toMatchObject({
    name: "AbortError",
  });
  await ready;
  session.dispose();
  await cancelled;
});

it("a new seek cancels an in-flight event-step query", async () => {
  const r = await recording(),
    playback = new Playback(new Session(r), {
      request: () => 0,
      cancel: () => {},
    });
  const activation = { mode: "activation" as const, time: 0, cursor: null };
  await playback.seek(activation);
  let signal: AbortSignal | undefined;
  vi.spyOn(r, "events").mockImplementation((query) => {
    signal = query?.signal;
    return untilAbort(signal);
  });
  const step = playback.step(1);
  await playback.seek(activation);
  await step;
  expect(signal?.aborted).toBe(true);
  expect(playback.status).toBe("paused");
  playback.dispose();
});
