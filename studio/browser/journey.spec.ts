/** P3 keyboardAndRecoveryJourney: every primary action by keyboard, visible focus, dialog focus,
 * accessible names, no hover-only or unexplained disabled controls, reduced motion, resizing,
 * disconnection, expired authentication, an empty project and a failed model. Assertions, not screenshots. */
import { test, expect, type Page } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { mkdir, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { ProjectFixture } from "./project-fixture";

const MODEL = `from dataclasses import dataclass
from simulatte import Environment, Layout, model
from simulatte.builders import build_immediate_release_system
from simulatte.collectors import ShopFloorKPIs
from simulatte.scenario import Scenario

@dataclass(frozen=True)
class Params:
    servers: int = 3

@model(params=Params, horizon=40)
def build(env, layout, params):
    system = build_immediate_release_system(env=env, scenario=Scenario.pure_job_shop(n_servers=params.servers))
    ShopFloorKPIs(system.shop_floor).attach(env)
`;
const BROKEN = `raise RuntimeError("broken on purpose: the model cannot be imported")\n`;
const FOCUS_OUTLINE = { width: "3px", color: "rgb(69, 169, 157)" };

/** One full interactive run and one completed single-configuration experiment. */
const seed = (fixture: ProjectFixture) =>
  JSON.parse(
    execFileSync(
      fixture.python,
      [
        "-I",
        "-c",
        `
from pathlib import Path
import sys, json
from simulatte.projects import StudioTarget, install_starter, starter_files
from simulatte.projects.service import ProjectService
from simulatte.experiments import load_experiment
root=Path(sys.argv[1]); install_starter(root, starter_files('production'))
(root/'models/production.py').write_text(sys.argv[2])
service=ProjectService.open(StudioTarget(root,None))
try:
 pool=service.environments; pool.activate(pool.use_existing(Path(sys.executable)).id)
 co=service.ensure_coordinator(); model=service.workspace.models[0].id
 prepared=co.prepare_model(model,timeout=60)
 request=co.make_request(prepared,level='full')
 admission=co.admit_run(model,request,prepared.revision); co.wait(admission.key,timeout=90)
 path=root/'quick.experiment.json'
 path.write_text(json.dumps({'schema':1,'name':'Quick study','model':'models/production.py:build','replications':{'count':1},'configurations':{'grid':{'servers':[2]}},'level':'kpi'}))
 run=co.contexts[model].start_experiment(load_experiment(path))
 outcome=co.wait_experiment(run,timeout=90)
 print(json.dumps({'run':run,'attempt':admission.key.attempt_id,'status':outcome['status'],'model':model}))
finally: service.close()
`,
        fixture.root,
        MODEL,
      ],
      { encoding: "utf8" },
    ).trim(),
  ) as { run: string; attempt: string; status: string; model: string };

const focused = (page: Page) =>
  page.evaluate(() => {
    const el = document.activeElement as HTMLElement | null;
    if (!el) return { name: "", tag: "", outline: { width: "", color: "", style: "" }, inDialog: false };
    const style = getComputedStyle(el);
    const name =
      el.getAttribute("aria-label") ??
      (el instanceof HTMLInputElement || el instanceof HTMLSelectElement || el instanceof HTMLTextAreaElement
        ? Array.from(el.labels ?? []).map((l) => l.textContent?.trim()).join(" ")
        : (el.textContent?.trim() ?? ""));
    return {
      name,
      tag: el.tagName.toLowerCase(),
      outline: { width: style.outlineWidth, color: style.outlineColor, style: style.outlineStyle },
      inDialog: el.closest("dialog[open]") !== null,
    };
  });
/** Press Tab (or Shift+Tab) until the focused control's accessible name matches; links need Alt+Tab in WebKit.
 * The sidebar precedes the content, so its links are reached backwards from wherever a page left the focus. */
async function tabTo(page: Page, browserName: string, name: RegExp, { backward = false, limit = backward ? 160 : 60 } = {}) {
  const key = (browserName === "webkit" ? "Alt+" : "") + (backward ? "Shift+Tab" : "Tab");
  for (let i = 0; i < limit; i++) {
    await page.keyboard.press(key);
    const current = await focused(page);
    if (name.test(current.name)) return current;
  }
  throw new Error(`No control named ${name} reachable by ${key}`);
}
/** Audit the visible workspace: accessible names, hover-independent visibility and explained disabled controls. */
const audit = (page: Page) =>
  page.evaluate(() => {
    const visible = (el: HTMLElement) =>
      el.closest("[hidden]") === null &&
      el.closest("details:not([open]) > :not(summary)") === null &&
      el.getClientRects().length > 0;
    const unnamed: string[] = [],
      hoverOnly: string[] = [],
      unexplained: string[] = [];
    const scope = document.querySelector(".ws-body") ?? document.body;
    for (const el of Array.from(scope.querySelectorAll<HTMLElement>("button, a[href], input, select, textarea, summary"))) {
      if (!visible(el)) continue;
      const labelled =
        el.getAttribute("aria-label") ||
        el.getAttribute("aria-labelledby") ||
        (el instanceof HTMLInputElement || el instanceof HTMLSelectElement || el instanceof HTMLTextAreaElement
          ? (el.labels?.length ?? 0) > 0 || el.type === "checkbox" || el.type === "radio"
          : (el.textContent?.trim() ?? "").length > 0);
      if (!labelled) unnamed.push(el.outerHTML.slice(0, 80));
      const style = getComputedStyle(el);
      if (style.opacity === "0" || style.visibility === "hidden") hoverOnly.push(el.outerHTML.slice(0, 80));
      if (el instanceof HTMLButtonElement && el.disabled && !(el.title || el.getAttribute("aria-describedby")))
        unexplained.push(el.outerHTML.slice(0, 80));
    }
    const blank = Array.from(document.querySelectorAll<HTMLElement>(".ws-content > *")).length === 0;
    return { unnamed, hoverOnly, unexplained, blank };
  });
const overflow = (page: Page) =>
  page.evaluate(() => {
    const content = document.querySelector<HTMLElement>(".ws-content")!;
    return {
      document: document.documentElement.scrollWidth - window.innerWidth,
      content: content.scrollWidth - content.clientWidth,
      sidebar: document.querySelector<HTMLElement>(".ws-sidebar")!.getBoundingClientRect().width,
    };
  });

test("keyboardAndRecoveryJourney", async ({ page, browserName, context }) => {
  test.setTimeout(300000);
  const fixture = new ProjectFixture();
  const errors: string[] = [], network: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (message) => {
    // The disconnection and expiry steps make the browser log refused connections and 403s on purpose.
    if (message.type() === "error") (/WebSocket|Failed to load resource|ERR_|Forbidden|403|Could not connect|establish a connection|network/i.test(message.text()) ? network : errors).push(message.text());
  });
  const clean = async (label: string) => {
    const report = await audit(page);
    expect(report, label).toEqual({ unnamed: [], hoverOnly: [], unexplained: [], blank: false });
  };
  try {
    await fixture.setup();
    // Empty project: a guided first action, never empty charts.
    const empty = join(fixture.directory, "empty");
    await mkdir(empty);
    await page.goto(await fixture.launch(empty));
    await page.waitForURL((url) => url.pathname === "/");
    await expect(page.getByRole("heading", { name: "Start with a model" })).toBeVisible();
    await expect(page.getByRole("button", { name: /^Production/ })).toBeEnabled();
    await expect(page.getByRole("link", { name: "Register an existing Python model →" })).toBeVisible();
    await clean("empty project");
    await fixture.stop();
    // Seeded project: keyboard-only journey through the primary actions.
    const seeded = seed(fixture);
    expect(seeded.status).toBe("completed");
    await page.addInitScript(() => {
      const Native = window.WebSocket;
      let socket: WebSocket | undefined;
      window.WebSocket = class extends Native {
        constructor(url: string | URL, protocols?: string | string[]) {
          super(url, protocols);
          socket = this;
        }
      };
      Object.assign(window, { journeyDisconnect: () => socket?.close() });
    });
    await page.goto(await fixture.launch());
    await page.waitForURL((url) => url.pathname === "/");
    const origin = new URL(page.url()).origin;
    await page.setViewportSize({ width: 1440, height: 900 });
    await expect(page.getByRole("heading", { name: "Recent runs" })).toBeVisible();
    await clean("overview");
    await page.keyboard.press(browserName === "webkit" ? "Alt+Tab" : "Tab");
    expect((await focused(page)).name).toBe("Skip to content");
    await page.keyboard.press("Enter");
    await expect(page.locator("#workspace-content")).toBeFocused();
    const card = await tabTo(page, browserName, /Configure and run/);
    expect(card.outline.style).toBe("solid");
    expect(card.outline.width).toBe(FOCUS_OUTLINE.width);
    expect(card.outline.color).toBe(FOCUS_OUTLINE.color);
    await page.keyboard.press("Enter");
    await expect(page.getByRole("heading", { name: "Production", exact: true })).toBeVisible();
    expect(await page.evaluate(() => location.hash)).toBe("#/model/" + seeded.model);
    // A reopened project inspects the model again before it can run; that is a keyboard action too.
    await expect(page.getByRole("button", { name: /^(Prepare model|Run)$/ }).first()).toBeVisible({ timeout: 30000 });
    if (await page.getByRole("button", { name: "Prepare model", exact: true }).isVisible()) {
      await clean("model page before preparation");
      await tabTo(page, browserName, /^Prepare model$/);
      await page.keyboard.press("Enter");
    }
    await expect(page.getByRole("button", { name: "Run", exact: true })).toBeVisible({ timeout: 60000 });
    await clean("model page");
    const seedField = await tabTo(page, browserName, /^Seed$/);
    expect(seedField.outline.width).toBe(FOCUS_OUTLINE.width);
    await page.keyboard.press("ControlOrMeta+a");
    await page.keyboard.type("5");
    await tabTo(page, browserName, /^Run$/);
    await page.keyboard.press("Enter");
    await expect(page.locator(".ws-page-heading .ws-badge")).toHaveText("succeeded", { timeout: 60000 });
    await expect(page.locator(".ws-recording .status")).toHaveText("paused", { timeout: 30000 });
    const runRoute = await page.evaluate(() => location.hash);
    await clean("run page");
    await tabTo(page, browserName, /^Runs$/, { backward: true });
    await page.keyboard.press("Enter");
    await expect(page.getByRole("region", { name: "Interactive runs" })).toBeVisible();
    await expect(page.getByText("All 3 saved runs loaded.")).toBeVisible();
    await clean("runs page");
    await tabTo(page, browserName, /^Edit notes of/);
    await page.keyboard.press("Space");
    await expect(page.getByRole("form", { name: "Run notes" })).toBeVisible();
    await tabTo(page, browserName, /^Run name$/);
    await page.keyboard.type("Keyboard run");
    await tabTo(page, browserName, /^Save notes$/);
    await page.keyboard.press("Enter");
    await expect(page.getByRole("link", { name: "Keyboard run" })).toBeVisible();
    await tabTo(page, browserName, /^Experiments$/, { backward: true });
    await page.keyboard.press("Enter");
    await expect(page.getByRole("heading", { name: "Experiment runs" })).toBeVisible();
    await clean("experiments page");
    await tabTo(page, browserName, /^Quick study$/);
    await page.keyboard.press("Enter");
    await expect(page.locator(".run-details h4")).toContainText("Quick study · completed");
    await clean("experiment detail");
    // The delete confirmation is a modal dialog: focus stays inside, Escape cancels and focus returns.
    await tabTo(page, browserName, /^Delete$/);
    await page.keyboard.press("Enter");
    const dialog = page.locator("dialog[open]");
    await expect(dialog).toBeVisible();
    await expect(dialog).toContainText("cannot be undone");
    expect((await focused(page)).inDialog).toBe(true);
    for (let i = 0; i < 4; i++) {
      await page.keyboard.press(browserName === "webkit" ? "Alt+Tab" : "Tab");
      expect((await focused(page)).inDialog, "focus stays inside the dialog").toBe(true);
    }
    await page.keyboard.press("Escape");
    await expect(dialog).toHaveCount(0);
    expect((await focused(page)).name).toBe("Delete");
    await expect(page.locator(".run-details h4")).toContainText("Quick study · completed");
    // The comparison link precedes the action buttons in the heading, so it is two Shift+Tabs away.
    await tabTo(page, browserName, /^Compare numerically/, { backward: true });
    await page.keyboard.press("Enter");
    await expect(page.getByRole("region", { name: "Comparison of Quick study" })).toBeVisible();
    await clean("comparison page");
    // Reduced motion: no transitions or animations once the preference is set.
    await page.emulateMedia({ reducedMotion: "reduce" });
    expect(
      await page.evaluate(() => {
        const link = document.querySelector<HTMLElement>(".ws-sidebar nav a")!;
        const button = document.querySelector<HTMLElement>(".ws-content button, .ws-content a[href]")!;
        return [getComputedStyle(link).transitionDuration, getComputedStyle(button).transitionDuration, getComputedStyle(link).animationName];
      }),
    ).toEqual(["0s", "0s", "none"]);
    await page.emulateMedia({ reducedMotion: "no-preference" });
    expect(await page.evaluate(() => getComputedStyle(document.querySelector(".ws-sidebar nav a")!).transitionDuration)).not.toBe("0s");
    // Resizing: nothing overflows horizontally and the sidebar narrows at 1024 px.
    const widths: number[] = [];
    for (const [width, height] of [[1440, 900], [1280, 800], [1024, 768]] as const) {
      await page.setViewportSize({ width, height });
      const measure = await overflow(page);
      expect(measure.document, `${width}px document overflow`).toBeLessThanOrEqual(0);
      expect(measure.content, `${width}px content overflow`).toBeLessThanOrEqual(1);
      widths.push(measure.sidebar);
    }
    expect(widths[2]!).toBeLessThan(widths[0]!);
    await page.setViewportSize({ width: 1440, height: 900 });
    // Failed model: the readiness failure names the problem; saved results stay viewable.
    await writeFile(join(fixture.root, "models/production.py"), BROKEN);
    await page.goto(origin + "/#/model/" + seeded.model);
    await page.getByText("Automatic runs and model preparation", { exact: true }).click();
    await page.getByRole("button", { name: "Refresh model metadata", exact: true }).click();
    const failure = page.locator(".ws-failure");
    await expect(failure.first()).toContainText("broken on purpose", { timeout: 60000 });
    await expect(failure.first()).toHaveAttribute("data-kind", "model");
    await expect(page.getByRole("heading", { name: "Production", exact: true })).toBeVisible();
    await clean("failed model");
    await page.goto(origin + "/" + runRoute);
    await expect(page.locator(".ws-recording .status")).toHaveText("paused", { timeout: 30000 });
    // Transient disconnection: the designed notice, the page stays, the route survives the reconnect.
    await page.evaluate(() => (window as unknown as { journeyDisconnect(): void }).journeyDisconnect());
    await expect(page.locator(".ws-notice")).toContainText("Reconnecting. Drafts are retained");
    await expect(page.locator(".ws-connection")).toHaveText("connected", { timeout: 30000 });
    await expect(page.locator(".ws-notice")).toHaveCount(0);
    expect(await page.evaluate(() => location.hash)).toBe(runRoute);
    await expect(page.locator(".ws-page-heading h1")).toBeVisible();
    await clean("reconnected run page");
    // Server stopped: the page being read is preserved behind the reconnecting notice.
    await fixture.stop();
    await expect(page.locator(".ws-notice")).toContainText("Reconnecting", { timeout: 30000 });
    await expect(page.locator(".ws-connection")).toHaveText("reconnecting");
    await expect(page.locator(".ws-page-heading h1")).toBeVisible();
    await expect(page.getByRole("heading", { name: "Keep context with this run" })).toBeVisible();
    await clean("disconnected run page");
    // Relaunch: a new serving link opens the workspace and the bookmarked route still resolves.
    const link = await fixture.launch();
    await page.goto(link);
    await page.waitForURL((url) => url.pathname === "/");
    await page.evaluate((hash) => { location.hash = hash; }, runRoute);
    await expect(page.locator(".ws-page-heading .ws-badge")).toHaveText("succeeded", { timeout: 30000 });
    await expect(page.locator(".ws-connection")).toHaveText("connected");
    // Expired authentication: the session cookie is gone, the designed screen appears, nothing goes blank.
    await context.clearCookies();
    await page.evaluate(() => (window as unknown as { journeyDisconnect(): void }).journeyDisconnect());
    await expect(page.locator(".ws-connection")).toHaveText("authentication_required", { timeout: 30000 });
    await expect(page.locator(".ws-notice")).toContainText("Open a fresh link from the serving terminal");
    await expect(page.locator(".ws-page-heading h1")).toBeVisible();
    await clean("expired authentication");
    // Engines differ in whether refused reconnects reach the console; only page errors fail the journey.
    expect(errors).toEqual([]);
  } finally {
    await fixture.close();
  }
});
