/** Saved analysis outputs, numeric first: numbers, tables, distributions, images, then isolated figures. */
import { useEffect, useState, type ReactNode } from "react";
import { formatValue, untag, type Plain } from "../analysis/codec";
import { Diagnostic } from "../workspace/feedback";
import { DistributionSummary } from "./distribution";
import { FigureFrame, rendererState, validateFigure, type FrameState } from "./figure-frame";
import { ArtifactTable } from "./table";
import type { ArtifactSource, OutputDescriptor, OutputKind, OutputManifest, OutputMetadata } from "./types";
import "./artifacts.css";
const ORDER: Record<OutputKind, number> = { number: 0, table: 1, distribution: 2, image: 3, figure: 4 };
/** Numeric-first order; outputs of one kind keep the order the analysis emitted them in. */
export function orderOutputs(outputs: readonly OutputDescriptor[]): OutputDescriptor[] {
  return outputs
    .map((output, index) => ({ output, index }))
    .sort((a, b) => (ORDER[a.output.kind] ?? 9) - (ORDER[b.output.kind] ?? 9) || a.index - b.index)
    .map(({ output }) => output);
}
export function ProvenanceNotes({ metadata }: { metadata: OutputMetadata }) {
  const availability = Object.entries(metadata.input_availability ?? {});
  const missing = availability.flatMap(([dataset, item]) =>
    (item.missing ?? []).map(
      (m) =>
        `${dataset}: replication ${m.index ?? "?"}` +
        (m.configuration === undefined || m.configuration === null ? "" : ` of configuration ${m.configuration}`) +
        (m.status ? ` (${m.status})` : "") +
        (m.missing_fields?.length ? ` lacks ${m.missing_fields.join(", ")}` : "") +
        (m.reason ? `: ${m.reason}` : ""),
    ),
  );
  return (
    <div className="af-provenance">
      <span className={"ws-badge " + (metadata.partial_snapshot ? "amber" : "")}>
        {metadata.partial_snapshot ? "Partial snapshot" : "Complete snapshot"} · {metadata.available} of {metadata.planned}{" "}
        replications
      </span>
      <small className="ws-identity">
        {metadata.inferential_convention} convention
        {availability.length
          ? " · valid inputs: " + availability.map(([dataset, item]) => `${dataset} ${item.valid}`).join(", ")
          : ""}
      </small>
      {missing.length > 0 && (
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
      )}
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
}: {
  output: OutputDescriptor;
  title?: string;
  children: ReactNode;
  extra?: Record<string, string>;
}) {
  return (
    <section className={"ws-card af-card af-" + output.kind} data-kind={output.kind} data-output={output.name} {...extra}>
      <header className="af-card-head">
        <h3>{output.name}</h3>
        <small className="ws-identity">{title ?? output.kind}</small>
      </header>
      {children}
      <ProvenanceNotes metadata={output.metadata} />
    </section>
  );
}
function NumberTile({ output, source }: { output: OutputDescriptor; source: ArtifactSource }) {
  const load = useArtifactJson(source, output, artifact(output, "number.json"));
  const value = load.status === "ready" && load.value && typeof load.value === "object" && !Array.isArray(load.value)
    ? (load.value as { [key: string]: Plain }).value
    : null;
  return (
    <section className="ws-tile af-card af-number" data-kind="number" data-output={output.name}>
      <h3>{output.name}</h3>
      <Pending load={load}>
        {() => (
          <p className="ws-number af-value">
            {formatValue(value)}
            {output.metadata.unit && <small> {output.metadata.unit}</small>}
          </p>
        )}
      </Pending>
      <ProvenanceNotes metadata={output.metadata} />
    </section>
  );
}
function ImageCard({ output, source }: { output: OutputDescriptor; source: ArtifactSource }) {
  const path = artifact(output, "image.png") ?? artifact(output, "image.svg");
  const load = useArtifactBlob(source, output, path);
  const url = useObjectUrl(load.status === "ready" ? load.value : null);
  const filename = path ? path.slice(path.lastIndexOf("/") + 1) : output.name;
  return (
    <Card output={output} title={output.metadata.media_type ?? "image"}>
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
function FigureCard({ output, source }: { output: OutputDescriptor; source: ArtifactSource }) {
  const path = artifact(output, "figure.json");
  const blob = useArtifactBlob(source, output, path);
  const load = useArtifactJson(source, output, path);
  const url = useObjectUrl(blob.status === "ready" ? blob.value : null);
  const [frame, setFrame] = useState<FrameState>("loading");
  const renderer = rendererState(output.renderer);
  const verdict = load.status === "ready" ? validateFigure(load.value) : null;
  const figureState =
    renderer.state !== "ready" ? renderer.state : verdict === null ? "loading" : !verdict.ok ? "refused" : frame;
  return (
    <Card output={output} title={`plotly figure · renderer ${output.renderer?.version ?? "unknown"}`} extra={{ "data-figure": figureState }}>
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
}: {
  output: OutputDescriptor;
  source: ArtifactSource;
  tableLink: (name: string) => string | null;
}) {
  const load = useArtifactJson(source, output, artifact(output, "distribution.json"));
  return (
    <Card output={output}>
      <Pending load={load}>{(value) => <DistributionSummary output={output} value={value} tableLink={tableLink} />}</Pending>
    </Card>
  );
}
export function ArtifactView({ manifest, source }: { manifest: OutputManifest; source: ArtifactSource }) {
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
  return (
    <div className="af-outputs">
      {numbers.length > 0 && (
        <div className="ws-tiles af-numbers">
          {numbers.map((o) => (
            <NumberTile key={o.id} output={o} source={source} />
          ))}
        </div>
      )}
      {rest.map((o) => (
        <div key={o.id} id={"output-" + encodeURIComponent(o.name)}>
          {o.kind === "table" ? (
            <Card output={o} title={`table · ${(o.metadata.row_count ?? 0).toLocaleString()} rows`}>
              <ArtifactTable output={o} source={source} />
            </Card>
          ) : o.kind === "distribution" ? (
            <DistributionCard output={o} source={source} tableLink={tableLink} />
          ) : o.kind === "image" ? (
            <ImageCard output={o} source={source} />
          ) : o.kind === "figure" ? (
            <FigureCard output={o} source={source} />
          ) : (
            <Card output={o}>
              <Diagnostic lines={[`Unsupported output kind ${String(o.kind)}`]} />
            </Card>
          )}
        </div>
      ))}
    </div>
  );
}
