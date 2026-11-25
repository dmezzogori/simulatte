import { defineConfig, devices } from "@playwright/test";
import { resolve } from "node:path";

// The absolute path `vite` needs, from this file's directory (studio/) whatever the current directory. Playwright
// loads this config as CommonJS (studio/package.json has no "type": "module"), so `__dirname` is defined.
const harness = resolve(__dirname, "harness/vite.config.ts");
const scene = "pnpm --filter @simulatte/scene exec vite";
export default defineConfig({
  testDir: "browser",
  fullyParallel: false,
  timeout: 60000,
  workers: 1,
  use: {
    baseURL: "http://127.0.0.1:4173",
    viewport: { width: 1440, height: 1000 },
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
    { name: "firefox", use: { ...devices["Desktop Firefox"] } },
    { name: "webkit", use: { ...devices["Desktop Safari"] } },
  ],
  webServer: [
    {
      command:
        "pnpm --filter @simulatte/viewer exec vite preview --host 127.0.0.1 --port 4173 --strictPort",
      url: "http://127.0.0.1:4173",
      reuseExistingServer: false,
    },
    {
      // The map harness (harness/): built to .build/harness (git-ignored) and served on its own port. `vite` does
      // not resolve from the studio/ root, so it runs as the scene package's, with an absolute config path.
      command: `${scene} build --config "${harness}" && ${scene} preview --host 127.0.0.1 --port 4174 --strictPort --config "${harness}"`,
      url: "http://127.0.0.1:4174",
      reuseExistingServer: false,
      timeout: 120000,
    },
  ],
});
