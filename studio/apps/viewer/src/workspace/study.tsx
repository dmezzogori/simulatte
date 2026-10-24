/** One experiment run read as a study's results: the forest-plot figure first, then its replications. */
import { useEffect, useRef, useState } from "react";
import { ExperimentCharts } from "../experiment-charts";
import type { FeedSnapshot } from "../experiment-feed";
import { ReplicationActions } from "../experiments";
import { replicationMarks } from "../findings";
import { MAX_OVERLAY } from "../overlay-charts";
import type { ResultsOptions } from "../results-table";
import type { ExperimentReport, ExperimentRun, ReplicationEntry, StudioClient } from "../studio-client";
import { ResultsPanel } from "./results-figure";
import "./findings.css";

const committed = (item: ReplicationEntry) => (item.status === "succeeded" || item.status === "reused") && item.trace !== null;
/** A run named for people: the experiment and when it ran; the run id stays a diagnostic. */
export function runDate(seconds: number): string {
  return new Date(seconds * 1000).toLocaleString(undefined, { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
}
export function runTitle(run: { name: string; created: number }): string {
  return `${run.name} · run of ${runDate(run.created)}`;
}
/** "7 of 8 replications succeeded, 1 failed": the run's state in one sentence. */
export function progressSentence(counts: Record<string, number>): string {
  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  const done = (counts.succeeded ?? 0) + (counts.reused ?? 0);
  const rest = Object.entries(counts)
    .filter(([status, n]) => n > 0 && status !== "succeeded" && status !== "reused")
    .map(([status, n]) => `${n} ${status}`);
  return `${done} of ${total} ${total === 1 ? "replication" : "replications"} succeeded` + (rest.length ? ", " + rest.join(", ") : "");
}
/** The first configuration is the baseline until the reader chooses another one or none. */
export function defaultBaseline(report: Pick<ExperimentReport, "configurations">, options: ResultsOptions, touched: boolean): ResultsOptions | null {
  if (touched || options.baseline !== null || report.configurations.length < 2) return null;
  return { ...options, baseline: report.configurations[0]!.label };
}
export function StudyResults({
  client,
  run,
  live,
  onOpen,
  onError,
  presentationHref,
}: {
  client: StudioClient;
  run: ExperimentRun;
  live: FeedSnapshot;
  onOpen(report: ExperimentReport, replication: ReplicationEntry): void;
  onError(message: string): void;
  presentationHref?: (attempt: string) => string;
}) {
  const [options, setOptions] = useState<ResultsOptions>({ level: 0.95, bonferroni: false, baseline: null });
  const [touched, setTouched] = useState(false);
  const [report, setReport] = useState<ExperimentReport | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [configuration, setConfiguration] = useState<number | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const request = useRef(0);
  const progress = live.progress(run.run_id)?.configurations;
  const progressKey = JSON.stringify(live.counts(run));
  useEffect(() => {
    const id = ++request.current;
    setBusy(true);
    void client
      .command("comparison.get", {
        run_id: run.run_id,
        level: options.level,
        bonferroni: options.bonferroni,
        ...(options.baseline === null ? {} : { baseline: options.baseline }),
        ...(configuration === null ? {} : { configuration }),
      })
      .then((result) => {
        if (id !== request.current) return;
        if (!result.ok) throw new Error((result.error as { message?: string })?.message ?? JSON.stringify(result.error));
        const value = result.report as ExperimentReport;
        const next = defaultBaseline(value, options, touched);
        if (next) setOptions(next);
        else setReport(value);
      })
      .catch((error) => {
        if (id === request.current) onError(String(error));
      })
      .finally(() => {
        if (id === request.current) setBusy(false);
      });
    return () => {
      request.current++;
    };
  }, [client, run.run_id, run.status, options.level, options.bonferroni, options.baseline, configuration, progressKey]);
  if (!report)
    return (
      <div className="ws-state ws-loading-state" role="status" aria-busy="true">
        <span className="ws-spinner" aria-hidden="true" />
        <p>Loading results…</p>
      </div>
    );
  const replications = report.replications.filter((item) => status === null || item.status === status);
  const chosen = report.replications.filter((item) => item.attempt !== null && selected.includes(item.attempt));
  const statuses = [...new Set(report.replications.map((r) => r.status))];
  const label = (index: number) => report.configurations[index]?.label ?? String(index);
  return (
    <>
      <ResultsPanel
        sources={[{ report }]}
        options={options}
        onOptions={(next) => {
          setTouched(true);
          setOptions(next);
        }}
        busy={busy}
      />
      <div className="fp-foot">
        <span>
          {runTitle(run)}. Replication <i>i</i> of every configuration uses the seed base seed + <i>i</i> of the definition, so
          the configurations share their random numbers replication by replication.
        </span>
        <details>
          <summary>Identifiers</summary>
          <dl className="ws-pairs">
            <dt>Experiment run</dt>
            <dd>
              <code>{run.run_id}</code>
            </dd>
            <dt>Experiment</dt>
            <dd>
              <code>{run.experiment_id}</code>
            </dd>
          </dl>
        </details>
      </div>
      <section className="st-section" id="replications" aria-labelledby="st-replications">
        <h2 id="st-replications">Replications</h2>
        <div className="st-strips">
          {report.configurations.map((c) => {
            const counts = progress?.[c.index] ?? c.counts;
            return (
              <div key={c.index} className="st-strip">
                <span>{c.label}</span>
                <span className="fp-marks-line">
                  <span className="ws-visually-hidden">{replicationMarks(counts).summary}</span>
                  <span aria-hidden="true" className="fp-marks">
                    {replicationMarks(counts, 200).marks.map((m, i) => (
                      <i key={i} className={"fp-mark " + m} />
                    ))}
                  </span>
                </span>
              </div>
            );
          })}
        </div>
        {report.missing_total > 0 && (
          <p className="st-missing">
            Excluded from every interval and difference:{" "}
            {report.missing.map((m) => `${label(m.configuration)} replication ${m.index} (${m.status})`).join("; ")}
            {report.missing.length < report.missing_total && `; and ${report.missing_total - report.missing.length} more`}.
          </p>
        )}
        <details>
          <summary>Replication list ({report.replications_total})</summary>
          <div className="st-filters">
            <label className="fp-inline-field">
              <span>Status</span>
              <select value={status ?? ""} onChange={(event) => setStatus(event.target.value || null)}>
                <option value="">All</option>
                {statuses.map((item) => (
                  <option key={item} value={item}>
                    {item}
                  </option>
                ))}
              </select>
            </label>
            <label className="fp-inline-field">
              <span>Configuration</span>
              <select value={configuration ?? ""} disabled={busy} onChange={(event) => setConfiguration(event.target.value === "" ? null : Number(event.target.value))}>
                <option value="">All</option>
                {report.configurations.map((item) => (
                  <option key={item.index} value={item.index}>
                    {item.label}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <div className="ws-table-wrap">
            <table className="ws-table">
              <thead>
                <tr>
                  <th scope="col">Configuration</th>
                  <th scope="col" className="ws-num">
                    Replication
                  </th>
                  <th scope="col">Status</th>
                  <th scope="col">Chart overlay</th>
                  <th scope="col">Recording</th>
                </tr>
              </thead>
              <tbody>
                {replications.map((item) => (
                  <tr key={`${item.configuration}\u0000${item.index}`}>
                    <td>{label(item.configuration)}</td>
                    <td className="ws-num">{item.index}</td>
                    <td>
                      <span className={"ws-badge " + item.status}>{item.status}</span>
                    </td>
                    <td>
                      <input
                        type="checkbox"
                        aria-label={`Overlay ${label(item.configuration)} replication ${item.index}`}
                        disabled={!committed(item) || (!selected.includes(item.attempt!) && selected.length >= MAX_OVERLAY)}
                        checked={item.attempt !== null && selected.includes(item.attempt)}
                        onChange={(event) =>
                          setSelected(event.target.checked ? [...selected, item.attempt!] : selected.filter((attempt) => attempt !== item.attempt))
                        }
                      />
                    </td>
                    <td className="ws-controls">
                      <ReplicationActions item={item} onOpen={() => onOpen(report, item)} {...(presentationHref ? { presentationHref } : {})} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {report.replications.length < report.replications_total && (
            <p className="ws-hint">
              Showing the first {report.replications.length} of {report.replications_total} replications; choose a configuration to list its own.
            </p>
          )}
        </details>
        <details>
          <summary>Charts over simulated time</summary>
          <ExperimentCharts client={client} report={report} selected={chosen} />
        </details>
      </section>
    </>
  );
}
