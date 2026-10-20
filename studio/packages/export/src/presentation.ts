/** Presentation exports: one frozen snapshot (manifest, target, options, verified static views) shared by the
 * target preview, the video frames and the standalone HTML. Everything selected is either rendered or refused
 * with the item named; nothing is dropped or truncated. */
import type { PreparedFrame, Session } from "@simulatte/playback";
import type { Annotation, EvidenceKind, EvidenceLabels, EvidenceSelection, PresentationManifest } from "@simulatte/playback";
export type { Annotation, EvidenceKind, EvidenceLabels, EvidenceSelection, PresentationManifest };
import type { Scene } from "@simulatte/scene";
import { canonicalHash, sha256Hex } from "./canonical";
import { validateMedia, type MediaOptions } from "./media";

export const HTML_SOURCE_LIMIT = 100 * 1024 * 1024;
export const HTML_OUTPUT_LIMIT = 150 * 1024 * 1024;
/** One static view (a captured figure or a saved image) may not exceed this many bytes. */
export const STATIC_LIMIT = 16 * 1024 * 1024;
export const MAX_EVIDENCE = 12;
export const MAX_ANNOTATIONS = 50;
export const MAX_EXCERPT_ROWS = 50;
export const MAX_EXCERPT_COLUMNS = 12;
export const MAX_CELL_CHARS = 200;
export const SNAPSHOT_DOMAIN = "export-snapshot-v1";
export const EVIDENCE_DOMAIN = "presentation-evidence-output-v1";

export type VideoOptions = MediaOptions & { panel: boolean };
export interface HtmlOptions {
  panel: boolean;
  /** Bytes of the recording that the HTML will embed. */
  traceSize: number;
}
export type TargetOptions = VideoOptions | HtmlOptions;
export type ExportTarget = "video" | "html";
export interface EvidenceField {
  name: string;
  dtype: unknown;
  unit?: string | null;
}
/** The P3 ``OutputDescriptor`` as the export needs it. */
export interface EvidenceOutput {
  id: string;
  name: string;
  kind: EvidenceKind;
  artifacts: { path: string; length: number; sha256: string }[];
  metadata: {
    schema?: { fields: EvidenceField[] };
    row_count?: number;
    media_type?: string;
    unit?: string | null;
    [key: string]: unknown;
  };
  renderer: { name?: string; version?: string } | null;
}
/** Verified bytes and untagged values of saved analysis outputs (the viewer builds it over the P3 reader). */
export interface EvidenceSource {
  output(executionId: string, outputId: string): Promise<EvidenceOutput>;
  value(executionId: string, output: EvidenceOutput): Promise<unknown>;
  rows(executionId: string, output: EvidenceOutput, offset: number, limit: number): Promise<Record<string, unknown>[]>;
  summary(executionId: string, output: EvidenceOutput): Promise<Record<string, unknown>>;
  file(executionId: string, path: string): Promise<Blob>;
  /** A PNG of a Plotly figure from the sandboxed local renderer; rejects when it cannot render offline. */
  capture(executionId: string, output: EvidenceOutput, width: number, height: number, signal: AbortSignal): Promise<Blob>;
}
export interface StaticView {
  evidence_id: string;
  media_type: string;
  sha256: string;
  size: number;
  bytes: Uint8Array;
  width: number;
  height: number;
}
export type EvidenceExcerpt =
  | { id: string; kind: "number"; value: string }
  | { id: string; kind: "table"; columns: string[]; rows: string[][]; offset: number; total: number }
  | { id: string; kind: "distribution"; entries: [string, string][] }
  | { id: string; kind: "image" | "figure"; static_id: string };
