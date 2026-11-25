import type { FacilityItem, MapModel, OrderItem, VehicleItem } from "./model";

/** One line of the click detail; `refs` are the ids named in it that can be selected, in order of appearance. */
export interface DetailLine {
  text: string;
  refs?: readonly string[];
}

type Fields = Record<string, unknown>;
const finite = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
const strings = (v: unknown): string[] | null =>
  Array.isArray(v) ? v.filter((id): id is string => typeof id === "string") : null;
const one = (n: number): string => n.toFixed(1);
const joined = (ids: readonly string[], none: string): string => (ids.length ? ids.join(", ") : none);
const quantities = (load: Readonly<Record<string, number>>): string =>
  Object.entries(load)
    .map(([sku, n]) => `${n} ${sku}`)
    .join(", ");
const degrees = (radians: number): number => (((Math.round((radians * 180) / Math.PI) % 360) + 360) % 360) % 360;

/**
 * The readable description of an element (spec section 5.7), from the map model and, for what the model does
 * not carry, the frame's state. Null for an id of a kind it does not cover.
 */
export function describe(model: MapModel, state: Readonly<Record<string, Fields>>, id: string): readonly DetailLine[] | null {
  const fields = Object.hasOwn(state, id) ? state[id]! : null;
  const kind = fields?.["$kind"];
  const selectable = new Set<string>([
    ...model.vehicles.map((v) => v.id),
    ...model.orders.map((o) => o.id),
    ...model.facilities.map((f) => f.id),
    ...model.nodes.map((n) => n.id),
    ...model.strips.map((s) => s.id),
    ...Object.keys(model.zones),
  ]);
  /** A line, with a ref for each of its ids that is in the model, once each. */
  const make = (text: string, named: readonly string[] = []): DetailLine => {
    const refs = [...new Set(named.filter((name) => selectable.has(name)))];
    return refs.length ? { text, refs } : { text };
  };
  const time = (v: unknown): string | null => (finite(v) === null ? null : one(v as number));

  const vehicle = model.vehicles.find((v) => v.id === id);
  if (vehicle && kind === "agv") return vehicleLines(model, state, vehicle, make);
  const order = model.orders.find((o) => o.id === id);
  if (order && kind === "order") return orderLines(order, fields!, make, time);
  const facility = model.facilities.find((f) => f.id === id);
  if (facility && fields) return facilityLines(model, facility, fields, make);
  if (fields && kind === "node" && model.nodes.some((n) => n.id === id)) return nodeLines(model, id, fields, make);
  if (fields && kind === "segment" && model.strips.some((s) => s.id === id)) return segmentLines(id, fields, make);
  if (fields && kind === "zone" && Object.hasOwn(model.zones, id)) return zoneLines(id, fields, make);
  return null;
}

type Make = (text: string, named?: readonly string[]) => DetailLine;

