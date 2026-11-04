import {describe, expect, it} from 'vitest';
import type {ExecutionProvenance} from '@simulatte/trace';
import {backendObservation, supportsFullReplay} from '../src/backend-observation';

function execution(backend: unknown): ExecutionProvenance {
  return {header: {}, footer: null, initial: {resolved: {capabilities: {execution_backend: backend}}}} as ExecutionProvenance;
}
const capabilities = {schema: 1, name: 'native-production', experimental: true};
const observation = {schema: 1, kind: 'scalar_snapshots', boundary: 'right_open', requested_interval: 1,
  effective_interval: 2, max_samples: 5, scheduled_samples: 5};

describe('experimental backend observations', () => {
  it('preserves legacy replay behavior while honoring backend trace capabilities', () => {
    expect(supportsFullReplay(null)).toBe(true);
    expect(supportsFullReplay(execution({...capabilities, trace_levels: ['kpi']}))).toBe(false);
    expect(supportsFullReplay(execution({...capabilities, trace_levels: ['kpi', 'full']}))).toBe(true);
  });
  it('labels native snapshots and makes coarsening visible', () => {
    expect(backendObservation(execution({...capabilities, observation})))
      .toBe('Native production · experimental · KPI snapshots every 2 time units · 5 samples per KPI (spacing increased to fit limit)');
    expect(backendObservation(execution({...capabilities, observation: {...observation, requested_interval: 2}})))
      .toBe('Native production · experimental · KPI snapshots every 2 time units · 5 samples per KPI');
  });
  it('describes final-only native recordings without inventing a live series', () => {
    expect(backendObservation(execution(capabilities))).toBe('Native production · experimental · final KPI scalars');
    expect(backendObservation(execution({schema: 1, name: 'other'}))).toBe('other · final KPI scalars');
  });
  it.each([null, [], {}, {schema: 2, name: 'native-production'}, {schema: 1, name: '<img src=x>'}])('ignores absent or malformed backend metadata %j', backend => {
    expect(backendObservation(execution(backend))).toBeNull();
    expect(backendObservation(null)).toBeNull();
  });
  it.each([{max_samples: 4097}, {scheduled_samples: 6}, {effective_interval: Infinity}, {requested_interval: 0}])('does not render malformed sampling metadata %j', change => {
    expect(backendObservation(execution({...capabilities, observation: {...observation, ...change}})))
      .toBe('Native production · experimental');
  });
});
