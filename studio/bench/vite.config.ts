import { defineConfig } from "vite";
export default defineConfig({
  root: "bench",
  base: "./",
  build: { outDir: "dist", emptyOutDir: true, target: "es2022" },
  resolve: {
    alias: {
      "@simulatte/trace": new URL(
        "../packages/trace/src/index.ts",
        import.meta.url,
      ).pathname,
      "@simulatte/playback": new URL(
        "../packages/playback/src/index.ts",
        import.meta.url,
      ).pathname,
      "@simulatte/scene": new URL(
        "../packages/scene/src/index.ts",
        import.meta.url,
      ).pathname,
      "@simulatte/export": new URL(
        "../packages/export/src/index.ts",
        import.meta.url,
      ).pathname,
    },
  },
});
