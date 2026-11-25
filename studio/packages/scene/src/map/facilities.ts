import type { Point } from "../project";
import { SYMBOL_PX, fanOut, naturalCompare, type FacilityItem, type OrderItem } from "./model";

type Fields = Record<string, unknown>;
const object = (v: unknown): Fields =>
  v !== null && typeof v === "object" && !Array.isArray(v) ? (v as Fields) : {};
const finite = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
const text = (v: unknown): string | null => (typeof v === "string" ? v : null);
const named = (v: unknown): string | null => (typeof v === "string" && v !== "" ? v : null);
const CLOSED = new Set(["COMPLETED", "FAILED", "CANCELLED"]);
const KINDS = ["warehouse", "parking_area", "charging_station"] as const;
type Kind = (typeof KINDS)[number];

/** Every order in the state, with `open` false for those that ended but are not yet retired. */
export function buildOrders(state: Readonly<Record<string, Fields>>): OrderItem[] {
  const orders: OrderItem[] = [];
  for (const [id, fields] of Object.entries(state)) {
    if (fields["$kind"] !== "order") continue;
    orders.push({
      id,
      sku: text(fields["sku"]) ?? "",
      quantity: finite(fields["quantity"]) ?? 0,
      origin: text(fields["origin"]) ?? "",
      destination: text(fields["destination"]) ?? "",
      status: text(fields["status"]) ?? "",
      agv: text(fields["agv"]),
      picked: fields["picked_at"] !== null && fields["picked_at"] !== undefined,
      createdAt: finite(fields["created_at"]) ?? 0,
      open: !CLOSED.has(String(fields["status"])),
    });
  }
  return orders;
}

/** The nodes a facility is bound to: `<id>:node`, `<id>:input_bays/<i>` and `<id>:output_bays/<i>`. */
function boundNodes(bindings: Fields, id: string): string[] {
  const nodes: string[] = [];
  for (const [key, value] of Object.entries(bindings)) {
    if (!key.startsWith(`${id}:`)) continue;
    const rest = key.slice(id.length + 1);
    if (rest !== "node" && !/^(?:input|output)_bays\/\d+$/.test(rest)) continue;
    const endpoint = text(object(value)["endpoint"]);
    if (endpoint !== null && !nodes.includes(endpoint)) nodes.push(endpoint);
  }
  return nodes;
}

function countOf(kind: Kind, fields: Fields): string {
  if (kind === "warehouse") {
    const inventory = fields["inventory"];
    if (inventory === null || typeof inventory !== "object" || Array.isArray(inventory)) return "";
    const quantities = Object.values(inventory).filter((q): q is number => finite(q) !== null);
    return quantities.length === 1 ? String(Math.round(quantities[0]!)) : `${quantities.length} SKUs`;
  }
  const capacity = finite(kind === "parking_area" ? fields["capacity"] : fields["slots"]);
  if (capacity === null) return "";
  const used =
    kind === "parking_area"
      ? Array.isArray(fields["parked"])
        ? fields["parked"].length
        : 0
      : (finite(fields["slots_in_use"]) ?? 0);
  return `${used} / ${capacity}`;
}

export interface FacilityInput {
  layout: Fields;
  state: Readonly<Record<string, Fields>>;
  /** Positions of every placed entity. */
  positions: ReadonlyMap<string, Point>;
  orders: readonly OrderItem[];
}

/** Warehouses, parking areas and charging stations that have a position, in natural id order. */
export function buildFacilities(input: FacilityInput): FacilityItem[] {
  const placements = object(object(input.layout["resolved"])["placements"]);
  const bindings = object(input.layout["bindings"]);
  const waiting = new Map<string, number>();
  for (const order of input.orders)
    if (order.status === "PENDING") waiting.set(order.origin, (waiting.get(order.origin) ?? 0) + 1);
  const items: Omit<FacilityItem, "offset">[] = [];
  for (const [id, fields] of Object.entries(input.state)) {
    const kind = KINDS.find((k) => k === fields["$kind"]);
    const at = input.positions.get(id);
    if (!kind || !at) continue;
    const place = object(placements[id]),
      footprint = object(place["footprint"]);
    const size = (v: unknown) => {
      const n = finite(v);
      return n !== null && n > 0 ? n : 1;
    };
    items.push({
      id,
      kind,
      x: at.x,
      y: at.y,
      label: named(object(place["visual"])["label"]) ?? named(fields["label"]) ?? id,
      width: size(footprint["width"]),
      depth: size(footprint["depth"]),
      rotation: ((finite(place["rotation"]) ?? 0) * Math.PI) / 180,
      nodes: boundNodes(bindings, id),
      count: countOf(kind, fields),
      badge: waiting.get(id) ?? 0,
    });
  }
  items.sort((a, b) => naturalCompare(a.id, b.id));
  const offsets = fanOut(items, SYMBOL_PX.facility);
  return items.map((item) => ({ ...item, offset: offsets.get(item.id)! }));
}
