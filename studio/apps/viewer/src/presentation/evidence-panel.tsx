/** The fixed evidence panel of a presentation and the helpers that choose evidence from saved analyses.
 * Labels are copied from the output metadata when an item is selected and never edited afterwards. */
import { Fragment, useEffect, useState, type ReactNode } from "react";
import type { EvidenceChoice, EvidenceLabels, EvidenceSelection, PresentationManifest } from "@simulatte/playback";
import { canonicalHash } from "@simulatte/export";
import { formatValue, untag, type Plain } from "../analysis/codec";
import { FigureFrame, rendererState, validateFigure } from "../artifacts/figure-frame";
import { httpArtifactSource, type FetchApi } from "../artifacts/source";
import type { ArtifactSource, OutputDescriptor, OutputManifest, Row } from "../artifacts/types";
import "./presentation.css";

/** One analysis execution's artifacts plus its output descriptors, which list each output's artifact paths. */
export interface EvidenceReader extends ArtifactSource {
  descriptor(outputId: string): Promise<OutputDescriptor>;
}
/** Where the panel reads evidence bytes: one P3 artifact reader per analysis execution. */
export type EvidenceSource = (executionId: string) => EvidenceReader;
/** Readers over ``analysis.get`` (one call per execution, cached) and the artifact routes, as the export reads them. */
export function analysisEvidenceSource(
  command: (type: "analysis.get", body: Record<string, unknown>) => Promise<Record<string, unknown>>,
  fetchApi: FetchApi,
): EvidenceSource {
  const readers = new Map<string, EvidenceReader>();
  return (executionId) => {
    let reader = readers.get(executionId);
    if (!reader) {
      let manifest: Promise<OutputManifest> | null = null;
      const load = () =>
        (manifest ??= command("analysis.get", { execution_id: executionId }).then((reply) => {
          if (!reply.ok) throw new Error(((reply.error as { message?: string }) ?? {}).message ?? "Analysis unavailable");
          if (!reply.output) throw new Error("The analysis has no outputs");
          return untag(reply.output) as unknown as OutputManifest;
        })).catch((error: unknown) => {
          manifest = null;
          throw error;
        });
      reader = {
        ...httpArtifactSource(fetchApi, executionId),
        async descriptor(outputId) {
          const found = (await load()).outputs.find((o) => o.id === outputId);
          if (!found) throw new Error("The output is not in the analysis manifest");
          return found;
        },
      };
      readers.set(executionId, reader);
    }
    return reader;
  };
}
export interface EvidenceOrigin {
  execution_id: string;
  analysis_name: string;
  snapshot_hash: string;
}
/** The identity a selection is bound to: the domain-separated canonical hash of the plain OutputDescriptor,
 * byte-identical to the server's ``canonical_hash("presentation-evidence-output-v1", descriptor)``. */
export const EVIDENCE_HASH_DOMAIN = "presentation-evidence-output-v1";
export async function contentHash(output: OutputDescriptor): Promise<string> {
  return canonicalHash(EVIDENCE_HASH_DOMAIN, output);
}
/** The immutable labels of an output as P3 describes it: unit, population, sample counts and convention. */
export function labelsOf(output: OutputDescriptor, origin: EvidenceOrigin): EvidenceLabels {
  const m = output.metadata;
  const population = typeof m["population"] === "string" ? (m["population"] as string) : null;
  return Object.freeze({
    name: output.name,
    unit: typeof m.unit === "string" ? m.unit : null,
    population,
    sample: Object.freeze({ planned: m.planned, available: m.available, partial: m.partial_snapshot }),
    convention: m.inferential_convention,
    source: Object.freeze({ analysis_name: origin.analysis_name, snapshot_hash: origin.snapshot_hash, execution_id: origin.execution_id }),
  });
}
/** A selection over one output; the choice must fit the output kind. */
export async function evidenceFromOutput(output: OutputDescriptor, origin: EvidenceOrigin, choice: EvidenceChoice): Promise<EvidenceSelection> {
  return Object.freeze({
    id: crypto.randomUUID(),
    execution_id: origin.execution_id,
    output_id: output.id,
    content_hash: await contentHash(output),
    kind: output.kind,
    selection: choice,
    labels: labelsOf(output, origin),
  });
}
/** Readable qualifiers: units, population, sample size, partial snapshot and convention. */
export function qualifierText(labels: EvidenceLabels): string[] {
  const lines = [];
  if (labels.unit) lines.push(`unit ${labels.unit}`);
  if (labels.population) lines.push(`population ${labels.population}`);
  lines.push(`${labels.sample.available} of ${labels.sample.planned} replications${labels.sample.partial ? " · partial snapshot" : ""}`);
  lines.push(labels.convention);
  return lines;
}
export function EvidenceQualifiers({ labels }: { labels: EvidenceLabels }) {
  return (
    <ul className="pv-qualifiers" aria-label={`Qualifiers of ${labels.name}`}>
      {qualifierText(labels).map((line) => (
        <li key={line}>{line}</li>
      ))}
      <li className="pv-source">
        {labels.source.analysis_name} · snapshot {labels.source.snapshot_hash.slice(0, 12)}
      </li>
    </ul>
  );
}
type Loaded<T> = { status: "loading" } | { status: "ready"; value: T } | { status: "error"; message: string };
function useArtifact<T>(load: ((signal: AbortSignal) => Promise<T>) | null, deps: readonly unknown[]): Loaded<T> {
  const [state, setState] = useState<Loaded<T>>({ status: "loading" });
  useEffect(() => {
    if (!load) return;
    const controller = new AbortController();
    setState({ status: "loading" });
    load(controller.signal).then(
      (value) => {
        if (!controller.signal.aborted) setState({ status: "ready", value });
      },
      (error: unknown) => {
        if (!controller.signal.aborted) setState({ status: "error", message: error instanceof Error ? error.message : String(error) });
      },
    );
    return () => {
      controller.abort();
    };
  }, deps);
  return state;
}
/** An item's bytes at the path its output descriptor lists (never derived from the label name), as the export
 * reads them. With several candidate files the first one the output lists wins. */
