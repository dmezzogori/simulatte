// Dedicated worker: one request is acknowledged only after the positioned write completes.
interface SyncHandle {
  write(data: Uint8Array, options: { at: number }): number;
  flush(): void;
  close(): void;
}
let handle: SyncHandle | null = null;
self.onmessage = async (event: MessageEvent) => {
  const { id, type, data, position } = event.data;
  try {
    if (type === "open") {
      const root = await navigator.storage.getDirectory();
      const file = await root.getFileHandle(event.data.name);
      handle = await (
        file as unknown as { createSyncAccessHandle(): Promise<SyncHandle> }
      ).createSyncAccessHandle();
    } else if (type === "write") {
      if (!handle) throw new Error("Output is closed");
      let offset = 0;
      while (offset < data.length) {
        const written = handle.write(data.subarray(offset), {
          at: position + offset,
        });
        if (!written) throw new Error("Storage write made no progress");
        offset += written;
      }
    } else if (type === "close") {
      try {
        handle?.flush();
      } finally {
        handle?.close();
        handle = null;
      }
    }
    self.postMessage({ id });
  } catch (error) {
    self.postMessage({ id, error: String(error) });
  }
};
export {};
