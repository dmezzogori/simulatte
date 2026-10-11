import { createRequire } from "node:module";
import { open } from "node:fs/promises";
const require = createRequire(
  new URL("../packages/export/package.json", import.meta.url),
);
const { ZipWriter, Uint8ArrayReader } = await import(
  require.resolve("@zip.js/zip.js")
);
const filename = process.argv[2] ?? "/tmp/sp3-zip64.zip";
const file = await open(filename, "w");
let position = 0,
  peak = 0;
const sample = () => {
  peak = Math.max(peak, process.memoryUsage().rss);
};
const zip = new ZipWriter(
  new WritableStream({
    async write(data) {
      // A sparse filesystem output keeps the >4 GiB fixture inexpensive without changing zip.js offsets/CRC.
      if (data.some((byte) => byte !== 0))
        await file.write(data, 0, data.length, position);
      position += data.length;
      sample();
    },
  }),
  { zip64: true, level: 0, bufferedWrite: false, useWebWorkers: false },
);
const started = performance.now();
for (let i = 0; i < 100000; i++) {
  await zip.add(
    `frame-${String(i).padStart(6, "0")}.png`,
    new Uint8ArrayReader(new Uint8Array()),
  );
  if (i % 10000 === 0)
    console.log(
      JSON.stringify({
        entries: i,
        rss: process.memoryUsage().rss,
        heap: process.memoryUsage().heapUsed,
      }),
    );
}
let remaining = 2 ** 32 + 1024;
const zero = new Uint8Array(1024 * 1024);
await zip.add(
  "large-offset.bin",
  new ReadableStream({
    pull(controller) {
      if (!remaining) {
        controller.close();
        return;
      }
      const n = Math.min(remaining, zero.length);
      controller.enqueue(zero.subarray(0, n));
      remaining -= n;
    },
  }),
);
await zip.close();
await file.truncate(position);
await file.close();
console.log(
  JSON.stringify({
    entries: 100001,
    bytes: position,
    rssPeak: peak,
    seconds: (performance.now() - started) / 1000,
  }),
);