export interface ExportLimits {
  frames: number | null;
  /** Estimated HTML bytes including the trace, statics and excerpts; null for video. */
  htmlBytes: number | null;
  staticBytes: number;
  animation: { width: number; height: number } | null;
  panelWidth: number | null;
}
export interface ExportSnapshot {
  manifest: PresentationManifest;
  target: ExportTarget;
  options: TargetOptions;
  statics: StaticView[];
  evidence: EvidenceExcerpt[];
  /** ``canonical_hash("export-snapshot-v1", {manifest, target, options, statics})`` as the server computes it. */
  hash: string;
  limits: ExportLimits;
}
/** A named refusal: what cannot be exported, why, and what resolves it. Nothing is ever dropped instead. */
export class ExportRefusal extends Error {
  constructor(
    readonly item: string,
    readonly reason: string,
    readonly remedy: string,
  ) {
    super(`${item}: ${reason}. ${remedy}`);
    this.name = "ExportRefusal";
  }
}
const isVideo = (options: TargetOptions): options is VideoOptions => "format" in options;
/** The hashed statics: identities only, never bytes. */
export function staticIdentities(statics: readonly StaticView[]): { evidence_id: string; media_type: string; sha256: string; size: number }[] {
  return statics.map((s) => ({ evidence_id: s.evidence_id, media_type: s.media_type, sha256: s.sha256, size: s.size }));
}
/** Hash of the frozen export input; identical to the server's ``snapshot_hash`` for the same JSON. */
export function snapshotHash(
  manifest: PresentationManifest,
  target: ExportTarget,
  options: TargetOptions,
  statics: readonly { evidence_id: string; media_type: string; sha256: string; size: number }[],
): Promise<string> {
  return canonicalHash(SNAPSHOT_DOMAIN, { manifest, target, options, statics });
}
/** ``EvidenceSelection.content_hash``: Python ``output_content_hash`` over the plain output descriptor. */
export function outputContentHash(descriptor: EvidenceOutput): Promise<string> {
  return canonicalHash(EVIDENCE_DOMAIN, descriptor);
}
/** Right-side evidence panel: 30 % of the output, at least 320 px. */
export function panelWidth(width: number): number {
  return Math.max(320, Math.round(width * 0.3));
}
export function animationArea(width: number, height: number, panel: boolean): { width: number; height: number; panelWidth: number } {
  const pw = panel ? panelWidth(width) : 0;
  return { width: width - pw, height, panelWidth: pw };
}
/** The camera that shows the whole saved world rectangle inside ``width`` × ``height`` by containment: the longer
 * side is letterboxed, nothing is cropped. Pixel-space camera as ``Scene`` uses it. */
export function containBounds(
  bounds: readonly [number, number, number, number],
  width: number,
  height: number,
): { x: number; y: number; scale: number } {
  const [x0, y0, x1, y1] = bounds;
  if (![x0, y0, x1, y1].every(Number.isFinite) || x1 <= x0 || y1 <= y0 || width <= 0 || height <= 0)
    throw new RangeError("Invalid camera bounds");
  const scale = Math.min(width / (x1 - x0), height / (y1 - y0));
  return { x: width / 2 - ((x0 + x1) / 2) * scale, y: height / 2 + ((y0 + y1) / 2) * scale, scale };
}
export function formatCell(value: unknown): string {
  if (value === null || value === undefined) return "—";
  switch (typeof value) {
    case "bigint":
      return value.toString();
    case "number":
      return Number.isNaN(value) ? "NaN" : value === Infinity ? "+∞" : value === -Infinity ? "−∞" : String(value);
    case "boolean":
      return value ? "true" : "false";
    case "string":
      return value;
    default:
      return JSON.stringify(value, (_, item: unknown) =>
        typeof item === "bigint" ? item.toString() : typeof item === "number" && !Number.isFinite(item) ? formatCell(item) : item,
      );
  }
}
export function qualifiers(labels: EvidenceLabels): string {
  const parts = [
    labels.unit ? `unit ${labels.unit}` : "",
    labels.population ? `population ${labels.population}` : "",
    `${labels.sample.available} of ${labels.sample.planned} replications${labels.sample.partial ? " · partial snapshot" : ""}`,
    labels.convention,
  ].filter(Boolean);
  return parts.join(" · ");
}
export function provenanceLine(snapshot: Pick<ExportSnapshot, "manifest" | "hash">): string {
  const m = snapshot.manifest;
  return `presentation ${(m.id ?? "unsaved").slice(0, 8)} r${m.revision ?? 0} · recording ${m.recording.attempt_id.slice(0, 8)} · snapshot ${snapshot.hash.slice(0, 12)}`;
}

