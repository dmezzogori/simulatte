/** Saved analysis outputs, numeric first: numbers, tables, distributions, images, then isolated figures. */
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { formatValue, untag, type Plain } from "../analysis/codec";
import { FullDigits, outputTitle, readableValue } from "./format";
export { outputTitle, readableValue } from "./format";
import { Diagnostic } from "../workspace/feedback";
import { DistributionSummary } from "./distribution";
import { FigureFrame, rendererState, validateFigure, type FrameState } from "./figure-frame";
import { ArtifactTable, CsvButton, SummaryTable } from "./table";
import type { ArtifactSource, OutputDescriptor, OutputKind, OutputManifest, OutputMetadata } from "./types";
import "./artifacts.css";
const ORDER: Record<OutputKind, number> = { number: 0, table: 1, distribution: 2, image: 3, figure: 4 };
/** Tables up to this many rows are read as summaries: shown whole, without paging, sorting or filtering controls. */
export const SUMMARY_ROWS = 50;
export const isSummary = (output: OutputDescriptor) => output.kind === "table" && (output.metadata.row_count ?? Infinity) <= SUMMARY_ROWS;
/** Order by meaning: numbers, summary tables, distributions, images and figures, then whole datasets. Outputs of one
 * kind keep the order the analysis emitted them in. */
