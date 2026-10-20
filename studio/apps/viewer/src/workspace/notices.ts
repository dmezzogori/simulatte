/** Completion notices: a pure reducer over notifications. It never touches drafts, and it changes the route only for an explicit navigation. */
import type { Notification } from "../studio-client";
import { parseRoute, routeHash, type WorkspaceRoute } from "./state";
export interface Notice {
  id: string;
  kind: "run" | "experiment" | "analysis";
  tone: "success" | "failure";
  title: string;
  text: string;
  /** The page that shows the finished work. */
  hash: string;
}
export interface NoticeContext {
  models: readonly { id: string; name: string }[];
  activeRun: { attempt_id: string; model_id: string | null } | null;
}
export interface ShellState {
  route: WorkspaceRoute;
  notices: Notice[];
}
export type ShellAction =
  | { type: "navigate"; hash: string }
  | { type: "notification"; message: Notification; context: NoticeContext }
  | { type: "dismiss"; id: string };
export const MAX_NOTICES = 5;
/** Interactive attempt outcomes. The project server forwards the coordinator's raw kinds
 * (``result``, ``failed``, …) while the legacy server wraps them as ``run_state``. */
const RUN_OUTCOMES: Record<string, string> = {
  result: "succeeded",
  succeeded: "succeeded",
  failed: "failed",
  cancelled: "cancelled",
  lost: "lost",
  disk_full: "disk_full",
  validation_failed: "validation_failed",
};
const RUN_WORDING: Record<string, string> = {
  succeeded: "completed",
  failed: "failed",
  cancelled: "was stopped",
  lost: "was lost",
  disk_full: "stopped because the disk is full",
  validation_failed: "failed validation",
};
const EXPERIMENT_OUTCOMES = new Set(["completed", "failed", "cancelled"]);
const ANALYSIS_OUTCOMES = new Set(["succeeded", "failed", "cancelled", "lost"]);
const text = (value: unknown): string => (typeof value === "string" ? value : "");
/** The notice a notification deserves, or ``null`` for progress and non-terminal states. */
export function noticeFor(
  message: Notification,
  context: NoticeContext,
): Notice | null {
  const body = (
    message.body !== null && typeof message.body === "object" ? message.body : {}
  ) as Record<string, unknown>;
  const type = message.type as string;
  if (type === "run_state" || type in RUN_OUTCOMES) {
    const outcome = RUN_OUTCOMES[type === "run_state" ? text(body.status) : type];
    const attempt = message.attempt_id ?? text(body.attempt_id);
    if (!outcome || !attempt) return null;
    const owner =
      context.activeRun?.attempt_id === attempt
        ? context.models.find((m) => m.id === context.activeRun?.model_id)?.name
        : undefined;
    const success = outcome === "succeeded";
    return {
      id: `run:${attempt}:${outcome}`,
      kind: "run",
      tone: success ? "success" : "failure",
      title: `${owner ?? "Run"} ${RUN_WORDING[outcome]}`,
      text: success
        ? "Results are saved in this project."
        : "Diagnostics are on the run page.",
      hash: routeHash({ page: "run", attempt }),
    };
  }
  if (type === "experiment_state") {
    const run = text(body.run_id),
      status = text(body.status);
    if (!run || !EXPERIMENT_OUTCOMES.has(status)) return null;
    return {
      id: `experiment:${run}:${status}`,
      kind: "experiment",
      tone: status === "completed" ? "success" : "failure",
      title: `${text(body.name) || "Experiment"} ${status}`,
      text:
        status === "completed"
          ? "Replication results are ready to compare."
          : "Replication outcomes are on the experiment page.",
      hash: routeHash({ page: "experiments", run }),
    };
  }
  if (type === "analysis_state") {
    const execution = text(body.execution_id),
      state = text(body.state);
    if (!execution || !ANALYSIS_OUTCOMES.has(state)) return null;
    return {
      id: `analysis:${execution}:${state}`,
      kind: "analysis",
      tone: state === "succeeded" ? "success" : "failure",
      title: `Analysis ${state === "succeeded" ? "completed" : state}`,
      text:
        state === "succeeded"
          ? "Outputs are saved with their inputs."
          : "Diagnostics are on the analysis page.",
      hash: routeHash({ page: "analyses", execution }),
    };
  }
  return null;
}
export function shellReducer(state: ShellState, action: ShellAction): ShellState {
  switch (action.type) {
    case "navigate":
      return { ...state, route: parseRoute(action.hash) };
    case "notification": {
      const notice = noticeFor(action.message, action.context);
      if (
        !notice ||
        notice.hash === routeHash(state.route) ||
        state.notices.some((n) => n.id === notice.id)
      )
        return state;
      return {
        ...state,
        notices: [notice, ...state.notices].slice(0, MAX_NOTICES),
      };
    }
    case "dismiss":
      return state.notices.some((n) => n.id === action.id)
        ? { ...state, notices: state.notices.filter((n) => n.id !== action.id) }
        : state;
  }
}
