import { test, expect, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import {createHash} from "node:crypto";
import { resolve } from "node:path";
import type { ExperimentReport, ExperimentRun } from "../apps/viewer/src/studio-client";

// Studio mode against `vite preview`: a mocked control socket announces three committed results whose
// prefixes serve the fixture recordings through the authenticated range protocol of HttpSource.
const fixtures = resolve("../tests/fixtures/traces/generated");
const secret = "compare-secret";
const recordings = {
  a: readFileSync(resolve(fixtures, "shop_small.simtrace")),
  b: readFileSync(resolve(fixtures, "lumscor.simtrace")),
  c: readFileSync(resolve(fixtures, "layout_kpi.simtrace")),
  e: readFileSync(resolve(fixtures, "layout_full.simtrace")), // The full replay of c.
};
type Id = keyof typeof recordings;
const attempt = (id: Id) => id.repeat(32);
const result = (id: Id, revision: number) => ({
  key: { request_id: id.repeat(64), attempt_id: attempt(id), generation: 1 },
  revision,
  created: 0,
  trace: prefix(id),
});
const prefix = (id: Id) => ({ attempt: attempt(id), generation: 1, length: recordings[id].length, sha256: "0".repeat(64), url: `/api/prefix/${attempt(id)}/1` });
const replayed = { request_id: "e".repeat(64), original: "c".repeat(64), key: { request_id: "e".repeat(64), attempt_id: attempt("e"), generation: 1 } };
const state = {
  revision: { number: 1, digest: "d".repeat(64) },
  inputs: null,
  startup: { phase: "ready", error: null },
  active: null,
  results: [result("a", 1), result("b", 2), result("c", 3)],
  metadata: null,
  view: null,
  last_good: null,
  fatal: null,
  experiments: [],
  replays: [],
  workers: { local: 1, workers: [], slots: [], error: null },
};

async function studio(page: Page, experiment?: {run: ExperimentRun; report: ExperimentReport}, delivery = "event", host = "legacy") {
  const errors: string[] = [], sessions: (string | undefined)[] = [];
  const project = {name:"Replay ordering",id:"project",phase:"ready",read_only:false,error:null,
    models:[{id:"model-a",key:"a",name:"Production",entrypoint:"plant.py:build",layout:"layout.json",revision:null,inputs:null,metadata:null,auto_session:null}],
    history:[],active_run:null,generations:[],environment:null,preparation_progress:"",legacy_reference:null};
  const currentState = {...state, experiments: experiment ? [experiment.run] : [], ...(host === "workspace" ? {project} : {})};
  await page.route("**/api/state", route=>route.fulfill({status:200,contentType:"application/json",body:JSON.stringify(currentState)}));
  let release = () => {};
  let delivered = false;
  const held = new Promise<void>(resolve=>{release=resolve;});
  const oldState = {...currentState,replays:[{...replayed,status:"succeeded",verified:false,reason:"old digest",trace:prefix("e")}]};
  const bytes = Buffer.from(JSON.stringify(oldState)), digest = createHash("sha256").update(bytes).digest("hex");
  await page.route("**/api/references/"+digest, async route=>{
    await held; await route.fulfill({status:200,contentType:"application/json",body:bytes}); delivered=true;
  });
  let publish = () => {};
  let comparisons = 0;
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  await page.addInitScript((value) => {
    sessionStorage.setItem("simulatte-mode", "studio");
    sessionStorage.setItem("simulatte-session", value);
  }, secret);
  let sequence = 0;
  await page.routeWebSocket(/\/ws$/, (socket) => {
    publish = () => socket.send(JSON.stringify({v: 1, type: "state", event_seq: ++sequence, body: currentState}));
    socket.onMessage((raw) => {
      const message = JSON.parse(String(raw));
      if (message.type === "auth") {
        expect(message.secret).toBe(secret);
        publish();
      } else if (message.type === "comparison.get" && experiment) {
        comparisons++;
        socket.send(JSON.stringify({v: 1, type: "response", id: message.id, body: {ok: true, report: experiment.report}}));
      } else if (message.type === "replay.start") {
        expect(message.body).toEqual({ request_id: "c".repeat(64), ...(host === "workspace" ? {model_id:"model-a"} : {}) });
        const notify = (type: string, body: unknown) => socket.send(JSON.stringify({ v: 1, type, event_seq: ++sequence, body }));
        const reply = () => socket.send(JSON.stringify({ v: 1, type: "response", id: message.id, body: { ok: true, request_id: replayed.request_id, original: replayed.original } }));
        if(delivery !== "snapshot-before-response") reply();
        if(delivery.startsWith("old-")) {
          notify("state", delivery === "old-reference" ? {ref:{id:digest,sha256:digest,length:bytes.length,url:"/api/references/"+digest}} : oldState);
          notify("replay_state", {request_id:replayed.request_id,original:replayed.original,status:"running",verified:null});
          return;
        }
        notify("replay_state", { request_id: replayed.request_id, original: replayed.original, status: "running", verified: null });
        // The replay publishes but fails verification: the state lists it, unverified.
        notify("state", { ...state, replays: [{ ...replayed, status: "succeeded", verified: false, reason: "fingerprint digest", trace: prefix("e") }] });
        if(delivery === "event") notify("replay_state", { request_id: replayed.request_id, original: replayed.original, status: "succeeded", verified: false });
        if(delivery === "snapshot-before-response") reply();
      } else
        socket.send(JSON.stringify({ v: 1, type: "response", id: message.id, body: message.type === "state.get" ? { ok: true, state: currentState } : { ok: true } }));
    });
  });
  await page.route(/\/api\/prefix\//, async (route) => {
    const request = route.request();
    const id = new URL(request.url()).pathname.split("/")[3]![0] as Id;
    const data = recordings[id], etag = `"${id}"`;
    sessions.push(request.headers()["x-simulatte-session"]);
    if (request.method() === "HEAD")
      return route.fulfill({ status: 200, headers: { "content-length": String(data.length), etag } });
    const [, start, end] = /^bytes=(\d+)-(\d+)$/.exec(request.headers()["range"] ?? "")!.map(Number);
    return route.fulfill({
      status: 206,
      headers: { etag, "content-range": `bytes ${start}-${end}/${data.length}` },
      body: data.subarray(start!, end! + 1),
    });
  });
  return { errors, sessions, publish: () => publish(), comparisons: () => comparisons, release:()=>release(), delivered:()=>delivered };
}
const times = (page: Page) =>
  page.evaluate(() =>
    ["A", "B"].map((side) => Number(document.querySelector<HTMLOutputElement>(`output[aria-label="Side ${side} time"]`)?.dataset["time"])),
  );

