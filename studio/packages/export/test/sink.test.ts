import { afterEach, expect, it, vi } from "vitest";
import { destination, memoryDestination, MEMORY_LIMIT } from "../src/sink";
afterEach(() => vi.unstubAllGlobals());
it("bounds memory output incrementally and refuses positioned overwrites", async () => {
  const sink = memoryDestination("frames.zip");
  await sink.write(new Uint8Array(10), 0);
  await expect(sink.write(new Uint8Array(1), 0)).rejects.toThrow("sequential");
  await expect(sink.write(new Uint8Array(MEMORY_LIMIT), 10)).rejects.toThrow(
    "64 MiB",
  );
  expect((await sink.finish()).blob?.size).toBe(10);
  await sink.abort();
});
it("uses bounded PNG fallback when a browser exposes but denies OPFS", async () => {
  vi.stubGlobal("navigator", {
    storage: {
      getDirectory: async () => {
        throw new DOMException("private browsing", "SecurityError");
      },
    },
  });
  const sink = await destination("frames.zip", true);
  await sink.write(new Uint8Array([1, 2]), 0);
  expect((await sink.finish()).kind).toBe("memory");
  await expect(destination("video.mp4")).rejects.toThrow("unavailable");
});
it("removes an OPFS partial after worker failure without waiting for the dead worker", async () => {
  let removed = 0;
  const root = {
    getFileHandle: async () => ({}),
    removeEntry: async () => {
      removed++;
    },
  };
  vi.stubGlobal("navigator", {
    storage: { getDirectory: async () => root },
    locks: {
      request: async (_name: string, callback: () => Promise<void>) =>
        callback(),
    },
  });
  class Worker {
    onmessage: ((e: unknown) => void) | null = null;
    onerror: ((e: unknown) => void) | null = null;
    postMessage(message: { id: number; type: string }) {
      queueMicrotask(() => {
        if (message.type === "open")
          this.onmessage?.({ data: { id: message.id } });
        else this.onerror?.({ message: "worker died" });
      });
    }
    terminate() {}
  }
  vi.stubGlobal("Worker", Worker);
  const sink = await destination("test.mp4");
  await expect(sink.write(new Uint8Array([1]), 0)).rejects.toThrow(
    "worker died",
  );
  await sink.abort();
  expect(removed).toBe(1);
});
it("removes a created file if worker construction fails", async () => {
  let removed = 0;
  vi.stubGlobal("navigator", {
    storage: {
      getDirectory: async () => ({
        getFileHandle: async () => ({}),
        removeEntry: async () => {
          removed++;
        },
      }),
    },
    locks: {
      request: async (_name: string, callback: () => Promise<void>) =>
        callback(),
    },
  });
  vi.stubGlobal(
    "Worker",
    class {
      constructor() {
        throw new Error("CSP denied worker");
      }
    },
  );
  await expect(destination("test.mp4")).rejects.toThrow("CSP denied");
  expect(removed).toBe(1);
});
it("preserves positioned direct-file writes and propagates quota/finalization failures", async () => {
  const writes: unknown[] = [];
  let closed = 0,
    aborted = 0;
  const writable = {
    write: async (value: unknown) => {
      writes.push(value);
    },
    close: async () => {
      closed++;
    },
    abort: async () => {
      aborted++;
    },
  };
  vi.stubGlobal("showSaveFilePicker", async () => ({
    createWritable: async () => writable,
  }));
  const sink = await destination("video.mp4");
  await sink.write(new Uint8Array([1, 2]), 20);
  await sink.write(new Uint8Array([3]), 0);
  expect(writes).toEqual([
    { type: "write", position: 20, data: new Uint8Array([1, 2]) },
    { type: "write", position: 0, data: new Uint8Array([3]) },
  ]);
  expect((await sink.finish()).kind).toBe("direct");
  expect(closed).toBe(1);
  writable.write = async () => {
    throw new DOMException("disk full", "QuotaExceededError");
  };
  await expect(sink.write(new Uint8Array([4]), 1)).rejects.toThrow("disk full");
  await sink.abort();
  expect(aborted).toBe(1);
  writable.close = async () => {
    throw new Error("flush failed");
  };
  await expect(sink.finish()).rejects.toThrow("flush failed");
});
it("does not hide an OPFS quota error behind the memory fallback", async () => {
  vi.stubGlobal("navigator", {
    storage: {
      getDirectory: async () => {
        throw new DOMException("quota", "QuotaExceededError");
      },
    },
  });
  await expect(destination("frames.zip", true)).rejects.toThrow("quota");
});
