import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";
const here = (relative: string) => fileURLToPath(new URL(relative, import.meta.url));
const FRAME_SCRIPT = here("./frame/plotly-frame.ts");
const FRAME_DOCUMENT = here("./frame/plotly-frame.html");
/**
 * Emit the sandboxed figure frame as ``plotly-frame.html`` with a classic ``<script src>``.
 *
 * The frame runs with an opaque origin (``sandbox="allow-scripts"``), so a module script would need
 * CORS headers the Studio server never sends; a classic script under ``script-src 'self'`` loads.
 * The script chunk must therefore be self-contained: no imports, exports or ``import.meta``.
 */
function plotlyFrame(): Plugin {
  return {
    name: "simulatte-plotly-frame",
    generateBundle(_options, bundle) {
      const chunk = Object.values(bundle).find(
        (item) => item.type === "chunk" && item.isEntry && item.facadeModuleId === FRAME_SCRIPT,
      );
      if (!chunk || chunk.type !== "chunk") throw new Error("The plotly-frame entry chunk is missing");
      if (chunk.imports.length || chunk.dynamicImports.length || chunk.exports.length || chunk.code.includes("import.meta"))
        throw new Error("plotly-frame.js must be self-contained: no imports, exports or import.meta");
      const template = readFileSync(FRAME_DOCUMENT, "utf8");
      if (!template.includes('<script src="%FRAME_SCRIPT%"></script>') || /https?:\/\//.test(template))
        throw new Error("plotly-frame.html must reference only the placeholder script and no URL");
      this.emitFile({
        type: "asset",
        fileName: "plotly-frame.html",
        source: template.replace("%FRAME_SCRIPT%", "./" + chunk.fileName),
      });
    },
  };
}
export default defineConfig({
  plugins: [react({ babel: { plugins: ["babel-plugin-react-compiler"] } }), plotlyFrame()],
  base: "./",
  build: {
    target: "es2022",
    outDir: "dist",
    emptyOutDir: true,
    rollupOptions: { input: { index: here("./index.html"), "plotly-frame": FRAME_SCRIPT } },
  },
  server: { host: "127.0.0.1" },
});
