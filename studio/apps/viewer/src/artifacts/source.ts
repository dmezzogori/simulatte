/** Authenticated HTTP reader over ``/api/analysis/<execution>`` through the Studio client's ``fetchApi``. */
import { untag } from "../analysis/codec";
import { ArtifactError, type ArtifactSource, type Row, type RowsPage, type TableQueryStrings } from "./types";
export const MAX_PAGE = 500;
export type FetchApi = (path: string, init?: RequestInit) => Promise<Response>;
const SAFE_SEGMENT = /^[A-Za-z0-9._-]+$/;
async function refuse(response: Response): Promise<never> {
  let message = `Request failed (${response.status})`;
  let details: unknown;
  try {
    const body = (await response.json()) as { error?: unknown; state?: unknown; details?: unknown };
    if (typeof body.error === "string") message = body.error;
    details = body.details;
    if (typeof body.state === "string") message += ` (execution ${body.state})`;
  } catch {
    /* A non-JSON refusal keeps the status text. */
  }
  throw new ArtifactError(message, response.status, response.status === 503, details);
}
function validatePage(body: unknown): RowsPage {
  const page = body as Partial<RowsPage>;
  if (
    !page ||
    !Array.isArray(page.rows) ||
    typeof page.row_count !== "number" ||
    typeof page.indexed !== "boolean" ||
    (page.next_cursor !== null && typeof page.next_cursor !== "string")
  )
    throw new ArtifactError("Invalid table page", 0);
  const rows = page.rows.map((row) => {
    const value = untag(row);
    if (value === null || typeof value !== "object" || Array.isArray(value)) throw new ArtifactError("Invalid table row", 0);
    return value as Row;
  });
  return { rows, next_cursor: page.next_cursor, row_count: page.row_count, indexed: page.indexed };
}
/** Relative artifact paths from the manifest, one encoded segment at a time; nothing else is addressable. */
export function artifactPath(executionId: string, relative: string): string {
  const parts = relative.split("/");
  if (!parts.length || parts.some((part) => !SAFE_SEGMENT.test(part) || part === "." || part === ".."))
    throw new ArtifactError("Unsupported artifact path", 0);
  return `/api/analysis/${encodeURIComponent(executionId)}/artifacts/` + parts.map(encodeURIComponent).join("/");
}
export function httpArtifactSource(fetchApi: FetchApi, executionId: string): ArtifactSource {
  const base = `/api/analysis/${encodeURIComponent(executionId)}/outputs/`;
  const ok = async (path: string, signal?: AbortSignal) => {
    const response = await fetchApi(path, signal ? { signal } : undefined);
    if (!response.ok) await refuse(response);
    return response;
  };
  return {
    async page(outputId, cursor, limit, query, signal) {
      const search = new URLSearchParams({ limit: String(Math.min(MAX_PAGE, Math.max(1, Math.trunc(limit)))) });
      if (cursor) search.set("cursor", cursor);
      if (query?.sort) search.set("sort", query.sort);
      if (query?.filter) search.set("filter", query.filter);
      const response = await ok(base + encodeURIComponent(outputId) + "/rows?" + search.toString(), signal);
      return validatePage(await response.json());
    },
    async blob(_outputId, path) {
      return (await ok(artifactPath(executionId, path))).blob();
    },
    async csv(outputId) {
      return (await ok(base + encodeURIComponent(outputId) + "/csv")).blob();
    },
  };
}
/** Save a blob through a temporary object URL and the anchor ``download`` attribute; no navigation, no new window. */
export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.rel = "noopener";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
