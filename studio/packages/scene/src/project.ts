import type { PreparedFrame } from "@simulatte/playback";
import type { State } from "@simulatte/trace";
import { motionProgress } from "./motion";
export interface Point {
  x: number;
  y: number;
}
export interface Glyph extends Point {
  id: string;
  kind: string;
  label: string;
  width: number;
  depth: number;
  rotation: number;
  status: string;
  color: number;
  queue: number;
  unplaced: boolean;
  diagnostic: string | null;
  badge: string;
  shape: string;
}
export interface Projection {
  glyphs: readonly Glyph[];
  links: readonly (readonly [Point, Point])[];
  fallback: boolean;
  ports: readonly Point[];
}
export const object = (v: unknown): Record<string, unknown> =>
  v !== null && typeof v === "object" && !Array.isArray(v)
    ? (v as Record<string, unknown>)
    : {};
const finite = (v: unknown, defaultValue = 0): number =>
  typeof v === "number" && Number.isFinite(v) ? v : defaultValue;
const position = (v: unknown): Point | null =>
  Array.isArray(v) &&
  typeof v[0] === "number" &&
  typeof v[1] === "number" &&
  Number.isFinite(v[0]) &&
  Number.isFinite(v[1])
    ? { x: v[0], y: v[1] }
    : null;
