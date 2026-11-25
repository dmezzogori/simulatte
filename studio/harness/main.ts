// The render harness: one fixture frame drawn by the scene at a fixed size, for the screenshots of
// harness/render.mjs. Query parameters: frame, scale, x, y, select, labels. Unknown parameters are ignored.
import { Scene } from "@simulatte/scene";
import type { PreparedFrame } from "@simulatte/playback";

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
  // `labels` is read by the tasks that add a label switch to the scene
  document.title = "ready";
}

main().catch((error: unknown) => {
  document.title = "error";
  document.body.append(String(error));
});
