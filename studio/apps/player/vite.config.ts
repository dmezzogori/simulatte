import { defineConfig } from "vite";
export default defineConfig({
  build: {
    target: "es2022",
    outDir: "dist",
    emptyOutDir: true,
    lib: {
      entry: "src/index.ts",
      name: "SimulattePlayer",
      formats: ["iife"],
      fileName: () => "player.js",
    },
    rollupOptions: { output: { inlineDynamicImports: true } },
  },
});
