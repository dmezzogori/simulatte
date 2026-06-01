// Execute every `.run`-tagged docs block under Pyodide against the freshly
// built simulatte wheel. Exits non-zero if any block raises.
import { loadPyodide } from "pyodide";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, resolve } from "node:path";
import { extractRunnableBlocks } from "./extract-runnable.mjs";

const REPO = resolve(import.meta.dirname, "../..");
const DOCS = join(REPO, "docs");
const WHEELS_DIR = join(DOCS, "assets/wheels");

// The wheel keeps its real PEP 427 name; find it (don't hardcode the version).
const wheelName = readdirSync(WHEELS_DIR).find((f) => /^simulatte-.*\.whl$/.test(f));
if (!wheelName) {
  console.error("No simulatte-*.whl in docs/assets/wheels — run scripts/build_docs_wheel.sh first.");
  process.exit(1);
}
const WHEEL = join(WHEELS_DIR, wheelName);

function walk(dir) {
  const out = [];
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) out.push(...walk(p));
    else if (name.endsWith(".md")) out.push(p);
  }
  return out;
}

const blocks = [];
for (const file of walk(DOCS)) {
  for (const src of extractRunnableBlocks(readFileSync(file, "utf8"))) {
    blocks.push({ file: file.replace(REPO + "/", ""), src });
  }
}
console.log(`Found ${blocks.length} runnable block(s).`);
if (blocks.length === 0) process.exit(0);

const pyodide = await loadPyodide();
await pyodide.loadPackage(["sqlite3", "micropip"]);
const micropip = pyodide.pyimport("micropip");
// Load the locally built wheel into the Pyodide FS and install from there.
// (emfs install of a correctly-named wheel is verified to work on 0.28.3.)
pyodide.FS.writeFile("/" + wheelName, readFileSync(WHEEL));
await micropip.install("emfs:/" + wheelName);

let failures = 0;
for (const { file, src } of blocks) {
  pyodide.setStdout({ batched: () => {} });
  pyodide.setStderr({ batched: () => {} });
  try {
    const ns = pyodide.toPy({ __name__: "__main__" });
    await pyodide.runPythonAsync(src, { globals: ns });
    ns.destroy();
    console.log(`PASS ${file}`);
  } catch (err) {
    failures++;
    console.error(`FAIL ${file}\n${(err && err.message) || err}\n`);
  }
}
console.log(`\n${blocks.length - failures}/${blocks.length} passed.`);
process.exit(failures ? 1 : 0);