export function equipment(location: unknown): string | null {
  if (typeof location !== "string") return null;
  const match = /^(?:psp|queue|server):(.+)$/.exec(location);
  return match?.[1] ?? null;
}
export class Projector {
  readonly #fallback = new Map<string, Point>();
  constructor(initial: State) {
    Object.keys(initial)
      .filter(
        (id) =>
          !["job", "order", "agv"].includes(String(initial[id]?.["$kind"])),
      )
      .sort()
      .forEach((id, i) =>
        this.#fallback.set(id, { x: (i % 6) * 5, y: -Math.floor(i / 6) * 4 }),
      );
  }
  project(frame: PreparedFrame): Projection {
    const layout = frame.layout?.schema_version === "1.0" ? frame.layout : null;
    const placements = object(object(layout?.["resolved"])["placements"]);
    const nodes = new Map<string, Point>();
    for (const [id, fields] of Object.entries(frame.state)) {
      const placed = position(object(placements[id])["position"]);
      if (placed) nodes.set(id, placed);
      else if (
        fields["$kind"] === "node" &&
        typeof fields["x"] === "number" &&
        typeof fields["y"] === "number"
      )
        nodes.set(id, { x: fields["x"], y: fields["y"] });
      else if (!layout && this.#fallback.has(id))
        nodes.set(id, this.#fallback.get(id)!);
    }
    const ports: Point[] = [];
    const glyphs: Glyph[] = [],
      links: (readonly [Point, Point])[] = [];
    const network = object(layout?.["network"]);
    const graph = object(network["graph"]);
    for (const raw of Array.isArray(graph["arcs"]) ? graph["arcs"] : []) {
      const arc = object(raw),
        a = nodes.get(String(arc["from"] ?? arc["source"])),
        b = nodes.get(String(arc["to"] ?? arc["target"]));
      if (a && b) links.push([a, b]);
    }
    const queueIndex = new Map<string, number>();
    for (const fields of Object.values(frame.state))
      for (const key of ["jobs", "queue", "users"]) {
        const values = fields[key];
        if (Array.isArray(values))
          for (let i = 0; i < values.length; i++)
            if (typeof values[i] === "string")
              queueIndex.set(values[i] as string, i);
      }
    let unplaced = 0;
    for (const [id, fields] of Object.entries(frame.state)) {
      const kind = String(fields["$kind"]),
        place = object(placements[id]),
        visual = object(place["visual"]),
        footprint = object(place["footprint"]);
      let point = nodes.get(id) ?? null,
        diagnostic: string | null = null;
      let status = typeof fields["state"] === "string" ? fields["state"] : kind;
      if (kind === "job") {
        const target = equipment(fields["location"]);
        point = target ? (nodes.get(target) ?? null) : null;
        status = String(fields["location"] ?? "Unlocated");
        if (point) {
          const index = queueIndex.get(id) ?? 0;
          if (index >= 20) continue;
          point = {
            x: point.x + (index % 5) * 0.22 - 0.5,
            y:
              point.y +
              (String(fields["location"]).startsWith("queue:") ? -1.2 : 0.1) -
              Math.floor(index / 5) * 0.22,
          };
        }
      }
      if (kind === "agv") {
        const motion = object(fields["motion"]);
        if (Object.keys(motion).length) {
          const from = nodes.get(String(motion["from"])),
            to = nodes.get(String(motion["to"]));
          if (!Number.isFinite(frame.time)) {
            point = nodes.get(String(fields["node"])) ?? from ?? null;
            diagnostic = "Static non-finite time";
          } else if (from && to) {
            const progress = motionProgress(motion, frame.time);
            point = {
              x: from.x + (to.x - from.x) * progress.fraction,
              y: from.y + (to.y - from.y) * progress.fraction,
            };
            diagnostic = progress.diagnostic;
          } else diagnostic = "Missing motion endpoint";
        } else point = nodes.get(String(fields["node"])) ?? null;
      }
      const isUnplaced = point === null;
      if (!point) {
        point = {
          x: (unplaced % 12) * 1.3,
          y: -7 - Math.floor(unplaced / 12) * 1.2,
        };
        unplaced++;
      }
      if (!isUnplaced)
        for (const port of Object.values(object(place["ports"]))) {
          const local = position(object(port)["position"]);
          if (local) {
            const angle = (finite(place["rotation"]) * Math.PI) / 180;
            ports.push({
              x:
                point.x + local.x * Math.cos(angle) - local.y * Math.sin(angle),
              y:
                point.y + local.x * Math.sin(angle) + local.y * Math.cos(angle),
            });
          }
        }
      const queue = Array.isArray(fields["queue"])
        ? fields["queue"].length
        : Array.isArray(fields["jobs"])
          ? fields["jobs"].length
          : 0;
      const busy = Array.isArray(fields["users"]) && fields["users"].length > 0;
      if (kind === "server")
        status = busy ? "Processing" : queue ? "Queued" : "Idle";
      let color =
        kind === "agv"
          ? 0x658776
          : kind === "job"
            ? 0x728e9d
            : busy
              ? 0xb7c9ad
              : 0xdce1dc;
      if (
        typeof visual["color"] === "string" &&
        /^#[0-9a-f]{6}$/i.test(visual["color"])
      )
        color = parseInt(visual["color"].slice(1), 16);
      glyphs.push({
        id,
        kind,
        label:
          typeof visual["label"] === "string"
            ? visual["label"]
            : typeof fields["label"] === "string"
              ? fields["label"]
              : id,
        ...point,
        width: finite(
          footprint["width"],
          kind === "node"
            ? 0.15
            : kind === "job"
              ? 0.17
              : kind === "agv"
                ? 0.6
                : 2,
        ),
        depth: finite(
          footprint["depth"],
          kind === "node"
            ? 0.15
            : kind === "job"
              ? 0.17
              : kind === "agv"
                ? 0.4
                : 1.2,
        ),
        rotation: (finite(place["rotation"]) * Math.PI) / 180,
        status,
        color,
        queue,
        unplaced: isUnplaced,
        diagnostic,
        shape: ["rectangle", "circle", "diamond"].includes(
          String(visual["shape"]),
        )
          ? String(visual["shape"])
          : "rectangle",
        badge:
          kind === "agv"
            ? `${status}${fields["load"] ? " · loaded" : " · empty"}${typeof fields["battery"] === "number" ? " · battery " + Number(fields["battery"]).toFixed(1) : ""}`
            : kind === "server"
              ? status
              : kind === "warehouse"
                ? `${Object.keys(object(fields["inventory"])).length} SKUs · ${finite(fields["slots_in_use"])} slots used`
                : kind === "charging_station"
                  ? `${finite(fields["slots_in_use"])} charging slots used`
                  : kind === "parking_area"
                    ? `${Array.isArray(fields["parked"]) ? fields["parked"].length : 0} parked`
                    : "",
      });
    }
    return { glyphs, links, ports, fallback: !layout };
  }
}

export interface TransferTrail {
  readonly from: Point;
  readonly to: Point;
  readonly born: number;
  readonly lifetime: number;
}
/** Bounded decoration history, derived only from observed physical destinations. */
export class TransferTrails {
  readonly #physical = new Map<string, { target: string; point: Point }>();
  #cursor: readonly [number, number] | null = null;
  #state: State | null = null;
  #time = -Infinity;
  #trails: TransferTrail[] = [];
  reset(): void {
    this.#physical.clear();
    this.#cursor = null;
    this.#state = null;
    this.#trails = [];
    this.#time = -Infinity;
  }
  update(
    frame: PreparedFrame,
    projection: Projection,
    clock = 0,
    lifetime = 200,
  ): readonly TransferTrail[] {
    const cursor = frame.cursor;
    const backward =
      frame.time < this.#time ||
      (!!cursor && !!this.#cursor && cursor[1] < this.#cursor[1]);
    if (!Number.isFinite(frame.time) || !cursor) {
      this.reset();
      return this.#trails;
    }
    if (backward) this.reset();
    const adjacent = !!this.#cursor && cursor[1] === this.#cursor[1] + 1;
    const crossed = frame.crossedEvents;
    // A gap invalidates the source of a future transfer, not decorations whose
    // transitions were already observed. Explicit seeks/backward playback reset them.
    if (
      (frame.crossingsComplete === false &&
        (!this.#cursor || cursor[1] !== this.#cursor[1])) ||
      (crossed === undefined && this.#cursor && cursor[1] > this.#cursor[1] + 1)
    )
      this.#physical.clear();
    const equipmentPoints = new Map<string, Point>();
    for (const glyph of projection.glyphs)
      if (!glyph.unplaced && !["job", "agv", "order"].includes(glyph.kind))
        equipmentPoints.set(glyph.id, { x: glyph.x, y: glyph.y });
    const locate = (id: string, location: unknown, decorate: boolean) => {
      const target = equipment(location);
      // Transit has no coordinates, but retains its last observed physical source.
      if (!target) return;
      const point = equipmentPoints.get(target);
      if (!point) {
        this.#physical.delete(id);
        return;
      }
      const prior = this.#physical.get(id);
      if (decorate && prior && prior.target !== target) {
        this.#trails.push({
          from: prior.point,
          to: point,
          born: clock,
          lifetime,
        });
        if (this.#trails.length > 256) this.#trails.shift();
      }
      this.#physical.set(id, { target, point });
      if (this.#physical.size > 1000)
        this.#physical.delete(this.#physical.keys().next().value!);
    };
    if (crossed !== undefined && !backward) {
      const createdJobs = new Set<string>();
      for (const event of crossed) {
        // Re-rendering one prepared frame must not replay its transitions twice.
        if (this.#cursor && event.seq <= this.#cursor[1]) continue;
        for (const op of event.deltas) {
          const id = op[1];
          if (typeof id !== "string") continue;
          if (op[0] === "create") {
            this.#physical.delete(id);
            if (op[2] === "job") {
              createdJobs.add(id);
              locate(id, object(op[3])["location"], false);
            }
          } else if (op[0] === "retire") {
            this.#physical.delete(id);
            createdJobs.delete(id);
          } else if (
            op[0] === "set" &&
            op[2] === "location" &&
            (createdJobs.has(id) ||
              this.#physical.has(id) ||
              this.#state?.[id]?.["$kind"] === "job" ||
              frame.state[id]?.["$kind"] === "job")
          ) {
            locate(id, op[3], true);
          }
        }
      }
    }
    // Seed the next crossing from recorded state, including jobs omitted by the
    // bounded queue glyph projection. Do not create trails from endpoint guesses.
    for (const [id, fields] of Object.entries(frame.state))
      if (fields["$kind"] === "job")
        locate(id, fields["location"], crossed === undefined && adjacent);
    for (const id of this.#physical.keys())
      if (!Object.hasOwn(frame.state, id)) this.#physical.delete(id);
    this.#state = frame.state;
    this.#time = frame.time;
    this.#cursor = cursor;
    this.#trails = this.#trails.filter(
      (trail) => clock >= trail.born && clock - trail.born < trail.lifetime,
    );
    return this.#trails;
  }
}
