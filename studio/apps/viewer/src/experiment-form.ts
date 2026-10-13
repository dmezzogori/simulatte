/** The Studio experiment form and its `experiment-v1` definition (design 4.1).
 * Constraints come from the published JSON schema; the server validates the full
 * definition against the prepared model when `experiment.define` saves it. */
import schema from '../../../../src/simulatte/experiments/schema/experiment-v1.schema.json';
import {parseExecutionOptions, parseField, type ParameterField, type Scalar} from './parameter-draft';
import type {StudioClient} from './studio-client';

export interface ExperimentForm {
  path: string; name: string; model: string;
  count: string; base_seed: string;
  mode: 'grid' | 'explicit';
  /** Comma-separated values per parameter (each trimmed); an empty entry keeps the model default. */
  grid: Record<string, string>;
  explicit: {label: string; values: Record<string, string>}[];
  /** One `label=file` per line. */
  layouts: string;
  /** `null` uses the model's declared stopping policy. */
  stopping: Record<string, string> | null;
  level: 'kpi' | 'full';
  /** One KPI key per line; none records all. */
  kpis: string;
  workers: string[];
}
export interface ExperimentDefinition {
  schema: 1; name: string; model: string;
  replications: {count: number; base_seed: number | string};
  configurations: {grid: Record<string, Scalar[]>} | {explicit: ({label?: string; parameters: Record<string, Scalar>})[]};
  layouts: {label: string; file: string}[];
  stopping: Record<string, string | number | null> | null;
  level: 'kpi' | 'full'; kpis: string[]; workers: string[];
}

const properties = schema.properties;
const replications = properties.replications.properties;
// The JSON import types `oneOf` branches as a union; name the branches this form reads.
type TextRule = {minLength?: number; maxLength?: number; pattern?: string};
const [seedNumber, seedText] = replications.base_seed.oneOf as unknown as [{maximum: number}, TextRule];
const [gridRule, explicitRule] = properties.configurations.oneOf as unknown as [
  {properties: {grid: {maxProperties: number}}},
  {properties: {explicit: {maxItems: number; items: {properties: {label: TextRule}}}}},
];
const explicitItem = explicitRule.properties.explicit;
const nameRule = schema.$defs.name;
const identifier = new RegExp(schema.$defs.identifier.pattern, 'u');
const MAX_CONFIGURATIONS = 10_000, MAX_REPLICATIONS = 1_000_000;

export function emptyForm(model: string): ExperimentForm {
  return {path: '', name: '', model, count: '10', base_seed: '0', mode: 'grid', grid: {}, explicit: [{label: '', values: {}}],
    layouts: '', stopping: null, level: 'kpi', kpis: '', workers: []};
}
const message = (error: unknown) => error instanceof Error ? error.message : String(error);
const unique = (values: readonly unknown[]) => new Set(values.map(value => JSON.stringify(value))).size === values.length;
function text(value: string, rule: TextRule, what: string): string {
  const length = Array.from(value).length;
  if (length < (rule.minLength ?? 0) || length > (rule.maxLength ?? Infinity) || /[\u0000-\u001f]/.test(value) ||
      (rule.pattern !== undefined && !new RegExp(rule.pattern, 'u').test(value))) throw new Error(`Invalid ${what}`);
  return value;
}
function seed(raw: string, count: number): number | string {
  const value = raw.trim() || '0';
  if (!/^(0|[1-9][0-9]*)$/.test(value) || value.length > 19) throw new Error('Base seed must be a nonnegative integer');
  const big = BigInt(value);
  if (big + BigInt(Math.max(count, 1)) - 1n >= 2n ** 63n) throw new Error('Replication seeds must stay below 2**63');
  if (big <= BigInt(seedNumber.maximum)) return Number(value);
  return text(value, seedText, 'base seed');
}
function values(field: ParameterField, raw: string): Scalar[] {
  const parsed = raw.split(',').map(item => {
    try {return parseField(field, item.trim());} catch (error) {throw new Error(`${item.trim() || '(empty)'}: ${message(error)}`);}
  });
  if (!unique(parsed)) throw new Error('A value repeats');
  return parsed;
}

