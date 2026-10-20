/** Positioned, backpressured output. Media bytes are never accumulated by the exporter. */
export interface Destination {
  write(data: Uint8Array<ArrayBuffer>, position: number): Promise<void>;
  finish(): Promise<ExportFile>;
  abort(): Promise<void>;
}
export interface ExportFile {
  name: string;
  kind: "direct" | "temporary" | "memory";
  blob?: Blob;
  remove(): Promise<void>;
}
const PREFIX = "simulatte-export-";
export const MEMORY_LIMIT = 64 * 1024 * 1024;
export async function abandonedExports(): Promise<string[]> {
  if (!navigator.storage?.getDirectory) return [];
  const root = await navigator.storage.getDirectory();
  const locks = await navigator.locks?.query();
  const active = new Set(locks?.held?.map((lock) => lock.name));
  const names: string[] = [];
  for await (const [name] of (
    root as unknown as { entries(): AsyncIterable<[string, FileSystemHandle]> }
  ).entries())
    if (name.startsWith(PREFIX) && !active.has(name)) names.push(name);
  return names;
}
export async function removeAbandoned(name: string): Promise<void> {
  if (!name.startsWith(PREFIX)) throw new Error("Not a viewer temporary file");
  if (!navigator.locks)
    throw new Error(
      "This browser cannot safely check whether another tab owns the temporary file",
    );
  await navigator.locks.request(name, { ifAvailable: true }, async (lock) => {
    if (!lock)
      throw new Error("This temporary file is in use by another viewer tab");
    await (await navigator.storage.getDirectory()).removeEntry(name);
  });
}
async function reserve(name: string): Promise<() => void> {
  if (!navigator.locks)
    throw new Error("Temporary export storage requires browser lock support");
  let release!: () => void;
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  return new Promise((resolve, reject) => {
    void navigator.locks
      .request(name, async () => {
        resolve(release);
        await held;
      })
      .catch(reject);
  });
}
export function memoryDestination(name: string): Destination {
  let size = 0;
  const chunks: Uint8Array<ArrayBuffer>[] = [];
  return {
    async write(data, position) {
      if (position !== size)
        throw new Error(
          "Memory fallback supports sequential PNG archives only",
        );
      if (size + data.length > MEMORY_LIMIT)
        throw new Error("PNG archive exceeds the 64 MiB memory fallback limit");
      chunks.push(data.slice());
      size += data.length;
    },
    async finish() {
      return {
        name,
        kind: "memory",
        blob: new Blob(chunks, { type: "application/zip" }),
        remove: async () => {
          chunks.length = 0;
        },
      };
    },
    async abort() {
      chunks.length = 0;
    },
  };
}
/** ``usePicker=false`` skips the direct file picker so the finished bytes stay readable (size and digest). */
export async function destination(
  name: string,
  allowMemory = false,
  usePicker = true,
): Promise<Destination> {
  const picker = (
    globalThis as unknown as {
      showSaveFilePicker?: (options: {
        suggestedName: string;
      }) => Promise<FileSystemFileHandle>;
    }
  ).showSaveFilePicker;
  if (picker && usePicker) {
    const handle = await picker({ suggestedName: name });
    const file = await handle.createWritable();
    return {
      write: async (data, position) => {
        await file.write({ type: "write", position, data });
      },
      finish: async () => {
        await file.close();
        return { name, kind: "direct", remove: async () => {} };
      },
      abort: async () => {
        await file.abort();
      },
    };
  }
  let root: FileSystemDirectoryHandle | undefined;
  if (navigator.storage?.getDirectory) {
    try {
      root = await navigator.storage.getDirectory();
    } catch (error) {
      if (
        !(error instanceof DOMException) ||
        ![
          "SecurityError",
          "NotAllowedError",
          "NotSupportedError",
          "UnknownError",
        ].includes(error.name)
      )
        throw error;
      if (!allowMemory)
        throw new Error(
          `Temporary storage is unavailable (${error.name}). Use a browser profile with storage enabled, or a small PNG archive.`,
        );
    }
  }
  if (!root) {
    if (allowMemory) return memoryDestination(name);
    throw new Error(
      "Video export requires file saving or origin-private storage. Use a local Chromium or Safari browser, or export a small PNG archive.",
    );
  }
  const temporary = PREFIX + crypto.randomUUID();
  const release = await reserve(temporary);
  let worker: Worker | undefined,
    closed = false,
    removed = false,
    fatal: Error | null = null,
    id = 0;
  const pending = new Map<
    number,
    {
      resolve(): void;
      reject(error: Error): void;
      timer: ReturnType<typeof setTimeout>;
    }
  >();
  const fail = (error: Error) => {
    fatal = error;
    for (const task of pending.values()) {
      clearTimeout(task.timer);
      task.reject(error);
    }
    pending.clear();
    worker?.terminate();
  };
  const call = (message: object, transfer: Transferable[] = []) =>
    new Promise<void>((resolve, reject) => {
      if (fatal) {
        reject(fatal);
        return;
      }
      const key = ++id;
      const timer = setTimeout(
        () => fail(new Error("Output worker stopped responding")),
        30000,
      );
      pending.set(key, { resolve, reject, timer });
      try {
        worker!.postMessage({ ...message, id: key }, transfer);
      } catch (error) {
        fail(error instanceof Error ? error : new Error(String(error)));
      }
    });
  const remove = async () => {
    if (removed) return;
    try {
      await root!.removeEntry(temporary);
      removed = true;
    } catch (error) {
      if (error instanceof DOMException && error.name === "NotFoundError")
        removed = true;
      else throw error;
    } finally {
      release();
    }
  };
  let handle: FileSystemFileHandle;
  try {
    handle = await root.getFileHandle(temporary, { create: true });
    worker = new Worker(new URL("./sink-worker.ts", import.meta.url), {
      type: "module",
    });
    worker.onmessage = (
      event: MessageEvent<{ id: number; error?: string }>,
    ) => {
      const task = pending.get(event.data.id);
      if (!task) return;
      pending.delete(event.data.id);
      clearTimeout(task.timer);
      if (event.data.error) task.reject(new Error(event.data.error));
      else task.resolve();
    };
    worker.onerror = (event) => fail(new Error(event.message));
    worker.onmessageerror = () =>
      fail(new Error("Output worker message could not be decoded"));
    // Resolve the file by name inside the worker: Safari cannot clone file handles in all versions.
    await call({ type: "open", name: temporary });
  } catch (error) {
    worker?.terminate();
    try {
      await remove();
    } catch (cleanup) {
      throw new AggregateError(
        [error, cleanup],
        "Output failed; a temporary copy may remain in browser site data",
      );
    }
    throw error;
  }
  return {
    async write(data, position) {
      for (let offset = 0; offset < data.length; offset += 4 * 1024 * 1024) {
        const part = data.slice(offset, offset + 4 * 1024 * 1024);
        await call({ type: "write", data: part, position: position + offset }, [
          part.buffer,
        ]);
      }
    },
    async finish() {
      await call({ type: "close" });
      closed = true;
      worker!.terminate();
      return { name, kind: "temporary", blob: await handle.getFile(), remove };
    },
    async abort() {
      try {
        if (!closed && !fatal) await call({ type: "close" });
      } finally {
        closed = true;
        worker!.terminate();
        await remove();
      }
    },
  };
}
