import type {Revision} from './studio-client';

export type Scalar = string | number | boolean;
export interface ParameterField {
  name: string; type: 'int' | 'float' | 'bool' | 'str' | 'enum'; required: boolean;
  default: Scalar | null; choices: Scalar[]; range: {min: number | null; max: number | null} | null;
  length: {min: number; max: number | null} | null; description: string;
}
export interface ParameterSchema {version: 1; fields: ParameterField[];}
const finite = (raw: string): number => {
  if (!/^[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?$/.test(raw) || !Number.isFinite(Number(raw)))
    throw new Error('Enter a finite number');
  return Number(raw) || 0;
};
const integer = (raw: string): number => {
  if (!/^[+-]?[0-9]+$/.test(raw)) throw new Error('Enter an integer');
  const value = Number(raw);
  if (!Number.isSafeInteger(value)) throw new Error('Enter a safe integer');
  return value || 0;
};
export function parseField(field: ParameterField, raw: string): Scalar {
  let value: Scalar;
  switch (field.type) {
    case 'int': value = integer(raw); break;
    case 'float': value = finite(raw); break;
    case 'bool':
      if (raw !== 'true' && raw !== 'false') throw new Error('Choose true or false');
      value = raw === 'true'; break;
    default: value = raw;
  }
  if (typeof value === 'string') {
    if (Array.from(value).some(char => char.length === 1 && char.charCodeAt(0) >= 0xd800 && char.charCodeAt(0) <= 0xdfff)) throw new Error('Enter valid Unicode text');
    if (new TextEncoder().encode(value).length > 65536) throw new Error('Text exceeds 64 KiB');
    const length = Array.from(value).length;
    if (field.length && (length < field.length.min || field.length.max !== null && length > field.length.max))
      throw new Error('Text length is outside the allowed range');
  }
  if (typeof value === 'number' && field.range &&
      (field.range.min !== null && value < field.range.min || field.range.max !== null && value > field.range.max))
    throw new Error('Value is outside the allowed range');
  if ((field.type === 'enum' || field.choices.length > 0) && !field.choices.includes(value)) throw new Error('Choose an allowed choice');
  return value;
}
interface Base {schema: ParameterSchema; values: Record<string, Scalar>; revision: Revision;}
/** Unsaved edits keep their original optimistic-concurrency base until reviewed. */
export class ParameterDraft {
  #base: Base;
  #next: Base | null = null;
  #edits = new Map<string, string>();
  constructor(schema: ParameterSchema, values: Record<string, Scalar>, revision: Revision) {
    this.#base = structuredClone({schema, values, revision});
  }
  get schema(): ParameterSchema {return this.#base.schema;}
  get revision(): Revision {return this.#base.revision;}
  get conflict(): boolean {return this.#next !== null;}
  get dirty(): boolean {return this.#edits.size > 0;}
  get removed(): string[] {return [...this.#edits.keys()].filter(name => !this.schema.fields.some(field => field.name === name));}
  raw(name: string): string | undefined {
    if (this.#edits.has(name)) return this.#edits.get(name);
    if (Object.hasOwn(this.#base.values, name)) return String(this.#base.values[name]);
    const field = this.schema.fields.find(field => field.name === name);
    return field && !field.required && field.default !== null ? String(field.default) : undefined;
  }
  set(name: string, raw: string): void {this.#edits.set(name, raw);}
  discard(name: string): void {this.#edits.delete(name);}
  observe(schema: ParameterSchema, values: Record<string, Scalar>, revision: Revision, preserve = false): void {
    if (revision.number < this.revision.number || revision.number === this.revision.number && revision.digest === this.revision.digest) return;
    const next = structuredClone({schema, values, revision});
    if (this.dirty || preserve) this.#next = next;
    else {this.#base = next; this.#next = null;}
  }
  rebase(mode: 'keep' | 'reload'): void {
    if (this.#next) this.#base = this.#next;
    this.#next = null;
    if (mode === 'reload') this.#edits.clear();
  }
  validate(): {values: Record<string, Scalar>; issues: Record<string, string>} {
    const values: Record<string, Scalar> = Object.create(null), issues: Record<string, string> = Object.create(null);
    for (const field of this.schema.fields) {
      const raw = this.raw(field.name);
      try {
        if (raw === undefined) throw new Error('Required parameter missing');
        values[field.name] = parseField(field, raw);
      } catch (error) {issues[field.name] = error instanceof Error ? error.message : String(error);}
    }
    for (const name of this.removed) issues[name] = 'Parameter was removed; discard this edit';
    return {values, issues};
  }
  accept(revision: Revision): void {
    const {values, issues} = this.validate();
    if (Object.keys(issues).length) throw new Error('Cannot accept invalid parameters');
    // A concurrent update still needs review; acknowledging this Apply cannot erase it.
    this.#base = structuredClone({schema: this.schema, values, revision});
    this.#edits.clear();
    if (this.#next && this.#next.revision.number <= revision.number) this.#next = null;
  }
}
export interface ExecutionControls {seed: string; level: string; kpis: string[]; stopping: Record<string, string>;}
export function parseExecutionOptions(options: ExecutionControls) {
  if (!/^(0|[1-9][0-9]*)$/.test(options.seed) || options.seed.length > 19 || BigInt(options.seed) >= 2n ** 63n)
    throw new Error('Seed must be an integer between 0 and 9223372036854775807');
  if (options.level !== 'full' && options.level !== 'kpi') throw new Error('Choose a trace level');
  if (new Set(options.kpis).size !== options.kpis.length || options.kpis.some(name => !name)) throw new Error('Choose unique KPI names');
  const stop = options.stopping;
  let stopping: Record<string, string | number | null>;
  if (stop.type === 'horizon') {
    const horizon = finite(stop.horizon ?? ''), warmup = finite(stop.warmup ?? '');
    if (!(0 <= warmup && warmup < horizon)) throw new Error('Require horizon > warmup >= 0');
    stopping = {type: 'horizon', horizon, warmup};
  } else if (stop.type === 'finite_population') {
    const arrivals = stop.arrivals ? integer(stop.arrivals) : null, cutoff = stop.cutoff ? finite(stop.cutoff) : null;
    const safety_limit = finite(stop.safety_limit ?? '');
    if ((arrivals === null) === (cutoff === null)) throw new Error('Specify exactly one of arrivals or cutoff');
    if (safety_limit <= 0 || arrivals !== null && arrivals < 0 || cutoff !== null && !(0 <= cutoff && cutoff < safety_limit))
      throw new Error('Require positive safety limit, nonnegative arrivals and safety limit > cutoff >= 0');
    stopping = {type: 'finite_population', arrivals, cutoff, safety_limit};
  } else throw new Error('Choose a stopping policy');
  return {seed: options.seed, level: options.level, kpis: [...options.kpis], stopping};
}
