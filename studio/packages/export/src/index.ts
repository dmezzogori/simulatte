import {freezePresentation} from '@simulatte/playback';
import type {LayoutBundle} from '@simulatte/trace';
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
export const HTML_SOURCE_LIMIT = 100 * 1024 * 1024,
  HTML_OUTPUT_LIMIT = 150 * 1024 * 1024;
export async function offlineHtml(
  source: Blob,
  script: string,
  style = "",
  signal?: AbortSignal,
  presentation: LayoutBundle | null = null,
): Promise<Blob> {
  if (source.size > HTML_SOURCE_LIMIT)
    throw new RangeError("Offline HTML supports traces up to 100 MiB");
  signal?.throwIfAborted();
  // Capture before the first await; an editor update cannot alter an in-flight export.
  const pinned = presentation === null ? null : freezePresentation(presentation);
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
  let presentationBlock = '';
  if (pinned) {
    signal?.throwIfAborted();
    const digest = await crypto.subtle.digest('SHA-256', await source.arrayBuffer());
    const trace_sha256 = Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('');
    const document = JSON.stringify({version: 1, trace_sha256, layout: pinned}).replaceAll('<', '\\u003c');
    presentationBlock = `<script id="presentation" type="application/json">${document}</script>`;
  }
  const suffix = `</script>${presentationBlock}<script>${safeScript}</script></body></html>`;
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
