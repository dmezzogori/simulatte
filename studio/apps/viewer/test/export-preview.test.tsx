import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { ExportRefusal, type ExportSnapshot } from "@simulatte/export";
import { RefusalNotice, limitsSummary, previewSize } from "../src/presentation/export-preview";
import { MEDIA_TYPES, exportOutcome } from "../src/export-dialog";
import { imagePassthrough, CaptureError } from "../src/artifacts/static-capture";

const snapshot = (target: ExportSnapshot["target"], options: ExportSnapshot["options"], limits: Partial<ExportSnapshot["limits"]> = {}): ExportSnapshot =>
  ({
    manifest: { name: "Lateness", time: { start: 2, end: 8 } } as ExportSnapshot["manifest"],
    target,
    options,
    statics: [{ evidence_id: "f", media_type: "image/png", sha256: "a".repeat(64), size: 1000, bytes: new Uint8Array(0), width: 1, height: 1 }],
    evidence: [{ id: "n", kind: "number", value: "3" }, { id: "f", kind: "figure", static_id: "f" }],
    hash: "abcdef0123456789".repeat(4),
    limits: { frames: null, htmlBytes: null, staticBytes: 1000, animation: null, panelWidth: null, ...limits },
  }) as ExportSnapshot;

describe("export preview", () => {
  it("previews video at the target size and HTML through the same renderer at 1280 × 720", () => {
    const video = snapshot("video", { start: 0, end: 1, fps: 30, rate: 1, width: 1920, height: 1080, format: "mp4", background: "#ffffff", overlays: true, panel: true }, { frames: 31, animation: { width: 1344, height: 1080 }, panelWidth: 576 });
    expect(previewSize(video)).toEqual({ width: 1920, height: 1080 });
    expect(previewSize(snapshot("html", { panel: true, traceSize: 10 }))).toEqual({ width: 1280, height: 720 });
    expect(limitsSummary(video)).toEqual([
      "MP4 · 1920 × 1080 · 30 fps · 31 of 100,000 frames",
      "animation 1344 × 1080 · evidence panel 576 px",
      "1 static view · 2 evidence items · snapshot abcdef012345",
    ]);
    expect(limitsSummary(snapshot("html", { panel: true, traceSize: 10 }, { htmlBytes: 30 * 1048576 }))[0]).toBe("offline HTML about 30 MiB of 150 MiB including 0.00 MiB of static views");
  });
  it("names the refused item, its reason and the remedy without dropping content", () => {
    const html = renderToStaticMarkup(<RefusalNotice refusal={new ExportRefusal("annotation 3", "does not fit the 576 × 1080 evidence panel", "shorten the text")} />);
    expect(html).toContain('role="alert"');
    expect(html).toContain('data-item="annotation 3"');
    expect(html).toContain("Export refused: annotation 3");
    expect(html).toContain("shorten the text");
    expect(html).toContain("Nothing is omitted or truncated");
  });
  it("reports the saved file to export.finish with its exact size and digest", async () => {
    const blob = new Blob(["html"], { type: "text/html" });
    const outcome = await exportOutcome({ name: "p.html", blob }, MEDIA_TYPES.html);
    expect(outcome).toMatchObject({ state: "succeeded", output: { name: "p.html", size: 4, media_type: "text/html" } });
    expect((outcome as { output: { sha256: string } }).output.sha256).toMatch(/^[0-9a-f]{64}$/);
    await expect(exportOutcome({ name: "clip.mp4" }, MEDIA_TYPES.mp4)).rejects.toThrow("cannot be read back");
  });
  it("passes PNG and SVG bytes through and refuses other image types", () => {
    const svg = new Blob(["<svg/>"]);
    expect(imagePassthrough(svg, "image/svg+xml").type).toBe("image/svg+xml");
    expect(() => imagePassthrough(svg, "text/html")).toThrow(CaptureError);
  });
});
