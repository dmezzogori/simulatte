// Executed only after both production builds succeed. Packaging validates this snapshot.
import {readdir, readFile, writeFile, mkdir} from "node:fs/promises";
import {createHash} from "node:crypto";
import {resolve, relative, join} from "node:path";
const root = resolve(import.meta.dirname, "../..");
const excluded = new Set(["node_modules", "dist", "test-results", "playwright-report", ".vite", ".build"]);
async function files(directory) {
  const result = [];
  for (const entry of await readdir(directory, {withFileTypes: true})) {
    if (excluded.has(entry.name)) continue;
    const path = join(directory, entry.name);
    if (entry.isDirectory()) result.push(...await files(path)); else if (entry.isFile()) result.push(path);
  }
  return result;
}
const digest = createHash("sha256");
// JSON schemas outside studio/ that the frontend imports; hatch_build.py lists the same files.
const schemas = ["layout/schema/layout-v1.schema.json", "experiments/schema/experiment-v1.schema.json"];
const inputs = [...schemas.map(name => join(root, "src/simulatte", name)), ...await files(join(root, "studio"))];
for (const path of inputs.sort()) {
  digest.update(relative(root,path).split("\\").join("/")+"\0");
  digest.update(createHash("sha256").update(await readFile(path)).digest());
}
const fingerprint = digest.digest("hex");
const cache = join(root,"studio/.build");
if (process.argv[2] === "begin") {
  await mkdir(cache,{recursive:true});
  await writeFile(join(cache,"source.json"), JSON.stringify({source_fingerprint:fingerprint}));
} else {
  const before=JSON.parse(await readFile(join(cache,"source.json"),"utf8"));
  if(before.source_fingerprint!==fingerprint) throw new Error("Frontend inputs changed during the build; run the build again");
  await writeFile(join(root,"studio/apps/viewer/dist/build-stamp.json"), JSON.stringify({source_fingerprint:fingerprint})+"\n");
}