export function orderOutputs(outputs: readonly OutputDescriptor[]): OutputDescriptor[] {
  const rank = (o: OutputDescriptor) => (o.kind === "table" && !isSummary(o) ? 5 : (ORDER[o.kind] ?? 9));
  return outputs
    .map((output, index) => ({ output, index }))
    .sort((a, b) => rank(a.output) - rank(b.output) || a.index - b.index)
    .map(({ output }) => output);
}
/** The snapshot facts every output repeats: shown once when all outputs share them. */
export function snapshotKey(metadata: OutputMetadata): string {
  return JSON.stringify([metadata.partial_snapshot, metadata.available, metadata.planned, metadata.input_availability ?? {}]);
}
/** Keys Studio writes itself; everything else is the analysis' own description of the output. */
const RECORDED_KEYS = new Set(["partial_snapshot", "planned", "available", "inferential_convention", "input_availability", "unit", "schema", "row_count", "media_type"]);
/** Enumerated metadata values in the product's words. */
const READABLE: Record<string, string> = { summary_only: "Summary statistics only" };
const scalar = (value: unknown) => value === null || ["string", "number", "boolean", "bigint"].includes(typeof value);
/** The analysis' metadata (population, convention, aggregation, window, …) as "key value" qualifiers, like the presentation evidence panel. */
export function metadataQualifiers(metadata: OutputMetadata): string[] {
  const lines: string[] = [];
  for (const [key, value] of Object.entries(metadata)) {
    if (RECORDED_KEYS.has(key)) continue;
    const text = scalar(value)
      ? formatValue(value)
      : Array.isArray(value) && value.length > 0 && value.every(scalar)
        ? value.map((item) => formatValue(item)).join(", ")
        : null;
    if (text === null || text === "—" || text === "") continue;
    const words = key.replaceAll("_", " ");
    const label = words.charAt(0).toUpperCase() + words.slice(1);
    if (READABLE[text]) lines.push(READABLE[text]);
    else if (value === true) lines.push(label);
    else if (value === false) lines.push("Not " + words);
    else lines.push(`${label}: ${text.replaceAll("_", " ")}`);
  }
  return lines;
}
export function missingLines(metadata: OutputMetadata): string[] {
  return Object.entries(metadata.input_availability ?? {}).flatMap(([dataset, item]) =>
    (item.missing ?? []).map(
      (m) =>
        `${dataset}: replication ${m.index ?? "?"}` +
        (m.configuration === undefined || m.configuration === null ? "" : ` of configuration ${m.configuration}`) +
        (m.status ? ` (${m.status})` : "") +
        (m.missing_fields?.length ? ` lacks ${m.missing_fields.join(", ")}` : "") +
        (m.reason ? `: ${m.reason}` : ""),
    ),
  );
}
function MissingInputs({ metadata }: { metadata: OutputMetadata }) {
  const missing = missingLines(metadata);
  if (!missing.length) return null;
  return (
    <details className="ws-diagnostic af-missing">
      <summary>
        {missing.length} unavailable {missing.length === 1 ? "input" : "inputs"}
      </summary>
      <ul>
        {missing.map((line, i) => (
          <li key={i}>{line}</li>
        ))}
      </ul>
    </details>
  );
}
export function ProvenanceNotes({ metadata, shared = false }: { metadata: OutputMetadata; shared?: boolean }) {
  const availability = Object.entries(metadata.input_availability ?? {});
  if (shared) {
    // The analysis' own convention, when it names one, is the more precise of the two.
    const convention = metadata.inferential_convention.replaceAll("_", " ");
    const own = metadataQualifiers(metadata);
    const lines = "convention" in metadata ? own : [convention.charAt(0).toUpperCase() + convention.slice(1) + " convention", ...own];
    return <p className="af-qualifiers">{lines.join(" · ")}</p>;
  }
  return (
    <div className="af-provenance">
      <span className={"ws-badge " + (metadata.partial_snapshot ? "amber" : "completed")}>
        {metadata.partial_snapshot ? "Partial snapshot" : "Complete snapshot"} · {metadata.available} of {metadata.planned}{" "}
        replications
      </span>
      <small className="ws-identity">
        {metadata.inferential_convention} convention
        {availability.length
          ? " · valid inputs: " + availability.map(([dataset, item]) => `${dataset} ${item.valid}`).join(", ")
          : ""}
      </small>
      {metadataQualifiers(metadata).map((line) => (
        <small key={line} className="ws-identity af-qualifier">
          {line}
        </small>
      ))}
      <MissingInputs metadata={metadata} />
    </div>
  );
}
type Load<T> = { status: "loading" } | { status: "ready"; value: T } | { status: "error"; message: string };
function useArtifactBlob(source: ArtifactSource, output: OutputDescriptor, path: string | undefined): Load<Blob> {
  const [load, setLoad] = useState<Load<Blob>>({ status: "loading" });
  useEffect(() => {
    if (!path) {
      setLoad({ status: "error", message: "The output lists no artifact file" });
      return;
    }
    let cancelled = false;
    setLoad({ status: "loading" });
    source
      .blob(output.id, path)
      .then((blob) => {
        if (!cancelled) setLoad({ status: "ready", value: blob });
      })
      .catch((error: unknown) => {
        if (!cancelled) setLoad({ status: "error", message: String(error instanceof Error ? error.message : error) });
      });
    return () => {
      cancelled = true;
    };
  }, [source, output.id, path]);
  return load;
}
function useObjectUrl(blob: Blob | null): string | null {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    if (!blob) return;
    const created = URL.createObjectURL(blob);
    setUrl(created);
    return () => {
      URL.revokeObjectURL(created);
      setUrl(null);
    };
  }, [blob]);
  return url;
}
async function parseTagged(blob: Blob): Promise<Plain> {
  return untag(JSON.parse(await blob.text()));
}
function useArtifactJson(source: ArtifactSource, output: OutputDescriptor, path: string | undefined): Load<Plain> {
  const blob = useArtifactBlob(source, output, path);
  const [load, setLoad] = useState<Load<Plain>>({ status: "loading" });
  useEffect(() => {
    if (blob.status !== "ready") {
      setLoad(blob.status === "error" ? blob : { status: "loading" });
      return;
    }
    let cancelled = false;
    parseTagged(blob.value)
      .then((value) => {
        if (!cancelled) setLoad({ status: "ready", value });
      })
      .catch((error: unknown) => {
        if (!cancelled) setLoad({ status: "error", message: String(error instanceof Error ? error.message : error) });
      });
    return () => {
      cancelled = true;
    };
  }, [blob]);
  return load;
}
const artifact = (output: OutputDescriptor, suffix: string) =>
  output.artifacts.find((a) => a.path.endsWith(suffix))?.path;
