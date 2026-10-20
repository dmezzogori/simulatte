/** Saved analyses and new analysis admission. Task 3 fills this page; the route identities are already honoured. */
import type { ConnectionStatus, StudioClient } from "../studio-client";
import { EmptyState } from "./feedback";
import type { ProjectState } from "./state";
export function AnalysesPage({
  executionId,
  registrationKey,
}: {
  client: StudioClient;
  state: ProjectState;
  status: ConnectionStatus;
  executionId?: string;
  registrationKey?: string;
}) {
  const title = registrationKey
    ? "New analysis"
    : executionId
      ? "Saved analysis"
      : "Analyses";
  return (
    <>
      <div className="ws-page-heading">
        <div className="ws-eyebrow">REPRODUCIBLE ANALYSIS</div>
        <h1>{title}</h1>
        <p>Named definitions, saved executions, their inputs and outputs.</p>
        {registrationKey && (
          <p className="ws-identity">
            Definition: <code>{registrationKey}</code>
          </p>
        )}
        {executionId && (
          <p className="ws-identity">
            Execution: <code>{executionId}</code>
          </p>
        )}
      </div>
      <EmptyState
        screen={{
          title: "This workspace is not available yet",
          text: "Saved model runs and recordings remain available in Runs. Analyses run from the terminal keep their outputs in the project.",
          link: { label: "Browse runs", hash: "#/runs" },
        }}
      />
    </>
  );
}
