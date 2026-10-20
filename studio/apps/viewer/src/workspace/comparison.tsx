/** Comparison of selected runs or replications. Task 2 fills this page; the route identity is already honoured. */
import type { StudioClient } from "../studio-client";
import { EmptyState } from "./feedback";
import type { ProjectState } from "./state";
export function ComparisonPage({
  selection,
}: {
  client: StudioClient;
  state: ProjectState;
  selection: string[];
}) {
  return (
    <>
      <div className="ws-page-heading">
        <div className="ws-eyebrow">NUMERICAL COMPARISON</div>
        <h1>Comparison</h1>
        <p>Paired differences and intervals across the selected results.</p>
      </div>
      {selection.length > 0 && (
        <ul className="ws-identity-list" aria-label="Selected results">
          {selection.map((id) => (
            <li key={id}>
              <code>{id}</code>
            </li>
          ))}
        </ul>
      )}
      <EmptyState
        screen={{
          title: selection.length
            ? "Comparison tables are not available yet"
            : "Nothing selected to compare",
          text: selection.length
            ? "The selected results stay in this address; their comparison arrives with the experiments workspace."
            : "Choose configurations or runs from an experiment to compare them.",
          link: { label: "Open experiments", hash: "#/experiments" },
        }}
      />
    </>
  );
}
