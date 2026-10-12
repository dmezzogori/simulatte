import {describe, expect, it} from 'vitest';
import {LayoutDraft} from '../src/layout-history';
const revision = (number: number) => ({number, digest: String(number).repeat(64)});
const layer = {schema_version: '1.0', placements: {machine: {position: [1, 2], visual: {label: 'old'}}}};
describe('layout patch history', () => {
  it('groups a gesture into one reversible patch and preserves unrelated newer fields', () => {
    const draft = new LayoutDraft(layer, revision(1));
    draft.edit({...layer, placements: {machine: {position: [3, 4], visual: {label: 'old'}}}});
    const first = draft.proposal();
    expect(first.revision).toEqual(revision(1));
    draft.accept(revision(2));
    draft.observe({...first.layer, grid: {size: 5}}, revision(3));
    draft.undo();
    expect(draft.proposal()).toEqual({revision: revision(3), layer: {...layer, grid: {size: 5}}});
    draft.accept(revision(4));
    draft.redo();
    expect(draft.proposal().layer).toEqual({...first.layer, grid: {size: 5}});
    expect(draft.proposal().revision).toEqual(revision(4));
  });
  it('preserves unsaved drafts on conflicts and replays only reviewed non-conflicting edits', () => {
    const draft = new LayoutDraft(layer, revision(1));
    draft.edit({...layer, network: {mode: 'explicit'}});
    draft.observe({...layer, grid: {size: 2}}, revision(2));
    expect(draft.conflict).toBe(true);
    expect(() => draft.proposal()).toThrow(/revision/);
    draft.rebase('keep');
    expect(draft.proposal().layer).toEqual({...layer, grid: {size: 2}, network: {mode: 'explicit'}});
  });
  it('refuses conflicting undo and removed parents without overwriting new values', () => {
    const draft = new LayoutDraft(layer, revision(1));
    draft.edit({...layer, placements: {machine: {position: [3, 4], visual: {label: 'old'}}}});
    draft.accept(revision(2));
    draft.observe({...layer, placements: {}}, revision(3));
    expect(() => draft.undo()).toThrow(/changed/);
    expect(draft.dirty).toBe(false);
  });
  it('treats lists as atomic and preserves drafts when rebase detects overlapping changes', () => {
    const draft = new LayoutDraft(layer, revision(1));
    draft.edit({...layer, placements: {machine: {position: [3, 2], visual: {label: 'old'}}}});
    draft.observe({...layer, placements: {machine: {position: [1, 4], visual: {label: 'old'}}}}, revision(2));
    expect(() => draft.rebase('keep')).toThrow(/changed/);
    expect(draft.layer.placements).toEqual({machine: {position: [3, 2], visual: {label: 'old'}}});
    draft.rebase('reload');
    expect(draft.dirty).toBe(false);
    expect(draft.revision).toEqual(revision(2));
  });
  it('does not consume undo history until a write succeeds and clears redo after a new edit', () => {
    const draft = new LayoutDraft(layer, revision(1));
    draft.edit({...layer, grid: {size: 1}}); draft.accept(revision(2));
    draft.undo(); const pending = draft.proposal();
    expect(draft.proposal()).toEqual(pending);
    draft.accept(revision(3)); expect(draft.canRedo).toBe(true);
    draft.edit({...layer, grid: {size: 2}}); draft.accept(revision(4));
    expect(draft.canRedo).toBe(false);
  });
  it('owns copied values, handles literal prototype keys safely and bounds history', () => {
    const draft = new LayoutDraft(layer, revision(1), {entries: 2, bytes: 4096});
    const next = JSON.parse('{"schema_version":"1.0","placements":{"__proto__":{"visual":{"label":"safe"}}}}');
    draft.edit(next); next.placements.__proto__.visual.label = 'mutated';
    expect(JSON.stringify(draft.layer)).toContain('safe');
    draft.accept(revision(2));
    for (let i = 3; i <= 6; i++) {draft.edit({schema_version: '1.0', grid: {size: i}}); draft.accept(revision(i));}
    expect(draft.historySize).toBeLessThanOrEqual(2);
    expect(({} as Record<string, unknown>).visual).toBeUndefined();
    expect(() => draft.edit({schema_version: '1.0', grid: {size: NaN}})).toThrow(/finite/);
  });
});

it('keeps an immutable render snapshot stable through unrelated UI updates', () => {
  const draft = new LayoutDraft(layer, revision(1));
  const snapshot = draft.snapshot;
  expect(draft.snapshot).toBe(snapshot);
  expect(Object.isFrozen(snapshot)).toBe(true);
  expect(Object.isFrozen(snapshot.placements)).toBe(true);
  draft.edit({...layer, grid: {dx: 2}});
  expect(draft.snapshot).not.toBe(snapshot);
  expect(snapshot).toEqual(layer);
});
