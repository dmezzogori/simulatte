import {useEffect, useState} from 'react';
import canonicalSchema from '../../../../src/simulatte/layout/schema/layout-v1.schema.json';
import type {Json, Layer} from './layout-history';

export interface Rule {
  $ref?: string; type?: string; const?: Json; enum?: Json[]; default?: Json;
  anyOf?: Rule[]; oneOf?: Rule[]; properties?: Record<string, Rule>; required?: string[];
  additionalProperties?: Rule | boolean; items?: Rule | boolean; prefixItems?: Rule[];
  minItems?: number; maxItems?: number; minimum?: number; exclusiveMinimum?: number; minLength?: number;
}
export const layoutRule = canonicalSchema as Rule;
export function resolveRule(rule: Rule): Rule {
  if (!rule.$ref) return rule;
  const target = (canonicalSchema.$defs as Record<string, Rule>)[rule.$ref.slice(8)];
  if (!target) throw new Error('Unsupported layout field');
  const {$ref: _, ...rest} = rule;
  return {...resolveRule(target), ...rest};
}
export function initialValue(input: Rule): Json {
  const rule = resolveRule(input);
  if ('const' in rule) return structuredClone(rule.const!);
  if ('default' in rule) return structuredClone(rule.default!);
  if (rule.enum) return structuredClone(rule.enum[0]!);
  if (rule.oneOf || rule.anyOf) return initialValue((rule.oneOf ?? rule.anyOf)![0]!);
  if (rule.type === 'object') return Object.fromEntries((rule.required ?? []).map(key => [key, initialValue(rule.properties![key]!)]));
  if (rule.type === 'array') return Array.from({length: Math.max(rule.minItems ?? 0, rule.prefixItems?.length ?? 0)}, (_, i) => initialValue(rule.prefixItems?.[i] ?? (typeof rule.items === 'object' ? rule.items : {})));
  if (rule.type === 'boolean') return true;
  if (rule.type === 'integer' || rule.type === 'number') return Math.max(0, rule.minimum ?? 0, rule.exclusiveMinimum === undefined ? 0 : rule.exclusiveMinimum + 1);
  return rule.minLength ? `id-${crypto.randomUUID()}` : '';
}
const object = (value: Json): value is Layer => value !== null && typeof value === 'object' && !Array.isArray(value);
function branchName(input: Rule, index: number): string {
  const rule = resolveRule(input);
  if (input.$ref === '#/$defs/elementAddress') return 'Element address';
  if (input.$ref === '#/$defs/selector') return 'Selector';
  if (rule.anyOf && !rule.type) return 'Keep inherited network type';
  if (rule.type === 'object' && !rule.properties?.type && !rule.properties?.kind) {
    if (rule.properties?.lanes) return 'Inherited lanes';
    if (rule.properties?.nodes) return 'Inherited explicit network';
    if (rule.properties?.bounds && rule.properties?.neighbors) return 'Inherited lattice';
  }
  const discriminator = Object.entries(rule.properties ?? {}).find(([, value]) => 'const' in value);
  return discriminator ? discriminator[0] === '$unset' ? 'Remove optional value ($unset)' : String(discriminator[1].const) : rule.type ?? `variant ${index + 1}`;
}
function matches(input: Rule, value: Json): boolean {
  const rule = resolveRule(input);
  if (rule.oneOf || rule.anyOf) return (rule.oneOf ?? rule.anyOf)!.some(branch => matches(branch, value));
  if ('const' in rule) return value === rule.const;
  if (rule.enum) return rule.enum.includes(value);
  if (rule.type === 'object') return object(value) && (rule.additionalProperties !== false || Object.keys(value).every(key => Object.hasOwn(rule.properties ?? {}, key))) && Object.entries(rule.properties ?? {}).every(([key, child]) => !('const' in child) || value[key] === child.const);
  if (rule.type === 'array') return Array.isArray(value);
  if (rule.type === 'integer' || rule.type === 'number') return typeof value === 'number';
  return typeof value === rule.type;
}
interface Props {rule: Rule; value: Json; path: string; change(value: Json): void; invalid(path: string, invalid: boolean): void;}
function NumberField({value, path, change, invalid, integer}: {value: Json; path: string; change(value: Json): void; invalid(path: string, invalid: boolean): void; integer: boolean}) {
  const [raw, setRaw] = useState(String(value));
  useEffect(() => {setRaw(String(value)); invalid(path, false);}, [value, path]);
  useEffect(() => () => invalid(path, false), [path]);
  return <input aria-label={path} inputMode={integer ? 'numeric' : 'decimal'} value={raw} onChange={event => {
    const raw = event.target.value; setRaw(raw);
    const valid = (integer ? /^[+-]?[0-9]+$/ : /^[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?$/).test(raw) &&
      Number.isFinite(Number(raw)) && (!integer || Number.isSafeInteger(Number(raw)));
    invalid(path, !valid); if (valid) change(Number(raw) || 0);
  }} />;
}
function IdentifierField({path, value, change}: {path: string; value: string; change(value: string): void}) {
  const [raw, setRaw] = useState(value);
  useEffect(() => setRaw(value), [value]);
  return <label>{path}<input value={raw} onChange={event => setRaw(event.target.value)} onBlur={() => {if (raw !== value) change(raw);}} /></label>;
}
function Reassign({name, names, change}: {name: string; names: string[]; change(name: string): void}) {
  const [target, setTarget] = useState('');
  return <span><label>Reassign {name} to<input value={target} onChange={event => setTarget(event.target.value)} /></label>
    <button type="button" disabled={!target || names.includes(target)} onClick={() => {change(target); setTarget('');}}>Reassign {name}</button></span>;
}
/** Each SP2 schema field is reachable through typed controls; arrays replace wholly. */
export function SchemaField({rule: input, value, path, change, invalid}: Props) {
  const rule = resolveRule(input);
  const [name, setName] = useState('');
  const [page, setPage] = useState(0);
  const [variant, setVariant] = useState<number | null>(null);
  const branches = rule.oneOf ?? rule.anyOf;
  if (branches) {
    const selected = variant !== null && branches[variant] && matches(branches[variant]!, value) ? variant : Math.max(0, branches.findIndex(branch => matches(branch, value)));
    return <div className="layout-field"><label>{path} variant<select aria-label={`${path} variant`} value={selected} onChange={event => {const index = Number(event.target.value); setVariant(index); change(initialValue(branches[index]!));}}>
      {branches.map((branch, index) => <option key={index} value={index}>{branchName(branch, index)}</option>)}
    </select></label><SchemaField key={selected} rule={branches[selected]!} value={value} path={path} change={change} invalid={invalid} /></div>;
  }
  if ('const' in rule) return <span>{path}: {String(rule.const)}</span>;
  if (rule.enum || rule.type === 'boolean') {
    const choices = rule.enum ?? [true, false];
    return <label>{path}<select aria-label={path} value={choices.findIndex(choice => choice === value)} onChange={event => change(choices[Number(event.target.value)]!)}>
      {choices.map((choice, index) => <option key={index} value={index}>{String(choice)}</option>)}
    </select></label>;
  }
  if (rule.type === 'object') {
    const data = object(value) ? value : {};
    const properties = rule.properties ?? {};
    const keys = [...new Set([...Object.keys(properties), ...Object.keys(data)])];
    const pages = Math.max(1, Math.ceil(keys.length / 30)), current = Math.min(page, pages - 1);
    const replace = (key: string, value: Json | undefined) => {
      const next = {...data};
      if (value === undefined) delete next[key];
      else Object.defineProperty(next, key, {value, enumerable: true, configurable: true, writable: true});
      change(next);
    };
    return <fieldset className="layout-object"><legend>{path}</legend>
      {keys.slice(current * 30, (current + 1) * 30).map(key => {
        const child = Object.hasOwn(properties, key) ? properties[key]! : typeof rule.additionalProperties === 'object' ? rule.additionalProperties : {};
        const present = Object.hasOwn(data, key), required = rule.required?.includes(key);
        return <div key={key} className="layout-property">
          {present ? <><SchemaField rule={child} value={data[key]!} path={`${path}.${key}`} change={value => replace(key, value)} invalid={invalid} />
            {!required && <button type="button" onClick={() => replace(key, undefined)}>Reset {key}</button>}
            {!Object.hasOwn(properties, key) && <Reassign name={key} names={Object.keys(data)} change={target => {
              const next = {...data}; const entry = next[key]!; delete next[key];
              Object.defineProperty(next, target, {value: entry, enumerable: true, configurable: true, writable: true}); change(next);
            }} />}</> :
            <button type="button" onClick={() => replace(key, initialValue(child))}>Set {key}</button>}
        </div>;
      })}
      {pages > 1 && <div><button type="button" disabled={current === 0} onClick={() => setPage(current - 1)}>Previous fields</button>
        <span>{current + 1} / {pages}</span><button type="button" disabled={current + 1 === pages} onClick={() => setPage(current + 1)}>Next fields</button></div>}
      {typeof rule.additionalProperties === 'object' && <div><label>New {path} name<input value={name} onChange={event => setName(event.target.value)} /></label>
        <button type="button" disabled={!name || Object.hasOwn(data, name)} onClick={() => {replace(name, initialValue(rule.additionalProperties as Rule)); setName(''); setPage(Math.floor(keys.length / 30));}}>Add named entry</button></div>}
    </fieldset>;
  }
  if (rule.type === 'array') {
    const data = Array.isArray(value) ? value : [];
    const pages = Math.max(1, Math.ceil(data.length / 30)), current = Math.min(page, pages - 1);
    return <fieldset className="layout-array"><legend>{path}</legend>
      {data.slice(current * 30, (current + 1) * 30).map((value, offset) => {
        const index = current * 30 + offset, child = rule.prefixItems?.[index] ?? (typeof rule.items === 'object' ? rule.items : {});
        return <div key={object(value) && typeof value.id === 'string' ? value.id : index}>
          <SchemaField rule={child} value={value} path={`${path}[${index}]`} invalid={invalid} change={value => {const next = [...data]; next[index] = value; change(next);}} />
          {!rule.prefixItems && <><button type="button" onClick={() => change(data.filter((_, i) => i !== index))}>Remove item {index + 1}</button>
            <button type="button" onClick={() => {const next = [...data]; next.splice(index + 1, 0, initialValue(child)); change(next);}}>Insert after {index + 1}</button></>}
        </div>;
      })}
      {pages > 1 && <div><button type="button" disabled={current === 0} onClick={() => setPage(current - 1)}>Previous items</button>
        <span>{current + 1} / {pages}</span><button type="button" disabled={current + 1 === pages} onClick={() => setPage(current + 1)}>Next items</button></div>}
      {rule.items !== false && <button type="button" disabled={data.length >= (rule.maxItems ?? Infinity)} onClick={() => {
        change([...data, initialValue(typeof rule.items === 'object' ? rule.items : {})]); setPage(Math.floor(data.length / 30));
      }}>Add item to {path}</button>}
    </fieldset>;
  }
  if (rule.type === 'number' || rule.type === 'integer') return <label>{path}<NumberField value={value} path={path} change={change} invalid={invalid} integer={rule.type === 'integer'} /></label>;
  if (path.endsWith('.id')) return <IdentifierField path={path} value={String(value)} change={change} />;
  return <label>{path}<input value={String(value)} onChange={event => change(event.target.value)} /></label>;
}
