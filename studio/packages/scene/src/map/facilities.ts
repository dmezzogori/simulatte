import type { Point } from "../project";
import { SYMBOL_PX, fanOut, naturalCompare, type FacilityItem, type OrderItem, type StaticMap } from "./model";
import { finite, named, object, quantitiesOf, text, type Fields } from "./read";

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

const BOUND = /^(.*):(?:node|(?:input|output)_bays\/\d+)$/;

/**
 * The nodes each facility is bound to: `<id>:node`, `<id>:input_bays/<i>` and `<id>:output_bays/<i>`, by facility id,
 * in binding order, each once. The rest of a key never holds a colon, so the id is all before the last one.
 */
export function indexBindings(bindings: Fields): Map<string, string[]> {
  const index = new Map<string, string[]>();
  for (const [key, value] of Object.entries(bindings)) {
    const id = BOUND.exec(key)?.[1];
    if (id === undefined) continue;
    const endpoint = text(object(value)["endpoint"]);
    if (endpoint === null) continue;
    const nodes = index.get(id) ?? index.set(id, []).get(id)!;
    if (!nodes.includes(endpoint)) nodes.push(endpoint);
  }
  return index;
}

/** A warehouse's stock per SKU; null for the other kinds and for an inventory that is not recorded. */
function stockOf(kind: Kind, fields: Fields): Record<string, number> | null {
  const inventory = fields["inventory"];
  if (kind !== "warehouse" || inventory === null || typeof inventory !== "object" || Array.isArray(inventory)) return null;
  return quantitiesOf(inventory);
}

/** What is shown under the name: a warehouse's stock (from `stockOf`), the occupancy of the others. */
function countOf(kind: Kind, fields: Fields, stock: Readonly<Record<string, number>> | null): string {
  if (kind === "warehouse") {
    if (stock === null) return "";
    const quantities = Object.values(stock);
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
export function buildFacilities(input: FacilityInput, cache?: StaticMap): FacilityItem[] {
  const placements = object(object(input.layout["resolved"])["placements"]);
  let bound = cache?.bindings;
  if (!bound) {
    bound = indexBindings(object(input.layout["bindings"]));
    if (cache) cache.bindings = bound;
  }
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
    const stock = stockOf(kind, fields);
    items.push({
      id,
      kind,
      x: at.x,
      y: at.y,
      label: named(object(place["visual"])["label"]) ?? named(fields["label"]) ?? id,
      width: size(footprint["width"]),
      depth: size(footprint["depth"]),
      rotation: ((finite(place["rotation"]) ?? 0) * Math.PI) / 180,
      nodes: [...(bound.get(id) ?? [])],
      count: countOf(kind, fields, stock),
      stock,
      badge: waiting.get(id) ?? 0,
    });
  }
  items.sort((a, b) => naturalCompare(a.id, b.id));
  const offsets = fanOut(items, SYMBOL_PX.facility);
  return items.map((item) => ({ ...item, offset: offsets.get(item.id)! }));
}
