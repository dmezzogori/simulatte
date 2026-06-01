// Pyodide host worker. One instance per page; reused across Run clicks.
// Protocol (from controller):
//   { type: "config", wheelUrl }   -> set the simulatte wheel URL (once)
//   { type: "run", id, source }    -> execute `source`, tagging replies with `id`
// Protocol (to controller), all carrying the run `id`:
//   { id, kind: "status", text }   -> boot/progress message
//   { id, kind: "stdout"|"stderr", text }
//   { id, kind: "error", text }    -> python traceback or fatal load failure
//   { id, kind: "done" }           -> run finished (success or error)

const PYODIDE_VERSION = "0.28.3";
const PYODIDE_CDN = `https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/`;

importScripts(`${PYODIDE_CDN}pyodide.js`);

let pyodidePromise = null; // memoized boot
let wheelUrl = null;

async function bootPyodide(id) {
  const status = (text) => self.postMessage({ id, kind: "status", text });
  status("Downloading Python runtime…");
  const pyodide = await loadPyodide({ indexURL: PYODIDE_CDN });
  // sqlite3 is unvendored in Pyodide and is imported at module load by
  // simulatte/logger.py (which environment.py imports). Without it nothing imports.
  status("Loading numpy / matplotlib…");
  await pyodide.loadPackage(["sqlite3", "micropip"]);
  status("Installing simulatte…");
  const micropip = pyodide.pyimport("micropip");
  await micropip.install(wheelUrl);
  return pyodide;
}

self.onmessage = async (event) => {
  const data = event.data;
  if (data.type === "config") {
    wheelUrl = data.wheelUrl;
    return;
  }
  if (data.type !== "run") return;

  const { id, source } = data;
  let pyodide;
  try {
    if (!pyodidePromise) pyodidePromise = bootPyodide(id);
    pyodide = await pyodidePromise;
  } catch (err) {
    pyodidePromise = null; // allow a later retry
    self.postMessage({
      id,
      kind: "error",
      text: "Couldn't load the Python runtime. Check your connection and try again.",
    });
    self.postMessage({ id, kind: "done" });
    return;
  }

  pyodide.setStdout({ batched: (text) => self.postMessage({ id, kind: "stdout", text }) });
  pyodide.setStderr({ batched: (text) => self.postMessage({ id, kind: "stderr", text }) });

  let namespace;
  try {
    namespace = pyodide.toPy({ __name__: "__main__" }); // fresh, isolated per run
    await pyodide.runPythonAsync(source, { globals: namespace });
    self.postMessage({ id, kind: "done" });
  } catch (err) {
    self.postMessage({ id, kind: "error", text: String((err && err.message) || err) });
    self.postMessage({ id, kind: "done" });
  } finally {
    if (namespace) namespace.destroy();
  }
};
