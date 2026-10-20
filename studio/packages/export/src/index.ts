import {freezePresentation} from '@simulatte/playback';
import type {LayoutBundle} from '@simulatte/trace';
import {HTML_OUTPUT_LIMIT, HTML_SOURCE_LIMIT, type ExportSnapshot} from './presentation';
/** Validated manual export timeline. The terminal state receives one complete frame. */
export function frameSchedule(
  start: number,
  end: number,
  fps: number,
  rate: number,
): Readonly<{
  count: number;
  frame: (
    index: number,
  ) => Readonly<{ time: number; timestamp: number; duration: number }>;
}> {
  if (
    ![start, end, fps, rate].every(Number.isFinite) ||
    end < start ||
    fps <= 0 ||
    fps > 1000 ||
    rate <= 0
  )
    throw new RangeError("Invalid export interval, fps or rate");
  const count = Math.ceil(((end - start) * fps) / rate) + 1;
  if (!Number.isSafeInteger(count) || count < 1 || count > 100000)
    throw new RangeError("Exports are limited to 100,000 frames");
  const frame = (index: number) => {
    if (!Number.isInteger(index) || index < 0 || index >= count)
      throw new RangeError("Frame index out of range");
    const time = index === count - 1 ? end : start + (index * rate) / fps;
    const timestamp = Math.round((index * 1000000) / fps),
      next = Math.round(((index + 1) * 1000000) / fps);
    if (
      !Number.isFinite(time) ||
      !Number.isSafeInteger(timestamp) ||
      !Number.isSafeInteger(next) ||
      next <= timestamp
    )
      throw new RangeError("Export timestamps exceed numeric precision");
    return Object.freeze({ time, timestamp, duration: next - timestamp });
  };
  let previous = -Infinity;
  for (let i = 0; i < count; i++) {
    const f = frame(i);
    if (i && f.time <= previous)
      throw new RangeError("Export time step is below numeric precision");
    previous = f.time;
  }
  return Object.freeze({ count, frame });
}
export { HTML_SOURCE_LIMIT, HTML_OUTPUT_LIMIT };
function base64(bytes: Uint8Array): string {
  let binary = "";
  for (let i = 0; i < bytes.length; i += 16384) binary += String.fromCharCode(...bytes.subarray(i, i + 16384));
  return btoa(binary);
}
/** The ``#presentation`` version-2 block: the frozen manifest, the verified static views and the evidence excerpts. */
export function presentationBlock(snapshot: ExportSnapshot, trace_sha256: string): string {
  const document = {
    version: 2,
    trace_sha256,
    snapshot_hash: snapshot.hash,
    manifest: snapshot.manifest,
    statics: snapshot.statics.map((s) => ({
      evidence_id: s.evidence_id,
      media_type: s.media_type,
      sha256: s.sha256,
      size: s.size,
      width: s.width,
      height: s.height,
      data_base64: base64(s.bytes),
    })),
    evidence: snapshot.evidence,
  };
  return JSON.stringify(document).replaceAll("<", "\\u003c");
}
export async function offlineHtml(
  source: Blob,
  script: string,
  style = "",
  signal?: AbortSignal,
  presentation: LayoutBundle | null = null,
  snapshot: ExportSnapshot | null = null,
): Promise<Blob> {
  if (source.size > HTML_SOURCE_LIMIT)
    throw new RangeError("Offline HTML supports traces up to 100 MiB");
  signal?.throwIfAborted();
  // Capture before the first await; an editor update cannot alter an in-flight export.
  const pinned = snapshot ? freezePresentation(snapshot.manifest.layout) : presentation === null ? null : freezePresentation(presentation);
  const safeStyle = style.replace(/<\/style/gi, "<\\/style");
  const safeScript = script.replace(/<\/script/gi, "<\\/script");
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(safeScript),
  );
  const hash = btoa(String.fromCharCode(...new Uint8Array(digest)));
  const styleDigest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(safeStyle),
  );
  const styleHash = btoa(String.fromCharCode(...new Uint8Array(styleDigest)));
  const head = `<!doctype html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'sha256-${hash}'; style-src 'sha256-${styleHash}'; img-src data:; font-src data:; connect-src 'none'; worker-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'"><title>Simulatte recorded run</title><style>${safeStyle}</style></head><body><script id="trace" type="application/octet-stream">`;
  let block = '';
  if (pinned) {
    signal?.throwIfAborted();
    const traceDigest = await crypto.subtle.digest('SHA-256', await source.arrayBuffer());
    const trace_sha256 = Array.from(new Uint8Array(traceDigest), byte => byte.toString(16).padStart(2, '0')).join('');
    if (snapshot && trace_sha256 !== snapshot.manifest.recording.trace_sha256)
      throw new RangeError("The recording bytes differ from the presentation's recording");
    const document = snapshot ? presentationBlock(snapshot, trace_sha256) : JSON.stringify({version: 1, trace_sha256, layout: pinned}).replaceAll('<', '\\u003c');
    block = `<script id="presentation" type="application/json">${document}</script>`;
  }
  const suffix = `</script>${block}<script>${safeScript}</script></body></html>`;
  const exactSize =
    Math.ceil(source.size / 3) * 4 + new Blob([head, suffix]).size;
  if (exactSize > HTML_OUTPUT_LIMIT)
    throw new RangeError("Offline HTML output exceeds 150 MiB");
  const chunks: string[] = [];
  const stride = 3 * 16384;
  for (let i = 0; i < source.size; i += stride) {
    signal?.throwIfAborted();
    const bytes = new Uint8Array(
      await source.slice(i, i + stride).arrayBuffer(),
    );
    let binary = "";
    for (const byte of bytes) binary += String.fromCharCode(byte);
    chunks.push(btoa(binary));
    if (i % (stride * 64) === 0)
      await new Promise((resolve) => setTimeout(resolve, 0));
  }

  signal?.throwIfAborted();
  const result = new Blob([head, ...chunks, suffix], { type: "text/html" });
  if (result.size > HTML_OUTPUT_LIMIT)
    throw new RangeError("Offline HTML output exceeds 150 MiB");
  return result;
}
export function download(blob: Blob, name: string): void {
  const url = URL.createObjectURL(blob),
    a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 60000);
}
export {
  destination,
  abandonedExports,
  removeAbandoned,
  type Destination,
  type ExportFile,
} from "./sink";
export {
  exportMedia,
  codecFor,
  validateMedia,
  type MediaOptions,
} from "./media";
export {
  prepareExport,
  renderPresentationFrame,
  snapshotHash,
  outputContentHash,
  staticIdentities,
  layoutPanel,
  panelWidth,
  animationArea,
  containBounds,
  applyFraming,
  provenanceLine,
  qualifiers,
  formatCell,
  imageSize,
  wrapText,
  ExportRefusal,
  STATIC_LIMIT,
  MAX_EVIDENCE,
  MAX_EXCERPT_ROWS,
  MAX_EXCERPT_COLUMNS,
  type ExportSnapshot,
  type ExportTarget,
  type TargetOptions,
  type VideoOptions,
  type HtmlOptions,
  type EvidenceSource,
  type EvidenceOutput,
  type EvidenceExcerpt,
  type EvidenceSelection,
  type EvidenceLabels,
  type PresentationManifest,
  type StaticView,
  type PanelLayout,
  type PresentationScene,
} from "./presentation";
export { canonicalPack, canonicalHash, sha256Hex, sha256Blob, Sha256 } from "./canonical";
