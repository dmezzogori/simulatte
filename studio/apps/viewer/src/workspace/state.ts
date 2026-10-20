import type { AttemptKey, Revision, Prefix, Reference } from "../studio-client";
import type { ParameterSchema } from "../parameter-draft";
/** Focused routes. P1 values stay valid; sub-identities are optional segments. */
export type WorkspaceRoute =
  | { page: "overview" | "models" | "presentations" | "settings" }
  | { page: "model"; id: string }
  | { page: "run"; attempt: string }
  | { page: "runs"; model?: string }
  | { page: "experiments"; run?: string }
  | { page: "comparison"; selection: string[] }
  | { page: "analyses"; execution?: string; definition?: string };
const PLAIN_PAGES = [
  "overview",
  "models",
  "runs",
  "experiments",
  "analyses",
  "presentations",
  "settings",
] as const;
/** The route of a location hash; malformed input opens the overview. */
export function parseRoute(hash: string): WorkspaceRoute {
  try {
    const parts = hash.replace(/^#\/?/, "").replace(/\/$/, "").split("/");
    const [page = "", first = "", second = ""] = parts;
    if (parts.length === 1) {
      if (page === "compare") return { page: "comparison", selection: [] };
      if ((PLAIN_PAGES as readonly string[]).includes(page))
        return { page } as WorkspaceRoute;
    } else if (parts.length === 2 && first) {
      if (page === "compare") {
        const selection = first.split(",").map(decodeURIComponent);
        if (selection.every(Boolean)) return { page: "comparison", selection };
      } else if (page !== "analyses" || first !== "new") {
        const id = decodeURIComponent(first);
        if (page === "model") return { page, id };
        if (page === "run") return { page, attempt: id };
        if (page === "runs") return { page, model: id };
        if (page === "experiments") return { page, run: id };
        if (page === "analyses") return { page, execution: id };
      }
    } else if (parts.length === 3 && page === "analyses" && first === "new" && second)
      return { page, definition: decodeURIComponent(second) };
  } catch {
    /* An invalid bookmark opens the overview. */
  }
  return { page: "overview" };
}
/** The inverse of ``parseRoute``. */
export function routeHash(route: WorkspaceRoute): string {
  const e = encodeURIComponent;
  switch (route.page) {
    case "model":
      return "#/model/" + e(route.id);
    case "run":
      return "#/run/" + e(route.attempt);
    case "runs":
      return "#/runs" + (route.model ? "/" + e(route.model) : "");
    case "experiments":
      return "#/experiments" + (route.run ? "/" + e(route.run) : "");
    case "comparison":
      return (
        "#/compare" +
        (route.selection.length ? "/" + route.selection.map(e).join(",") : "")
      );
    case "analyses":
      return (
        "#/analyses" +
        (route.definition
          ? "/new/" + e(route.definition)
          : route.execution
            ? "/" + e(route.execution)
            : "")
      );
    default:
      return "#/" + route.page;
  }
}
export interface Crumb {
  label: string;
  hash?: string;
}
const SECTION_LABELS: Record<WorkspaceRoute["page"], string> = {
  overview: "Overview",
  models: "Models",
  model: "Models",
  runs: "Runs",
  run: "Runs",
  experiments: "Experiments",
  comparison: "Experiments",
  analyses: "Analyses",
  presentations: "Presentations",
  settings: "Settings",
};
/** The sidebar entry a route belongs to. */
export function routeSection(route: WorkspaceRoute): string {
  return SECTION_LABELS[route.page].toLowerCase();
}
/** Breadcrumbs: the section, then the selected identity when the route has one. */
export function routeCrumbs(
  route: WorkspaceRoute,
  models: readonly { id: string; name: string }[],
): Crumb[] {
  const section = SECTION_LABELS[route.page];
  const home: Crumb = { label: section, hash: "#/" + section.toLowerCase() };
  const model = (id: string) =>
    models.find((m) => m.id === id)?.name ?? "Unknown model";
  switch (route.page) {
    case "model":
      return [home, { label: model(route.id) }];
    case "run":
      return [home, { label: "Run" }];
    case "runs":
      return route.model ? [home, { label: model(route.model) }] : [home];
    case "experiments":
      return route.run ? [home, { label: "Experiment run" }] : [home];
    case "comparison":
      return [home, { label: "Comparison" }];
    case "analyses":
      return route.definition
        ? [home, { label: "New analysis" }]
        : route.execution
          ? [home, { label: "Analysis" }]
          : [home];
    default:
      return [{ label: section }];
  }
}
export interface ProjectModel {
  id: string;
  key: string;
  name: string;
  entrypoint: string;
  layout: string;
  revision: Revision | null;
  inputs: {
    options: Record<string, unknown>;
    issues: Record<string, unknown>;
  } | null;
  metadata: {
    selected: string;
    models: {
      name: string;
      parameters: ParameterSchema;
      stopping: Record<string, unknown>;
    }[];
  } | null;
  view?: import("../studio-client").View | null;
  auto_session: string | null;
  metadata_reference?: Reference | null;
  experiment_files?: string[];
}
export interface HistoryItem {
  id: string;
  model_id: string | null;
  name: string;
  status: string;
  created: number;
  has_result: boolean;
  important: boolean;
  text: string;
  details: unknown;
}
export interface Generation {
  id: string;
  state: string;
  kind: string;
  interpreter: string;
  runtime: Record<string, unknown>;
}
export interface ProjectState {
  name: string;
  id: string | null;
  phase: "setup" | "ready" | "recovery";
  read_only: boolean;
  error: string | null;
  models: ProjectModel[];
  history: HistoryItem[];
  active_run: {
    key: AttemptKey;
    model_id: string | null;
    status: string;
    details: unknown;
  } | null;
  generations: Generation[];
  environment: Generation | null;
  preparation_progress: string;
  legacy_reference: string | null;
  legacy_layout?: string | null;
}
export interface RunDetails {
  name: string;
  text: string;
  important: boolean;
  request_id: string;
  generation: number;
  id: string;
  status: string;
  revision: number;
  request: Record<string, unknown>;
  details: unknown;
  trace: Prefix | null;
  layout: Reference | null;
  artifacts: { path: string; length: number }[];
}
export function validateProjectState(value: unknown): ProjectState {
  const p = value as ProjectState;
  if (
    !p ||
    typeof p.name !== "string" ||
    !["setup", "ready", "recovery"].includes(p.phase) ||
    typeof p.read_only !== "boolean" ||
    !Array.isArray(p.models) ||
    !p.models.every(
      (m) =>
        typeof m.id === "string" &&
        typeof m.name === "string" &&
        typeof m.entrypoint === "string",
    ) ||
    !Array.isArray(p.history) ||
    !Array.isArray(p.generations) ||
    (p.active_run !== null &&
      (!p.active_run?.key || typeof p.active_run.key.attempt_id !== "string"))
  )
    throw new Error("Invalid project state");
  return p;
}
export interface ModelDraft {
  revision: Revision;
  options: Record<string, unknown>;
  dirty: boolean;
  conflict: boolean;
  latest: { revision: Revision; options: Record<string, unknown> };
}
export class WorkspaceDrafts {
  #models = new Map<string, ModelDraft>();
  get(id: string): ModelDraft | undefined {
    return this.#models.get(id);
  }
  observe(
    id: string,
    revision: Revision,
    options: Record<string, unknown>,
  ): void {
    const old = this.get(id);
    const latest = { revision, options: structuredClone(options) };
    if (!old || !old.dirty)
      this.#models.set(id, {
        ...latest,
        dirty: false,
        conflict: false,
        latest,
      });
    else if (revision.number !== old.revision.number) {
      old.conflict = true;
      old.latest = latest;
    }
  }
  edit(id: string, options: Record<string, unknown>): void {
    const d = this.get(id);
    if (d) {
      d.options = structuredClone(options);
      d.dirty = true;
    }
  }
  reload(id: string): void {
    const d = this.get(id);
    if (d)
      this.#models.set(id, {
        ...d.latest,
        dirty: false,
        conflict: false,
        latest: d.latest,
      });
  }
  accept(id: string, revision: Revision): void {
    const d = this.get(id);
    if (d) {
      d.revision = revision;
      d.dirty = false;
      d.conflict = false;
      d.latest = { revision, options: d.options };
    }
  }
}