test("compare refreshes committed replications when the selected experiment completes", async ({page}) => {
  const run: ExperimentRun = {run_id: "f".repeat(64), experiment_id: "d".repeat(64), name: "Live sweep",
    status: "running", created: 0, counts: {pending: 1}};
  const report: ExperimentReport = {run_id: run.run_id, name: run.name, status: "running", level: 0.95,
    effective_level: 0.95, pointwise: true, m: 0, rows: [], missing: [], missing_total: 1,
    configurations: [{index: 0, label: "defaults/base", parameters: {}, counts: {pending: 1}, mixed_runtime: false}],
    replications: [{configuration: 0, index: 0, status: "pending", attempt: null, trace: null}], replications_total: 1};
  const {errors, publish, comparisons} = await studio(page, {run, report});
  await page.goto("/");
  await expect(page.locator(".studio-controls > div [role=status]")).toHaveText("connected");
  await page.getByText("Compare", {exact: true}).click();
  await page.getByLabel("Experiment run").selectOption(run.run_id);
  await expect.poll(comparisons).toBe(1);
  const replications = page.getByLabel("Side A recording").locator('optgroup[label="Experiment replications"] option');
  await expect(replications).toHaveCount(0);

  run.status = report.status = "completed";
  run.counts = report.configurations[0]!.counts = {succeeded: 1};
  report.missing_total = 0;
  report.replications = [{configuration: 0, index: 0, status: "succeeded", attempt: attempt("e"),
    request_id: "e".repeat(64), trace: prefix("e")}];
  publish();
  await expect(replications).toHaveCount(1);
  await expect(replications).toHaveText("Live sweep · defaults/base · replication 0");
  await page.getByLabel("Side A recording").selectOption(attempt("e"));
  await expect(page.locator('.compare-scene[data-side="A"] canvas')).toHaveCount(1);
  expect(errors).toEqual([]);
});

