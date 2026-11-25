import { describe, expect, it } from "vitest";
import type { PreparedFrame } from "@simulatte/playback";
import { Projector, type Projection } from "../src/project";
import type { MapModel } from "../src/map/model";
import { onMap, pickMap } from "../src/draw/pick";
import { NODE_PX } from "../src/draw/node";
import type { Glyph } from "../src/project";
import { loadFrame } from "./fixtures";

const projectionOf = (name: string): Projection => {
  const frame: PreparedFrame = loadFrame(name);
  return new Projector(frame.initialState ?? frame.state).project(frame);
};
const mapOf = (projection: Projection): MapModel => {
  if (!projection.map) throw new Error("no map");
  return projection.map;
};
/** The camera of a 1200 x 500 canvas centred on (45, 0) at `scale` pixels per metre. */
const cameraAt = (scale: number) => ({ x: 600 - 45 * scale, y: 250, scale });
const screen = (camera: { x: number; y: number; scale: number }, p: { x: number; y: number }) => ({
  x: camera.x + p.x * camera.scale,
  y: camera.y - p.y * camera.scale,
});

describe("pickMap at symbol level", () => {
  const projection = projectionOf("corridor-wait"),
    map = mapOf(projection),
    camera = cameraAt(8);
  const pick = (x: number, y: number) => pickMap(map, projection.glyphs, camera, x, y);
  const agv = map.vehicles.find((v) => v.id === "AGV-1")!;
  const centre = screen(camera, agv);

  it("hits a vehicle inside its symbol, larger than its footprint", () => {
    // 5 px along the heading is beyond the 0.6 m half-length of the footprint at 8 px per metre
    const along = { x: centre.x + 5 * Math.cos(agv.heading), y: centre.y - 5 * Math.sin(agv.heading) };
    expect(pick(along.x, along.y)).toBe("AGV-1");
  });
  it("hits nothing 30 px away", () => {
    expect(pick(centre.x, centre.y - 30)).toBeNull();
    expect(pick(centre.x + 30 * Math.cos(agv.heading), centre.y - 30 * Math.sin(agv.heading))).toBeNull();
  });
  it("hits a warehouse inside its symbol, moved clear of its bay", () => {
    const east = map.facilities.find((f) => f.id === "East")!;
    const bay = screen(camera, map.nodes.find((n) => n.id === "E")!);
    // the symbol is moved east of the bay, so a point beyond the true position is inside it
    expect(pick(bay.x + 23, bay.y)).toBe("East");
    expect(pick(screen(camera, east).x + 20, bay.y)).toBe("East");
    // Without the move the 28 px symbol would span 2 px left to 26 px right of the bay: 33 px right is inside only
    // because of the move, 3 px right is outside only because of it.
    expect(screen(camera, east).x - bay.x).toBe(12);
    expect(pick(bay.x + 33, bay.y)).toBe("East");
    expect(pick(bay.x + 3, bay.y)).toBeNull();
  });
  it("does not pick nodes", () => {
    const m1 = screen(camera, map.nodes.find((n) => n.id === "M1")!);
    expect(pick(m1.x, m1.y)).toBeNull();
  });
  it("picks the parking area around its node", () => {
    const s = screen(camera, map.nodes.find((n) => n.id === "S")!);
    expect(pick(s.x, s.y)).toBe("Siding");
  });
});