/** The canonical definition (every key, defaults explicit), or the issues keyed by form field. */
export function buildDefinition(form: ExperimentForm, fields: readonly ParameterField[]): {definition: ExperimentDefinition | null; issues: Record<string, string>} {
  const issues: Record<string, string> = {};
  const check = <T>(key: string, compute: () => T): T | undefined => {
    try {return compute();} catch (error) {issues[key] = message(error); return undefined;}
  };
  check('path', () => {
    const parts = form.path.split('/');
    if (!form.path.endsWith('.experiment.json') || form.path.startsWith('/') || form.path.includes('\\') ||
        parts.some(part => !part || part === '..' || part.startsWith('.')))
      throw new Error('Use a project-relative path ending in .experiment.json');
  });
  const name = check('name', () => text(form.name, properties.name, 'name: use 1 to 256 characters'));
  const model = check('model', () => text(form.model, properties.model, 'model reference: use file.py:function'));
  const count = check('replications.count', () => {
    const value = Number(form.count);
    if (!/^[1-9][0-9]*$/.test(form.count) || value < replications.count.minimum || value > replications.count.maximum)
      throw new Error(`Replications must be an integer from ${replications.count.minimum} to ${replications.count.maximum}`);
    return value;
  });
  const base_seed = check('replications.base_seed', () => seed(form.base_seed, count ?? 1));
  let configurations: ExperimentDefinition['configurations'] | undefined, size = 1;
  if (form.mode === 'grid') {
    const grid: Record<string, Scalar[]> = {};
    for (const field of fields) {
      const raw = form.grid[field.name] ?? '';
      check(`grid.${field.name}`, () => {
        if (raw.trim() === '') {
          if (field.required && field.default === null) throw new Error('Required parameter needs at least one value');
          return;
        }
        grid[field.name] = values(field, raw);
        size *= grid[field.name]!.length;
      });
    }
    if (Object.keys(grid).length > gridRule.properties.grid.maxProperties) issues.configurations = 'At most 64 grid parameters';
    configurations = {grid};
  } else {
    const explicit: {label?: string; parameters: Record<string, Scalar>}[] = [];
    form.explicit.forEach((row, index) => check(`explicit.${index}`, () => {
      const parameters: Record<string, Scalar> = {};
      for (const field of fields) {
        const raw = (row.values[field.name] ?? '').trim();
        if (raw === '') {
          if (field.required && field.default === null) throw new Error(`${field.name}: Required parameter needs a value`);
          continue;
        }
        try {parameters[field.name] = parseField(field, raw);} catch (error) {throw new Error(`${field.name}: ${message(error)}`);}
      }
      const label = row.label.trim();
      explicit.push(label ? {label: text(label, explicitItem.items.properties.label, 'label (no "/")'), parameters} : {parameters});
    }));
    if (!explicit.length || explicit.length > explicitItem.maxItems) issues.configurations = 'List 1 to 10000 configurations';
    const labels = explicit.flatMap(item => item.label === undefined ? [] : [item.label]);
    if (!unique(labels)) issues.configurations = 'Configuration labels must be unique';
    size = explicit.length;
    configurations = {explicit};
  }
  const layouts = check('layouts', () => {
    const listed = form.layouts.split('\n').filter(line => line.trim()).map(line => {
      const separator = line.indexOf('=');
      if (separator < 0) throw new Error('Write each layout variant as label=file');
      return {label: text(line.slice(0, separator).trim(), nameRule, `layout label "${line.slice(0, separator).trim()}"`),
        file: text(line.slice(separator + 1).trim(), properties.layouts.items.properties.file, 'layout file')};
    });
    if (!unique(listed.map(item => item.label)) || !unique(listed.map(item => item.file))) throw new Error('Layout labels and files must be unique');
    return listed;
  });
  const stopping = check('stopping', () => form.stopping === null ? null
    : parseExecutionOptions({seed: '0', level: 'kpi', kpis: [], stopping: form.stopping}).stopping);
  if (!(properties.level.enum as readonly string[]).includes(form.level)) issues.level = 'Choose kpi or full';
  const kpis = check('kpis', () => {
    const keys = form.kpis.split('\n').map(line => line.trim()).filter(Boolean);
    if (!unique(keys)) throw new Error('KPI keys must be unique');
    return keys;
  });
  const workers = check('workers', () => {
    for (const worker of form.workers) text(worker, nameRule, `worker name "${worker}"`);
    if (!unique(form.workers)) throw new Error('Workers must be unique');
    return [...form.workers];
  });
  const total = size * Math.max(1, layouts?.length ?? 0);
  if (total > MAX_CONFIGURATIONS) issues.configurations = 'An experiment has at most 10000 configurations';
  else if (count !== undefined && total * count > MAX_REPLICATIONS) issues.configurations = 'An experiment has at most 1000000 replications';
  for (const key of Object.keys(issues)) if (!issues[key]) delete issues[key];
  if (Object.keys(issues).length || name === undefined || model === undefined || count === undefined || base_seed === undefined ||
      !configurations || !layouts || stopping === undefined || !kpis || !workers) return {definition: null, issues};
  return {definition: {schema: 1, name, model, replications: {count, base_seed}, configurations, layouts, stopping, level: form.level, kpis, workers}, issues};
}

/** Saves the definition through `experiment.define` only after client-side validation. */
export async function defineExperiment(client: Pick<StudioClient, 'command'>, form: ExperimentForm, fields: readonly ParameterField[], overwrite = false):
    Promise<{ok: true; path: string; experiment_id: string} | {ok: false; issues: Record<string, string>; error?: unknown}> {
  const {definition, issues} = buildDefinition(form, fields);
  if (!definition) return {ok: false, issues};
  const result = await client.command('experiment.define', {path: form.path, definition: definition as unknown as Record<string, unknown>, overwrite});
  if (!result.ok) return {ok: false, issues: {}, error: result.error};
  return {ok: true, path: String(result.path), experiment_id: String(result.experiment_id)};
}