// --- Panel layout: the same deterministic text metrics for the limit check, the preview and every video frame.
const CHAR = 0.55; // average glyph advance as a fraction of the font size for system sans text
export interface PanelMetrics {
  font: number;
  small: number;
  line: number;
  pad: number;
  inner: number;
}
export function panelMetrics(width: number, height: number): PanelMetrics {
  const s = Math.min(2, Math.max(0.6, height / 1080));
  const font = Math.round(14 * s), small = Math.round(11 * s), pad = Math.round(16 * s);
  return { font, small, line: 1.3, pad, inner: panelWidth(width) - 2 * pad };
}
/** Greedy word wrap at a character budget; a single overlong word is broken. */
export function wrapText(text: string, maxChars: number): string[] {
  const lines: string[] = [];
  for (const paragraph of text.split(/\r?\n/)) {
    let line = "";
    for (const word of paragraph.split(/\s+/).filter(Boolean)) {
      if (!line) line = word;
      else if (line.length + 1 + word.length <= maxChars) line += " " + word;
      else {
        lines.push(line);
        line = word;
      }
      while (line.length > maxChars) {
        lines.push(line.slice(0, maxChars));
        line = line.slice(maxChars);
      }
    }
    lines.push(line);
  }
  return lines;
}
export type PanelItem =
  | { kind: "text"; text: string; size: number; weight: "bold" | "normal"; color: string }
  | { kind: "gap"; height: number }
  | { kind: "table"; columns: string[]; rows: string[][]; size: number; columnWidth: number }
  | { kind: "image"; static_id: string; width: number; height: number };
