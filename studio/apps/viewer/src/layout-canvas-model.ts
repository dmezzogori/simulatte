import type {Layer, Json} from './layout-history';
export interface XY {x: number; y: number;}
export interface CanvasTarget extends XY {
  id: string; name: string; type: 'placement' | 'vertex' | 'node' | 'blocked' | 'port'; kind: string;
  path: (string | number)[]; parent: Layer; inherited: Layer; named: boolean;
  width: number; depth: number; rotation: number;
}
export const asObject = (value: unknown): Layer => value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Layer : {};
function merge(base: Layer, patch: Layer): Layer {
  const result = {...base};
  for (const [key, value] of Object.entries(patch)) {
    if (asObject(value).$unset === true) delete result[key];
    else Object.defineProperty(result, key, {value: value !== null && typeof value === 'object' && !Array.isArray(value) ? merge(asObject(base[key]), value) : value, enumerable: true, configurable: true, writable: true});
  }
  return result;
}
/** Ghost geometry only: Python resolution remains authoritative for the physical graph. */
export function canvasTargets(layer: Layer, bundle: Layer): CanvasTarget[] {
  const resolved = merge(asObject(bundle.resolved), layer), targets = asObject(bundle.targets), result: CanvasTarget[] = [];
  const add = (path: (string | number)[], name: string, type: CanvasTarget['type'], parent: Layer, named = true, kind = type as string) => {
    const point = parent.position;
    if (!Array.isArray(point) || typeof point[0] !== 'number' || typeof point[1] !== 'number' || !Number.isFinite(point[0]) || !Number.isFinite(point[1])) return;
    const footprint = asObject(parent.footprint);
    const width = Number(parent.width ?? footprint.width ?? (type === 'placement' ? 1 : .3));
    const depth = Number(parent.depth ?? footprint.depth ?? (type === 'placement' ? 1 : .3));
    result.push({id: JSON.stringify(path), name, type, kind, path, parent, inherited: resolved, named, x: point[0], y: point[1],
      width: Number.isFinite(width) && width > 0 ? width : .3, depth: Number.isFinite(depth) && depth > 0 ? depth : .3,
      rotation: typeof parent.rotation === 'number' ? parent.rotation : 0});
  };
  for (const [id, value] of Object.entries(asObject(resolved.placements))) {
    const placement = asObject(value), target = asObject(targets[id]);
    add(['placements', id], id, 'placement', placement, target.explicitly_named === true, String(placement.kind ?? target.kind ?? 'entity'));
    // Ports use local coordinates; convert their offsets into world coordinates for handles.
    for (const [port, value] of Object.entries(asObject(placement.ports))) {
      const data = asObject(value), point = data.position, origin = placement.position;
      if (Array.isArray(point) && Array.isArray(origin)) {
        const radians = Number(placement.rotation ?? 0) * Math.PI / 180;
        const x = Number(point[0]), y = Number(point[1]);
        add(['placements', id, 'ports', port], `${id} / ${port}`, 'port', {...data, position: [Number(origin[0]) + x * Math.cos(radians) - y * Math.sin(radians), Number(origin[1]) + x * Math.sin(radians) + y * Math.cos(radians), 0]}, target.explicitly_named === true);
      }
    }
  }
  const network = asObject(resolved.network);
  for (const [lane, value] of Object.entries(asObject(network.lanes))) {
    const vertices = asObject(value).vertices;
    if (Array.isArray(vertices)) vertices.forEach((value, index) => {
      const vertex = asObject(value);
      add(['network', 'lanes', lane, 'vertices', index], `${lane} / ${String(vertex.id)}`, 'vertex', vertex);
    });
  }
  if (Array.isArray(network.nodes)) network.nodes.forEach((value, index) => {const node = asObject(value); add(['network', 'nodes', index], String(node.id), 'node', node);});
  for (const [id, value] of Object.entries(asObject(network.blocked))) add(['network', 'blocked', id], id, 'blocked', asObject(value));
  return result;
}
function parentFor(layer: Layer, target: CanvasTarget): Layer {
  let current: Json = layer, inherited: Json = target.inherited;
  for (const [index, part] of target.path.entries()) {
    const key = String(part);
    const source: Json | undefined = Array.isArray(inherited) ? inherited[Number(part)] : asObject(inherited)[key];
    const container = current as Layer;
    if (!Object.hasOwn(container, key)) {
      // Lists replace wholly in SP2. Copy the inherited list before editing one vertex.
      const next = Array.isArray(source) ? structuredClone(source) : {};
      Object.defineProperty(container, key, {value: next, enumerable: true, writable: true, configurable: true});
    }
    current = container[key]!; inherited = source ?? {};
    if (current === null || typeof current !== 'object') throw new Error('Canvas target changed; review the layout');
    if (index === 0 && part === 'network' && !Object.hasOwn(current, 'type') && asObject(inherited).type)
      (current as Layer).type = asObject(inherited).type!;
  }
  const parent = current as Layer;
  if (target.type === 'placement' && !Object.hasOwn(parent, 'kind')) parent.kind = target.kind;
  if (target.type === 'port') {
    const entity = asObject(asObject(layer.placements)[String(target.path[1])]);
    if (!Object.hasOwn(entity, 'kind')) entity.kind = asObject(asObject(target.inherited.placements)[String(target.path[1])]).kind!;
  }
  return parent;
}
export function moveTargets(layer: Layer, targets: CanvasTarget[], delta: XY): Layer {
  if (![delta.x, delta.y].every(Number.isFinite)) throw new Error('Movement must be finite');
  const next = structuredClone(layer);
  for (const target of targets) {
    let x = target.x + delta.x, y = target.y + delta.y;
    if (target.type === 'port') {
      const placement = asObject(asObject(target.inherited.placements)[String(target.path[1])]);
      const origin = placement.position as number[], radians = Number(placement.rotation ?? 0) * Math.PI / 180;
      const movingParent = targets.some(other => other.type === 'placement' && other.path[1] === target.path[1]);
      const dx = x - origin[0]! - (movingParent ? delta.x : 0), dy = y - origin[1]! - (movingParent ? delta.y : 0);
      x = dx * Math.cos(radians) + dy * Math.sin(radians); y = -dx * Math.sin(radians) + dy * Math.cos(radians);
    }
    if (![x, y].every(Number.isFinite)) throw new Error('Movement exceeds finite coordinates');
    parentFor(next, target).position = [x, y, 0];
  }
  return next;
}
export function rotateTargets(layer: Layer, targets: CanvasTarget[], degrees: number): Layer {
  if (!Number.isFinite(degrees)) throw new Error('Rotation must be finite');
  if (targets.some(target => !['placement', 'blocked'].includes(target.type))) throw new Error('Rotate placements or blocked rectangles');
  const next = structuredClone(layer);
  for (const target of targets) parentFor(next, target).rotation = ((target.rotation % 360 + degrees % 360) % 360 + 360) % 360;
  return next;
}
export function snapDelta(delta: XY, anchor: XY, grid: Layer): XY {
  const origin = Array.isArray(grid.origin) ? grid.origin : [0, 0];
  const axis = (value: number, base: number, spacing: Json | undefined, start: Json | undefined) => {
    const step = typeof spacing === 'number' && spacing > 0 ? spacing : 1, offset = typeof start === 'number' ? start : 0;
    return Math.round((base + value - offset) / step) * step + offset - base;
  };
  return {x: axis(delta.x, anchor.x, grid.dx, origin[0]), y: axis(delta.y, anchor.y, grid.dy, origin[1])};
}
