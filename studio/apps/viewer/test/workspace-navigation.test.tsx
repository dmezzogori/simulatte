import { describe, it, expect, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import {
  parseRoute,
  routeHash,
  routeCrumbs,
  WorkspaceDrafts,
  type ProjectState,
  type WorkspaceRoute,
} from "../src/workspace/state";
import {
  shellReducer,
  noticeFor,
  MAX_NOTICES,
  type NoticeContext,
  type ShellState,
} from "../src/workspace/notices";
import {
  classifyFailure,
  describeMessage,
  Feedback,
  type PageState,
} from "../src/workspace/feedback";
import { WorkspaceShell } from "../src/workspace/shell";
import { StudioClient, type Notification } from "../src/studio-client";

const notification = (
  type: string,
  body: unknown,
  extra: Partial<Notification> = {},
): Notification =>
  ({ v: 1, type, event_seq: 1, body, ...extra }) as unknown as Notification;
const context: NoticeContext = {
  models: [
    { id: "model-a", name: "A" },
    { id: "model-b", name: "B" },
  ],
  activeRun: { attempt_id: "attempt-a", model_id: "model-a" },
};
const project = (overrides: Partial<ProjectState> = {}): ProjectState => ({
  name: "Plant",
  id: "p",
  phase: "ready",
  read_only: false,
  error: null,
  models: [
    {
      id: "model-a",
      key: "a",
      name: "A",
      entrypoint: "a.py:build",
      layout: "layouts/a.json",
      revision: null,
      inputs: null,
      metadata: null,
      auto_session: null,
    },
  ],
  history: [],
  active_run: null,
  generations: [],
  environment: null,
  preparation_progress: "",
  legacy_reference: null,
  ...overrides,
});

describe("focused routes", () => {
  it("round trips every route variant through the hash", () => {
    const routes: WorkspaceRoute[] = [
      { page: "overview" },
      { page: "models" },
      { page: "presentations" },
      { page: "settings" },
      { page: "model", id: "id a/b" },
      { page: "run", attempt: "attempt-1" },
      { page: "runs" },
      { page: "runs", model: "model a" },
      { page: "experiments" },
      { page: "experiments", run: "r".repeat(64) },
      { page: "comparison", selection: [] },
      { page: "comparison", selection: ["one", "two,three", "fo/ur"] },
      { page: "analyses" },
      { page: "analyses", execution: "exec-1" },
      { page: "analyses", definition: "lateness/by family" },
    ];
    for (const route of routes)
      expect(parseRoute(routeHash(route))).toStrictEqual(route);
    expect(routeHash({ page: "runs", model: "m" })).toBe("#/runs/m");
    expect(routeHash({ page: "experiments", run: "r" })).toBe("#/experiments/r");
    expect(routeHash({ page: "comparison", selection: ["a", "b"] })).toBe(
      "#/compare/a,b",
    );
    expect(routeHash({ page: "analyses", definition: "k" })).toBe(
      "#/analyses/new/k",
    );
    expect(routeHash({ page: "analyses", execution: "e" })).toBe(
      "#/analyses/e",
    );
    expect(parseRoute("#/runs/")).toStrictEqual({ page: "runs" });
    expect(parseRoute("")).toStrictEqual({ page: "overview" });
  });
  it("opens the overview for malformed input", () => {
    for (const hash of [
      "#/model/%ZZ",
      "#/model",
      "#/model/",
      "#/run",
      "#/run/a/b",
      "#/runs/a/b",
      "#/compare/a,,b",
      "#/compare/%E0%A4%A",
      "#/analyses/new",
      "#/analyses/new/",
      "#/analyses/a/b",
      "#/experiments/a/b",
      "#/nowhere",
      "#//",
      "javascript:alert(1)",
    ])
      expect(parseRoute(hash), hash).toStrictEqual({ page: "overview" });
  });
  it("builds breadcrumbs with the selected identities", () => {
    const models = [{ id: "model-a", name: "A" }];
    expect(routeCrumbs({ page: "model", id: "model-a" }, models)).toEqual([
      { label: "Models", hash: "#/models" },
      { label: "A" },
    ]);
    expect(routeCrumbs({ page: "runs", model: "model-a" }, models)).toEqual([
      { label: "Runs", hash: "#/runs" },
      { label: "A" },
    ]);
    expect(
      routeCrumbs({ page: "comparison", selection: ["x"] }, models),
    ).toEqual([{ label: "Experiments", hash: "#/experiments" }, { label: "Comparison" }]);
    expect(routeCrumbs({ page: "analyses", definition: "k" }, models)).toEqual([
      { label: "Analyses", hash: "#/analyses" },
      { label: "New analysis" },
    ]);
    expect(routeCrumbs({ page: "overview" }, models)).toEqual([
      { label: "Overview" },
    ]);
  });
});

describe("completion notices", () => {
  it("completionPreservesRouteAndDraft", () => {
    const drafts = new WorkspaceDrafts();
    drafts.observe("model-b", { number: 1, digest: "b".repeat(64) }, { seed: "3" });
    drafts.edit("model-b", { seed: "44" });
    const before = structuredClone(drafts.get("model-b"));
    for (const route of [
      { page: "analyses", execution: "exec-9" },
      { page: "run", attempt: "attempt-b" },
    ] as WorkspaceRoute[]) {
      const state = { route, notices: [] };
      const next = shellReducer(state, {
        type: "notification",
        message: notification("run_state", { status: "succeeded" }, { attempt_id: "attempt-a" }),
        context,
      });
      expect(next.route).toBe(route);
      expect(next.notices).toHaveLength(1);
      expect(next.notices[0]).toMatchObject({
        kind: "run",
        tone: "success",
        hash: "#/run/attempt-a",
      });
      expect(next.notices[0]!.title).toContain("A");
    }
    expect(drafts.get("model-b")).toEqual(before);
    expect(drafts.get("model-b")?.dirty).toBe(true);
    expect(drafts.get("model-b")?.options.seed).toBe("44");
  });
  it("accepts the raw attempt outcomes the project server forwards", () => {
    const result = noticeFor(
      notification("result", { attempt_id: "attempt-a" }, { attempt_id: "attempt-a" }),
      context,
    );
    expect(result).toMatchObject({ id: "run:attempt-a:succeeded", hash: "#/run/attempt-a" });
    const failed = noticeFor(
      notification("failed", { message: "boom" }, { attempt_id: "attempt-z" }),
      { ...context, activeRun: null },
    );
    expect(failed).toMatchObject({ tone: "failure", hash: "#/run/attempt-z" });
    expect(failed!.title).toContain("Run");
    for (const type of ["queued", "ready", "heartbeat", "progress", "kpi", "trace_available"])
      expect(noticeFor(notification(type, {}, { attempt_id: "attempt-a" }), context)).toBeNull();
  });
  it("links experiment and analysis outcomes and ignores non-terminal states", () => {
    expect(
      noticeFor(notification("experiment_state", { run_id: "run-1", status: "completed", name: "Sweep" }), context),
    ).toMatchObject({ kind: "experiment", tone: "success", hash: "#/experiments/run-1" });
    expect(
      noticeFor(notification("experiment_state", { run_id: "run-1", status: "running", name: "Sweep" }), context),
    ).toBeNull();
    expect(
      noticeFor(notification("experiment_state", { run_id: "run-1", status: "deleted" }), context),
    ).toBeNull();
    expect(
      noticeFor(notification("analysis_state", { execution_id: "exec-1", state: "failed" }), context),
    ).toMatchObject({ kind: "analysis", tone: "failure", hash: "#/analyses/exec-1" });
    expect(
      noticeFor(notification("analysis_state", { execution_id: "exec-1", state: "running" }), context),
    ).toBeNull();
  });
  it("deduplicates, caps, dismisses and skips the page being read", () => {
    let state: ShellState = { route: { page: "run", attempt: "attempt-a" }, notices: [] };
    const done = notification("run_state", { status: "succeeded" }, { attempt_id: "attempt-a" });
    expect(shellReducer(state, { type: "notification", message: done, context })).toBe(state);
    state = { ...state, route: { page: "overview" } };
    state = shellReducer(state, { type: "notification", message: done, context });
    state = shellReducer(state, { type: "notification", message: done, context });
    expect(state.notices).toHaveLength(1);
    for (let i = 0; i < MAX_NOTICES + 2; i++)
      state = shellReducer(state, {
        type: "notification",
        message: notification("analysis_state", { execution_id: `e${i}`, state: "succeeded" }),
        context,
      });
    expect(state.notices).toHaveLength(MAX_NOTICES);
    expect(state.notices[0]!.hash).toBe(`#/analyses/e${MAX_NOTICES + 1}`);
    const dismissed = shellReducer(state, { type: "dismiss", id: state.notices[0]!.id });
    expect(dismissed.notices).toHaveLength(MAX_NOTICES - 1);
    expect(dismissed.route).toBe(state.route);
    expect(shellReducer(state, { type: "dismiss", id: "missing" })).toBe(state);
    expect(shellReducer(state, { type: "navigate", hash: "#/runs/m" }).route).toStrictEqual({ page: "runs", model: "m" });
  });
});

describe("page states", () => {
  const render = (state: PageState<string>) =>
    renderToStaticMarkup(
      <Feedback
        state={state}
        loading="Loading the run…"
        empty={{ title: "No runs yet", text: "Choose a model first.", link: { label: "Choose a model", hash: "#/models" } }}
      >
        {(value) => <p>{value}</p>}
      </Feedback>,
    );
  it("renders designed loading, empty, error and stale screens", () => {
    expect(render({ status: "loading" })).toContain('role="status"');
    expect(render({ status: "loading" })).toContain("Loading the run…");
    const empty = render({ status: "empty", action: { label: "Refresh", invoke() {} } });
    expect(empty).toContain("No runs yet");
    expect(empty).toContain('href="#/models"');
    expect(empty).toContain(">Refresh</button>");
    const disabled = render({ status: "empty", action: { label: "Run", invoke() {}, disabledReason: "Prepare the model first." } });
    expect(disabled).toMatch(/<button[^>]*disabled=""/);
    expect(disabled).toContain("Prepare the model first.");
    const error = render({ status: "error", message: "Command outcome is unknown; resynchronize before trying again", retry: { label: "Try again", invoke() {} } });
    expect(error).toContain('role="alert"');
    expect(error).toContain('data-kind="connection"');
    expect(error).toContain(">Try again</button>");
    expect(render({ status: "ready", value: "shown" })).toBe("<p>shown</p>");
    const stale = render({ status: "stale", value: "old", reason: "Inputs changed since this run." });
    expect(stale).toContain("<p>old</p>");
    expect(stale).toContain("Inputs changed since this run.");
    expect(stale).toContain('role="status"');
  });
  it("distinguishes model, dependency, missing-data and connection failures", () => {
    expect(classifyFailure("ModuleNotFoundError: No module named 'plotly'").kind).toBe("dependency");
    expect(classifyFailure("Traceback (most recent call last):\n  File \"a.py\"\nValueError: bad horizon").kind).toBe("model");
    expect(classifyFailure("insufficient_input: no replications match the selection").kind).toBe("missing-data");
    expect(classifyFailure("Studio must synchronize before commands").kind).toBe("connection");
    expect(classifyFailure("Reference unavailable; resynchronize Studio").kind).toBe("connection");
    expect(classifyFailure("No recording available for this run").kind).toBe("missing-data");
    expect(classifyFailure("¯\\_(ツ)_/¯").kind).toBe("unknown");
    const traceback = classifyFailure("Traceback (most recent call last):\n  File \"a.py\", line 2\nValueError: bad horizon");
    expect(traceback.summary).toBe("ValueError: bad horizon");
    expect(traceback.diagnostic.join("\n")).toContain("File \"a.py\"");
  });
  it("keeps raw hashes out of the primary text", () => {
    const hash = "c".repeat(64);
    const message = describeMessage(`Snapshot ${hash} is missing`);
    expect(message.summary).not.toContain(hash);
    expect(message.summary).toContain("Snapshot");
    expect(message.diagnostic.join("\n")).toContain(hash);
    const html = render({ status: "error", message: `Snapshot ${hash} is missing` });
    expect(html.split(hash)).toHaveLength(2);
    expect(html).toMatch(new RegExp(`<details[^>]*>.*${hash}.*</details>`));
    expect(html.slice(0, html.indexOf("<details"))).not.toContain(hash);
  });
});

describe("shell", () => {
  const shell = (hash: string, overrides: Partial<ProjectState> = {}) => {
    (globalThis as { location?: unknown }).location = { hash, origin: "http://127.0.0.1" };
    (globalThis as { sessionStorage?: unknown }).sessionStorage = { getItem: () => null };
    try {
      const client = new StudioClient("secret", { origin: "http://127.0.0.1" });
      return renderToStaticMarkup(
        <WorkspaceShell client={client} state={project(overrides)} status="connected" />,
      );
    } finally {
      delete (globalThis as { location?: unknown }).location;
      delete (globalThis as { sessionStorage?: unknown }).sessionStorage;
    }
  };
  it("renders placeholder pages with their identities and one navigation surface", () => {
    const comparison = shell("#/compare/one,two");
    expect(comparison.match(/aria-label="Project navigation"/g)).toHaveLength(1);
    expect(comparison).not.toContain('role="tab"');
    expect(comparison).toContain("one");
    expect(comparison).toContain("two");
    expect(comparison).toContain('aria-label="Breadcrumb"');
    expect(comparison).toContain(">Comparison<");
    const analyses = shell("#/analyses/new/lateness");
    expect(analyses).toContain("lateness");
    expect(analyses).toContain(">New analysis<");
    const execution = shell("#/analyses/exec-7");
    expect(execution).toContain("exec-7");
    const runs = shell("#/runs/model-a");
    expect(runs).toContain('aria-current="page"');
    expect(runs).toContain('aria-busy="true"');
    expect(runs).toContain('value="model-a" selected=""');
    expect(runs).toContain(">Runs of A<");
    expect(shell("#/runs")).toContain("No runs yet");
    // The experiments page wraps the legacy tools (useSyncExternalStore), which cannot render statically;
    // its selected run is covered by the breadcrumb test and the browser spec.
    expect(shell("#/presentations")).toContain("Presentations");
  });
  it("renders notices with a link and a dismiss control", () => {
    (globalThis as { location?: unknown }).location = { hash: "#/overview" };
    try {
      const client = new StudioClient("secret", { origin: "http://127.0.0.1" });
      const html = renderToStaticMarkup(
        <WorkspaceShell client={client} state={project()} status="connected" notices={[{ id: "n", kind: "run", tone: "success", title: "A completed", text: "Results are saved.", hash: "#/run/attempt-a" }]} />,
      );
      expect(html).toMatch(/<div[^>]*class="ws-toast[^"]*"[^>]*role="status"/);
      expect(html).toContain('href="#/run/attempt-a"');
      expect(html).toContain("Dismiss");
    } finally {
      delete (globalThis as { location?: unknown }).location;
    }
  });
});

describe("authenticated API fetch", () => {
  it("sends the session header on same-origin requests only", async () => {
    const calls: [URL, RequestInit][] = [];
    const fetcher = vi.fn(async (url: URL | RequestInfo, init?: RequestInit) => {
      calls.push([url as URL, init!]);
      return new Response("{}", { status: 200 });
    });
    const client = new StudioClient("s3cret", { origin: "http://127.0.0.1:4173", fetch: fetcher as unknown as typeof fetch });
    const response = await client.fetchApi("/api/analysis/e/outputs/t/rows?limit=5", { method: "GET" });
    expect(response.status).toBe(200);
    expect(calls[0]![0].href).toBe("http://127.0.0.1:4173/api/analysis/e/outputs/t/rows?limit=5");
    const headers = new Headers(calls[0]![1].headers);
    expect(headers.get("X-Simulatte-Session")).toBe("s3cret");
    expect(calls[0]![1]).toMatchObject({ credentials: "same-origin", redirect: "error", mode: "same-origin", referrerPolicy: "no-referrer" });
    for (const path of ["//evil.example/api/x", "http://evil.example/api/x", "/bootstrap?token=1", "api/state"])
      await expect(client.fetchApi(path)).rejects.toThrow();
    expect(calls).toHaveLength(1);
  });
});
