/** The ``#presentation`` block of an offline HTML: version 1 (a pinned layout) or version 2 (the frozen
 * presentation manifest, verified static views and evidence excerpts). Pure validation; no DOM. */
export interface StaticEntry {
  evidence_id: string;
  media_type: string;
  sha256: string;
  size: number;
  width: number;
  height: number;
  /** A ``data:`` URL; the offline CSP allows ``img-src data:`` and nothing else. */
  url: string;
}
export type Excerpt =
  | { id: string; kind: "number"; value: string }
  | { id: string; kind: "table"; columns: string[]; rows: string[][]; offset: number; total: number }
  | { id: string; kind: "distribution"; entries: [string, string][] }
  | { id: string; kind: "image" | "figure"; static_id: string };
export interface Manifest {
  schema: 1;
  id?: string;
  revision?: number;
  name: string;
  notes: string;
  recording: { attempt_id: string; request_id: string; trace_sha256: string };
  layout: unknown;
  camera: { bounds: [number, number, number, number] };
  visibility: { entities: string[] | null; labels: string[] | null };
  measurements: string[];
  time: { start: number; end: number };
  rate: number;
  annotations: { id: string; text: string; time: number | null }[];
  evidence: {
    id: string;
    kind: string;
    labels: { name: string; unit: string | null; population: string | null; sample: { planned: number; available: number; partial: boolean }; convention: string; source: { analysis_name: string; snapshot_hash: string; execution_id: string } };
  }[];
  created: number;
}
export interface PresentationBlock {
  version: 1 | 2;
  layout: unknown;
  manifest: Manifest | null;
  statics: StaticEntry[];
  evidence: Excerpt[];
  snapshot_hash: string | null;
}
const MISMATCH = "Presentation metadata does not match this trace";
const object = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === "object" && !Array.isArray(value);
const keys = (value: Record<string, unknown>) => Object.keys(value).sort().join(",");
const IMAGE_TYPES = new Set(["image/png", "image/svg+xml"]);
const BASE64 = /^[A-Za-z0-9+/]*={0,2}$/;
/** Validate the parsed block against the embedded trace's SHA-256; anything unexpected is refused. */
export function readPresentationBlock(data: unknown, traceSha256: string): PresentationBlock {
  if (!object(data) || data.trace_sha256 !== traceSha256) throw new Error(MISMATCH);
  if (data.version === 1) {
    if (keys(data) !== "layout,trace_sha256,version") throw new Error(MISMATCH);
    return { version: 1, layout: data.layout, manifest: null, statics: [], evidence: [], snapshot_hash: null };
  }
  if (data.version !== 2 || keys(data) !== "evidence,manifest,snapshot_hash,statics,trace_sha256,version") throw new Error(MISMATCH);
  const manifest = data.manifest;
  if (!object(manifest) || manifest.schema !== 1 || !object(manifest.recording) || manifest.recording.trace_sha256 !== traceSha256) throw new Error(MISMATCH);
  if (!object(manifest.time) || !object(manifest.camera) || !Array.isArray(manifest.measurements) || !Array.isArray(manifest.annotations) || !Array.isArray(manifest.evidence))
    throw new Error("Presentation manifest is incomplete");
  if (!Array.isArray(data.statics) || !Array.isArray(data.evidence) || typeof data.snapshot_hash !== "string") throw new Error(MISMATCH);
  const statics: StaticEntry[] = data.statics.map((item: unknown) => {
    if (!object(item) || typeof item.media_type !== "string" || !IMAGE_TYPES.has(item.media_type) || typeof item.data_base64 !== "string" || !BASE64.test(item.data_base64))
      throw new Error("Presentation static view is not a PNG or SVG image");
    return {
      evidence_id: String(item.evidence_id),
      media_type: item.media_type,
      sha256: String(item.sha256),
      size: Number(item.size),
      width: Number(item.width),
      height: Number(item.height),
      url: `data:${item.media_type};base64,${item.data_base64}`,
    };
  });
  return {
    version: 2,
    layout: manifest.layout,
    manifest: manifest as unknown as Manifest,
    statics,
    evidence: data.evidence as Excerpt[],
    snapshot_hash: data.snapshot_hash,
  };
}
/** The saved time range clamped to the recording's finite range (the whole recording without a manifest). */
export function viewRange(manifest: Pick<Manifest, "time"> | null, finite: readonly [number, number]): { start: number; end: number } {
  if (!manifest) return { start: finite[0], end: finite[1] };
  const start = Math.max(finite[0], Math.min(finite[1], manifest.time.start));
  const end = Math.max(start, Math.min(finite[1], manifest.time.end));
  return { start, end };
}
export function qualifierText(labels: Manifest["evidence"][number]["labels"]): string {
  return [
    labels.unit ? `unit ${labels.unit}` : "",
    labels.population ? `population ${labels.population}` : "",
    `${labels.sample.available} of ${labels.sample.planned} replications${labels.sample.partial ? " · partial snapshot" : ""}`,
    labels.convention,
    `analysis ${labels.source.analysis_name} · snapshot ${labels.source.snapshot_hash.slice(0, 12)} · execution ${labels.source.execution_id.slice(0, 8)}`,
  ]
    .filter(Boolean)
    .join(" · ");
}
/** Play stays available at the end of the saved view: pressing it replays from the view's start. */
export function playControl(
  state: { frame: boolean; finiteRange: boolean; time: number; status: string },
  view: { start: number; end: number } | null,
): { disabled: boolean; restart: boolean } {
  const restart = view !== null && Number.isFinite(state.time) && (state.status === "ended" || state.time >= view.end);
  return {
    disabled: !state.frame || !state.finiteRange || !Number.isFinite(state.time) || (state.status === "ended" && !restart),
    restart,
  };
}
