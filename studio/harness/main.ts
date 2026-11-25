// The render harness: one fixture frame drawn by the scene at a fixed size, for the screenshots of
// harness/render.mjs. Query parameters: frame, scale, x, y, select, labels, panel (1: the Overview panel beside the
// canvas, with default options). Unknown parameters are ignored.
import { Scene, overview } from "@simulatte/scene";
import type { PreparedFrame } from "@simulatte/playback";
import { createOverviewPanel, renderOverview } from "../apps/viewer/src/overview-panel";

const WIDTH = 1200;
const HEIGHT = 500;
const fixtures = import.meta.glob("../fixtures/map/*.json", { eager: true, import: "default" }) as Record<
  string,
  Pick<PreparedFrame, "time" | "cursor" | "state" | "initialState" | "layout">
>;

function number(params: URLSearchParams, name: string): number | null {
  const raw = params.get(name);
  if (raw === null || raw === "") return null;
  const value = Number(raw);
  if (!Number.isFinite(value)) throw new Error(`the parameter ${name} is not a number: ${raw}`);
  return value;
}

async function main(): Promise<void> {
  const params = new URLSearchParams(location.search);
  const name = params.get("frame") ?? "";
  const file = fixtures[`../fixtures/map/${name}.json`];
  if (!file) throw new Error(`no fixture named "${name}"`);
  const frame: PreparedFrame = {
    ...file,
    mode: "continuous",
    catalog: {},
    kpis: {},
    diagnostics: [],
    crossedEvents: [],
    crossingsComplete: true,
  };
  const scene = new Scene();
  scene.decorativeTransfers = false;
  scene.labels = params.get("labels") !== "0";
  await scene.init(document.getElementById("host")!, WIDTH, HEIGHT);
  scene.render(frame); // the first render fits the camera
  const scale = number(params, "scale");
  if (scale !== null) {
    // the centre defaults to the centre of the fit
    const fit = scene.camera;
    const x = number(params, "x") ?? (WIDTH / 2 - fit.x) / fit.scale;
    const y = number(params, "y") ?? (fit.y - HEIGHT / 2) / fit.scale;
    scene.camera = { x: WIDTH / 2 - x * scale, y: HEIGHT / 2 + y * scale, scale };
  }
  const select = params.get("select");
  if (select) scene.select(select);
  if (params.get("panel") === "1") await mountPanel(scene);
  document.title = "ready";
}

/** The Overview panel beside the canvas, as the viewer wires it: a click selects the id in the scene and centres on it. */
async function mountPanel(scene: Scene): Promise<void> {
  await import("../apps/viewer/src/style.css");
  const panel = createOverviewPanel(() => refresh());
  panel.element.id = "overview";
  panel.element.style.cssText = `position:absolute;left:${WIDTH + 10}px;top:0;width:420px;height:${HEIGHT}px;background:#fff`;
  document.body.append(panel.element);
  const refresh = (): void => {
    const map = scene.map;
    renderOverview(panel.body, map ? overview(map, panel.options()) : null, (id) => {
      scene.select(id);
      scene.focus(id);
    });
  };
  refresh();
}

main().catch((error: unknown) => {
  document.title = "error";
  document.body.append(String(error));
});