describe("pickMap order and levels", () => {
  it("tests vehicles before facilities", () => {
    const projection = projectionOf("corridor-idle"),
      map = mapOf(projection),
      camera = cameraAt(8);
    const s = screen(camera, map.nodes.find((n) => n.id === "S")!);
    expect(pickMap(map, projection.glyphs, camera, s.x, s.y)).toBe("AGV-2");
  });
  it("includes the fan-out offset of a vehicle", () => {
    const projection = projectionOf("advanced-charging"),
      map = mapOf(projection),
      camera = cameraAt(8);
    const park = screen(camera, map.nodes.find((n) => n.id === "PARK")!);
    expect(pickMap(map, projection.glyphs, camera, park.x - 24, park.y)).toBe("AGV-1");
    expect(pickMap(map, projection.glyphs, camera, park.x + 24, park.y)).toBe("AGV-5");
  });
  it("uses the footprint at true-size level, and nodes are picked there", () => {
    const projection = projectionOf("corridor-wait"),
      map = mapOf(projection),
      camera = cameraAt(30);
    const agv = screen(camera, map.vehicles.find((v) => v.id === "AGV-1")!);
    expect(pickMap(map, projection.glyphs, camera, agv.x + 15, agv.y)).toBe("AGV-1");
    expect(pickMap(map, projection.glyphs, camera, agv.x + 22, agv.y)).toBeNull();
    const m1 = screen(camera, map.nodes.find((n) => n.id === "M1")!);
    expect(pickMap(map, projection.glyphs, camera, m1.x, m1.y)).toBe("M1");
  });
  it("leaves a kind with a registered renderer to its glyph", () => {
    const projection = projectionOf("corridor-wait"),
      map = mapOf(projection),
      camera = cameraAt(8);
    const agv = map.vehicles.find((v) => v.id === "AGV-1")!;
    const centre = screen(camera, agv);
    // the glyph is the 1.2 m footprint: 5 px along the heading is outside it
    expect(pickMap(map, projection.glyphs, camera, centre.x + 5, centre.y, (kind) => kind === "agv")).toBeNull();
    expect(pickMap(map, projection.glyphs, camera, centre.x + 3, centre.y, (kind) => kind === "agv")).toBe("AGV-1");
  });
});

describe("nodes at true-size level", () => {
  const projection = projectionOf("corridor-wait"),
    map = mapOf(projection);
  const m1 = map.nodes.find((n) => n.id === "M1")!;
  it("are hit within their drawn radius, however far the scale zooms", () => {
    for (const scale of [20, 100]) {
      const camera = cameraAt(scale),
        at = screen(camera, m1);
      expect(pickMap(map, projection.glyphs, camera, at.x + NODE_PX - 1, at.y), `${scale} px/m inside`).toBe("M1");
      expect(pickMap(map, projection.glyphs, camera, at.x + NODE_PX + 2, at.y), `${scale} px/m outside`).toBeNull();
    }
  });
  it("keep a registered renderer's glyph test", () => {
    const camera = cameraAt(100),
      at = screen(camera, m1);
    // the glyph is 0.15 m wide: 7 px is inside its half-width of 7.5 px at 100 px per metre
    expect(pickMap(map, projection.glyphs, camera, at.x + 7, at.y, (kind) => kind === "node")).toBe("M1");
  });
});

describe("what a map draws and picks (M24)", () => {
  const glyph = (kind: string): Glyph => ({ ...projectionOf("corridor-wait").glyphs[0]!, id: `a ${kind}`, kind });
  it("leaves out orders and fleets unless a renderer is registered for their kind", () => {
    expect(onMap(glyph("order"))).toBe(false);
    expect(onMap(glyph("fleet"))).toBe(false);
    expect(onMap(glyph("agv"))).toBe(true);
    expect(onMap(glyph("server"))).toBe(true);
    expect(onMap(glyph("order"), (kind) => kind === "order")).toBe(true);
  });
  it("does not pick an order drawn under the pointer", () => {
    const projection = projectionOf("corridor-wait"),
      map = mapOf(projection),
      camera = cameraAt(8);
    const far = { ...glyph("order"), id: "order-x", x: 45, y: 30, width: 4, depth: 4 };
    const at = screen(camera, far);
    expect(pickMap(map, [...projection.glyphs, far], camera, at.x, at.y)).toBeNull();
    expect(pickMap(map, [...projection.glyphs, far], camera, at.x, at.y, (kind) => kind === "order")).toBe("order-x");
  });
});
