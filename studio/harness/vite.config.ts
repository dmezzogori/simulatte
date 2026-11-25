// A plain object: `vite` does not resolve from the studio/ root, so this file imports nothing from it.
// Run it as `pnpm --filter @simulatte/scene exec vite build --config <absolute path to this file>`.
import { fileURLToPath } from "node:url";

const here = fileURLToPath(new URL(".", import.meta.url)).replace(/\/$/, "");
const studio = fileURLToPath(new URL("..", import.meta.url)).replace(/\/$/, "");
const source = (name: string) => `${studio}/packages/${name}/src/index.ts`;

export default {
  root: here,
  base: "./",
  build: { outDir: `${studio}/.build/harness`, emptyOutDir: true, target: "es2022" },
  resolve: {
    alias: {
      "@simulatte/trace": source("trace"),
      "@simulatte/playback": source("playback"),
      "@simulatte/scene": source("scene"),
    },
  },
};