export interface PanelLayout {
  width: number;
  height: number;
  metrics: PanelMetrics;
  items: { item: PanelItem; y: number; height: number }[];
  /** Height the content needs; above ``height - footer`` the layout does not fit. */
  used: number;
  footer: number;
}
const INK = "#1d343a", MUTED = "#667a80";
interface LayoutInput {
  manifest: PresentationManifest;
  evidence: EvidenceExcerpt[];
  statics: Pick<StaticView, "evidence_id" | "width" | "height">[];
  hash: string;
}
/** Lay the panel out top to bottom. Throws ``ExportRefusal`` naming the first item that does not fit. */
export function layoutPanel(input: LayoutInput, width: number, height: number, measurements: Record<string, string> = {}): PanelLayout {
  const m = panelMetrics(width, height);
  const chars = (size: number) => Math.max(8, Math.floor(m.inner / (size * CHAR)));
  const items: PanelLayout["items"] = [];
  const footer = Math.round(m.small * m.line * 2 + m.pad);
  let y = m.pad;
  let current = "";
  const place = (item: PanelItem, h: number) => {
    if (y + h > height - footer)
      throw new ExportRefusal(
        current,
        `does not fit the ${panelWidth(width)} × ${height} evidence panel`,
        "shorten the text, choose a smaller excerpt, remove the item, or export at a taller size",
      );
    items.push({ item, y, height: h });
    y += h;
  };
  const text = (value: string, size: number, weight: "bold" | "normal" = "normal", color = INK) => {
    for (const line of wrapText(value, chars(size))) place({ kind: "text", text: line, size, weight, color }, Math.round(size * m.line));
  };
  const gap = (h = Math.round(m.pad / 2)) => place({ kind: "gap", height: h }, h);
  const manifest = input.manifest;
  current = "title";
  text(manifest.name, Math.round(m.font * 1.25), "bold");
  if (manifest.notes) {
    current = "notes";
    text(manifest.notes, m.small, "normal", MUTED);
  }
  if (manifest.measurements.length) {
    gap();
    current = "measurements";
    text("Measurements", m.small, "bold", MUTED);
    for (const key of manifest.measurements) text(`${key}: ${measurements[key] ?? "—"}`, m.font);
  }
  const statics = new Map(input.statics.map((s) => [s.evidence_id, s]));
  for (const excerpt of input.evidence) {
    const selection = manifest.evidence.find((e) => e.id === excerpt.id);
    if (!selection) continue;
    const name = selection.labels.name;
    current = `${excerpt.kind} ${name}`;
    gap();
    text(name, m.font, "bold");
    if (excerpt.kind === "number") text(`${excerpt.value}${selection.labels.unit ? " " + selection.labels.unit : ""}`, Math.round(m.font * 1.5), "bold");
    else if (excerpt.kind === "table") {
      const columnWidth = Math.floor(m.inner / excerpt.columns.length);
      const capacity = Math.max(1, Math.floor(columnWidth / (m.small * CHAR)));
      for (const row of [excerpt.columns, ...excerpt.rows])
        for (const cell of row)
          if (cell.length > capacity)
            throw new ExportRefusal(
              current,
              `the value "${cell.length > 24 ? cell.slice(0, 24) + "…" : cell}" is wider than its ${columnWidth} px column`,
              "choose fewer columns or a smaller excerpt, or export at a wider size",
            );
      const rowHeight = Math.round(m.small * m.line);
      place({ kind: "table", columns: excerpt.columns, rows: excerpt.rows, size: m.small, columnWidth }, rowHeight * (excerpt.rows.length + 1));
      text(`rows ${excerpt.offset + 1}–${excerpt.offset + excerpt.rows.length} of ${excerpt.total}`, m.small, "normal", MUTED);
    } else if (excerpt.kind === "distribution") for (const [key, value] of excerpt.entries) text(`${key}: ${value}`, m.small);
    else {
      const s = statics.get(excerpt.static_id);
      if (!s) throw new ExportRefusal(current, "has no static view", "generate a static view or remove the item");
      const scale = Math.min(m.inner / s.width, (height * 0.2) / s.height);
      place({ kind: "image", static_id: excerpt.static_id, width: Math.round(s.width * scale), height: Math.round(s.height * scale) }, Math.round(s.height * scale) + 4);
    }
    text(qualifiers(selection.labels), m.small, "normal", MUTED);
  }
  if (manifest.annotations.length) {
    gap();
    current = "annotations";
    text("Annotations", m.small, "bold", MUTED);
    manifest.annotations.forEach((a, i) => {
      current = `annotation ${i + 1}`;
      text((a.time === null ? "" : `t = ${formatCell(a.time)} · `) + a.text, m.small);
    });
  }
  return { width: panelWidth(width), height, metrics: m, items, used: y, footer };
}