function vehicleLines(model: MapModel, state: Readonly<Record<string, Fields>>, v: VehicleItem, make: Make): DetailLine[] {
  const lines: DetailLine[] = [make([v.label, ...(v.fleet !== null ? [`fleet ${v.fleet}`] : []), v.state].join(" · "))];

  // load and order: the order only when it is open
  const order = v.order === null ? undefined : model.orders.find((o) => o.id === v.order);
  const parts = [v.loaded ? `Load ${quantities(v.load)}` : "Empty"];
  const named: string[] = [];
  if (v.order !== null && (!order || order.open)) {
    named.push(v.order);
    let text = v.order;
    if (order) {
      named.push(order.origin, order.destination);
      text += `, ${order.origin} → ${order.destination}`;
      const picked = finite(Object.hasOwn(state, order.id) ? state[order.id]!["picked_at"] : null);
      if (order.picked && picked !== null) text += `, picked at ${one(picked)}`;
    }
    parts.push(text);
  }
  lines.push(make(parts.join(" · "), named));

  // where it is
  const where: string[] = [];
  const at: string[] = [];
  if (v.arc) {
    where.push(`On ${v.arc.from} → ${v.arc.to}`);
    at.push(v.arc.from, v.arc.to);
  } else if (v.node !== null) {
    where.push(`At node ${v.node}`);
    at.push(v.node);
  }
  where.push(`heading ${degrees(v.heading)}°`);
  if (v.speed !== null) where.push(`speed ${one(v.speed)} m/s`);
  lines.push(make(where.join(" · "), at));

  if (v.battery !== null) {
    const percent = v.batteryCapacity !== null && v.batteryCapacity > 0 ? ` (${Math.round((100 * v.battery) / v.batteryCapacity)} %)` : "";
    lines.push(make(`Battery ${one(v.battery)}${percent}`));
  }

  if (v.route) {
    const nodes = [...v.route.nodes];
    if (nodes[nodes.length - 1] !== v.route.destination) nodes.push(v.route.destination);
    const next = nextNode(nodes, v);
    lines.push(make(`Route ${nodes.map((n, i) => (i === next ? `[${n}]` : n)).join(" → ")}`, nodes));
  }

  // a stranded vehicle keeps its last wait: the map shows it as stranded, so the detail does not call it waiting
  if (v.wait && v.shown === "waiting") {
    const seconds = Math.max(0, Math.floor(model.time - v.wait.since));
    lines.push(make(`Waiting ${seconds} s (since ${one(v.wait.since)})${v.wait.node !== "" ? ` to enter ${v.wait.node}` : ""}`, [v.wait.node]));
    if (v.wait.resources.length) lines.push(make(`Needs ${v.wait.resources.join(", ")}`, v.wait.resources));
    if (v.wait.blockers.length) lines.push(make(`Blocked by ${v.wait.blockers.join(", ")}`, v.wait.blockers));
  }

  if (v.holds.length) lines.push(make(`Holds ${v.holds.join(", ")}`, v.holds));
  if (v.sized) lines.push(make(`Size ${one(v.length)} × ${one(v.width)} m`));
  return lines;
}

/** The index of the next node of the route: the one after the vehicle's node, or its arc's `to`. */
function nextNode(nodes: readonly string[], v: VehicleItem): number {
  if (v.arc) {
    for (let i = 0; i + 1 < nodes.length; i++) if (nodes[i] === v.arc.from && nodes[i + 1] === v.arc.to) return i + 1;
    return nodes.indexOf(v.arc.to);
  }
  const i = v.node === null ? -1 : nodes.indexOf(v.node);
  return i >= 0 && i + 1 < nodes.length ? i + 1 : -1;
}

function orderLines(
  order: OrderItem,
  fields: Fields,
  make: Make,
  time: (v: unknown) => string | null,
): DetailLine[] {
  const lines = [make(`${order.id} · ${order.status}`), make(`Carries ${order.quantity} ${order.sku}`)];
  lines.push(make(`From ${order.origin}`, [order.origin]), make(`To ${order.destination}`, [order.destination]));
  for (const [label, field] of [
    ["Created", "created_at"],
    ["Dispatched", "dispatched_at"],
    ["Picked", "picked_at"],
    ["Delivered", "delivered_at"],
  ] as const) {
    const t = time(fields[field]);
    if (t !== null) lines.push(make(`${label} at ${t}`));
  }
  lines.push(order.agv !== null ? make(`Vehicle ${order.agv}`, [order.agv]) : make("No vehicle yet"));
  return lines;
}

