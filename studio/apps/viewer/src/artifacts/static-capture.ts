/** Static views of analysis evidence without Python: PNG/SVG images pass through from the verified artifact, and
 * Plotly figures are rendered to PNG by the same sandboxed local frame the analysis page uses. Nothing here fetches
 * from the network; a figure that cannot render offline is refused with the reason. */
import { untag } from "../analysis/codec";
import { FRAME_DOCUMENT, acceptsFrameMessage, frameUrl, pngBlob, rendererState, validateFigure } from "./figure-frame";
import { httpArtifactSource, type FetchApi } from "./source";
import { excerptRows } from "../presentation/evidence-panel";
import type { OutputDescriptor, OutputManifest } from "./types";
import type { EvidenceOutput, EvidenceSource } from "@simulatte/export";
export const CAPTURE_TIMEOUT = 30000;
export class CaptureError extends Error {
  override name = "CaptureError";
}
/** Render a validated figure in a hidden sandboxed frame and return the PNG the renderer produces. */
export function captureFigurePng(spec: unknown, width: number, height: number, signal?: AbortSignal, timeout = CAPTURE_TIMEOUT): Promise<Blob> {
  const verdict = validateFigure(spec);
  if (!verdict.ok) return Promise.reject(new CaptureError(verdict.reason));
  return new Promise<Blob>((resolve, reject) => {
    const nonce = crypto.randomUUID();
    const frame = document.createElement("iframe");
    frame.setAttribute("sandbox", "allow-scripts");
    frame.setAttribute("aria-hidden", "true");
    frame.tabIndex = -1;
    frame.referrerPolicy = "no-referrer";
    frame.style.cssText = `position:fixed;left:-20000px;top:0;width:${width}px;height:${height}px;border:0;opacity:0;pointer-events:none`;
    frame.title = `Static capture (${FRAME_DOCUMENT})`;
    let done = false;
    const finish = (outcome: () => void) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      window.removeEventListener("message", listener);
      signal?.removeEventListener("abort", onAbort);
      frame.remove();
      outcome();
    };
    const onAbort = () => finish(() => reject(signal?.reason ?? new DOMException("Capture aborted", "AbortError")));
    const timer = setTimeout(() => finish(() => reject(new CaptureError("the renderer did not answer within the capture time limit"))), timeout);
    const listener = (event: MessageEvent) => {
      const accepted = acceptsFrameMessage(event, nonce, frame.contentWindow);
      if (!accepted) return;
      switch (accepted.type) {
        case "ready":
          frame.contentWindow?.postMessage({ v: 1, nonce, type: "figure", figure: verdict.figure }, "*");
          break;
        case "rendered":
          frame.contentWindow?.postMessage({ v: 1, nonce, type: "image" }, "*");
          break;
        case "image": {
          const blob = pngBlob(accepted.data.data);
          finish(() => (blob ? resolve(blob) : reject(new CaptureError("the renderer returned no readable PNG"))));
          break;
        }
        case "error":
          finish(() => reject(new CaptureError(typeof accepted.data.message === "string" ? accepted.data.message : "the renderer failed")));
          break;
      }
    };
    if (signal?.aborted) return onAbort();
    signal?.addEventListener("abort", onAbort, { once: true });
    window.addEventListener("message", listener);
    frame.src = frameUrl(nonce);
    document.body.append(frame);
  });
}
/** Images pass through unchanged: the verified PNG or SVG bytes are the static view. */
export function imagePassthrough(blob: Blob, mediaType: string): Blob {
  if (mediaType !== "image/png" && mediaType !== "image/svg+xml") throw new CaptureError(`unsupported image type ${mediaType}`);
  return blob.type === mediaType ? blob : new Blob([blob], { type: mediaType });
}
const artifact = (output: OutputDescriptor, suffix: string) => output.artifacts.find((a) => a.path.endsWith(suffix))?.path;
const asRecord = (value: unknown): Record<string, unknown> => (value !== null && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {});
/** The export's evidence source over the P3 analysis commands and readers of one Studio client. */
export function evidenceSource(
  command: (type: "analysis.get", body: Record<string, unknown>) => Promise<Record<string, unknown>>,
  fetchApi: FetchApi,
): EvidenceSource {
  const manifests = new Map<string, Promise<OutputManifest>>();
  const manifest = (executionId: string) => {
    let pending = manifests.get(executionId);
    if (!pending) {
      pending = command("analysis.get", { execution_id: executionId }).then((reply) => {
        if (!reply.ok) throw new Error(((reply.error as { message?: string }) ?? {}).message ?? "analysis unavailable");
        const execution = reply.execution as { state?: string };
        if (execution?.state !== "succeeded") throw new Error(`the analysis is ${execution?.state ?? "unknown"}`);
        if (!reply.output) throw new Error("the analysis has no outputs");
        return untag(reply.output) as unknown as OutputManifest;
      });
      manifests.set(executionId, pending);
    }
    return pending;
  };
  const reader = (executionId: string) => httpArtifactSource(fetchApi, executionId);
  const json = async (executionId: string, path: string) => untag(JSON.parse(await (await reader(executionId).blob("", path)).text()));
  return {
    async output(executionId, outputId) {
      const found = (await manifest(executionId)).outputs.find((o) => o.id === outputId);
      if (!found) throw new Error("the output is not in the analysis manifest");
      return found as unknown as EvidenceOutput;
    },
    async value(executionId, output) {
      const path = artifact(output as unknown as OutputDescriptor, "number.json");
      if (!path) throw new Error("the number has no saved file");
      return asRecord(await json(executionId, path)).value;
    },
    async rows(executionId, output, offset, limit) {
      // The same bounded excerpt the Studio evidence panel shows (saved order, offset and limit).
      return excerptRows(reader(executionId), output.id, { offset, limit, columns: [] });
    },
    async summary(executionId, output) {
      const path = artifact(output as unknown as OutputDescriptor, "distribution.json");
      if (!path) throw new Error("the distribution has no saved file");
      return asRecord(asRecord(await json(executionId, path)).summary);
    },
    async file(executionId, path) {
      const blob = await reader(executionId).blob("", path);
      return path.endsWith(".svg") ? imagePassthrough(blob, "image/svg+xml") : imagePassthrough(blob, "image/png");
    },
    async capture(executionId, output, width, height, signal) {
      const state = rendererState(output.renderer);
      if (state.state !== "ready") throw new CaptureError(state.label);
      const path = artifact(output as unknown as OutputDescriptor, "figure.json");
      if (!path) throw new CaptureError("the figure has no saved specification");
      return captureFigurePng(await json(executionId, path), width, height, signal);
    },
  };
}
