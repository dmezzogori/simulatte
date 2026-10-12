import {describe, expect, it} from 'vitest';
import {validateLayoutPatch} from '@simulatte/trace';
import {initialValue, layoutRule, resolveRule} from '../src/layout-schema-form';
describe('schema form defaults', () => {
  it('starts with an exact schema version and constructs valid coordinate and scalar controls', () => {
    expect(initialValue(layoutRule)).toEqual({schema_version: '1.0'});
    expect(initialValue({$ref: '#/$defs/point'})).toEqual([0, 0, 0]);
    expect(initialValue({$ref: '#/$defs/capacity'})).toBe(1);
    expect(initialValue({$ref: '#/$defs/positive'})).toBe(1);
    expect(initialValue({$ref: '#/$defs/unset'})).toEqual({$unset: true});
    expect(resolveRule({$ref: '#/$defs/capacity'}).minimum).toBe(1);
  });
  it('creates stable, unique vertex identities and exposes each network family', () => {
    const a = initialValue({$ref: '#/$defs/vertex'}), b = initialValue({$ref: '#/$defs/vertex'});
    expect(a).not.toEqual(b);
    const branches = resolveRule({$ref: '#/$defs/network'}).oneOf!;
    expect(branches.map(branch => branch.properties?.type?.const ?? 'inherited')).toEqual(['lattice', 'lanes', 'explicit', 'inherited']);
    for (const branch of branches) expect(() => validateLayoutPatch({schema_version: '1.0', network: initialValue(branch)})).not.toThrow();
  });
  it('constructs schema-valid placements, ports, selectors and overrides without code evaluation', () => {
    const placements = {machine: initialValue({$ref: '#/$defs/placement'})};
    const overrides = [initialValue({$ref: '#/$defs/override'})];
    expect(() => validateLayoutPatch({schema_version: '1.0', placements, overrides})).not.toThrow();
    expect(() => resolveRule({$ref: '#/$defs/unknown'})).toThrow();
  });
});
