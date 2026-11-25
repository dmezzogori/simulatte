import { nameOf, naturalCompare, type MapModel, type Shown, type VehicleItem } from "./model";

/** One line of the panel: the id to select, the state mark of a vehicle (else null) and the text. */
export interface OverviewLine {
  id: string;
  shown: Shown | null;
  text: string;
}
/** The vehicles of one fleet; `fleet` is "" for those without one. */
export interface OverviewGroup {
  fleet: string;
  /** Every vehicle of the fleet by state, whatever the filter. */
  counts: Readonly<Record<Shown, number>>;
  lines: readonly OverviewLine[];
  /** Lines that match the filter and did not fit under the cap. */
  hidden: number;
}
export interface Overview {
  vehicles: readonly OverviewGroup[];
  /** `counts` has no prototype: a status may be named `__proto__`. */
  goods: { counts: Readonly<Record<string, number>>; lines: readonly OverviewLine[]; hidden: number };
  traffic: string;
  facilities: readonly OverviewLine[];
}
export interface OverviewOptions {
  /** A case-insensitive substring of the vehicle's text. */
  filter: string;
  waitingOnly: boolean;
  /** Lines per group and for the goods; 200 when omitted. */
  cap?: number;
}

const CAP = 200;
const SHOWN: readonly Shown[] = ["stranded", "waiting", "moving", "working", "idle", "charging", "resting", "unknown"];

const quantities = (load: Readonly<Record<string, number>>): string =>
  Object.entries(load)
    .map(([sku, n]) => `${n} ${sku}`)
    .join(", ");

/** The text of a vehicle, from the model's own `doing` and `goal` so that the map and the panel agree. */
function vehicleText(v: VehicleItem): string {
  const load = v.loaded ? `loaded, ${quantities(v.load)}` : "empty";
  return `${v.label} · ${load}${v.goal !== null ? ` → ${v.goal}` : ""} · ${v.doing}`;
}

/** Stranded first, then waiting by longest wait, then the rest by id. */
function rank(v: VehicleItem): number {
  return v.shown === "stranded" ? 0 : v.shown === "waiting" ? 1 : 2;
}
/** A wait whose start is not recorded counts as the shortest. */
function byAttention(a: VehicleItem, b: VehicleItem): number {
  const ranks = rank(a) - rank(b);
  if (ranks !== 0) return ranks;
  const x = a.wait?.since ?? Infinity,
    y = b.wait?.since ?? Infinity;
  if (a.shown === "waiting" && x !== y) return x < y ? -1 : 1;
  return naturalCompare(a.id, b.id);
}

function trafficText(model: MapModel): string {
  const t = model.traffic;
  const parts = [`${t.waiting} waiting`, `longest ${t.longestWait} s`];
  if (t.waits !== null) parts.push(`waits ${t.waits}`);
  if (t.stuckWaits !== null) parts.push(`stuck ${t.stuckWaits}`);
  if (t.deadlocks !== null) parts.push(`deadlocks ${t.deadlocks}`);
  return parts.join(" · ");
}

/** The panel's content as data, built from the map model. Pure: nothing here touches the DOM. */
export function overview(model: MapModel, options: OverviewOptions): Overview {
  const cap = options.cap ?? CAP;
  const filter = options.filter.toLowerCase();
  const fleets = new Map<string, VehicleItem[]>();
  for (const v of model.vehicles) {
    const key = v.fleet ?? "";
    (fleets.get(key) ?? fleets.set(key, []).get(key)!).push(v);
  }
  const names = [...fleets.keys()].sort((a, b) => (a === "" ? 1 : b === "" ? -1 : naturalCompare(a, b)));
  const vehicles = names.map((fleet): OverviewGroup => {
    const members = fleets.get(fleet)!;
    const counts = Object.fromEntries(SHOWN.map((s) => [s, 0])) as Record<Shown, number>;
    for (const v of members) counts[v.shown]++;
    const matching = members
      .filter((v) => !options.waitingOnly || v.shown === "waiting")
      .sort(byAttention)
      .map((v): OverviewLine => ({ id: v.id, shown: v.shown, text: vehicleText(v) }))
      .filter((line) => line.text.toLowerCase().includes(filter));
    return { fleet, counts, lines: matching.slice(0, cap), hidden: Math.max(0, matching.length - cap) };
  });
  const open = model.orders.filter((o) => o.open).sort((a, b) => a.createdAt - b.createdAt || naturalCompare(a.id, b.id));
  const statuses: Record<string, number> = Object.create(null);
  for (const o of open) statuses[o.status] = (statuses[o.status] ?? 0) + 1;
  const goods = open.slice(0, cap).map(
    (o): OverviewLine => ({
      id: o.id,
      shown: null,
      text: `${o.id} · ${o.quantity} ${o.sku} ${nameOf(model, o.origin)} → ${nameOf(model, o.destination)} · ${
        o.agv === null ? "unassigned" : o.picked ? `on ${nameOf(model, o.agv)}` : `${nameOf(model, o.agv)}, not picked`
      }`,
    }),
  );
  const facilities = [...model.facilities]
    .sort((a, b) => naturalCompare(a.id, b.id))
    .map((f): OverviewLine => {
      const stock = Object.entries(f.stock ?? {}).sort(([a], [b]) => naturalCompare(a, b));
      const detail = stock.length ? stock.map(([sku, n]) => `${sku} ${n}`).join(", ") : f.count;
      return { id: f.id, shown: null, text: detail ? `${f.label} · ${detail}` : f.label };
    });
  return {
    vehicles,
    goods: { counts: statuses, lines: goods, hidden: Math.max(0, open.length - cap) },
    traffic: trafficText(model),
    facilities,
  };
}
