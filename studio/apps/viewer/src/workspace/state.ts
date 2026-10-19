import type { AttemptKey, Revision, Prefix, Reference } from "../studio-client";
import type { ParameterSchema } from "../parameter-draft";
export type WorkspaceRoute =
  | {
      page:
        | "overview"
        | "models"
        | "runs"
        | "experiments"
        | "analyses"
        | "presentations"
        | "settings";
    }
  | { page: "model"; id: string }
  | { page: "run"; attempt: string };
export function parseRoute(hash: string): WorkspaceRoute {
  try {
    const [page, id] = hash.replace(/^#\/?/, "").split("/");
    if (page === "model" && id) return { page, id: decodeURIComponent(id) };
    if (page === "run" && id) return { page, attempt: decodeURIComponent(id) };
    if (
      [
        "models",
        "runs",
        "experiments",
        "analyses",
        "presentations",
        "settings",
      ].includes(page ?? "")
    )
      return { page } as WorkspaceRoute;
  } catch {
    /* An invalid bookmark opens the overview. */
  }
  return { page: "overview" };
}
export function routeHash(route: WorkspaceRoute): string {
  return (
    "#/" +
    route.page +
    ("id" in route
      ? "/" + encodeURIComponent(route.id)
      : "attempt" in route
        ? "/" + encodeURIComponent(route.attempt)
        : "")
  );
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
