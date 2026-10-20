/** Plotly figures render in a sandboxed, opaque-origin frame; the parent validates the spec and owns the nonce-bound channel. */
import { useEffect, useRef, useState } from "react";
import { version } from "plotly.js-dist-min/package.json";
import { downloadBlob } from "./source";
export const PACKAGED_PLOTLY_VERSION: string = version;
export const FRAME_DOCUMENT = "plotly-frame.html";
export interface FigureSpec {
  data: Record<string, unknown>[];
  layout: Record<string, unknown>;
  frames?: unknown[];
}
export type FigureVerdict = { ok: true; figure: FigureSpec } | { ok: false; reason: string };
const ALLOWED_TOP = ["data", "layout", "frames"];
const FORBIDDEN_KEYS = new Set(["config", "__proto__", "constructor", "prototype"]);
/** Trace types that fetch tiles or topology from a network service. */
const NETWORK_TRACES = new Set([
  "scattermapbox",
  "choroplethmapbox",
  "densitymapbox",
  "scattermap",
  "choroplethmap",
  "densitymap",
  "scattergeo",
  "choropleth",
]);
const NETWORK_LAYOUT = new Set(["mapbox", "map", "geo"]);
const URL_LIKE = /^\s*(?:[a-z][a-z0-9+.-]*:|\/\/)/i;
const SCRIPT_LIKE = /javascript:|vbscript:|<script|href\s*=/i;
const isObject = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value);
class Refusal extends Error {}
/** ``styles`` marks ``layout.template.data``: style defaults keyed by trace type, not traces, so the trace-type refusal does not apply there. */
function inspect(value: unknown, path: string, depth: number, styles = false): void {
  if (depth > 64) throw new Refusal(`${path}: nesting too deep`);
  if (typeof value === "string") {
    if (SCRIPT_LIKE.test(value)) throw new Refusal(`${path}: script-like text or href is not allowed`);
    return;
  }
  if (Array.isArray(value)) {
    value.forEach((item, index) => inspect(item, `${path}[${index}]`, depth + 1, styles));
    return;
  }
  if (!isObject(value)) return;
  for (const [key, item] of Object.entries(value)) {
    const here = `${path}.${key}`;
    if (FORBIDDEN_KEYS.has(key)) throw new Refusal(`${here}: key is not allowed`);
    if (path === "layout" && NETWORK_LAYOUT.has(key))
      throw new Refusal(`${here}: map and geo figures need external tiles`);
    if (!styles && key === "type" && typeof item === "string" && NETWORK_TRACES.has(item))
      throw new Refusal(`${here}: trace type ${item} needs external tiles`);
    if (typeof item === "string") {
      if (key === "href") throw new Refusal(`${here}: href is not allowed`);
      if (/src$/i.test(key)) throw new Refusal(`${here}: source references are not allowed`);
      if (key === "template") throw new Refusal(`${here}: template references are not allowed; embed the template`);
      if (key === "source" && URL_LIKE.test(item)) throw new Refusal(`${here}: URL sources are not allowed`);
    }
    inspect(item, here, depth + 1, styles || (key === "data" && (path === "layout.template" || path.endsWith(".layout.template"))));
  }
}
/** Only ``data``, ``layout`` and ``frames`` pass; URLs, ``*src`` references, hrefs, script-like text, ``config`` and tile-based traces are refused. */
export function validateFigure(spec: unknown): FigureVerdict {
  try {
    if (!isObject(spec)) throw new Refusal("figure specification must be an object");
    const unexpected = Object.keys(spec).filter((key) => !ALLOWED_TOP.includes(key));
    if (unexpected.length) throw new Refusal(`unexpected key ${unexpected[0]}; only data, layout and frames are rendered`);
    if (!Array.isArray(spec.data) || !spec.data.every(isObject)) throw new Refusal("data must be a list of traces");
    if (!isObject(spec.layout)) throw new Refusal("layout must be an object");
    if (spec.frames !== undefined && !Array.isArray(spec.frames)) throw new Refusal("frames must be a list");
    inspect(spec.data, "data", 0);
    inspect(spec.layout, "layout", 0);
    if (spec.frames !== undefined) inspect(spec.frames, "frames", 0);
    const figure: FigureSpec = { data: spec.data as Record<string, unknown>[], layout: spec.layout };
    if (spec.frames !== undefined) figure.frames = spec.frames;
    return { ok: true, figure };
  } catch (error) {
    if (error instanceof Refusal) return { ok: false, reason: error.message };
    throw error;
  }
}
export type RendererState = { state: "ready" } | { state: "mismatch" | "unknown"; label: string };
/** The figure's recorded renderer against the packaged plotly.js; anything else is labelled, not rendered. */
export function rendererState(
  renderer: { name?: string; version?: string } | null | undefined,
  packaged: string = PACKAGED_PLOTLY_VERSION,
): RendererState {
  if (!renderer || typeof renderer.version !== "string" || !renderer.version)
    return { state: "unknown", label: `Renderer not recorded; Studio packages plotly.js ${packaged}` };
  if (renderer.name !== "plotly.js" || renderer.version !== packaged)
    return {
      state: "mismatch",
      label: `Renderer mismatch: the figure was saved for ${renderer.name ?? "an unknown renderer"} ${renderer.version}; Studio packages plotly.js ${packaged}`,
    };
  return { state: "ready" };
}
export function frameUrl(nonce: string): string {
  return FRAME_DOCUMENT + "#" + nonce;
}
export interface FrameMessage {
  type: string;
  data: Record<string, unknown>;
}
/** A message counts only when it comes from the frame's own window and carries the frame's nonce. */
export function acceptsFrameMessage(
  event: { source: unknown; data: unknown },
  nonce: string,
  frameWindow: unknown,
): FrameMessage | null {
  if (!frameWindow || event.source !== frameWindow || !isObject(event.data)) return null;
  const data = event.data;
  if (data.v !== 1 || data.nonce !== nonce || typeof data.type !== "string") return null;
  return { type: data.type, data };
}
/** The PNG the frame returned, or ``null`` for anything that is not a well-formed PNG data URL. */
export function pngBlob(dataUrl: unknown): Blob | null {
  if (typeof dataUrl !== "string" || !dataUrl.startsWith("data:image/png;base64,")) return null;
  let binary: string;
  try {
    binary = atob(dataUrl.slice("data:image/png;base64,".length));
  } catch {
    return null;
  }
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return new Blob([bytes], { type: "image/png" });
}
export type FrameState = "loading" | "rendered" | "error";
export function FigureFrame({
  figure,
  name,
  onState,
}: {
  figure: FigureSpec;
  name: string;
  onState?: (state: FrameState) => void;
}) {
  const [nonce] = useState(() => crypto.randomUUID());
  const frame = useRef<HTMLIFrameElement>(null);
  const [state, setState] = useState<FrameState>("loading");
  const [message, setMessage] = useState("");
  const [exporting, setExporting] = useState(false);
  useEffect(() => {
    const listener = (event: MessageEvent) => {
      const accepted = acceptsFrameMessage(event, nonce, frame.current?.contentWindow);
      if (!accepted) return;
      switch (accepted.type) {
        case "ready":
          // The sandboxed frame has an opaque origin, so the target origin cannot be named.
          frame.current?.contentWindow?.postMessage({ v: 1, nonce, type: "figure", figure }, "*");
          break;
        case "rendered":
          setState("rendered");
          onState?.("rendered");
          break;
        case "error":
          setState("error");
          setMessage(typeof accepted.data.message === "string" ? accepted.data.message : "The renderer failed");
          onState?.("error");
          break;
        case "image": {
          setExporting(false);
          const blob = pngBlob(accepted.data.data);
          if (blob) downloadBlob(blob, name + ".png");
          else setMessage("The renderer returned no readable PNG");
          break;
        }
      }
    };
    window.addEventListener("message", listener);
    return () => window.removeEventListener("message", listener);
  }, [nonce, figure, name, onState]);
  return (
    <div className="af-frame" data-frame={state}>
      <iframe
        ref={frame}
        title={`Interactive figure ${name} (isolated renderer)`}
        sandbox="allow-scripts"
        referrerPolicy="no-referrer"
        src={frameUrl(nonce)}
      />
      <div className="af-frame-bar">
        <span role="status">
          {state === "loading" ? "Rendering in the isolated frame…" : state === "rendered" ? "Rendered locally" : message}
        </span>
        <button
          type="button"
          disabled={state !== "rendered" || exporting}
          onClick={() => {
            setExporting(true);
            frame.current?.contentWindow?.postMessage({ v: 1, nonce, type: "image" }, "*");
          }}
        >
          Download PNG
        </button>
      </div>
    </div>
  );
}
