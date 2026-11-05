/** vite build --ssr benchmark/charts.ts; node <out>/charts.js serial|batch <recordings...> */
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { basename } from "node:path";
import { openRecording, type Sample } from "../src";

const [mode, ...files] = process.argv.slice(2);
if ((mode !== "serial" && mode !== "batch") || !files.length)
  throw new Error("Expected serial|batch and at least one recording");

function digest(charts: readonly (readonly Sample[])[]): string {
  return createHash("sha256").update(JSON.stringify(charts, (_key, value) =>
    typeof value !== "number" ? value
      : Object.is(value, -0) ? "-0"
      : Number.isFinite(value) ? value : String(value),
  )).digest("hex");
}

const rows = [];
for (const file of files) {
  const data = await readFile(file);
  let reads = 0, bytes = 0;
  const recording = await openRecording({
    size: data.length,
    async read(offset, length) {
      const part = data.subarray(offset, offset + length);
      reads++;
      bytes += part.length;
      return part;
    },
  });
  const range = recording.finiteRange;
  if (!range) throw new Error("Recording has no finite chart range");
  for (const count of [1, 2, 4, 8].filter(n => n <= recording.seriesKeys.length)) {
    const keys = recording.seriesKeys.slice(0, count);
    const elapsed = [];
    let charts: readonly (readonly Sample[])[] = [];
    // Warm up each selection once. Timing excludes opening the recording.
    for (let i = 0; i < 6; i++) {
      reads = bytes = 0;
      const start = performance.now();
      if (mode === "batch") charts = await recording.charts(keys, range);
      else {
        const single = [];
        for (const key of keys) single.push(await recording.chart(key, range));
        charts = single;
      }
      if (i) elapsed.push(performance.now() - start);
    }
    rows.push({
      file: basename(file), trace_sha256: createHash("sha256").update(data).digest("hex"),
      keys, reads, bytes, point_counts: charts.map(points => points.length),
      points_sha256: digest(charts), median_ms: elapsed.sort((a, b) => a - b)[2],
    });
  }
}
console.log(JSON.stringify({mode, runtime: process.version, platform: process.platform, rows}, null, 2));