test("two committed recordings play in lockstep and closing the compare view keeps the main viewer", async ({ page }) => {
  const { errors, sessions } = await studio(page);
  await page.goto("/");
  await expect(page.locator(".studio-controls > div [role=status]")).toHaveText("connected");
  // The main viewer opens the newest result; tag its canvas to check it survives the compare view.
  await expect(page.locator(".run-title strong")).toHaveText("Run · revision 1");
  await expect(page.locator("footer .status")).toHaveText("paused");
  await expect(page.locator(".scene-host canvas")).toHaveCount(1);
  await page.locator(".scene-host canvas").evaluate((canvas) => canvas.setAttribute("data-main", "1"));

  await page.getByText("Compare", { exact: true }).click();
  await page.getByLabel("Side A recording").selectOption(attempt("a"));
  await page.getByLabel("Side B recording").selectOption(attempt("b"));
  await expect(page.locator('.compare-scene[data-side="A"] canvas')).toHaveCount(1);
  await expect(page.locator('.compare-scene[data-side="B"] canvas')).toHaveCount(1);
  await expect(page.getByLabel("Side B recording")).toContainText("full");
  await expect.poll(() => times(page)).toEqual([0, 0]);
  // The compare view owns two new canvases; the main scene keeps its own.
  await expect(page.locator('.scene-host canvas[data-main="1"]')).toHaveCount(1);

  await page.getByRole("button", { name: "Play both", exact: true }).click();
  await page.waitForTimeout(1000);
  const [a, b] = await times(page);
  expect(a).toBeGreaterThan(0.5);
  // Both sides are read in one task; at rate 1 they differ by at most one frame.
  expect(Math.abs(a! - b!)).toBeLessThanOrEqual(1 / 30);
  // Span playback frames: rewriting the label during a press suppresses WebKit's click.
  await page.getByRole("button", { name: "Pause both", exact: true }).click({ delay: 100 });
  await expect(page.getByRole("button", { name: "Play both", exact: true })).toBeVisible();
  await expect.poll(async () => new Set(await times(page)).size).toBe(1);

  await page.getByLabel("Compare time").fill("7");
  await expect.poll(() => times(page)).toEqual([7, 7]);
  await page.getByRole("button", { name: "Compare next event", exact: true }).click();
  await expect.poll(async () => { const [x, y] = await times(page); return x === y && x! > 7; }).toBe(true);
  await page.getByLabel("Compare time").fill("22");
  // Past its end (t = 20) side A holds its last time while side B follows the shared time.
  await expect.poll(() => times(page)).toEqual([20, 22]);

  // A KPI-only result offers the replay action and no scene.
  await page.getByLabel("Side B recording").selectOption(attempt("c"));
  await expect(page.getByRole("button", { name: "Replay at full level", exact: true })).toBeVisible();
  await expect(page.locator('.compare-scene[data-side="B"]')).toBeHidden();
  await expect(page.locator('.compare-scene[data-side="B"] canvas')).toHaveCount(0);
  await expect(page.getByLabel("Side B recording")).toContainText("KPI-only");
  // Replaying it at full level: an unverified replay is labelled and stays out until the user opts in.
  await page.getByRole("button", { name: "Replay at full level", exact: true }).click();
  await expect(page.getByText("unverified replay: fingerprint digest").first()).toBeVisible();
  await expect(page.getByText(/turn on Include unverified replays/)).toBeVisible();
  await expect(page.getByText("1 unverified replay(s) hidden")).toBeVisible();
  await expect(page.locator('.compare-scene[data-side="B"] canvas')).toHaveCount(0);
  await page.getByLabel("Include unverified replays").check();
  await expect(page.locator('.compare-scene[data-side="B"] canvas')).toHaveCount(1);
  await expect(page.getByLabel("Side B recording")).toContainText("Replay of Run · revision 3");
  await expect(page.locator('.compare-side[aria-label="Compare side B"] .replay-unverified')).toHaveText("unverified replay: fingerprint digest");

  await page.getByRole("button", { name: "Close compare", exact: true }).click();
  await expect(page.locator(".compare-scene")).toHaveCount(0);
  await expect(page.locator('.scene-host canvas[data-main="1"]')).toHaveCount(1);
  // The main viewer still renders: seek, step, its drawing survives and a WebGL context (when the
  // engine has one; headless Linux Firefox draws without WebGL) is not lost.
  await page.getByLabel("Simulation time").fill("4");
  await expect(page.locator("footer .transport output")).toContainText("t = 4");
  await page.getByRole("button", { name: "Next event", exact: true }).click();
  await expect(page.locator("footer .transport output")).toContainText("seq");
  expect(
    await page.locator('.scene-host canvas[data-main="1"]').evaluate((canvas: HTMLCanvasElement) => {
      // A canvas returns null for a context type other than the one it already holds.
      const gl = canvas.getContext("webgl2") ?? canvas.getContext("webgl");
      const blank = document.createElement("canvas");
      blank.width = canvas.width;
      blank.height = canvas.height;
      return { lost: gl?.isContextLost() ?? false, drawn: canvas.width > 0 && canvas.height > 0 && canvas.toDataURL() !== blank.toDataURL() };
    }),
  ).toEqual({ lost: false, drawn: true });
  expect(sessions.length).toBeGreaterThan(0);
  expect(new Set(sessions)).toEqual(new Set([secret]));
  expect(errors).toEqual([]);
});

