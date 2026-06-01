// In-browser execution controller. Finds tagged code blocks, injects a Run
// button + output panel, and drives a single per-page Pyodide worker.
// Marker locked in Task 2:
//   markdown: ```python { .run }
//   RUN_SELECTOR (DOM): .highlight.run   (confirmed in Task 2)
(function () {
  "use strict";

  const RUN_SELECTOR = ".highlight.run";
  const WORKER_PATH = "/assets/javascripts/pyodide-worker.js";
  const WHEELS_DIR = "/assets/wheels/"; // holds the real wheel + latest.json

  let worker = null;
  let configReady = null; // resolves once {type:"config"} has been posted
  let runChain = Promise.resolve(); // serialize runs across the page
  let counter = 0;
  const handlers = new Map(); // id -> { status, append, error, done }

  // Discover the REAL wheel filename (must not be renamed — micropip parses it
  // as a PEP 427 name) and tell the worker before any run is dispatched.
  function postConfig() {
    return fetch(new URL(WHEELS_DIR + "latest.json", location.origin))
      .then((r) => {
        if (!r.ok) throw new Error("latest.json " + r.status);
        return r.json();
      })
      .then(({ wheel }) => {
        worker.postMessage({
          type: "config",
          wheelUrl: new URL(WHEELS_DIR + wheel, location.origin).href,
        });
      });
  }

  function ensureWorker() {
    if (!worker) {
      worker = new Worker(new URL(WORKER_PATH, location.origin));
      worker.onmessage = (event) => {
        const m = event.data;
        const h = handlers.get(m.id);
        if (!h) return;
        if (m.kind === "status") h.status(m.text);
        else if (m.kind === "stdout") h.append(m.text, "stdout");
        else if (m.kind === "stderr") h.append(m.text, "stderr");
        else if (m.kind === "error") h.error(m.text);
        else if (m.kind === "done") h.done();
      };
    }
    // Rebuilt on demand so a transient latest.json fetch failure can recover.
    if (!configReady) configReady = postConfig();
    return worker;
  }

  function runSource(source, ui) {
    const id = ++counter;
    runChain = runChain.then(async () => {
      ui.reset();
      ui.setBusy(true);
      const w = ensureWorker();
      try {
        await configReady; // wheel URL resolved + config posted
      } catch (e) {
        configReady = null; // allow a later click to re-fetch latest.json
        ui.append("Couldn't locate the simulatte package. Try again.", "error");
        ui.setBusy(false);
        return;
      }
      return new Promise((resolve) => {
        handlers.set(id, {
          status: (t) => ui.status(t),
          append: (t, cls) => ui.append(t, cls),
          error: (t) => ui.append(t, "error"),
          done: () => {
            handlers.delete(id);
            ui.status("");
            ui.setBusy(false);
            resolve();
          },
        });
        w.postMessage({ type: "run", id, source });
      });
    });
    return runChain;
  }

  function buildUI(container, source) {
    const wrap = document.createElement("div");
    wrap.className = "sim-run";

    const bar = document.createElement("div");
    bar.className = "sim-run__bar";

    const button = document.createElement("button");
    button.type = "button";
    button.className = "sim-run__btn md-button";
    button.textContent = "▶ Run";

    const status = document.createElement("span");
    status.className = "sim-run__status";

    const output = document.createElement("pre");
    output.className = "sim-run__output";

    bar.appendChild(button);
    bar.appendChild(status);
    wrap.appendChild(bar);
    wrap.appendChild(output);
    container.insertAdjacentElement("afterend", wrap);

    const ui = {
      reset() {
        output.textContent = "";
        output.classList.remove("sim-run__output--visible");
      },
      setBusy(busy) {
        button.disabled = busy;
        button.textContent = busy ? "… Running" : "▶ Run";
      },
      status(text) {
        status.textContent = text || "";
      },
      append(text, cls) {
        output.classList.add("sim-run__output--visible");
        const span = document.createElement("span");
        span.className = "sim-run__line sim-run__line--" + cls;
        span.textContent = text;
        output.appendChild(span);
      },
    };

    button.addEventListener("click", () => runSource(source, ui));
  }

  function init() {
    document.querySelectorAll(RUN_SELECTOR).forEach((container) => {
      if (container.dataset.simRun === "1") return; // idempotent under instant nav
      const code = container.querySelector("pre > code") || container.querySelector("code");
      if (!code) return;
      container.dataset.simRun = "1";
      buildUI(container, code.textContent);
    });
  }

  // Material/Zensical 'navigation.instant' replaces page content via a
  // `document$` observable instead of full reloads. Use it when present.
  // (Bracket access: `document$` is a runtime global not in the Window type.)
  const docStream = window["document$"];
  if (docStream && typeof docStream.subscribe === "function") {
    docStream.subscribe(init);
  } else {
    document.addEventListener("DOMContentLoaded", init);
  }
})();