export async function evidenceFile(source: EvidenceReader, item: EvidenceSelection, ...files: [string, ...string[]]): Promise<Blob> {
  const { artifacts } = await source.descriptor(item.output_id);
  for (const file of files) {
    const listed = artifacts.find((a) => a.path === file || a.path.endsWith("/" + file));
    if (listed) return source.blob(item.output_id, listed.path);
  }
  throw new Error(`The output lists no ${files.join(" or ")}`);
}
/** One field of an analysis document (``number.json``, ``distribution.json``), written as tagged JSON. */
export function documentField(document: unknown, field: string): Plain {
  const plain = untag(document) as Record<string, Plain> | null;
  return plain !== null && typeof plain === "object" && field in plain ? plain[field]! : null;
}
const blobJson = async (blob: Blob | Promise<Blob>): Promise<unknown> => JSON.parse(await (await blob).text());
function NumberItem({ item, source }: { item: EvidenceSelection; source: EvidenceReader }) {
  const loaded = useArtifact(() => blobJson(evidenceFile(source, item, "number.json")), [item.output_id]);
  if (loaded.status !== "ready") return <p className="pv-value" role={loaded.status === "error" ? "alert" : "status"}>{loaded.status === "error" ? loaded.message : "…"}</p>;
  const value = documentField(loaded.value, "value");
  return (
    <p className="pv-value">
      {formatValue(value)}
      {item.labels.unit && <small> {item.labels.unit}</small>}
    </p>
  );
}
/** Rows ``offset..offset+limit`` of the selected columns, read through the bounded server pages. The cursor is
 * opaque, so pages are read in order until the offset is reached; the table's ``row_count`` bounds the walk. */