for(const delivery of ["snapshot", "snapshot-before-response"]) {
  test(`compare recovers a missed replay terminal notification (${delivery})`, async ({page}) => {
    const {errors} = await studio(page, undefined, delivery);
    await page.goto("/");
    await page.getByText("Compare", {exact:true}).click();
    await page.getByLabel("Side A recording").selectOption(attempt("c"));
    await page.getByRole("button", {name:"Replay at full level",exact:true}).click();
    await expect(page.getByText("unverified replay: fingerprint digest").first()).toBeVisible();
    await expect(page.getByText("Replay running at full level…")).toHaveCount(0);
    await page.getByLabel("Include unverified replays").check();
    await expect(page.locator('.compare-scene[data-side="A"] canvas')).toHaveCount(1);
    expect(errors).toEqual([]);
  });
}

for(const host of ["legacy", "workspace"]) for(const delivery of ["old-inline", "old-reference"]) {
  test(`${host} keeps newer replay running after older state (${delivery})`, async ({page}) => {
    const proof = await studio(page, undefined, delivery, host);
    await page.goto("/");
    if(host === "workspace") await page.getByRole('link',{name:'All experiments',exact:true}).click();
    await page.getByText("Compare", {exact:true}).click();
    await page.getByLabel("Side A recording").selectOption(attempt("c"));
    await page.getByRole("button", {name:"Replay at full level",exact:true}).click();
    await expect(page.getByText("Replay running at full level…")).toBeVisible();
    proof.release();
    if(delivery === "old-reference") await expect.poll(proof.delivered).toBe(true);
    await expect(page.getByText("1 unverified replay(s) hidden")).toBeVisible();
    await expect(page.getByText("Replay running at full level…")).toBeVisible();
    await expect(page.locator('.compare-scene[data-side="A"] canvas')).toHaveCount(0);
    expect(proof.errors).toEqual([]);
  });
}
