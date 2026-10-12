import {describe, expect, it} from 'vitest';
import {ParameterDraft, parseField, parseExecutionOptions, type ParameterSchema} from '../src/parameter-draft';
const revision = {number: 1, digest: 'a'.repeat(64)};
const newer = {number: 2, digest: 'b'.repeat(64)};
const schema: ParameterSchema = {version: 1, fields: [
  {name: 'n', type: 'int', required: false, default: 2, choices: [], range: {min: 1, max: 9}, length: null, description: 'Count'},
  {name: 'label', type: 'str', required: true, default: null, choices: [], range: null, length: {min: 0, max: 3}, description: ''},
]};

describe('parameter drafts', () => {
  it('keeps missing required values distinct from an explicitly empty string', () => {
    const draft = new ParameterDraft(schema, {n: 2}, revision);
    expect(draft.validate().issues).toHaveProperty('label');
    draft.set('label', '');
    expect(draft.validate()).toEqual({values: {n: 2, label: ''}, issues: {}});
  });
  it('uses exact numeric syntax, safe integers, Unicode lengths and inclusive constraints', () => {
    const field = schema.fields[0]!;
    expect(parseField(field, '+004')).toBe(4);
    for (const raw of ['4.0', '1e0', ' 4', '0x4', 'true', '10', '0', '９']) expect(() => parseField(field, raw)).toThrow();
    expect(parseField(schema.fields[1]!, '😀ab')).toBe('😀ab');
    expect(() => parseField(schema.fields[1]!, '😀abc')).toThrow(/length/);
    expect(parseField({...field, type: 'float', range: null}, '1e20')).toBe(1e20);
    for (const raw of ['', ' ', 'NaN', 'Infinity', '1_000', '0x10']) expect(() => parseField({...field, type: 'float'}, raw)).toThrow();
    expect(() => parseField({...field, range: null}, '9007199254740992')).toThrow(/safe/);
  });
  it('validates booleans, enums, choices and strings without implicit coercion', () => {
    const base = {...schema.fields[0]!, range: null};
    expect(parseField({...base, type: 'bool'}, 'false')).toBe(false);
    expect(() => parseField({...base, type: 'bool'}, '0')).toThrow();
    expect(parseField({...base, type: 'enum', choices: ['FAST', 'SLOW']}, 'FAST')).toBe('FAST');
    expect(() => parseField({...base, type: 'enum', choices: ['FAST']}, 'fast')).toThrow(/choice/);
    expect(() => parseField({...base, choices: [2, 4]}, '3')).toThrow(/choice/);
    expect(() => parseField({...base, type: 'str'}, 'x'.repeat(65537))).toThrow(/64 KiB/);
  });
  it('preserves dirty values and their old base until an explicit rebase', () => {
    const draft = new ParameterDraft(schema, {n: 2, label: 'old'}, revision);
    draft.set('n', '4');
    draft.observe(schema, {n: 3, label: 'new'}, newer);
    expect(draft.revision).toEqual(revision); expect(draft.conflict).toBe(true);
    expect(draft.validate().values.n).toBe(4);
    draft.rebase('keep');
    expect(draft.revision).toEqual(newer);
    expect(draft.validate().values).toEqual({n: 4, label: 'new'});
    expect(draft.dirty).toBe(true);
  });
  it('flags removed and incompatible dirty fields until reviewed', () => {
    const draft = new ParameterDraft(schema, {n: 2, label: 'old'}, revision);
    draft.set('n', '4'); draft.set('label', 'abc');
    const changed: ParameterSchema = {version: 1, fields: [{...schema.fields[0]!, type: 'bool', range: null, default: true}]};
    draft.observe(changed, {n: true}, newer); draft.rebase('keep');
    expect(draft.validate().issues).toHaveProperty('n');
    expect(draft.validate().issues).toHaveProperty('label');
    draft.discard('label'); draft.set('n', 'true');
    expect(draft.validate().issues).toEqual({});
  });
  it('loads authoritative values when clean and clears drafts only after accepted Apply', () => {
    const draft = new ParameterDraft(schema, {n: 2, label: 'a'}, revision);
    draft.observe(schema, {n: 3, label: 'b'}, newer);
    expect(draft.revision).toEqual(newer); expect(draft.conflict).toBe(false);
    draft.set('n', '+004'); draft.accept({...newer, number: 3});
    expect(draft.raw('n')).toBe('4'); expect(draft.dirty).toBe(false);
    draft.set('n', '5'); draft.observe(schema, {n: 6, label: 'c'}, {...newer, number: 4});
    draft.rebase('reload'); expect(draft.validate().values.n).toBe(6); expect(draft.dirty).toBe(false);
  });
});

describe('execution controls', () => {
  const options = {seed: '9223372036854775807', level: 'full', kpis: [], stopping: {type: 'horizon', horizon: '10', warmup: '2'}};
  it('preserves all seed bits and validates stopping boundaries', () => {
    expect(parseExecutionOptions(options)).toEqual({seed: options.seed, level: 'full', kpis: [], stopping: {type: 'horizon', horizon: 10, warmup: 2}});
    for (const seed of ['-1', '01', '1e3', '9223372036854775808']) expect(() => parseExecutionOptions({...options, seed})).toThrow(/seed/i);
    expect(() => parseExecutionOptions({...options, stopping: {type: 'horizon', horizon: '2', warmup: '2'}})).toThrow();
  });
  it('distinguishes finite arrival counts and cutoffs, including zero arrivals', () => {
    expect(parseExecutionOptions({...options, stopping: {type: 'finite_population', arrivals: '0', cutoff: '', safety_limit: '20'}}).stopping)
      .toEqual({type: 'finite_population', arrivals: 0, cutoff: null, safety_limit: 20});
    expect(() => parseExecutionOptions({...options, stopping: {type: 'finite_population', arrivals: '1', cutoff: '2', safety_limit: '20'}})).toThrow();
    expect(() => parseExecutionOptions({...options, stopping: {type: 'finite_population', arrivals: '', cutoff: '', safety_limit: '20'}})).toThrow();
    expect(() => parseExecutionOptions({...options, stopping: {type: 'finite_population', arrivals: '', cutoff: '20', safety_limit: '10'}})).toThrow();
  });
});
