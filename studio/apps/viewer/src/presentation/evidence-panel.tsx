/** The fixed evidence panel of a presentation and the helpers that choose evidence from saved analyses.
 * Labels are copied from the output metadata when an item is selected and never edited afterwards. */
import { Fragment, useEffect, useState, type ReactNode } from "react";
import type { EvidenceChoice, EvidenceLabels, EvidenceSelection, PresentationManifest } from "@simulatte/playback";
import { formatValue, untag } from "../analysis/codec";
import { FigureFrame, rendererState, validateFigure } from "../artifacts/figure-frame";
import type { ArtifactSource, OutputDescriptor, Row } from "../artifacts/types";
import "./presentation.css";

/** Where the panel reads evidence bytes: one P3 artifact source per analysis execution. */
export type EvidenceSource = (executionId: string) => ArtifactSource;
export interface EvidenceOrigin {
  execution_id: string;
  analysis_name: string;
  snapshot_hash: string;
}
/** Canonical JSON (sorted keys, no spaces, ASCII escapes), the form the content hash is taken over. */
export function canonicalJson(value: unknown): string {
  const encode = (item: unknown): string => {
    if (item === null || typeof item === "boolean") return JSON.stringify(item);
    if (typeof item === "number") {
      if (!Number.isFinite(item)) throw new TypeError("Canonical JSON needs finite numbers");
      return Number.isInteger(item) ? String(item) : JSON.stringify(item);
    }
    if (typeof item === "string") return JSON.stringify(item).replace(/[\u007f-￿]/g, (c) => "\\u" + c.charCodeAt(0).toString(16).padStart(4, "0"));
    if (Array.isArray(item)) return "[" + item.map(encode).join(",") + "]";
    if (typeof item === "object")
      return "{" + Object.keys(item as object).sort().map((k) => encode(k) + ":" + encode((item as Record<string, unknown>)[k])).join(",") + "}";
    throw new TypeError("Canonical JSON cannot encode " + typeof item);
  };
  return encode(value);
}
/** sha256 of the canonical OutputDescriptor JSON: the identity a selection is bound to. */
export async function contentHash(output: OutputDescriptor): Promise<string> {
  const bytes = new TextEncoder().encode(canonicalJson(output));
  return Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)), (b) => b.toString(16).padStart(2, "0")).join("");
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
function useArtifact<T>(load: (() => Promise<T>) | null, deps: readonly unknown[]): Loaded<T> {
  const [state, setState] = useState<Loaded<T>>({ status: "loading" });
  useEffect(() => {
    if (!load) return;
    let closed = false;
    setState({ status: "loading" });
    load().then(
      (value) => {
        if (!closed) setState({ status: "ready", value });
      },
      (error: unknown) => {
        if (!closed) setState({ status: "error", message: error instanceof Error ? error.message : String(error) });
      },
    );
    return () => {
      closed = true;
    };
  }, deps);
  return state;
}
const blobJson = async (blob: Blob): Promise<unknown> => JSON.parse(await blob.text());
function NumberItem({ item, source }: { item: EvidenceSelection; source: ArtifactSource }) {
  const loaded = useArtifact(() => blobJson(source.blob(item.output_id, "number.json")), [item.output_id]);
  if (loaded.status !== "ready") return <p className="pv-value" role={loaded.status === "error" ? "alert" : "status"}>{loaded.status === "error" ? loaded.message : "…"}</p>;
  const value = untag((loaded.value as { value?: unknown })?.value ?? null);
  return (
    <p className="pv-value">
      {formatValue(value)}
      {item.labels.unit && <small> {item.labels.unit}</small>}
    </p>
  );
}
/** Rows ``offset..offset+limit`` of the selected columns, read through the bounded server pages. */
export async function excerptRows(source: ArtifactSource, outputId: string, rows: { offset: number; limit: number; columns: readonly string[] }): Promise<Row[]> {
  const collected: Row[] = [];
  let cursor: string | null = null;
  let skipped = 0;
  for (let page = 0; page < 20 && collected.length < rows.limit; page++) {
    const batch = await source.page(outputId, cursor, Math.min(500, rows.offset + rows.limit - skipped - collected.length));
    for (const row of batch.rows) {
      if (skipped < rows.offset) skipped++;
      else if (collected.length < rows.limit) collected.push(rows.columns.length ? Object.fromEntries(rows.columns.map((c) => [c, row[c] ?? null])) : row);
    }
    cursor = batch.next_cursor;
    if (cursor === null) break;
  }
  return collected;
}
function TableItem({ item, source }: { item: EvidenceSelection; source: ArtifactSource }) {
  const rows = "rows" in item.selection ? item.selection.rows : null;
  const loaded = useArtifact(rows ? () => excerptRows(source, item.output_id, rows) : null, [item.output_id, rows?.offset, rows?.limit]);
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
function ImageItem({ item, source }: { item: EvidenceSelection; source: ArtifactSource }) {
  const loaded = useArtifact(async () => {
    const path = item.labels.unit === "image/svg+xml" ? "image.svg" : "image.png";
    try {
      return URL.createObjectURL(await source.blob(item.output_id, path));
    } catch {
      return URL.createObjectURL(await source.blob(item.output_id, path === "image.png" ? "image.svg" : "image.png"));
    }
  }, [item.output_id]);
  useEffect(() => () => {
    if (loaded.status === "ready") URL.revokeObjectURL(loaded.value);
  }, [loaded]);
  if (loaded.status !== "ready") return <p role={loaded.status === "error" ? "alert" : "status"}>{loaded.status === "error" ? loaded.message : "…"}</p>;
  return <img src={loaded.value} alt={item.labels.name} />;
}
function FigureItem({ item, source }: { item: EvidenceSelection; source: ArtifactSource }) {
  const size = "figure" in item.selection ? item.selection.figure : { width: 640, height: 360 };
  const loaded = useArtifact(() => blobJson(source.blob(item.output_id, "figure.json")), [item.output_id]);
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
function DistributionItem({ item, source }: { item: EvidenceSelection; source: ArtifactSource }) {
  const loaded = useArtifact(() => blobJson(source.blob(item.output_id, "distribution.json")), [item.output_id]);
  if (loaded.status !== "ready") return <p role={loaded.status === "error" ? "alert" : "status"}>{loaded.status === "error" ? loaded.message : "…"}</p>;
  const summary = untag((loaded.value as { summary?: unknown })?.summary ?? null) as Record<string, unknown> | null;
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
export function EvidenceItem({ item, source }: { item: EvidenceSelection; source: ArtifactSource | null }) {
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
