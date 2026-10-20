/** Numerical comparison of experiment runs and configurations at `#/compare/<run>[:<configuration>],…`.
 * Every number comes from `comparison.get`; the page computes nothing itself. */
import { useEffect, useState } from "react";
import type { ExperimentReport, StudioClient } from "../studio-client";
import { Counts } from "../experiments";
import { ResultsTable, intervalLabel, type ResultsOptions } from "../results-table";
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
/** One run's counts per configuration and its server-computed results table. Static markup only needs ``report``. */
export function ComparisonReport({
  report,
  options,
  onOptions,
  configurations = null,
  busy = false,
}: {
  report: ExperimentReport;
  options: ResultsOptions;
  onOptions: (options: ResultsOptions) => void;
  configurations?: ReadonlySet<number> | null;
  busy?: boolean;
}) {
  const shown = report.configurations.filter((c) => configurations === null || configurations.has(c.index));
  const filtered: ExperimentReport = configurations === null ? report : {
    ...report,
    rows: report.rows.filter((r) => configurations.has(r.configuration)),
    missing: report.missing.filter((m) => configurations.has(m.configuration)),
    ...(report.paired ? { paired: report.paired.filter((p) => configurations.has(p.configuration)) } : {}),
  };
  return (
    <section className="ws-card ws-comparison" aria-label={`Comparison of ${report.name}`}>
      <div className="ws-section-heading">
        <h2>
          {report.name} <span className={"ws-badge " + report.status}>{report.status}</span>
        </h2>
        <a href={routeHash({ page: "experiments", run: report.run_id })}>Open experiment →</a>
      </div>
      <table className="ws-table ws-counts">
        <thead>
          <tr>
            <th scope="col">Configuration</th>
            <th scope="col">Replications</th>
            <th scope="col">Runtime</th>
          </tr>
        </thead>
        <tbody>
          {shown.map((c) => (
            <tr key={c.index}>
              <td>{c.label}</td>
              <td>
                <Counts counts={c.counts} />
              </td>
              <td>{c.mixed_runtime ? <span className="badge mixed">mixed runtime</span> : "consistent"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="ws-hint">
        Estimates are replication-level t intervals within each experiment run ({intervalLabel(report)}). Failed or
        missing replications are listed below and excluded from every interval; a single replication gives no interval.
        Distributions of individual observations are not pooled across runs here.
      </p>
      <ResultsTable report={filtered} options={options} onOptions={onOptions} busy={busy} />
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
          <div className="ws-eyebrow">NUMERICAL COMPARISON</div>
          <h1>Comparison</h1>
          <p>Replication-level estimates and paired differences of the selected experiment runs.</p>
        </div>
        <a href="#/experiments">Compare recordings side by side →</a>
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
            {ids.map((id) => {
              const report = reports.get(id);
              return report ? (
                <ComparisonReport key={id} report={report} options={options} onOptions={setOptions} configurations={parsed.runs.get(id) ?? null} busy={busy} />
              ) : (
                <EmptyState key={id} screen={{ title: "Experiment run not available", text: "It may have been deleted; its selection stays in this address.", link: { label: "Open experiments", hash: "#/experiments" } }} />
              );
            })}
          </>
        )}
      </Feedback>
    </>
  );
}
