/** Numerical comparison of experiment runs and configurations at `#/compare/<run>[:<configuration>],…`.
 * Every number comes from `comparison.get`; the page computes nothing itself. */
import { useEffect, useState } from "react";
import type { ExperimentReport, StudioClient } from "../studio-client";
import type { ResultsOptions } from "../results-table";
import { ResultsPanel, type FigureSource } from "./results-figure";
import { progressSentence, runTitle } from "./study";
import { EmptyState, FailureNotice, Feedback, type PageState } from "./feedback";
import { routeHash, type ProjectState } from "./state";
import "./research.css";

/** At most this many runs are fetched for one comparison page. */
export const MAX_COMPARED = 8;
const RUN_ID = /^[a-f0-9]{64}$/;
export interface Selection {
  /** Run id → configuration indexes to show, or ``null`` for every configuration. */
  runs: Map<string, Set<number> | null>;
  invalid: string[];
}
/** ``<run_id>`` selects a whole run; ``<run_id>:<index>`` one configuration of it. */
export function parseSelection(selection: readonly string[]): Selection {
  const runs = new Map<string, Set<number> | null>();
  const invalid: string[] = [];
  for (const id of selection) {
    const [run = "", index] = id.split(":");
    if (!RUN_ID.test(run) || (index !== undefined && !/^(0|[1-9][0-9]{0,8})$/.test(index))) {
      invalid.push(id);
      continue;
    }
    const existing = runs.get(run);
    if (index === undefined) runs.set(run, null);
    else if (existing === undefined) runs.set(run, new Set([Number(index)]));
    else if (existing !== null) existing.add(Number(index));
  }
  return { runs, invalid };
}
/** The selected runs on one figure: each run's configurations are rows of the same KPI blocks, on shared axes.
 * Differences stay within a run, paired by replication; nothing is pooled across runs. */
export function ComparisonFigure({
  entries,
  options,
  onOptions,
  busy = false,
}: {
  entries: readonly { report: ExperimentReport; configurations?: ReadonlySet<number> | null; created?: number }[];
  options: ResultsOptions;
  onOptions: (options: ResultsOptions) => void;
  busy?: boolean;
}) {
  const sources: FigureSource[] = entries.map(({ report, configurations = null, created }) => ({
    report,
    configurations,
    title: created === undefined ? report.name : runTitle({ name: report.name, created }),
  }));
  return (
    <section className="ws-comparison" aria-label={"Comparison of " + entries.map(({ report }) => report.name).join(", ")}>
      <ul className="cmp-runs">
        {entries.map(({ report }) => (
          <li key={report.run_id}>
            <a href={routeHash({ page: "experiments", run: report.run_id })}>{report.name}</a>{" "}
            <span className={"ws-badge " + (report.missing_total > 0 ? "amber" : report.status)}>{report.status}</span>{" "}
            <span className="ws-hint">
              {report.configurations.map((c) => `${c.label}: ${progressSentence(c.counts)}`).join(" · ")}
            </span>
          </li>
        ))}
      </ul>
      <p className="ws-hint">
        Estimates are replication-level t intervals within each experiment run. Failed or missing replications are excluded from
        every interval; a single replication gives no interval. Differences are paired by replication inside one run and never
        across runs.
      </p>
      <ResultsPanel sources={sources} options={options} onOptions={onOptions} busy={busy}>
        {entries.some(({ report }) => report.missing_total > 0) && (
          <p className="st-missing">
            Excluded from every interval and difference:{" "}
            {entries
              .flatMap(({ report }) =>
                report.missing.map((m) => `${report.name} · ${report.configurations[m.configuration]?.label ?? m.configuration} replication ${m.index} (${m.status})`),
              )
              .join("; ")}
            .
          </p>
        )}
      </ResultsPanel>
    </section>
  );
}
export function ComparisonPage({
  client,
  state,
  selection,
}: {
  client: StudioClient;
  state: ProjectState;
  selection: string[];
}) {
  const parsed = parseSelection(selection);
  const ids = [...parsed.runs.keys()].slice(0, MAX_COMPARED);
  const [options, setOptions] = useState<ResultsOptions>({ level: 0.95, bonferroni: false, baseline: null });
  const [page, setPage] = useState<PageState<Map<string, ExperimentReport>>>(ids.length ? { status: "loading" } : { status: "empty" });
  const [busy, setBusy] = useState(false);
  const experiments = client.state?.experiments ?? [];
  const progress = ids.map((id) => experiments.find((e) => e.run_id === id)?.status ?? "").join();
  const load = async () => {
    if (!ids.length) {
      setPage({ status: "empty" });
      return;
    }
    setBusy(true);
    try {
      const reports = new Map<string, ExperimentReport>();
      const failures: string[] = [];
      for (const id of ids) {
        const r = await client.command("comparison.get", {
          run_id: id,
          level: options.level,
          bonferroni: options.bonferroni,
          ...(options.baseline === null ? {} : { baseline: options.baseline }),
        });
        if (r.ok) reports.set(id, r.report as ExperimentReport);
        else failures.push((r.error as { message?: string })?.message ?? "Comparison failed");
      }
      if (!reports.size) throw Error(failures[0] ?? "No comparison available");
      setPage(failures.length ? { status: "stale", value: reports, reason: failures.join("; ") } : { status: "ready", value: reports });
    } catch (e) {
      setPage({ status: "error", message: String(e), retry: { label: "Try again", invoke: () => void load() } });
    } finally {
      setBusy(false);
    }
  };
  useEffect(() => {
    void load();
  }, [client, selection.join(), options.level, options.bonferroni, options.baseline, progress]);
  return (
    <>
      <div className="ws-page-heading">
        <div>
          <h1>Comparison</h1>
          <p>The selected runs on shared axes, KPI by KPI. Bookmark this address to come back to the same selection.</p>
        </div>
        <a href="#/experiments">Compare recordings side by side</a>
      </div>
      {parsed.invalid.length > 0 && (
        <FailureNotice compact message={`Ignored ${parsed.invalid.length} selection entr${parsed.invalid.length === 1 ? "y" : "ies"} that name no experiment run.`} details={parsed.invalid} />
      )}
      {parsed.runs.size > MAX_COMPARED && (
        <FailureNotice compact message={`Showing the first ${MAX_COMPARED} of ${parsed.runs.size} selected runs.`} />
      )}
      {state.read_only && <p className="ws-hint">Read-only session: saved results are shown, nothing is recomputed.</p>}
      <Feedback
        state={page}
        loading="Loading comparison…"
        empty={{
          title: "Nothing selected to compare",
          text: "Choose experiment runs or configurations from an experiment to compare their results.",
          link: { label: "Open experiments", hash: "#/experiments" },
        }}
      >
        {(reports) => (
          <>
            {ids
              .filter((id) => !reports.has(id))
              .map((id) => (
                <EmptyState
                  key={id}
                  screen={{ title: "Experiment run not available", text: "It may have been deleted; its selection stays in this address.", link: { label: "Open experiments", hash: "#/experiments" } }}
                />
              ))}
            <ComparisonFigure
              entries={ids.flatMap((id) => {
                const report = reports.get(id);
                const created = experiments.find((e) => e.run_id === id)?.created;
                return report ? [{ report, configurations: parsed.runs.get(id) ?? null, ...(created === undefined ? {} : { created }) }] : [];
              })}
              options={options}
              onOptions={setOptions}
              busy={busy}
            />
          </>
        )}
      </Feedback>
    </>
  );
}
