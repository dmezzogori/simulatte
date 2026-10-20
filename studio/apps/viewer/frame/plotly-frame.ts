/** The isolated Plotly renderer: a sandboxed, opaque-origin document that never fetches.
 *
 * It accepts exactly one ``figure`` message whose nonce equals its URL hash and whose source is its
 * parent window, renders it with the packaged plotly.js and answers ``rendered``. ``image`` requests
 * with the same nonce are answered with a browser-generated PNG after rendering. Everything else
 * is ignored. The frame's CSP (``default-src 'none'; connect-src 'none'``) backs these rules.
 */
import * as Plotly from "plotly.js-dist-min";
const nonce = location.hash.slice(1);
const origin = new URL(location.href).origin; // the serving Studio document is the only allowed embedder
const host = document.getElementById("figure") as HTMLDivElement;
// showLink predates plotly.js 4 and is kept for older renderers; the cast admits it beside the typed keys.
const CONFIG = {
  staticPlot: false,
  displayModeBar: true,
  displaylogo: false,
  responsive: true,
  plotlyServerURL: "",
  showLink: false,
  editable: false,
  toImageButtonOptions: { format: "png" as const },
  // Defence in depth beside connect-src 'none': no cloud or Chart Studio buttons, and no in-frame
  // download button (a sandbox without allow-downloads would silently drop it; the parent exports PNG).
  showSendToCloud: false,
  showEditInChartStudio: false,
  modeBarButtonsToRemove: ["sendChartToCloud", "downloadJson"] as Plotly.ModeBarDefaultButtons[],
};
let taken = false;
let rendered = false;
const reply = (body: Record<string, unknown>) => window.parent.postMessage({ v: 1, nonce, ...body }, origin);
const failure = (error: unknown) =>
  reply({ type: "error", message: error instanceof Error ? error.message : String(error) });
window.addEventListener("message", (event) => {
  if (!nonce || event.source !== window.parent || event.origin !== origin) return;
  const data: unknown = event.data;
  if (!data || typeof data !== "object") return;
  const message = data as Record<string, unknown>;
  if (message.v !== 1 || message.nonce !== nonce) return;
  if (message.type === "figure") {
    if (taken) return;
    taken = true;
    const figure = message.figure as { data: Plotly.Data[]; layout: Partial<Plotly.Layout>; frames?: Plotly.Frame[] };
    const spec: Plotly.PlotlyDataLayoutConfig = { data: figure.data, layout: figure.layout, config: CONFIG as Partial<Plotly.Config> };
    if (figure.frames) spec.frames = figure.frames;
    Plotly.newPlot(host, spec)
      .then(() => {
        rendered = true;
        reply({ type: "rendered" });
      })
      .catch(failure);
  } else if (message.type === "image" && rendered) {
    const width = host.clientWidth || 800;
    const height = host.clientHeight || 450;
    Plotly.toImage(host, { format: "png", width, height, scale: 2 })
      .then((url) => reply({ type: "image", data: url }))
      .catch(failure);
  }
});
reply({ type: "ready" });
