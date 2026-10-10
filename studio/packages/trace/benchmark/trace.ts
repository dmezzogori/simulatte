/** CLI: pnpm --dir studio/packages/trace benchmark [path.simtrace ...] [--iterations N] [--cursors N] */
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { benchmarkTrace } from "./run";

const args = process.argv.slice(2);
const paths: string[] = [];
let iterations = 10;
let cursors = 8;
for (let i = 0; i < args.length; i++) {
  const arg = args[i]!;
  if (arg === "--iterations" || arg === "--cursors") {
    const value = Number(args[++i]);
    if (!Number.isSafeInteger(value) || value < 1) throw new Error(`${arg} needs a positive integer`);
    if (arg === "--iterations") iterations = value;
    else cursors = value;
  } else if (arg.startsWith("--")) {
    throw new Error(`unknown option ${arg}`);
  } else {
    paths.push(arg);
  }
}
if (!paths.length) paths.push("../../../tests/fixtures/traces/generated/shop_small.simtrace");
const results = [];
for (const path of paths) {
  const file = resolve(path);
  const data = await readFile(file);
  results.push({ file, ...await benchmarkTrace(Uint8Array.from(data).buffer, iterations, cursors) });
}
console.log(JSON.stringify({
  runtime: process.version,
  platform: `${process.platform}/${process.arch}`,
  timingUnit: "milliseconds",
  notes: {
    open: "in-memory source; includes metadata decoding and validation; excludes disk I/O",
    decode: "first decompressed chunk (or HEADER); excludes inflate and framing; one runtime warm-up",
    coldSeek: "prepare + stateAt using a fresh reader; excludes open; OS and runtime caches may be warm",
    warmSeek: "prepare + stateAt at the same cursor immediately after cold seek; chunk cache is warm",
  },
  results,
}, null, 2));
