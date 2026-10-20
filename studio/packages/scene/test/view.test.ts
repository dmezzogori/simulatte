import {describe, expect, it} from 'vitest';
import {boundsOfCamera, cameraForBounds, glyphVisible, labelVisible, worldToScreen} from '../src/view';
import type {Glyph} from '../src/project';

const glyph = (id: string, kind = 'server'): Glyph => ({id, kind, label: id, x: 0, y: 0, width: 1, depth: 1, rotation: 0, status: '', color: 0, queue: 0, unplaced: false, diagnostic: null, badge: '', shape: 'box'} as unknown as Glyph);

describe('camera framing', () => {
  it('contains the saved bounds with letterboxing and converts back to the visible world rectangle', () => {
    const wide = cameraForBounds([0, 0, 20, 10], 800, 600);
    expect(wide.scale).toBeCloseTo(40);
    expect(worldToScreen(wide, 0, 0)).toEqual({x: 0, y: 500});
    expect(worldToScreen(wide, 20, 10)).toEqual({x: 800, y: 100});
    const back = boundsOfCamera(wide, 800, 600);
    expect(back[0]).toBeCloseTo(0);
    expect(back[2]).toBeCloseTo(20);
    expect(back[1]).toBeCloseTo(-2.5);
    expect(back[3]).toBeCloseTo(12.5);
    const tall = cameraForBounds([-5, -5, 5, 35], 800, 600);
    expect(tall.scale).toBeCloseTo(15);
    expect(worldToScreen(tall, 0, 15)).toEqual({x: 400, y: 300});
    expect(() => cameraForBounds([1, 0, 1, 1], 800, 600)).toThrow(RangeError);
    expect(() => cameraForBounds([0, 0, 1, 1], 0, 600)).toThrow(RangeError);
  });
});
describe('visibility filters', () => {
  it('hides unlisted entities and honours explicit label lists regardless of zoom', () => {
    const all = {entities: null, labels: null};
    expect(glyphVisible(glyph('a'), all)).toBe(true);
    expect(glyphVisible(glyph('a'), {entities: new Set(['b']), labels: null})).toBe(false);
    expect(glyphVisible(glyph('a'), {entities: new Set(['a']), labels: null})).toBe(true);
    expect(labelVisible(glyph('a'), all, null, 50)).toBe(true);
    expect(labelVisible(glyph('a'), all, null, 10)).toBe(false);
    expect(labelVisible(glyph('j', 'job'), all, null, 50)).toBe(false);
    expect(labelVisible(glyph('n', 'node'), all, null, 50)).toBe(false);
    expect(labelVisible(glyph('n', 'node'), all, 'n', 50)).toBe(true);
    const picked = {entities: null, labels: new Set(['a'])};
    expect(labelVisible(glyph('a'), picked, null, 10)).toBe(true);
    expect(labelVisible(glyph('b'), picked, null, 50)).toBe(false);
    expect(labelVisible(glyph('a'), {entities: new Set(['b']), labels: new Set(['a'])}, null, 50)).toBe(false);
  });
});
