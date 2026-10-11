import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
export default defineConfig({
  plugins: [react({ babel: { plugins: ["babel-plugin-react-compiler"] } })],
  base: "./",
  build: { target: "es2022", outDir: "dist", emptyOutDir: true },
  server: { host: "127.0.0.1" },
});