// --- Static views
function pngSize(bytes: Uint8Array): { width: number; height: number } | null {
  if (bytes.length < 24 || bytes[0] !== 0x89 || bytes[1] !== 0x50) return null;
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  return { width: view.getUint32(16), height: view.getUint32(20) };
}
function svgSize(text: string): { width: number; height: number } | null {
  const open = /<svg\b[^>]*>/i.exec(text)?.[0];
  if (!open) return null;
  const attr = (name: string) => {
    const value = new RegExp(`\\s${name}\\s*=\\s*["']([^"']+)["']`, "i").exec(open)?.[1];
    return value === undefined ? null : parseFloat(value);
  };
  const width = attr("width"), height = attr("height");
  if (width && height && Number.isFinite(width) && Number.isFinite(height)) return { width, height };
  const box = /\sviewBox\s*=\s*["']\s*[-\d.e]+[\s,]+[-\d.e]+[\s,]+([-\d.e]+)[\s,]+([-\d.e]+)\s*["']/i.exec(open);
  if (box) return { width: parseFloat(box[1]!), height: parseFloat(box[2]!) };
  return null;
}
/** Pixel size of a PNG or SVG from its bytes, without decoding it. */
export function imageSize(mediaType: string, bytes: Uint8Array): { width: number; height: number } | null {
  if (mediaType === "image/png") return pngSize(bytes);
  if (mediaType === "image/svg+xml") return svgSize(new TextDecoder().decode(bytes.subarray(0, 4096)));
  return null;
}
async function staticView(evidence: EvidenceSelection, blob: Blob, mediaType: string): Promise<StaticView> {
  if (blob.size > STATIC_LIMIT)
    throw new ExportRefusal(`${evidence.kind} ${evidence.labels.name}`, `its static view is ${blob.size} bytes, above ${STATIC_LIMIT}`, "choose a smaller image or remove the item");
  const bytes = new Uint8Array(await blob.arrayBuffer());
  const size = imageSize(mediaType, bytes);
  if (!size || !(size.width > 0) || !(size.height > 0))
    throw new ExportRefusal(`${evidence.kind} ${evidence.labels.name}`, `its ${mediaType} static view has no readable size`, "regenerate the static view or remove the item");
  return { evidence_id: evidence.id, media_type: mediaType, sha256: await sha256Hex(bytes), size: bytes.length, bytes, ...size };
}

// --- prepareExport
async function excerptOf(
  evidence: EvidenceSelection,
  output: EvidenceOutput,
  source: EvidenceSource,
  signal: AbortSignal,
  statics: StaticView[],
): Promise<EvidenceExcerpt> {
  const item = `${evidence.kind} ${evidence.labels.name}`;
  const refuse = (reason: string, remedy: string): never => {
    throw new ExportRefusal(item, reason, remedy);
  };
  if ((await outputContentHash(output)) !== evidence.content_hash) refuse("the saved output changed since it was selected", "reselect the evidence from the current analysis");
  if (output.kind !== evidence.kind) refuse(`the saved output is a ${output.kind}`, "reselect the evidence");
  switch (evidence.kind) {
    case "number":
      return { id: evidence.id, kind: "number", value: formatCell(await source.value(evidence.execution_id, output)) };
    case "table": {
      const rows = "rows" in evidence.selection ? evidence.selection.rows : undefined;
      if (!rows) refuse("has no row selection", "choose the rows and columns to include");
      const fields = output.metadata.schema?.fields ?? [];
      const columns = [...rows!.columns];
      if (!columns.length || columns.length > MAX_EXCERPT_COLUMNS || rows!.limit < 1 || rows!.limit > MAX_EXCERPT_ROWS || rows!.offset < 0)
        refuse(`excerpts are limited to ${MAX_EXCERPT_ROWS} rows and ${MAX_EXCERPT_COLUMNS} columns`, "choose a smaller excerpt");
      const missing = columns.filter((c) => !fields.some((f) => f.name === c));
      if (missing.length) refuse(`column ${missing[0]} is not in the saved table`, "reselect the columns");
      const data = await source.rows(evidence.execution_id, output, rows!.offset, rows!.limit);
      const formatted = data.map((row) => columns.map((c) => formatCell(row[c])));
      for (const row of formatted)
        for (const cell of row)
          if (cell.length > MAX_CELL_CHARS) refuse(`a cell holds ${cell.length} characters, above ${MAX_CELL_CHARS}`, "choose other columns or a smaller excerpt");
      if (!formatted.length) refuse("the selected rows are past the end of the table", "choose rows that exist");
      return { id: evidence.id, kind: "table", columns, rows: formatted, offset: rows!.offset, total: output.metadata.row_count ?? formatted.length };
    }
    case "distribution": {
      const summary = await source.summary(evidence.execution_id, output);
      const entries = Object.entries(summary).map(([k, v]) => [k, formatCell(v)] as [string, string]);
      for (const [key, value] of entries)
        if (value.length > MAX_CELL_CHARS) refuse(`entry ${key} holds ${value.length} characters, above ${MAX_CELL_CHARS}`, "emit a smaller summary or remove the item");
      return { id: evidence.id, kind: "distribution", entries };
    }
    case "image": {
      const media = typeof output.metadata.media_type === "string" ? output.metadata.media_type : "";
      const path = output.artifacts.find((a) => a.path.endsWith("image.png") || a.path.endsWith("image.svg"))?.path;
      if (!path || !["image/png", "image/svg+xml"].includes(media)) refuse("has no saved PNG or SVG file", "regenerate the image or remove the item");
      let blob: Blob;
      try {
        blob = await source.file(evidence.execution_id, path!);
      } catch (error) {
        return refuse(`its static image is missing (${error instanceof Error ? error.message : String(error)})`, "regenerate the image from the analysis or remove the item");
      }
      signal.throwIfAborted();
      statics.push(await staticView(evidence, blob, media));
      return { id: evidence.id, kind: "image", static_id: evidence.id };
    }
    case "figure": {
      const size = "figure" in evidence.selection ? evidence.selection.figure : { width: 960, height: 540 };
      let blob: Blob;
      try {
        blob = await source.capture(evidence.execution_id, output, size.width, size.height, signal);
      } catch (error) {
        if (signal.aborted) throw error;
        return refuse(`has no static view (${error instanceof Error ? error.message : String(error)})`, "save a static image of the figure from the analysis, or remove the item");
      }
      statics.push(await staticView(evidence, blob, "image/png"));
      return { id: evidence.id, kind: "figure", static_id: evidence.id };
    }
  }
}
/** Validate every selected item, build verified static views, check the target limits and freeze the snapshot. */
export async function prepareExport(
  manifest: PresentationManifest,
  target: ExportTarget,
  options: TargetOptions,
  source: EvidenceSource,
  signal: AbortSignal,
): Promise<ExportSnapshot> {
  signal.throwIfAborted();
  if (manifest.evidence.length > MAX_EVIDENCE) throw new ExportRefusal("evidence", `${manifest.evidence.length} items exceed ${MAX_EVIDENCE}`, "remove evidence");
  if (manifest.annotations.length > MAX_ANNOTATIONS) throw new ExportRefusal("annotations", `${manifest.annotations.length} exceed ${MAX_ANNOTATIONS}`, "remove annotations");
  let frames: number | null = null, animation: ExportLimits["animation"] = null, panel: number | null = null;
  if (target === "video") {
    if (!isVideo(options)) throw new ExportRefusal("video options", "are missing", "choose a format, size and frame rate");
    try {
      frames = validateMedia(options).count;
    } catch (error) {
      throw new ExportRefusal("video options", error instanceof Error ? error.message : String(error), "adjust the interval, rate, frame rate or size");
    }
    const area = animationArea(options.width, options.height, options.panel);
    if (area.width < 160) throw new ExportRefusal("video size", `${options.width} px leaves ${area.width} px for the animation beside the evidence panel`, "export at least 480 px wide or turn the panel off");
    animation = { width: area.width, height: area.height };
    panel = options.panel ? area.panelWidth : null;
  } else {
    if (isVideo(options)) throw new ExportRefusal("html options", "carry video settings", "choose the HTML target");
    if (!Number.isSafeInteger(options.traceSize) || options.traceSize < 0) throw new ExportRefusal("recording", "has no size", "reopen the recording");
    if (options.traceSize > HTML_SOURCE_LIMIT) throw new ExportRefusal("recording", "exceeds the 100 MiB offline HTML trace limit", "export a video instead");
  }
  const statics: StaticView[] = [];
  const evidence: EvidenceExcerpt[] = [];
  for (const item of manifest.evidence) {
    signal.throwIfAborted();
    let output: EvidenceOutput;
    try {
      output = await source.output(item.execution_id, item.output_id);
    } catch (error) {
      throw new ExportRefusal(`${item.kind} ${item.labels.name}`, `its analysis output is unavailable (${error instanceof Error ? error.message : String(error)})`, "rerun the analysis or remove the item");
    }
    evidence.push(await excerptOf(item, output, source, signal, statics));
  }
  const staticBytes = statics.reduce((n, s) => n + s.size, 0);
  let htmlBytes: number | null = null;
  if (target === "html") {
    const excerptBytes = new TextEncoder().encode(JSON.stringify({ manifest, evidence })).length;
    htmlBytes = Math.ceil((options as HtmlOptions).traceSize / 3) * 4 + statics.reduce((n, s) => n + Math.ceil(s.size / 3) * 4, 0) + excerptBytes + 64 * 1024;
    if (htmlBytes > HTML_OUTPUT_LIMIT)
      throw new ExportRefusal("offline HTML", `would be about ${Math.round(htmlBytes / 1048576)} MiB including evidence, above 150 MiB`, "remove evidence, choose smaller excerpts or figures, or export a video");
  }
  const hash = await snapshotHash(manifest, target, options, staticIdentities(statics));
  if (target === "video" && panel !== null) layoutPanel({ manifest, evidence, statics, hash }, (options as VideoOptions).width, (options as VideoOptions).height);
  return { manifest, target, options, statics, evidence, hash, limits: { frames, htmlBytes, staticBytes, animation, panelWidth: panel } };
}

// --- Rendering shared by preview and video
/** A ``Scene`` with the P4 additions once they land (``fitBounds``, ``setVisibility``); containment falls back to the camera. */
export type PresentationScene = Scene & {
  fitBounds?(bounds: readonly [number, number, number, number]): void;
  setVisibility?(selection: { entities: Set<string> | null; labels: Set<string> | null }): void;
};
const framed = new WeakSet<object>();
/** Saved framing by containment and the saved visibility, applied once per scene after its first render. */
export function applyFraming(scene: PresentationScene, manifest: PresentationManifest, width: number, height: number): void {
  if (framed.has(scene)) return;
  framed.add(scene);
  if (typeof scene.setVisibility === "function")
    scene.setVisibility({
      entities: manifest.visibility.entities ? new Set(manifest.visibility.entities) : null,
      labels: manifest.visibility.labels ? new Set(manifest.visibility.labels) : null,
    });
  if (typeof scene.fitBounds === "function") scene.fitBounds(manifest.camera.bounds);
  else scene.camera = containBounds(manifest.camera.bounds, width, height);
}
type Drawable = CanvasImageSource;
const decoded = new WeakMap<ExportSnapshot, Map<string, Drawable>>();
async function drawable(snapshot: ExportSnapshot, view: StaticView): Promise<Drawable> {
  let cache = decoded.get(snapshot);
  if (!cache) decoded.set(snapshot, (cache = new Map()));
  const hit = cache.get(view.evidence_id);
  if (hit) return hit;
  const blob = new Blob([view.bytes as BlobPart], { type: view.media_type });
  let image: Drawable;
  if (view.media_type !== "image/svg+xml" && typeof createImageBitmap === "function") image = await createImageBitmap(blob);
  else {
    const element = new Image(view.width, view.height);
    const url = URL.createObjectURL(blob);
    try {
      element.src = url;
      await element.decode();
    } finally {
      URL.revokeObjectURL(url);
    }
    image = element;
  }
  cache.set(view.evidence_id, image);
  return image;
}
export type FrameContext = CanvasRenderingContext2D | OffscreenCanvasRenderingContext2D;
export interface RenderOptions {
  frame?: PreparedFrame;
  transferClock?: number;
  transferLifetime?: number;
  background?: string;
}
function measurementValues(frame: PreparedFrame | undefined, keys: readonly string[]): Record<string, string> {
  const values: Record<string, string> = {};
  const kpis = (frame as { kpis?: Record<string, unknown> } | undefined)?.kpis ?? {};
  for (const key of keys) {
    const sample = kpis[key] as { value?: unknown } | number | null | undefined;
    const value = sample !== null && typeof sample === "object" ? sample.value : sample;
    values[key] = formatCell(value);
  }
  return values;
}
/** Compose one output frame: the animation at the saved framing on the left, the fixed evidence panel on the right
 * and the plain-text provenance line. The scene must be initialized at the animation size. */
export async function renderPresentationFrame(
  snapshot: ExportSnapshot,
  simulationTime: number,
  destination: FrameContext,
  scene: PresentationScene,
  session: Session,
  options: RenderOptions = {},
): Promise<void> {
  const width = destination.canvas.width, height = destination.canvas.height;
  const panel = snapshot.options.panel;
  const area = animationArea(width, height, panel);
  const frame = options.frame ?? (await session.prepare({ mode: "continuous", time: simulationTime, cursor: null }));
  const renderOptions: { transferClock?: number; transferLifetime?: number } = {};
  if (options.transferClock !== undefined) renderOptions.transferClock = options.transferClock;
  if (options.transferLifetime !== undefined) renderOptions.transferLifetime = options.transferLifetime;
  scene.render(frame, renderOptions);
  if (!framed.has(scene)) {
    applyFraming(scene, snapshot.manifest, area.width, area.height);
    scene.render(frame, renderOptions);
  }
  const background = options.background ?? (isVideo(snapshot.options) ? snapshot.options.background : "#f4f5f0");
  destination.fillStyle = background;
  destination.fillRect(0, 0, width, height);
  destination.drawImage(scene.canvas, 0, 0, area.width, area.height);
  const ctx = destination as CanvasRenderingContext2D;
  if (panel) {
    const layout = layoutPanel(snapshot, width, height, measurementValues(frame, snapshot.manifest.measurements));
    const x0 = area.width, m = layout.metrics;
    ctx.fillStyle = "#fafbf7";
    ctx.fillRect(x0, 0, layout.width, height);
    ctx.fillStyle = "#dce5e5";
    ctx.fillRect(x0, 0, 1, height);
    ctx.textBaseline = "top";
    for (const { item, y } of layout.items) {
      if (item.kind === "text") {
        ctx.fillStyle = item.color;
        ctx.font = `${item.weight === "bold" ? "600 " : ""}${item.size}px system-ui, sans-serif`;
        ctx.fillText(item.text, x0 + m.pad, y);
      } else if (item.kind === "table") {
        const rowHeight = Math.round(item.size * m.line);
        ctx.font = `600 ${item.size}px ui-monospace, monospace`;
        ctx.fillStyle = MUTED;
        item.columns.forEach((c, i) => ctx.fillText(c, x0 + m.pad + i * item.columnWidth, y));
        ctx.font = `${item.size}px ui-monospace, monospace`;
        ctx.fillStyle = INK;
        item.rows.forEach((row, r) => row.forEach((cell, i) => ctx.fillText(cell, x0 + m.pad + i * item.columnWidth, y + rowHeight * (r + 1))));
      } else if (item.kind === "image") {
        const view = snapshot.statics.find((s) => s.evidence_id === item.static_id);
        if (view) ctx.drawImage(await drawable(snapshot, view), x0 + m.pad, y, item.width, item.height);
      }
    }
    ctx.fillStyle = MUTED;
    ctx.font = `${m.small}px system-ui, sans-serif`;
    ctx.fillText(provenanceLine(snapshot), x0 + m.pad, height - layout.footer + Math.round(m.pad / 2));
  } else {
    const size = Math.max(12, Math.round(height / 60));
    ctx.textBaseline = "alphabetic";
    ctx.font = `${size}px system-ui, sans-serif`;
    ctx.fillStyle = "#1d343a";
    ctx.textAlign = "right";
    ctx.fillText(provenanceLine(snapshot), width - 20, height - 24);
    ctx.textAlign = "left";
  }
}