function Pending({ load, children }: { load: Load<Plain>; children: (value: Plain) => ReactNode }) {
  if (load.status === "loading")
    return (
      <p className="af-loading" role="status" aria-busy="true">
        Loading…
      </p>
    );
  if (load.status === "error")
    return (
      <p role="alert" className="af-issue">
        {load.message}
      </p>
    );
  return <>{children(load.value)}</>;
}
function Card({
  output,
  title,
  children,
  extra,
  shared,
  action,
}: {
  output: OutputDescriptor;
  title?: string;
  children: ReactNode;
  extra?: Record<string, string>;
  shared: boolean;
  action?: ReactNode;
}) {
  return (
    <section className={"af-card af-" + output.kind} data-kind={output.kind} data-output={output.name} {...extra}>
      <header className="af-card-head">
        <h3>{outputTitle(output.name)}</h3>
        <span className="af-kind">
          {title ?? output.kind} · <code>{output.name}</code>
          {action}
        </span>
      </header>
      {children}
      <ProvenanceNotes metadata={output.metadata} shared={shared} />
    </section>
  );
}
function NumberFinding({ output, source, full, shared }: { output: OutputDescriptor; source: ArtifactSource; full: boolean; shared: boolean }) {
  const load = useArtifactJson(source, output, artifact(output, "number.json"));
  const value = load.status === "ready" && load.value && typeof load.value === "object" && !Array.isArray(load.value)
    ? (load.value as { [key: string]: Plain }).value
    : null;
  return (
    <section className="af-finding af-number" data-kind="number" data-output={output.name}>
      <h3>
        {outputTitle(output.name)} <code>{output.name}</code>
      </h3>
      <Pending load={load}>
        {() => (
          <p className="af-value">
            {readableValue(value, undefined, full)}
            {output.metadata.unit && <small> {output.metadata.unit}</small>}
          </p>
        )}
      </Pending>
      <ProvenanceNotes metadata={output.metadata} shared={shared} />
    </section>
  );
}
/** The snapshot every output shares, said once above them. */
function SharedBasis({ metadata }: { metadata: OutputMetadata }) {
  const availability = Object.entries(metadata.input_availability ?? {});
  return (
    <div className="af-basis" data-basis="shared">
      <p>
        <span className={"ws-badge " + (metadata.partial_snapshot ? "amber" : "completed")}>
          {metadata.partial_snapshot ? "Partial snapshot" : "Complete snapshot"} · {metadata.available} of {metadata.planned} replications
        </span>{" "}
        Every output below reads the same inputs
        {availability.length ? "; valid inputs: " + availability.map(([dataset, item]) => `${dataset} ${item.valid}`).join(", ") : ""}.
      </p>
      <MissingInputs metadata={metadata} />
    </div>
  );
}
function ImageCard({ output, source, shared }: { output: OutputDescriptor; source: ArtifactSource; shared: boolean }) {
  const path = artifact(output, "image.png") ?? artifact(output, "image.svg");
  const load = useArtifactBlob(source, output, path);
  const url = useObjectUrl(load.status === "ready" ? load.value : null);
  const filename = path ? path.slice(path.lastIndexOf("/") + 1) : output.name;
  return (
    <Card output={output} title={output.metadata.media_type ?? "image"} shared={shared}>
      {load.status === "error" ? (
        <p role="alert" className="af-issue">
          {load.message}
        </p>
      ) : url ? (
        <figure className="af-image">
          <img src={url} alt={`Saved image ${output.name}`} />
          <figcaption>
            <a href={url} download={`${output.name}-${filename}`}>
              Download {filename}
            </a>
          </figcaption>
        </figure>
      ) : (
        <p className="af-loading" role="status" aria-busy="true">
          Loading image…
        </p>
      )}
    </Card>
  );
}
function FigureCard({ output, source, shared }: { output: OutputDescriptor; source: ArtifactSource; shared: boolean }) {
  const path = artifact(output, "figure.json");
  const blob = useArtifactBlob(source, output, path);
  const load = useArtifactJson(source, output, path);
  const url = useObjectUrl(blob.status === "ready" ? blob.value : null);
  const [frame, setFrame] = useState<FrameState>("loading");
  const renderer = rendererState(output.renderer);
  // Validated once per loaded specification, not on every render.
  const verdict = useMemo(() => (load.status === "ready" ? validateFigure(load.value) : null), [load]);
  const figureState =
    renderer.state !== "ready" ? renderer.state : verdict === null ? "loading" : !verdict.ok ? "refused" : frame;
  return (
    <Card output={output} title={`Plotly figure, renderer ${output.renderer?.version ?? "unknown"}`} extra={{ "data-figure": figureState }} shared={shared}>
      {renderer.state !== "ready" ? (
        <div className="ws-state ws-stale af-refusal" role="status">
          <strong>Not rendered.</strong> {renderer.label}. The saved specification stays downloadable.
        </div>
      ) : (
        <Pending load={load}>
          {() =>
            verdict && !verdict.ok ? (
              <div className="ws-state ws-failure ws-compact af-refusal" role="alert" data-kind="model">
                <strong>Figure refused</strong>
                <p>The saved specification was not rendered: {verdict.reason}.</p>
                <p className="ws-hint">Studio renders only self-contained data, layout and frames; no URLs, scripts or network sources.</p>
              </div>
            ) : verdict ? (
              <FigureFrame figure={verdict.figure} name={output.name} onState={setFrame} />
            ) : null
          }
        </Pending>
      )}
      {url && (
        <p className="af-downloads">
          <a href={url} download={`${output.name}.json`}>
            Download figure JSON
          </a>
        </p>
      )}
    </Card>
  );
}
function DistributionCard({
  output,
  source,
  tableLink,
  shared,
}: {
  output: OutputDescriptor;
  source: ArtifactSource;
  tableLink: (name: string) => string | null;
  shared: boolean;
}) {
  const load = useArtifactJson(source, output, artifact(output, "distribution.json"));
  return (
    <Card output={output} title="distribution" shared={shared}>
      <Pending load={load}>{(value) => <DistributionSummary output={output} value={value} tableLink={tableLink} />}</Pending>
    </Card>
  );
}
export function ArtifactView({ manifest, source }: { manifest: OutputManifest; source: ArtifactSource }) {
  const [full, setFull] = useState(false);
  const outputs = orderOutputs(manifest.outputs);
  const numbers = outputs.filter((o) => o.kind === "number");
  const rest = outputs.filter((o) => o.kind !== "number");
  const tableLink = (name: string) =>
    outputs.some((o) => o.name === name && o.kind === "table") ? "#output-" + encodeURIComponent(name) : null;
  if (!outputs.length)
    return (
      <div className="ws-state ws-empty">
        <h2>No outputs</h2>
        <p>The analysis finished without emitting outputs.</p>
      </div>
    );
  const shared = outputs.length > 1 && new Set(outputs.map((o) => snapshotKey(o.metadata))).size === 1;
  return (
    <FullDigits.Provider value={full}>
    <div className="af-outputs">
      <div className="af-outputs-bar">
        {shared ? <SharedBasis metadata={outputs[0]!.metadata} /> : <span />}
        <label className="fp-check">
          <input type="checkbox" checked={full} onChange={(event) => setFull(event.target.checked)} />
          Full digits
        </label>
      </div>
      {numbers.length > 0 && (
        <div className="af-numbers">
          {numbers.map((o) => (
            <NumberFinding key={o.id} output={o} source={source} full={full} shared={shared} />
          ))}
        </div>
      )}
      {rest.map((o) => (
        <div key={o.id} id={"output-" + encodeURIComponent(o.name)}>
          {o.kind === "table" ? (
            <Card
              output={o}
              title={`table, ${(o.metadata.row_count ?? 0).toLocaleString()} ${o.metadata.row_count === 1 ? "row" : "rows"}`}
              shared={shared}
              {...(isSummary(o) ? { action: <CsvButton output={o} source={source} /> } : {})}
            >
              {isSummary(o) ? <SummaryTable output={o} source={source} full={full} /> : <ArtifactTable output={o} source={source} />}
            </Card>
          ) : o.kind === "distribution" ? (
            <DistributionCard output={o} source={source} tableLink={tableLink} shared={shared} />
          ) : o.kind === "image" ? (
            <ImageCard output={o} source={source} shared={shared} />
          ) : o.kind === "figure" ? (
            <FigureCard output={o} source={source} shared={shared} />
          ) : (
            <Card output={o} shared={shared}>
              <Diagnostic lines={[`Unsupported output kind ${String(o.kind)}`]} />
            </Card>
          )}
        </div>
      ))}
    </div>
    </FullDigits.Provider>
  );
}
