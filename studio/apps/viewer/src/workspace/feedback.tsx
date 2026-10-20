/** Shared page states: designed loading, empty, error and stale screens with a next action. */
import type { ReactNode } from "react";
export interface Action {
  label: string;
  invoke: () => void;
  /** Shown next to the disabled control instead of leaving it unexplained. */
  disabledReason?: string;
}
export type PageState<T> =
  | { status: "loading" }
  | { status: "ready"; value: T }
  | { status: "empty"; action?: Action }
  | { status: "error"; message: string; retry?: Action }
  | { status: "stale"; value: T; reason: string };
export type FailureKind =
  | "model"
  | "dependency"
  | "missing-data"
  | "connection"
  | "unknown";
export interface Failure {
  kind: FailureKind;
  title: string;
  hint: string;
  /** Human-readable text without raw hashes. */
  summary: string;
  /** Raw details for the collapsible diagnostic element. */
  diagnostic: string[];
}
const HASH = /\b[0-9a-f]{32,}\b/g;
/** Primary text without raw hashes or traceback bodies; those move to the diagnostic list. */
export function describeMessage(
  message: string,
  details?: unknown,
): { summary: string; diagnostic: string[] } {
  const diagnostic: string[] = [];
  const lines = message
    .split("\n")
    .map((line) => line.trimEnd())
    .filter(Boolean);
  let summary = lines.length > 1 ? lines[lines.length - 1]! : (lines[0] ?? "");
  if (lines.length > 1) diagnostic.push(message.trim());
  const hashes = summary.match(HASH);
  if (hashes) {
    if (lines.length === 1) diagnostic.push(summary);
    summary = summary.replace(HASH, "[identifier in details]");
  }
  if (details !== undefined && details !== null)
    diagnostic.push(
      typeof details === "string" ? details : JSON.stringify(details, null, 2),
    );
  return { summary: summary.trim() || "Something did not work.", diagnostic };
}
const RULES: [FailureKind, RegExp][] = [
  [
    "connection",
    /synchroniz|reconnect|outcome is unknown|disposed|queue is full|network|connection|socket|timed out|timeout|unreachable|offline/i,
  ],
  [
    "dependency",
    /no module named|modulenotfounderror|importerror|dependenc|environment|interpreter|uv sync|lockfile|locked|pip\b|package|generation/i,
  ],
  [
    "missing-data",
    /insufficient|missing|not found|no (?:recording|result|data|saved|replication|run)|unavailable|keyerror|deleted|pruned|empty|revoked/i,
  ],
  [
    "model",
    /traceback|error:|exception|entrypoint|\bmodel\b|parameter|syntax|typeerror|valueerror|attributeerror|assertion|validation|stale|conflict/i,
  ],
];
const TITLES: Record<FailureKind, [string, string]> = {
  connection: [
    "Studio is not connected",
    "Drafts are retained. Commands resume when the connection returns.",
  ],
  dependency: [
    "The project environment is incomplete",
    "Prepare or repair the environment in Settings, then try again.",
  ],
  "missing-data": [
    "The requested data is not available",
    "Choose another run or produce the data first.",
  ],
  model: [
    "The model raised an error",
    "Fix the code in your editor, then prepare the model again.",
  ],
  unknown: [
    "Something did not work",
    "Try again. The diagnostic details describe what happened.",
  ],
};
/** Classify a failure as a model, dependency, missing-data or connection problem. */
export function classifyFailure(message: string, details?: unknown): Failure {
  const kind = RULES.find(([, re]) => re.test(message))?.[0] ?? "unknown";
  const [title, hint] = TITLES[kind];
  return { kind, title, hint, ...describeMessage(message, details) };
}
export function ActionButton({
  action,
  primary = false,
}: {
  action: Action;
  primary?: boolean;
}) {
  return (
    <span className="ws-action">
      <button
        type="button"
        className={primary ? "primary" : undefined}
        disabled={!!action.disabledReason}
        onClick={action.invoke}
      >
        {action.label}
      </button>
      {action.disabledReason && <small>{action.disabledReason}</small>}
    </span>
  );
}
export function Diagnostic({ lines }: { lines: readonly string[] }) {
  return lines.length ? (
    <details className="ws-diagnostic">
      <summary>Diagnostic details</summary>
      <pre>{lines.join("\n\n")}</pre>
    </details>
  ) : null;
}
/** A designed failure screen; ``role="alert"`` announces it. */
export function FailureNotice({
  message,
  details,
  retry,
  compact = false,
  announce = true,
}: {
  message: string;
  details?: unknown;
  retry?: Action;
  compact?: boolean;
  /** ``false`` for standing input issues that must not be announced as a new alert. */
  announce?: boolean;
}) {
  const failure = classifyFailure(message, details);
  return (
    <div
      role={announce ? "alert" : "note"}
      className={"ws-state ws-failure" + (compact ? " ws-compact" : "")}
      data-kind={failure.kind}
    >
      <strong>{failure.title}</strong>
      <p>{failure.summary}</p>
      <p className="ws-hint">{failure.hint}</p>
      {retry && <ActionButton action={retry} />}
      <Diagnostic lines={failure.diagnostic} />
    </div>
  );
}
export interface EmptyScreen {
  title: string;
  text?: string;
  link?: { label: string; hash: string };
}
export function EmptyState({
  screen,
  action,
}: {
  screen: EmptyScreen;
  action?: Action;
}) {
  return (
    <div className="ws-state ws-empty">
      <h2>{screen.title}</h2>
      {screen.text && <p>{screen.text}</p>}
      {(screen.link || action) && (
        <div className="ws-actions">
          {action && <ActionButton action={action} primary />}
          {screen.link && (
            <a className="ws-next-link" href={screen.link.hash}>
              {screen.link.label} →
            </a>
          )}
        </div>
      )}
    </div>
  );
}
/** Renders the page state; ``children`` receives the ready or stale value. */
export function Feedback<T>({
  state,
  loading = "Loading…",
  empty,
  children,
}: {
  state: PageState<T>;
  loading?: string;
  empty: EmptyScreen;
  children: (value: T) => ReactNode;
}) {
  switch (state.status) {
    case "loading":
      return (
        <div className="ws-state ws-loading-state" role="status" aria-busy="true">
          <span className="ws-spinner" aria-hidden="true" />
          <p>{loading}</p>
        </div>
      );
    case "empty":
      return <EmptyState screen={empty} {...(state.action ? { action: state.action } : {})} />;
    case "error":
      return <FailureNotice message={state.message} {...(state.retry ? { retry: state.retry } : {})} />;
    case "stale":
      return (
        <>
          <div className="ws-state ws-stale" role="status">
            <strong>Shown data may be out of date.</strong> {state.reason}
          </div>
          {children(state.value)}
        </>
      );
    default:
      return <>{children(state.value)}</>;
  }
}
