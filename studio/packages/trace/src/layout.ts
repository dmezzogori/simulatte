/** Data-only layout-v1 metadata. Unknown schema versions remain opaque. No hash verifier or renderer. */
import { validateLayoutDefinition, validateLayoutPatch } from "./layout-schema";
import type { Wire } from "./wire";

export type LayoutBundle = Readonly<Record<string, Wire>> & { readonly schema_version: string };
const FIELDS = "schema_version algorithms stages topology targets layers dependencies resolved network aliases bindings diagnostics auto leaves required_paths full_hash physical_hash layers_hash".split(" ");
type Map = Readonly<Record<string, Wire>>;
function map(value: unknown): Map {
  if (value === null || typeof value !== "object" || Array.isArray(value)) throw new TypeError("layout requires a map");
  return value as Map;
}
function array(value: Wire | undefined): readonly Wire[] {
  if (!Array.isArray(value)) throw new TypeError("layout requires an array");
  return value;
}
function string(value: Wire | undefined): string {
  if (typeof value !== "string") throw new TypeError("layout requires a string");
  return value;
}
function finite(value: Wire): void {
  if (typeof value === "number" && !Number.isFinite(value)) throw new TypeError("layout numbers must be finite");
  if (value !== null && typeof value === "object") for (const item of Object.values(value)) finite(item);
}

/** Decoded wire maps are already deeply frozen by the trace codec. */
export function layoutOf(value: unknown): LayoutBundle {
  const data = map(value);
  const version = string(data["schema_version"]);
  if (version !== "1.0") return data as LayoutBundle;
  if (Object.keys(data).length !== FIELDS.length || FIELDS.some((key) => !(key in data))) throw new TypeError("layout-v1 has missing or unknown fields");
  const algorithms = map(data["algorithms"]);
  if (Object.keys(algorithms).length !== 3 || algorithms["auto"] !== "layered-v1" || algorithms["network"] !== "network-v1" || algorithms["hash"] !== "layout-hash-v1") throw new TypeError("unsupported layout algorithm");
  for (const name of ["full_hash", "physical_hash", "layers_hash"]) if (!/^[0-9a-f]{64}$/.test(string(data[name]))) throw new TypeError(`invalid layout ${name}`);
  for (const name of ["stages", "topology", "targets", "layers", "dependencies", "resolved", "auto", "bindings"]) map(data[name]);
  for (const name of ["aliases", "diagnostics", "leaves", "required_paths"]) array(data[name]);
  const layers = map(data["layers"]);
  if (map(layers["file"])["schema_version"] !== "1.0") throw new TypeError("invalid saved file layer");
  const code = array(layers["code"]);
  for (const item of code) {
    const pair = array(item);
    if (pair.length !== 2) throw new TypeError("invalid saved code layer");
    string(pair[0]);
    if (map(pair[1])["schema_version"] !== "1.0") throw new TypeError("invalid saved code patch");
  }
  Object.values(map(data["targets"])).forEach((value) => target(value, true));
  const closure = map(data["dependencies"]);
  if (closure["sealed"] !== true) throw new TypeError("layout dependency closure must be sealed");
  map(closure["bindings"]);
  for (const item of array(closure["queries"])) {
    const query = map(item), descriptor = map(query["descriptor"]);
    if (!["value", "presence", "variant", "membership", "selection"].includes(string(descriptor["kind"]))) throw new TypeError("unknown layout dependency query");
    array(descriptor["path"]).forEach(string);
    const count = query["code_count"];
    if (typeof count !== "number" || !Number.isInteger(count) || count < 0 || count > code.length) throw new TypeError("invalid dependency stage prefix");
    map(query["observed"]); map(query["targets"]);
    if (!["model", "network"].includes(string(query["origin"]))) throw new TypeError("invalid dependency origin");
  }
  if (data["network"] !== null) {
    const network = map(data["network"]);
    for (const name of ["nodes", "edges", "lineage", "adjacency", "weak_components", "strong_components"]) array(network[name]);
    const graph = map(network["graph"]);
    array(graph["nodes"]); array(graph["arcs"]);
    for (const item of array(network["nodes"])) {
      const node = map(item);
      string(node["id"]);
      if (array(node["position"]).length !== 3 || typeof node["enabled"] !== "boolean") throw new TypeError("invalid node definition");
    }
    for (const item of array(network["edges"])) if (![null, "both", "forward", "backward"].includes(map(item)["direction"] as string | null)) throw new TypeError("invalid edge direction");
  }
  details(data);
  finite(value as Wire);
  return data as LayoutBundle;
}