function facilityLines(model: MapModel, f: FacilityItem, fields: Fields, make: Make): DetailLine[] {
  const lines = [make(`${f.label} · ${f.kind.replace("_", " ")}`)];
  const slots = finite(fields["slots_in_use"]);
  if (f.kind === "warehouse") {
    const inventory = fields["inventory"];
    if (inventory !== null && typeof inventory === "object" && !Array.isArray(inventory))
      lines.push(make(`Stock ${Object.keys(f.stock).length ? quantities(f.stock) : "none"}`));
    if (slots !== null) lines.push(make(`Slots in use ${slots}`));
  } else if (f.kind === "parking_area") {
    const parked = strings(fields["parked"]) ?? [];
    const capacity = finite(fields["capacity"]);
    lines.push(make(`Parked ${parked.length}${capacity !== null ? ` of ${capacity}` : ""}${parked.length ? `: ${parked.join(", ")}` : ""}`, parked));
  } else if (slots !== null) {
    const total = finite(fields["slots"]);
    lines.push(make(`Slots in use ${slots}${total !== null ? ` of ${total}` : ""}`));
  }
  if (f.nodes.length) {
    lines.push(make(`${f.kind === "warehouse" ? "Bays at" : f.nodes.length > 1 ? "Nodes" : "Node"} ${f.nodes.join(", ")}`, f.nodes));
    const here = model.vehicles.filter((v) => v.node !== null && f.nodes.includes(v.node)).map((v) => v.id);
    lines.push(make(`Vehicles at it: ${joined(here, "none")}`, here));
  }
  if (f.kind === "warehouse") {
    const open = model.orders.filter((o) => o.open);
    const from = open.filter((o) => o.origin === f.id).map((o) => o.id);
    const to = open.filter((o) => o.destination === f.id).map((o) => o.id);
    lines.push(make(`Open orders from it: ${joined(from, "none")}`, from), make(`Open orders to it: ${joined(to, "none")}`, to));
  }
  return lines;
}

function nodeLines(model: MapModel, id: string, fields: Fields, make: Make): DetailLine[] {
  const lines = [make(`Node ${id}`)];
  const standing = strings(fields["agvs"]);
  if (standing) lines.push(make(`Vehicles on it: ${joined(standing, "none")}`, standing));
  const holders = strings(fields["reserved_by"]);
  if (holders) lines.push(make(`Held by: ${joined(holders, "nobody")}`, holders));
  const capacity = finite(fields["capacity"]);
  if (capacity !== null) lines.push(make(`Capacity ${capacity}`));
  for (const f of model.facilities) if (f.nodes.includes(id)) lines.push(make(`Bound to ${f.id}`, [f.id]));
  return lines;
}

function segmentLines(id: string, fields: Fields, make: Make): DetailLine[] {
  const lines = [make(`Segment ${id}`)];
  const source = typeof fields["source"] === "string" ? fields["source"] : null;
  const target = typeof fields["target"] === "string" ? fields["target"] : null;
  if (source !== null && target !== null) lines.push(make(`Between ${source} and ${target}`, [source, target]));
  const length = finite(fields["length"]);
  if (length !== null) lines.push(make(`Length ${one(length)} m`));
  if (source !== null && target !== null)
    for (const [from, to, holders] of [
      [source, target, strings(fields["forward"])],
      [target, source, strings(fields["backward"])],
    ] as const)
      if (holders) lines.push(make(`Held ${from} → ${to} by: ${joined(holders, "nobody")}`, holders));
  return lines;
}

function zoneLines(id: string, fields: Fields, make: Make): DetailLine[] {
  const lines = [make(`Zone ${id}`)];
  if (typeof fields["rule"] === "string") lines.push(make(`Rule ${fields["rule"]}`));
  const capacity = finite(fields["capacity"]);
  if (capacity !== null) lines.push(make(`Capacity ${capacity}`));
  const members = strings(fields["members"]);
  if (members) lines.push(make(`Members: ${joined(members, "none")}`, members));
  const holders = strings(fields["reserved_by"]);
  if (holders) lines.push(make(`Held by: ${joined(holders, "nobody")}`, holders));
  if (typeof fields["orientation"] === "string") lines.push(make(`Orientation ${fields["orientation"]}`));
  return lines;
}
