import { afterEach, beforeEach, expect, it, vi } from "vitest";
const calls = vi.hoisted(() => ({
  cancel: vi.fn(async () => {}),
  dispose: vi.fn(),
  add: vi.fn(async () => {}),
  render: vi.fn(),
}));
vi.mock("@simulatte/scene", () => ({
  Scene: class {
    app = { renderer: { background: { color: "" } } };
    canvas = {};
    onError: ((message: string) => void) | null = null;
    async init() {}
    render() {
      calls.render(this);
    }
    dispose() {
      calls.dispose();
    }
  },
}));
vi.mock("@simulatte/playback", () => ({
  Session: class {
    async prepare() {
      return {};
    }
    dispose() {
      calls.dispose();
    }
  },
}));
vi.mock("mediabunny", () => ({
  CanvasSource: class {
    add(...args: unknown[]) {
      return calls.add(...(args as []));
    }
  },
  Output: class {
    addVideoTrack() {}
    async start() {}
    async finalize() {}
    cancel() {
      return calls.cancel();
    }
  },
  Mp4OutputFormat: class {},
  WebMOutputFormat: class {},
  StreamTarget: class {},
}));
import { exportMedia, type MediaOptions } from "../src/media";
import type { Recording } from "@simulatte/trace";
const options: MediaOptions = {
  start: 0,
  end: 0,
  fps: 30,
  rate: 1,
  width: 320,
  height: 180,
  format: "mp4",
  background: "#ffffff",
  overlays: false,
};
const recording = { finiteRange: [0, 1] } as unknown as Recording;
beforeEach(() => {
  calls.cancel.mockClear();
  calls.dispose.mockClear();
  calls.add.mockReset().mockResolvedValue();
  calls.render.mockReset();
  vi.stubGlobal("VideoEncoder", {
    isConfigSupported: async () => ({ supported: true }),
  });
  vi.stubGlobal("document", {
    createElement: () => ({
      getContext: () => ({ fillRect() {}, drawImage() {}, fillText() {} }),
    }),
  });
});
afterEach(() => vi.unstubAllGlobals());
it("removes a staged result when cancellation arrives during destination finalization", async () => {
  const abort = new AbortController(),
    remove = vi.fn(async () => {}),
    cleanup = vi.fn(async () => {});
  const sink = {
    write: async () => {},
    finish: async () => {
      abort.abort();
      return { name: "clip", kind: "temporary" as const, remove };
    },
    abort: cleanup,
  };
  await expect(
    exportMedia(recording, options, sink, abort.signal, () => {}),
  ).rejects.toMatchObject({ name: "AbortError" });
  expect(remove).toHaveBeenCalledOnce();
  expect(cleanup).toHaveBeenCalledOnce();
  expect(calls.dispose).toHaveBeenCalledTimes(2);
});
it("preserves cleanup failure instead of claiming cancellation removed output", async () => {
  const abort = new AbortController();
  abort.abort();
  const sink = {
    write: async () => {},
    finish: vi.fn(),
    abort: async () => {
      throw new Error("quota cleanup failed");
    },
  };
  await expect(
    exportMedia(recording, options, sink, abort.signal, () => {}),
  ).rejects.toThrow("cleanup failed");
  expect(sink.finish).not.toHaveBeenCalled();
});
it("stops frame submission after cancellation and cancels the encoder", async () => {
  const abort = new AbortController();
  calls.add.mockImplementationOnce(async () => {
    abort.abort();
  });
  const sink = {
    write: async () => {},
    finish: vi.fn(),
    abort: vi.fn(async () => {}),
  };
  await expect(
    exportMedia(
      recording,
      { ...options, end: 1 },
      sink,
      abort.signal,
      () => {},
    ),
  ).rejects.toMatchObject({ name: "AbortError" });
  expect(calls.add).toHaveBeenCalledOnce();
  expect(calls.cancel).toHaveBeenCalledOnce();
  expect(sink.abort).toHaveBeenCalledOnce();
});
it("fails on graphics context loss before encoding the invalid frame and cleans output", async () => {
  calls.render.mockImplementationOnce((scene) =>
    scene.onError?.("Graphics context lost. Waiting for restoration."),
  );
  const sink = {
    write: async () => {},
    finish: vi.fn(),
    abort: vi.fn(async () => {}),
  };
  await expect(
    exportMedia(
      recording,
      options,
      sink,
      new AbortController().signal,
      () => {},
    ),
  ).rejects.toThrow("Export rendering failed: Graphics context lost");
  expect(calls.add).not.toHaveBeenCalled();
  expect(sink.finish).not.toHaveBeenCalled();
  expect(calls.cancel).toHaveBeenCalledOnce();
  expect(sink.abort).toHaveBeenCalledOnce();
  expect(calls.dispose).toHaveBeenCalledTimes(2);
});
it("removes a completed temporary result when graphics context loss arrives during sink finalization", async () => {
  let loseContext!: () => void;
  calls.render.mockImplementationOnce((scene) => {
    loseContext = () => scene.onError?.("Graphics context lost");
  });
  const remove = vi.fn(async () => {}),
    cleanup = vi.fn(async () => {});
  const sink = {
    write: async () => {},
    finish: async () => {
      loseContext();
      return { name: "clip", kind: "temporary" as const, remove };
    },
    abort: cleanup,
  };
  await expect(
    exportMedia(
      recording,
      options,
      sink,
      new AbortController().signal,
      () => {},
    ),
  ).rejects.toThrow("Export rendering failed");
  expect(remove).toHaveBeenCalledOnce();
  expect(cleanup).toHaveBeenCalledOnce();
});
