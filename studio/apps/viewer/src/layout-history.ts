import type {Revision} from './studio-client';
export type Json = null | boolean | number | string | Json[] | {[key: string]: Json};
export type Layer = {[key: string]: Json};
interface Patch {path: string[]; before: Json | undefined; after: Json | undefined;}
interface Base {layer: Layer; revision: Revision;}
const record = (value: Json | undefined): value is Layer => value !== undefined && value !== null && typeof value === 'object' && !Array.isArray(value);
function equal(a: Json | undefined, b: Json | undefined): boolean {
  if (a === b) return true;
  if (Array.isArray(a) && Array.isArray(b)) return a.length === b.length && a.every((value, index) => equal(value, b[index]));
  if (record(a) && record(b)) return Object.keys(a).length === Object.keys(b).length && Object.keys(a).every(key => Object.hasOwn(b, key) && equal(a[key], b[key]));
  return false;
}
function diff(before: Layer, after: Layer, path: string[] = []): Patch[] {
  const patches: Patch[] = [];
  for (const key of new Set([...Object.keys(before), ...Object.keys(after)])) {
    const a = Object.hasOwn(before, key) ? before[key] : undefined, b = Object.hasOwn(after, key) ? after[key] : undefined;
    if (record(a) && record(b)) patches.push(...diff(a, b, [...path, key]));
    else if (!equal(a, b)) patches.push({path: [...path, key], before: structuredClone(a), after: structuredClone(b)});
  }
  return patches;
}
function apply(layer: Layer, patches: Patch[]): Layer {
  const result = structuredClone(layer);
  for (const patch of patches) {
    let parent = result;
    for (const part of patch.path.slice(0, -1)) {
      const child = Object.hasOwn(parent, part) ? parent[part] : undefined;
      if (!record(child)) throw new Error(`Layout changed at ${patch.path.join('.')}; review the edit`);
      parent = child;
    }
    const key = patch.path.at(-1)!;
    const current = Object.hasOwn(parent, key) ? parent[key] : undefined;
    if (!equal(current, patch.before)) throw new Error(`Layout changed at ${patch.path.join('.')}; review the edit`);
    if (patch.after === undefined) delete parent[key];
    else Object.defineProperty(parent, key, {value: structuredClone(patch.after), enumerable: true, configurable: true, writable: true});
  }
  return result;
}
const inverse = (patches: Patch[]): Patch[] => patches.map(patch => ({path: [...patch.path], before: patch.after, after: patch.before}));
function size(value: unknown): number {
  return new TextEncoder().encode(JSON.stringify(value, (_, child: unknown) => {
    if (typeof child === 'number' && !Number.isFinite(child)) throw new Error('Layout numbers must be finite');
    return child;
  })).length;
}
/** History contains guarded inverse patches, never old whole-file restore commands. */
export class LayoutDraft {
  #base: Base;
  #next: Base | null = null;
  #layer: Layer;
  #snapshotSource: Layer | null = null;
  #snapshot: Layer | null = null;
  #undo: Patch[][] = [];
  #redo: Patch[][] = [];
  #operation: 'edit' | 'undo' | 'redo' = 'edit';
  constructor(layer: Layer, revision: Revision, private readonly limit = {entries: 64, bytes: 32 << 20}) {
    if (!Number.isSafeInteger(limit.entries) || limit.entries < 1 || !Number.isSafeInteger(limit.bytes) || limit.bytes < 1) throw new Error('Invalid history limits');
    this.check(layer); this.#base = structuredClone({layer, revision}); this.#layer = structuredClone(layer);
  }
  private check(layer: Layer): void {if (size(layer) > 16 << 20) throw new Error('Layout exceeds 16 MiB');}
  /** Stable immutable identity prevents unrelated live notifications cancelling gestures. */
  get snapshot(): Layer {
    if (this.#snapshotSource !== this.#layer) {
      const freeze = (value: Json): Json => {
        if (value && typeof value === 'object') {for (const child of Object.values(value)) freeze(child); Object.freeze(value);}
        return value;
      };
      this.#snapshot = freeze(structuredClone(this.#layer)) as Layer;
      this.#snapshotSource = this.#layer;
    }
    return this.#snapshot!;
  }
  get layer(): Layer {return structuredClone(this.#layer);}
  get revision(): Revision {return {...this.#base.revision};}
  get conflict(): boolean {return this.#next !== null;}
  get dirty(): boolean {return !equal(this.#base.layer, this.#layer);}
  get canUndo(): boolean {return !this.dirty && !this.conflict && this.#undo.length > 0;}
  get canRedo(): boolean {return !this.dirty && !this.conflict && this.#redo.length > 0;}
  get historySize(): number {return this.#undo.length + this.#redo.length;}
  edit(layer: Layer): void {
    this.check(layer);
    if (this.#operation !== 'edit') throw new Error('Finish or discard pending history action first');
    this.#layer = structuredClone(layer);
  }
  discard(): void {this.#layer = structuredClone(this.#base.layer); this.#operation = 'edit';}
  observe(layer: Layer, revision: Revision): void {
    if (revision.number <= this.revision.number) return;
    this.check(layer);
    const next = structuredClone({layer, revision});
    if (this.dirty) this.#next = next;
    else {this.#base = next; this.#layer = structuredClone(layer); this.#next = null;}
  }
  rebase(mode: 'keep' | 'reload'): void {
    if (!this.#next) return;
    const next = this.#next;
    const layer = mode === 'keep' ? apply(next.layer, diff(this.#base.layer, this.#layer)) : structuredClone(next.layer);
    this.check(layer);
    this.#base = next; this.#layer = layer; this.#next = null;
    if (mode === 'reload') this.#operation = 'edit';
  }
  proposal(): Base {
    if (this.conflict) throw new Error('Review the newer revision before writing');
    return {layer: this.layer, revision: this.revision};
  }
  undo(): void {
    if (!this.canUndo) throw new Error('No undo available; finish the current edit');
    this.#layer = apply(this.#base.layer, inverse(this.#undo.at(-1)!)); this.#operation = 'undo';
  }
  redo(): void {
    if (!this.canRedo) throw new Error('No redo available; finish the current edit');
    this.#layer = apply(this.#base.layer, this.#redo.at(-1)!); this.#operation = 'redo';
  }
  accept(revision: Revision): void {
    if (revision.number <= this.revision.number) throw new Error('Accepted revision must advance');
    const patches = diff(this.#base.layer, this.#layer);
    if (this.#operation === 'undo') this.#redo.push(this.#undo.pop()!);
    else if (this.#operation === 'redo') this.#undo.push(this.#redo.pop()!);
    else if (patches.length) {this.#undo.push(patches); this.#redo = [];}
    while (this.historySize > this.limit.entries || size([this.#undo, this.#redo]) > this.limit.bytes) {
      if (this.#undo.length) this.#undo.shift(); else this.#redo.shift();
    }
    this.#base = {layer: this.layer, revision: {...revision}}; this.#operation = 'edit';
    if (this.#next && this.#next.revision.number <= revision.number) this.#next = null;
  }
}