export async function excerptRows(source: ArtifactSource, outputId: string, rows: { offset: number; limit: number; columns: readonly string[] }, signal?: AbortSignal): Promise<Row[]> {
  const collected: Row[] = [];
  let cursor: string | null = null;
  let skipped = 0;
  let pages = Infinity;
  for (let page = 0; page < pages && collected.length < rows.limit; page++) {
    signal?.throwIfAborted();
    const batch = await source.page(outputId, cursor, Math.min(500, rows.offset + rows.limit - skipped - collected.length), undefined, signal);
    signal?.throwIfAborted();
    // Every page but the last holds at least one row, so no walk needs more pages than the table has rows.
    if (page === 0) {
      if (rows.offset >= batch.row_count) throw new Error(`Table excerpt offset ${rows.offset} exceeds ${batch.row_count} rows`);
      pages = batch.row_count + 1;
    }
    if (!batch.rows.length) break;
    for (const row of batch.rows) {
      if (skipped < rows.offset) skipped++;
      else if (collected.length < rows.limit) collected.push(rows.columns.length ? Object.fromEntries(rows.columns.map((c) => [c, row[c] ?? null])) : row);
    }
    cursor = batch.next_cursor;
    if (cursor === null) break;
  }
  return collected;
}
function TableItem({ item, source }: { item: EvidenceSelection; source: EvidenceReader }) {
  const rows = "rows" in item.selection ? item.selection.rows : null;
  const loaded = useArtifact(rows ? (signal) => excerptRows(source, item.output_id, rows, signal) : null, [source, item.output_id, rows?.offset, rows?.limit, rows?.columns.join("\0")]);
  if (!rows) return null;
  if (loaded.status !== "ready") return <p role={loaded.status === "error" ? "alert" : "status"}>{loaded.status === "error" ? loaded.message : "…"}</p>;
  const columns = rows.columns.length ? rows.columns : Object.keys(loaded.value[0] ?? {});
  return (
    <div className="pv-table">
      <table className="ws-table">
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c} scope="col">{c}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {loaded.value.map((row, i) => (
            <tr key={i}>
              {columns.map((c) => (
                <td key={c} className="ws-num">{formatValue(row[c])}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      <small>
        Rows {rows.offset + 1}–{rows.offset + loaded.value.length}
      </small>
    </div>
  );
}
function ImageItem({ item, source }: { item: EvidenceSelection; source: EvidenceReader }) {
  const loaded = useArtifact(async () => {
    return URL.createObjectURL(await evidenceFile(source, item, "image.png", "image.svg"));
  }, [item.output_id]);
  useEffect(() => () => {
    if (loaded.status === "ready") URL.revokeObjectURL(loaded.value);
  }, [loaded]);
  if (loaded.status !== "ready") return <p role={loaded.status === "error" ? "alert" : "status"}>{loaded.status === "error" ? loaded.message : "…"}</p>;
  return <img src={loaded.value} alt={item.labels.name} />;
}
function FigureItem({ item, source }: { item: EvidenceSelection; source: EvidenceReader }) {
  const size = "figure" in item.selection ? item.selection.figure : { width: 640, height: 360 };
  const loaded = useArtifact(() => blobJson(evidenceFile(source, item, "figure.json")), [item.output_id]);
  if (loaded.status !== "ready") return <p role={loaded.status === "error" ? "alert" : "status"}>{loaded.status === "error" ? loaded.message : "…"}</p>;
  const spec = untag(loaded.value) as { data?: unknown; layout?: unknown; renderer?: { name?: string; version?: string } };
  const renderer = rendererState(spec.renderer ?? null);
  if (renderer.state !== "ready") return <p className="pv-note" data-figure={renderer.state}>Not rendered: {renderer.label}</p>;
  const verdict = validateFigure(spec);
  if (!verdict.ok) return <p className="pv-note" data-figure="refused">Figure refused: {verdict.reason}</p>;
  return (
    <div className="pv-figure" style={{ aspectRatio: `${size.width} / ${size.height}` }}>
      <FigureFrame figure={verdict.figure} name={item.labels.name} />
    </div>
  );
}
function DistributionItem({ item, source }: { item: EvidenceSelection; source: EvidenceReader }) {
  const loaded = useArtifact(() => blobJson(evidenceFile(source, item, "distribution.json")), [item.output_id]);
  if (loaded.status !== "ready") return <p role={loaded.status === "error" ? "alert" : "status"}>{loaded.status === "error" ? loaded.message : "…"}</p>;
  const summary = documentField(loaded.value, "summary") as Record<string, unknown> | null;
  const quantiles = (summary?.["quantiles"] ?? {}) as Record<string, unknown>;
  return (
    <dl className="ws-pairs pv-distribution">
      <dt>Representation</dt>
      <dd>{formatValue(summary?.["representation"] ?? "—")}</dd>
      <dt>Count</dt>
      <dd className="ws-num">{formatValue(summary?.["count"] ?? "—")}</dd>
      {Object.entries(quantiles).map(([q, v]) => (
        <Fragment key={q}>
          <dt>q{q}</dt>
          <dd className="ws-num">{formatValue(v)}</dd>
        </Fragment>
      ))}
    </dl>
  );
}
export function EvidenceItem({ item, source }: { item: EvidenceSelection; source: EvidenceReader | null }) {
  const body: ReactNode = !source ? (
    <p className="pv-note">Evidence bytes are read from the saved analysis when a source is available.</p>
  ) : item.kind === "number" ? (
    <NumberItem item={item} source={source} />
  ) : item.kind === "table" ? (
    <TableItem item={item} source={source} />
  ) : item.kind === "image" ? (
    <ImageItem item={item} source={source} />
  ) : item.kind === "figure" ? (
    <FigureItem item={item} source={source} />
  ) : (
    <DistributionItem item={item} source={source} />
  );
  return (
    <section className="pv-evidence" data-evidence={item.id} data-kind={item.kind} aria-label={item.labels.name}>
      <h3>{item.labels.name}</h3>
      {body}
      <EvidenceQualifiers labels={item.labels} />
      <a className="pv-link" href={"#/analyses/" + encodeURIComponent(item.execution_id)}>
        Open analysis →
      </a>
    </section>
  );
}
/** The fixed right-side panel: cross-replication evidence, kept apart from the single-run measurements. */
export function EvidencePanel({ manifest, source }: { manifest: PresentationManifest; source: EvidenceSource | null }) {
  return (
    <aside className="pv-panel" aria-label="Supporting evidence">
      <h2>Supporting evidence</h2>
      <p className="pv-note">Findings across replications from saved analyses; the frozen snapshot of each is named.</p>
      {manifest.evidence.length === 0 && <p className="pv-note">No evidence selected.</p>}
      {manifest.evidence.map((item) => (
        <EvidenceItem key={item.id} item={item} source={source ? source(item.execution_id) : null} />
      ))}
      {manifest.annotations.length > 0 && (
        <section className="pv-annotations" aria-label="Annotations">
          <h3>Annotations</h3>
          <ul>
            {manifest.annotations.map((a) => (
              <li key={a.id}>
                {a.time !== null && <small>t = {formatValue(a.time)}</small>} {a.text}
              </li>
            ))}
          </ul>
        </section>
      )}
    </aside>
  );
}