function keys(value: Wire | undefined, required: string, optional = ""): Map {
  const result = map(value), expected = required.split(" "), allowed = (required + " " + optional).split(" ");
  if (expected.some((key) => !(key in result)) || Object.keys(result).some((key) => !allowed.includes(key))) throw new TypeError("invalid layout record fields");
  return result;
}
function strings(value: Wire | undefined): void { array(value).forEach(string); }
function point(value: Wire | undefined): void { validateLayoutDefinition(value, "point"); }
function address(value: Wire | undefined): void { validateLayoutDefinition(value, "address"); }
function patch(value: Wire | undefined): void { validateLayoutPatch(value); }
function target(value: Wire | undefined, named: boolean): void {
  const t = keys(value, "kind explicitly_placed" + (named ? " explicitly_named" : ""), "graph_position node_id");
  validateLayoutDefinition(t["kind"], "id");
  if (typeof t["explicitly_placed"] !== "boolean" || (named && typeof t["explicitly_named"] !== "boolean")) throw new TypeError("invalid target eligibility");
  if (t["graph_position"] !== undefined) {
    if (t["kind"] !== "node") throw new TypeError("graph position requires node target");
    point(t["graph_position"]);
  }
  if (t["node_id"] !== undefined) {
    if (t["kind"] !== "agv") throw new TypeError("bound node requires agv target");
    validateLayoutDefinition(t["node_id"], "id");
  }
}
function topology(value: Wire | undefined): void {
  for (const group of Object.values(keys(value, "flows shopfloors routings"))) for (const sequence of array(group)) for (const id of array(sequence)) validateLayoutDefinition(id, "id");
}
function nullableDefinition(value: Wire | undefined, name: "capacity" | "positive"): void {
  if (value !== null) validateLayoutDefinition(value, name);
}
function selection(d: Map, o: Map): void {
  const predicate = string(d["predicate"]), members = array(o["members"]), selected: string[] = [], flags = map(o["flags"]);
  strings(members); strings(o["selected"]);
  if (new Set(members).size !== members.length || Object.keys(flags).length !== members.length || members.some((id) => !Object.hasOwn(flags, id as string))) throw new TypeError("invalid selection candidate membership");
  for (const id of members as string[]) {
    const flag = keys(flags[id], predicate === "obstacle" ? "state value" : "state ports"), state = string(flag["state"]);
    if (!["present", "absent", "unset"].includes(state)) throw new TypeError("invalid selection flag state");
    let match: boolean;
    if (predicate === "obstacle") {
      if (state === "present" ? typeof flag["value"] !== "boolean" : flag["value"] !== null) throw new TypeError("invalid obstacle predicate value");
      match = flag["value"] === true;
    } else {
      const ports = map(flag["ports"]);
      if (Object.values(ports).some((v) => !["present", "absent", "unset"].includes(string(v))) || (state !== "present" && Object.keys(ports).length > 0)) throw new TypeError("invalid connector predicate ports");
      match = Object.values(ports).includes("present");
    }
    if (match) selected.push(id);
  }
  if (JSON.stringify(selected) !== JSON.stringify(o["selected"]) || (o["state"] !== "present" && members.length > 0)) throw new TypeError("selection result disagrees with flags");
}
function details(data: Map): void {
  const layers = keys(data["layers"], "code file");
  patch(layers["file"]); patch(data["resolved"]);
  for (const item of array(layers["code"])) patch(array(item)[1]);
  const stages = keys(data["stages"], "stages binders");
  for (const group of Object.values(stages)) for (const item of array(group)) {
    const stage = keys(item, "name reads after");
    string(stage["name"]); strings(stage["after"]);
    for (const value of array(stage["reads"])) {
      const read = keys(value, "kind", "path");
      if (!["value", "graph"].includes(string(read["kind"]))) throw new TypeError("invalid read grant");
      if (read["kind"] === "value") strings(read["path"]);
      address(read);
    }
  }
  topology(data["topology"]);
  const auto = keys(data["auto"], "algorithm data targets topology derived_positions");
  if (auto["algorithm"] !== "layered-v1") throw new TypeError("unsupported auto algorithm");
  patch(auto["data"]); Object.values(map(auto["targets"])).forEach((value) => target(value, false)); topology(auto["topology"]);
  Object.values(map(auto["derived_positions"])).forEach(point);
  const closure = keys(data["dependencies"], "queries bindings sealed");
  for (const value of array(closure["queries"])) {
    const q = keys(value, "descriptor observed code_count targets origin"), d = keys(q["descriptor"], "kind path", "predicate default");
    const kind = string(d["kind"]);
    const fields: Record<string, string> = { value: "state value eligible unset", presence: "state", variant: "state value", membership: "state members", selection: "state members flags selected" };
    const o = keys(q["observed"], fields[kind]!);
    if (!["present", "absent", "unset"].includes(string(o["state"]))) throw new TypeError("invalid dependency state");
    Object.values(map(q["targets"])).forEach(string);
    if (kind === "value") {
      if (typeof o["eligible"] !== "boolean") throw new TypeError("invalid physical eligibility");
      array(o["unset"]).forEach(strings);
    }
    if (kind === "membership") {
      const members = array(o["members"]);
      if (!(members.every((v) => typeof v === "string") || members.every((v, index) => v === index)) || new Set(members).size !== members.length) throw new TypeError("invalid dependency membership");
    }
    if (kind === "selection") {
      if (!["obstacle", "connector"].includes(string(d["predicate"]))) throw new TypeError("invalid selection predicate");
      selection(d, o);
    }
  }
  for (const item of array(data["diagnostics"])) {
    const d = keys(item, "code severity path stage target message related_paths");
    if (!["error", "warning"].includes(string(d["severity"]))) throw new TypeError("invalid diagnostic severity");
    string(d["code"]); string(d["message"]);
    for (const key of ["stage", "target"]) if (d[key] !== null) string(d[key]);
    strings(d["path"]); array(d["related_paths"]).forEach(strings);
  }
  for (const item of array(data["leaves"])) {
    const leaf = keys(item, "path state source schema_default");
    strings(leaf["path"]);
    if (!["present", "absent", "unset"].includes(string(leaf["state"])) || typeof leaf["schema_default"] !== "boolean") throw new TypeError("invalid leaf provenance");
    if (leaf["source"] !== null) {
      const source = keys(leaf["source"], "kind stage");
      if (!["auto", "code", "file"].includes(string(source["kind"]))) throw new TypeError("invalid leaf source");
      if (source["stage"] !== null) string(source["stage"]);
    }
  }
  for (const item of array(data["aliases"])) Object.values(keys(item, "address canonical")).forEach(address);
  for (const item of array(data["required_paths"])) {
    const p = keys(item, "source target path");
    address(p["source"]); address(p["target"]); array(p["path"]).forEach(address);
  }
  if (data["network"] !== null) {
    const network = keys(data["network"], "nodes edges clearance obstacles lineage adjacency weak_components strong_components graph");
    validateLayoutDefinition(network["clearance"], "nonnegative");
    for (const item of array(network["obstacles"])) {
      const obstacle = keys(item, "id owner vertices"); validateLayoutDefinition(obstacle["id"], "id");
      if (obstacle["owner"] !== null) validateLayoutDefinition(obstacle["owner"], "id");
      if (array(obstacle["vertices"]).length !== 4) throw new TypeError("obstacle requires four vertices");
      array(obstacle["vertices"]).forEach(point);
    }
    for (const item of array(network["nodes"])) {
      const node = keys(item, "address id position enabled capacity blockers");
      address(node["address"]); point(node["position"]); strings(node["blockers"]);
      if (node["capacity"] !== null && (typeof node["capacity"] !== "number" || !Number.isSafeInteger(node["capacity"]) || node["capacity"] < 1)) throw new TypeError("invalid node capacity");
    }
    for (const item of array(network["edges"])) {
      const edge = keys(item, "address source target enabled direction speed_limit lineage blockers");
      for (const name of ["address", "source", "target"]) address(edge[name]);
      array(edge["lineage"]).forEach(address); strings(edge["blockers"]);
      if (edge["enabled"] !== null && typeof edge["enabled"] !== "boolean") throw new TypeError("invalid edge enabled state");
      if (edge["speed_limit"] !== null && (typeof edge["speed_limit"] !== "number" || edge["speed_limit"] <= 0)) throw new TypeError("invalid edge speed");
    }
    for (const item of array(network["lineage"])) {
      const lineage = keys(item, "address children"); address(lineage["address"]); array(lineage["children"]).forEach(address);
    }
    for (const item of array(network["adjacency"])) {
      const adjacency = keys(item, "address edges"); address(adjacency["address"]);
      for (const edge of array(adjacency["edges"])) {
        const pair = array(edge); if (pair.length !== 2) throw new TypeError("invalid adjacency pair"); pair.forEach(address);
      }
    }
    for (const key of ["weak_components", "strong_components"]) for (const group of array(network[key])) array(group).forEach(address);
    const graph = keys(network["graph"], "nodes arcs");
    for (const item of array(graph["nodes"])) {
      const node = keys(item, "id position capacity"); validateLayoutDefinition(node["id"], "id"); point(node["position"]); nullableDefinition(node["capacity"], "capacity");
    }
    for (const item of array(graph["arcs"])) {
      const arc = keys(item, "source target speed_limit"); validateLayoutDefinition(arc["source"], "id"); validateLayoutDefinition(arc["target"], "id"); nullableDefinition(arc["speed_limit"], "positive");
    }
  }
}
